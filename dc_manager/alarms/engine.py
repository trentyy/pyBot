"""警報規則引擎：收到數值後判斷規則、發出 / 重複 / 解除提醒。

狀態流程（每條規則）：
    ok ──達到條件──> pending（需持續 for_seconds）──> firing ──回到恢復門檻──> ok
提醒事件（alarm_events）：firing ──按已知悉──> acked ──恢復──> resolved
無資料警報是獨立的事件，收到新數值就自動解除。
"""
from __future__ import annotations

import asyncio
import logging
import math
import operator as op
import re
import time as _time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import discord

from ..db import AlarmEvent, AlarmRule
from ..targets import target_mentions, targets_text
from ..utils import send_guild_log

if TYPE_CHECKING:
    from ..bot import DcManager

log = logging.getLogger(__name__)

PREFIX = "dcm:alarm:"
METRIC_RE = re.compile(r"^[A-Za-z0-9_.\-:/]{1,64}$")
OPERATORS: dict[str, Callable[[float, float], bool]] = {">=": op.ge, ">": op.gt, "<=": op.le, "<": op.lt}
OP_LABEL = {">=": "≥", ">": ">", "<=": "≤", "<": "<"}
KIND_NOTIFY = "notify"  # 達到就提醒一次
KIND_ALARM = "alarm"  # 重複提醒直到有人按已知悉
METRIC_FLUSH_SECONDS = 60


class InvalidReading(ValueError):
    pass


def validate_metric(name: str) -> str:
    name = (name or "").strip()
    if not METRIC_RE.match(name):
        raise InvalidReading("metric 名稱只能用英數字與 . _ - : /，長度 1~64，例如 furnace1.temp")
    return name


def validate_value(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidReading("value 必須是數字")
    value = float(value)
    if not math.isfinite(value):
        raise InvalidReading("value 不能是 NaN 或無限大")
    return value


def triggered(rule: AlarmRule, value: float) -> bool:
    return OPERATORS[rule.operator](value, rule.threshold)


def cleared(rule: AlarmRule, value: float) -> bool:
    """是否已恢復。有設恢復門檻時要回到門檻才算（遲滯），避免在門檻附近反覆觸發。"""
    if rule.clear_threshold is None:
        return not triggered(rule, value)
    if rule.operator in (">=", ">"):
        return value <= rule.clear_threshold
    return value >= rule.clear_threshold


def condition_text(rule: AlarmRule) -> str:
    text = f"`{rule.metric}` {OP_LABEL[rule.operator]} {rule.threshold:g}"
    if rule.for_seconds:
        text += f"，持續 {rule.for_seconds} 秒"
    if rule.clear_threshold is not None:
        text += f"（回到 {rule.clear_threshold:g} 才算恢復）"
    return text


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


@dataclass
class Latest:
    value: float
    ts: int
    source: str | None


# ---------- 訊息外觀 ----------


def event_embed(rule: AlarmRule, ev: AlarmEvent, current: float | None = None) -> discord.Embed:
    if ev.kind == "nodata":
        title = f"📡 {rule.name}：沒有收到資料"
    else:
        title = f"{'🚨' if rule.kind == KIND_ALARM else '🔔'} {rule.name}"
    if ev.status == "resolved":
        color = discord.Color.green()
    elif ev.status == "acked":
        color = discord.Color.gold()
    else:
        color = discord.Color.red() if rule.kind == KIND_ALARM else discord.Color.orange()

    embed = discord.Embed(title=title, color=color)
    if ev.kind == "nodata":
        embed.description = f"超過 {rule.nodata_minutes} 分鐘沒有收到 `{rule.metric}` 的數值，請檢查儀器電腦或網路。"
    else:
        embed.add_field(name="觸發數值", value=fmt(ev.value))
        if rule.kind == KIND_ALARM and ev.peak_value is not None and ev.peak_value != ev.value:
            embed.add_field(name="極值", value=fmt(ev.peak_value))
        if current is not None and ev.status != "resolved":
            embed.add_field(name="目前", value=fmt(current))
        embed.add_field(name="條件", value=condition_text(rule), inline=False)
    embed.add_field(name="開始", value=f"<t:{ev.started_at}:f>")
    embed.add_field(name="負責", value=targets_text(rule))

    notes = []
    if ev.acked_by:
        notes.append(f"👀 <@{ev.acked_by}> 已知悉（<t:{ev.acked_at}:t>）")
    if ev.silenced_until and ev.status == "firing":
        notes.append(f"🔕 靜音到 <t:{ev.silenced_until}:t>")
    if ev.status == "resolved":
        duration = (ev.resolved_at or ev.started_at) - ev.started_at
        notes.append(f"✅ 已恢復（<t:{ev.resolved_at}:f>，持續 {format_duration(duration)}）")
        if current is not None and ev.kind == "threshold":
            notes.append(f"恢復時數值：{fmt(current)}")
    if notes:
        embed.add_field(name="狀態", value="\n".join(notes), inline=False)
    embed.set_footer(text=f"規則 #{rule.id}")
    return embed


def event_view(rule: AlarmRule, ev: AlarmEvent) -> discord.ui.View | None:
    if ev.status != "firing":
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(style=discord.ButtonStyle.success, label="已知悉", emoji="👀", custom_id=f"{PREFIX}ack:{ev.id}")
    )
    if rule.kind == KIND_ALARM and rule.repeat_minutes:
        view.add_item(
            discord.ui.Button(style=discord.ButtonStyle.secondary, label="靜音", emoji="🔕", custom_id=f"{PREFIX}silence:{ev.id}")
        )
    return view


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分鐘"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小時 {minutes} 分" if minutes else f"{hours} 小時"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小時" if hours else f"{days} 天"


# ---------- 引擎 ----------


class AlarmEngine:
    def __init__(self, bot: "DcManager", *, clock: Callable[[], float] = _time.time):
        self.bot = bot
        self.clock = clock
        self.latest: dict[tuple[int, str], Latest] = {}
        self._dirty: set[tuple[int, str]] = set()
        self._source_seen: dict[int, int] = {}
        self._last_flush = 0
        self.started_at = self.now()
        # 數值處理、排程、按鈕共用一把鎖，避免同時改同一條規則
        self.lock = asyncio.Lock()

    def now(self) -> int:
        return int(self.clock())

    @property
    def db(self):
        return self.bot.db

    async def load(self) -> None:
        for guild_id, metric, value, ts, source in await self.db.load_alarm_metrics():
            self.latest[(guild_id, metric)] = Latest(value, ts, source)
        self.started_at = self.now()
        self._last_flush = self.started_at

    def metrics_for(self, guild_id: int) -> dict[str, Latest]:
        return {m: v for (g, m), v in self.latest.items() if g == guild_id}

    def mark_source_seen(self, source_id: int, ts: int) -> None:
        self._source_seen[source_id] = ts

    async def flush(self, force: bool = False) -> None:
        now = self.now()
        if not force and now - self._last_flush < METRIC_FLUSH_SECONDS:
            return
        self._last_flush = now
        rows = []
        for key in self._dirty:
            latest = self.latest.get(key)
            if latest:
                rows.append((key[0], key[1], latest.value, latest.ts, latest.source))
        self._dirty.clear()
        await self.db.save_alarm_metrics(rows)
        seen, self._source_seen = self._source_seen, {}
        await self.db.touch_alarm_sources(seen)

    # ---------- 收到數值 ----------

    async def ingest(self, guild_id: int, metric: str, value, ts: int | None = None, source: str | None = None) -> None:
        metric = validate_metric(metric)
        value = validate_value(value)
        now = self.now()
        ts = now if ts is None else int(ts)
        key = (guild_id, metric)
        current = self.latest.get(key)
        if current is not None and ts < current.ts:
            return  # 比目前的還舊，忽略
        self.latest[key] = Latest(value, ts, source)
        self._dirty.add(key)
        async with self.lock:
            for rule in await self.db.list_alarm_rules(guild_id, metric=metric, active_only=True):
                await self._evaluate(rule, value, now)

    async def _evaluate(self, rule: AlarmRule, value: float, now: int) -> None:
        if rule.nodata_event_id:
            await self._resolve(rule, rule.nodata_event_id, value, now)
            rule = await self.db.get_alarm_rule(rule.id)  # type: ignore[assignment]

        if triggered(rule, value):
            if rule.state == "ok":
                if rule.for_seconds > 0:
                    await self.db.update_alarm_rule(rule.id, state="pending", pending_since=now)
                else:
                    await self._fire(rule, "threshold", value, now)
            elif rule.state == "pending":
                if now - (rule.pending_since or now) >= rule.for_seconds:
                    await self._fire(rule, "threshold", value, now)
            elif rule.state == "firing" and rule.event_id:
                await self._track_peak(rule, rule.event_id, value)
        else:
            if rule.state == "pending":
                await self.db.update_alarm_rule(rule.id, state="ok", pending_since=None)
            elif rule.state == "firing":
                if cleared(rule, value):
                    if rule.event_id:
                        await self._resolve(rule, rule.event_id, value, now)
                    await self.db.update_alarm_rule(rule.id, state="ok", event_id=None, pending_since=None)
                elif rule.event_id:
                    await self._track_peak(rule, rule.event_id, value)

    async def _track_peak(self, rule: AlarmRule, event_id: int, value: float) -> None:
        ev = await self.db.get_alarm_event(event_id)
        if ev is None or ev.peak_value is None:
            return
        worse = value > ev.peak_value if rule.operator in (">=", ">") else value < ev.peak_value
        if worse:
            await self.db.update_alarm_event(event_id, peak_value=value)

    # ---------- 排程 ----------

    async def tick(self) -> None:
        now = self.now()
        async with self.lock:
            for rule in await self.db.list_alarm_rules(active_only=True):
                latest = self.latest.get((rule.guild_id, rule.metric))
                # 持續時間到了，但之後沒有新數值進來
                if rule.state == "pending" and latest and triggered(rule, latest.value):
                    if now - (rule.pending_since or now) >= rule.for_seconds:
                        await self._fire(rule, "threshold", latest.value, now)
                        rule = await self.db.get_alarm_rule(rule.id)  # type: ignore[assignment]
                # 無資料警報；剛開機時從開機時間起算，避免一開機就誤報
                if rule.nodata_minutes and not rule.nodata_event_id:
                    last = max(latest.ts if latest else 0, rule.created_ts, self.started_at)
                    if now - last >= rule.nodata_minutes * 60:
                        await self._fire(rule, "nodata", None, now)

            for ev in await self.db.firing_alarm_events():
                rule = await self.db.get_alarm_rule(ev.rule_id)
                if rule is None or not rule.active or rule.kind != KIND_ALARM or not rule.repeat_minutes:
                    continue
                if ev.silenced_until and now < ev.silenced_until:
                    continue
                if now - (ev.last_notified_at or ev.started_at) >= rule.repeat_minutes * 60:
                    await self._repeat(rule, ev, now)
        await self.flush()

    # ---------- 發送 ----------

    def _channel(self, guild: discord.Guild, channel_id: int | None):
        channel = guild.get_channel_or_thread(channel_id) if channel_id else None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    def current_value(self, rule: AlarmRule) -> float | None:
        latest = self.latest.get((rule.guild_id, rule.metric))
        return latest.value if latest else None

    async def pause_rule(self, rule: AlarmRule, guild: discord.Guild | None, reason: str) -> None:
        await self.db.update_alarm_rule(rule.id, active=0)
        log.warning("警報規則 #%s 已暫停：%s", rule.id, reason)
        if guild is not None:
            await send_guild_log(self.bot, guild, f"⚠️ 警報規則 #{rule.id}「{rule.name}」已暫停：{reason}")

    async def _fire(self, rule: AlarmRule, kind: str, value: float | None, now: int) -> AlarmEvent | None:
        guild = self.bot.get_guild(rule.guild_id)
        if guild is None:
            return None
        channel = self._channel(guild, rule.channel_id)
        if channel is None:
            await self.pause_rule(rule, guild, "找不到提醒頻道，請用 `/alarm rule edit` 重新設定後 `/alarm rule resume`。")
            return None
        ev = await self.db.create_alarm_event(rule.id, kind, now, value)
        if kind == "nodata":
            text = f"{targets_text(rule)} 📡 **{rule.name}**：超過 {rule.nodata_minutes} 分鐘沒有收到資料"
        else:
            icon = "🚨" if rule.kind == KIND_ALARM else "🔔"
            text = f"{targets_text(rule)} {icon} **{rule.name}**：`{rule.metric}` = {fmt(value)}"
        try:
            msg = await channel.send(
                content=text, embed=event_embed(rule, ev), view=event_view(rule, ev),
                allowed_mentions=target_mentions(rule),
            )
        except (discord.Forbidden, discord.NotFound):
            await self.db.update_alarm_event(ev.id, status="resolved", resolved_at=now)
            await self.pause_rule(rule, guild, f"bot 無法在 <#{rule.channel_id}> 發訊息，請檢查權限後 `/alarm rule resume`。")
            return None
        except discord.HTTPException:
            # 暫時性錯誤：不改規則狀態，下次收到數值 / 下次排程會再試
            await self.db.update_alarm_event(ev.id, status="resolved", resolved_at=now)
            log.warning("警報規則 #%s 發送失敗，稍後重試", rule.id, exc_info=True)
            return None
        await self.db.update_alarm_event(ev.id, channel_id=msg.channel.id, message_id=msg.id)
        if kind == "nodata":
            await self.db.update_alarm_rule(rule.id, nodata_event_id=ev.id)
        else:
            await self.db.update_alarm_rule(rule.id, state="firing", pending_since=None, event_id=ev.id)
        log.info("guild=%s 警報規則 #%s 觸發（%s, value=%s）", rule.guild_id, rule.id, kind, value)
        return await self.db.get_alarm_event(ev.id)

    async def _repeat(self, rule: AlarmRule, ev: AlarmEvent, now: int) -> None:
        guild = self.bot.get_guild(rule.guild_id)
        channel = self._channel(guild, ev.channel_id or rule.channel_id) if guild else None
        if channel is None:
            return
        current = self.current_value(rule)
        what = "仍然沒有收到資料" if ev.kind == "nodata" else f"仍在警報中（目前 {fmt(current)}）"
        content = f"{targets_text(rule)} 🔁 **{rule.name}** {what}，處理後請按「已知悉」。"
        try:
            if ev.message_id:
                ref = discord.MessageReference(message_id=ev.message_id, channel_id=channel.id, fail_if_not_exists=False)  # type: ignore[union-attr]
                await channel.send(content=content, reference=ref, allowed_mentions=target_mentions(rule))
            else:
                msg = await channel.send(
                    content=content, embed=event_embed(rule, ev, current), view=event_view(rule, ev),
                    allowed_mentions=target_mentions(rule),
                )
                await self.db.update_alarm_event(ev.id, channel_id=msg.channel.id, message_id=msg.id)
        except discord.HTTPException:
            log.warning("警報規則 #%s 重複提醒失敗", rule.id, exc_info=True)
        await self.db.update_alarm_event(ev.id, last_notified_at=now)

    async def _resolve(self, rule: AlarmRule, event_id: int, value: float | None, now: int) -> None:
        ev = await self.db.get_alarm_event(event_id)
        if ev is not None and ev.status != "resolved":
            await self.db.update_alarm_event(ev.id, status="resolved", resolved_at=now)
            ev = await self.db.get_alarm_event(ev.id)
            await self.edit_event_message(rule, ev, value)  # type: ignore[arg-type]
            guild = self.bot.get_guild(rule.guild_id)
            channel = self._channel(guild, ev.channel_id) if guild and ev else None  # type: ignore[union-attr]
            # 警報類型另外發一則「已恢復」（不 @ 人）
            if rule.kind == KIND_ALARM and channel is not None and ev and ev.message_id:
                ref = discord.MessageReference(message_id=ev.message_id, channel_id=channel.id, fail_if_not_exists=False)
                what = "已收到資料" if ev.kind == "nodata" else f"已恢復（目前 {fmt(value)}）"
                try:
                    await channel.send(f"✅ **{rule.name}** {what}", reference=ref)
                except discord.HTTPException:
                    pass
            log.info("guild=%s 警報規則 #%s 解除（%s）", rule.guild_id, rule.id, ev.kind if ev else "?")
        if ev is not None and ev.kind == "nodata":
            await self.db.update_alarm_rule(rule.id, nodata_event_id=None)

    async def edit_event_message(self, rule: AlarmRule, ev: AlarmEvent, current: float | None = None) -> None:
        guild = self.bot.get_guild(rule.guild_id)
        channel = self._channel(guild, ev.channel_id) if guild else None
        if channel is None or ev.message_id is None:
            return
        try:
            await channel.get_partial_message(ev.message_id).edit(  # type: ignore[union-attr]
                embed=event_embed(rule, ev, current), view=event_view(rule, ev)
            )
        except discord.HTTPException:
            log.info("無法更新警報訊息 %s", ev.message_id)

    # ---------- 已知悉 / 靜音 ----------

    async def ack(self, event_id: int, user_id: int) -> tuple[AlarmRule | None, AlarmEvent | None, bool]:
        now = self.now()
        async with self.lock:
            ok = await self.db.ack_alarm_event(event_id, user_id, now)
            ev = await self.db.get_alarm_event(event_id)
            rule = await self.db.get_alarm_rule(ev.rule_id) if ev else None
        return rule, ev, ok

    async def silence(self, event_id: int, minutes: int) -> AlarmEvent | None:
        async with self.lock:
            ev = await self.db.get_alarm_event(event_id)
            if ev is None or ev.status != "firing":
                return None
            await self.db.update_alarm_event(event_id, silenced_until=self.now() + minutes * 60)
            return await self.db.get_alarm_event(event_id)

    async def close_rule_events(self, rule: AlarmRule, note: str) -> None:
        """規則刪除前，把還在進行的提醒訊息改成已關閉。"""
        for event_id in (rule.event_id, rule.nodata_event_id):
            if not event_id:
                continue
            ev = await self.db.get_alarm_event(event_id)
            if ev is None or ev.status == "resolved":
                continue
            await self.db.update_alarm_event(ev.id, status="resolved", resolved_at=self.now())
            ev = await self.db.get_alarm_event(ev.id)
            guild = self.bot.get_guild(rule.guild_id)
            channel = self._channel(guild, ev.channel_id) if guild and ev else None  # type: ignore[union-attr]
            if channel is not None and ev and ev.message_id:
                embed = event_embed(rule, ev)
                embed.add_field(name="備註", value=note, inline=False)
                try:
                    await channel.get_partial_message(ev.message_id).edit(embed=embed, view=None)
                except discord.HTTPException:
                    pass
