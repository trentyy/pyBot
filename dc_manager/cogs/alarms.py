"""儀器警報的 Discord 指令、按鈕，以及接收器 / 輪詢 / 排程的啟停。"""
from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..alarms.engine import (
    KIND_ALARM,
    KIND_NOTIFY,
    OP_LABEL,
    PREFIX,
    AlarmEngine,
    InvalidReading,
    condition_text,
    event_embed,
    fmt,
    validate_metric,
)
from ..alarms.http_api import HttpReceiver, hash_token, new_token
from ..alarms.pg import ConfigError, PgPoller, load_config
from ..bot import reply
from ..db import AlarmRule
from ..targets import can_manage, is_admin, is_assignee, resolve_manager_role, target_mentions, targets_text
from ..utils import UserError, send_guild_log

if TYPE_CHECKING:
    from ..bot import DcManager

log = logging.getLogger(__name__)

TICK_SECONDS = 15
OPERATOR_CHOICES = [
    app_commands.Choice(name="≥ 大於等於（例如溫度達到）", value=">="),
    app_commands.Choice(name="> 大於", value=">"),
    app_commands.Choice(name="≤ 小於等於（例如降溫到）", value="<="),
    app_commands.Choice(name="< 小於", value="<"),
]
KIND_CHOICES = [
    app_commands.Choice(name="通知：達到條件提醒一次", value=KIND_NOTIFY),
    app_commands.Choice(name="警報：重複提醒直到有人按已知悉", value=KIND_ALARM),
]
KIND_LABEL = {KIND_NOTIFY: "通知", KIND_ALARM: "警報"}
SILENCE_OPTIONS = [(30, "30 分鐘"), (60, "1 小時"), (240, "4 小時"), (720, "12 小時"), (1440, "24 小時")]
STATE_LABEL = {"ok": "🟢 正常", "pending": "🟡 等待持續時間", "firing": "🔴 觸發中"}


def check_clear_threshold(operator: str, threshold: float, clear: float | None) -> None:
    if clear is None:
        return
    if operator in (">=", ">") and clear > threshold:
        raise UserError("「大於」條件的恢復門檻要小於等於觸發門檻，例如 ≥ 850、恢復門檻 840。")
    if operator in ("<=", "<") and clear < threshold:
        raise UserError("「小於」條件的恢復門檻要大於等於觸發門檻，例如 ≤ 50、恢復門檻 60。")


@app_commands.guild_only()
class Alarms(commands.GroupCog, group_name="alarm", group_description="儀器數值警報"):
    source = app_commands.Group(name="source", description="區網推送來源（需管理伺服器權限）")
    rule = app_commands.Group(name="rule", description="警報規則")

    def __init__(self, bot: "DcManager", *, engine: AlarmEngine | None = None, autostart: bool = True):
        self.bot = bot
        self.engine = engine or AlarmEngine(bot)
        self.autostart = autostart
        self.receiver: HttpReceiver | None = None
        self.poller: PgPoller | None = None
        super().__init__()

    # ---------- 啟停 ----------

    async def cog_load(self) -> None:
        if not self.autostart:
            return
        await self.engine.load()
        cfg = self.bot.config
        if cfg.alarm_http_enabled:
            self.receiver = HttpReceiver(self.engine, cfg.alarm_http_host, cfg.alarm_http_port)
            await self.receiver.start()
        if cfg.alarm_sources_file and cfg.alarm_sources_file.exists():
            try:
                sources = load_config(cfg.alarm_sources_file)
            except (ConfigError, ValueError, OSError) as e:
                log.error("警報資料來源設定檔有誤（%s），資料庫輪詢停用：%s", cfg.alarm_sources_file, e)
            else:
                if sources:
                    self.poller = PgPoller(self.engine, sources)
                    self.poller.start()
        self.scheduler.start()

    async def cog_unload(self) -> None:
        self.scheduler.cancel()
        if self.receiver:
            await self.receiver.stop()
        if self.poller:
            await self.poller.stop()
        try:
            await self.engine.flush(force=True)
        except Exception:
            log.exception("儲存最新數值失敗")

    @tasks.loop(seconds=TICK_SECONDS)
    async def scheduler(self) -> None:
        try:
            await self.engine.tick()
        except Exception:
            log.exception("警報排程錯誤")

    @scheduler.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ---------- 共用 ----------

    async def _get_manageable(self, interaction: discord.Interaction, rule_id: int) -> AlarmRule:
        rule = await self.bot.db.get_alarm_rule(rule_id)
        if rule is None or rule.guild_id != interaction.guild_id:
            raise UserError(f"找不到規則 #{rule_id}。")
        if not can_manage(interaction.user, rule):  # type: ignore[arg-type]
            raise UserError("你沒有管理這條規則的權限。")
        return rule

    def _require_admin(self, interaction: discord.Interaction) -> None:
        if not is_admin(interaction.user):  # type: ignore[arg-type]
            raise UserError("只有伺服器管理員（管理伺服器權限）可以管理推送來源。")

    def _check_channel(self, channel, member: discord.Member) -> None:
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise UserError("請選擇文字頻道。")
        perms = channel.permissions_for(channel.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise UserError(f"bot 需要在 {channel.mention} 有「檢視頻道、傳送訊息、嵌入連結」權限。")
        if not is_admin(member) and not channel.permissions_for(member).send_messages:
            raise UserError(f"你沒有在 {channel.mention} 發言的權限。")

    async def rule_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        member = interaction.user
        if interaction.guild_id is None or not isinstance(member, discord.Member):
            return []
        current = current.strip().lower().lstrip("#")
        out = []
        for r in await self.bot.db.list_alarm_rules(interaction.guild_id):
            if not can_manage(member, r):
                continue
            if current and current not in r.name.lower() and current not in r.metric.lower() and current != str(r.id):
                continue
            suffix = "" if r.active else "（暫停）"
            out.append(app_commands.Choice(name=f"#{r.id} {r.name} · {r.metric}{suffix}"[:100], value=r.id))
        return out[:25]

    async def metric_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        names = set(self.engine.metrics_for(interaction.guild_id))
        if self.poller:
            names.update(self.poller.known_metrics(interaction.guild_id))
        current = current.strip().lower()
        matches = sorted(n for n in names if current in n.lower())
        return [app_commands.Choice(name=n, value=n) for n in matches[:25]]

    async def source_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        names = [s.name for s in await self.bot.db.list_alarm_sources(interaction.guild_id)]
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]

    def rule_embed(self, rule: AlarmRule) -> discord.Embed:
        embed = discord.Embed(title=f"#{rule.id} {rule.name}", color=discord.Color.blurple())
        embed.add_field(name="條件", value=condition_text(rule), inline=False)
        embed.add_field(name="類型", value=KIND_LABEL[rule.kind] + (f"，每 {rule.repeat_minutes} 分鐘重複" if rule.kind == KIND_ALARM and rule.repeat_minutes else ""))
        embed.add_field(name="無資料警報", value=f"{rule.nodata_minutes} 分鐘" if rule.nodata_minutes else "關閉")
        embed.add_field(name="負責", value=targets_text(rule))
        embed.add_field(name="頻道", value=f"<#{rule.channel_id}>")
        embed.add_field(name="管理身分組", value=f"<@&{rule.manager_role_id}>" if rule.manager_role_id else "僅伺服器管理員")
        current = self.engine.latest.get((rule.guild_id, rule.metric))
        state = STATE_LABEL[rule.state] if rule.active else "⏸️ 暫停"
        if current:
            state += f" · 目前 {fmt(current.value)}（<t:{current.ts}:R>）"
        else:
            state += " · 還沒收到數值"
        embed.add_field(name="狀態", value=state, inline=False)
        return embed

    # ---------- /alarm source ----------

    @source.command(name="create", description="建立區網推送來源並產生 token")
    @app_commands.describe(name="來源名稱，例如 furnace-pc")
    async def source_create(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 40]):
        self._require_admin(interaction)
        token = new_token()
        try:
            await self.bot.db.create_alarm_source(interaction.guild_id, name, hash_token(token), interaction.user.id)  # type: ignore[arg-type]
        except sqlite3.IntegrityError:
            raise UserError(f"已經有名為 `{name}` 的來源。要換 token 請用 `/alarm source rotate`。") from None
        await reply(interaction, self._token_message(name, token))

    @source.command(name="rotate", description="重新產生來源的 token（舊 token 立即失效）")
    @app_commands.autocomplete(name=source_autocomplete)
    async def source_rotate(self, interaction: discord.Interaction, name: str):
        self._require_admin(interaction)
        token = new_token()
        if not await self.bot.db.rotate_alarm_source(interaction.guild_id, name, hash_token(token)):  # type: ignore[arg-type]
            raise UserError(f"找不到來源 `{name}`。")
        await reply(interaction, self._token_message(name, token))

    def _token_message(self, name: str, token: str) -> str:
        cfg = self.bot.config
        host = "<樹莓派IP>"
        port = cfg.alarm_http_port
        status = "" if cfg.alarm_http_enabled else "\n⚠️ bot 主機尚未開啟接收器（.env 設定 `ALARM_HTTP_ENABLED=true`）。"
        return (
            f"✅ 來源 `{name}` 的 token（**只顯示這一次**，請妥善保存）：\n```\n{token}\n```"
            f"儀器電腦送出數值的範例（PowerShell）：\n```powershell\n"
            f"Invoke-RestMethod -Method Post -Uri http://{host}:{port}/api/v1/readings `\n"
            f"  -Headers @{{Authorization = 'Bearer {token}'}} -ContentType 'application/json' `\n"
            f"  -Body '{{\"metric\": \"furnace1.temp\", \"value\": 852.3}}'\n```"
            f"也可以用專案裡的 `tools/send_reading.py`。{status}"
        )

    @source.command(name="list", description="列出推送來源")
    async def source_list(self, interaction: discord.Interaction):
        self._require_admin(interaction)
        sources = await self.bot.db.list_alarm_sources(interaction.guild_id)  # type: ignore[arg-type]
        if not sources:
            await reply(interaction, "還沒有推送來源。用 `/alarm source create` 建立。")
            return
        lines = [
            f"• `{s.name}` · 最後連線 " + (f"<t:{s.last_seen_at}:R>" if s.last_seen_at else "從未")
            for s in sources
        ]
        await reply(interaction, "\n".join(lines))

    @source.command(name="delete", description="刪除推送來源（token 立即失效）")
    @app_commands.autocomplete(name=source_autocomplete)
    async def source_delete(self, interaction: discord.Interaction, name: str):
        self._require_admin(interaction)
        if not await self.bot.db.delete_alarm_source(interaction.guild_id, name):  # type: ignore[arg-type]
            raise UserError(f"找不到來源 `{name}`。")
        await reply(interaction, f"🗑️ 已刪除來源 `{name}`")

    # ---------- /alarm rule ----------

    @rule.command(name="create", description="建立警報規則")
    @app_commands.describe(
        name="規則名稱，例如「A 爐加熱完成」",
        metric="監看的數值名稱，例如 furnace1.temp",
        operator="條件",
        threshold="門檻值",
        kind="通知一次，或重複提醒的警報",
        user="要提醒的成員",
        role="要提醒的身分組",
        channel="提醒發在哪個頻道（預設目前頻道）",
        for_seconds="連續達到幾秒才觸發（避免雜訊，預設 0）",
        clear_threshold="回到這個值才算恢復（選填）",
        repeat_minutes="警報類型：每幾分鐘重複提醒（預設 10，0 = 不重複）",
        nodata_minutes="超過幾分鐘沒收到數值就提醒（0 = 關閉）",
        manager_role="這條規則屬於哪個管理身分組",
    )
    @app_commands.choices(operator=OPERATOR_CHOICES, kind=KIND_CHOICES)
    @app_commands.autocomplete(metric=metric_autocomplete)
    async def rule_create(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 100],
        metric: str,
        operator: app_commands.Choice[str],
        threshold: float,
        kind: app_commands.Choice[str] | None = None,
        user: discord.Member | None = None,
        role: discord.Role | None = None,
        channel: discord.TextChannel | None = None,
        for_seconds: app_commands.Range[int, 0, 86400] = 0,
        clear_threshold: float | None = None,
        repeat_minutes: app_commands.Range[int, 0, 1440] = 10,
        nodata_minutes: app_commands.Range[int, 0, 10080] = 0,
        manager_role: discord.Role | None = None,
    ):
        member = interaction.user
        assert isinstance(member, discord.Member) and interaction.guild is not None
        managers = [] if is_admin(member) else await self.bot.db.get_alarm_managers(interaction.guild.id)
        manager_role_id = resolve_manager_role(member, manager_role, managers, "/alarmmanager add")
        try:
            metric = validate_metric(metric)
        except InvalidReading as e:
            raise UserError(str(e)) from None
        if user is None and role is None:
            raise UserError("請至少指定 `user` 或 `role` 其中一個提醒對象。")
        if role is not None and role.is_default():
            raise UserError("不能提醒 @everyone。")
        check_clear_threshold(operator.value, threshold, clear_threshold)
        target_channel = channel or interaction.channel
        self._check_channel(target_channel, member)

        kind_value = kind.value if kind else KIND_NOTIFY
        rule = await self.bot.db.create_alarm_rule(
            guild_id=interaction.guild.id,
            name=name,
            metric=metric,
            operator=operator.value,
            threshold=threshold,
            clear_threshold=clear_threshold,
            for_seconds=for_seconds,
            kind=kind_value,
            repeat_minutes=repeat_minutes if kind_value == KIND_ALARM else 0,
            nodata_minutes=nodata_minutes,
            channel_id=target_channel.id,  # type: ignore[union-attr]
            target_user_id=user.id if user else None,
            target_role_id=role.id if role else None,
            manager_role_id=manager_role_id,
            created_by=member.id,
            created_ts=self.engine.now(),
        )
        await send_guild_log(self.bot, interaction.guild, f"🆕 {member.mention} 建立了警報規則 #{rule.id}「{rule.name}」")
        known = set(self.engine.metrics_for(interaction.guild.id))
        if self.poller:
            known.update(self.poller.known_metrics(interaction.guild.id))
        hint = "" if metric in known else f"\nℹ️ 還沒收到 `{metric}` 的數值，請確認儀器電腦或資料庫設定的名稱一致。"
        await reply(interaction, f"✅ 已建立規則 #{rule.id}{hint}", embed=self.rule_embed(rule))

    @rule.command(name="edit", description="修改警報規則（只填要改的欄位）")
    @app_commands.choices(operator=OPERATOR_CHOICES, kind=KIND_CHOICES)
    @app_commands.autocomplete(rule_id=rule_autocomplete, metric=metric_autocomplete)
    @app_commands.rename(rule_id="rule")
    async def rule_edit(
        self,
        interaction: discord.Interaction,
        rule_id: int,
        name: app_commands.Range[str, 1, 100] | None = None,
        metric: str | None = None,
        operator: app_commands.Choice[str] | None = None,
        threshold: float | None = None,
        kind: app_commands.Choice[str] | None = None,
        user: discord.Member | None = None,
        role: discord.Role | None = None,
        remove_user: bool = False,
        remove_role: bool = False,
        channel: discord.TextChannel | None = None,
        for_seconds: app_commands.Range[int, 0, 86400] | None = None,
        clear_threshold: float | None = None,
        remove_clear_threshold: bool = False,
        repeat_minutes: app_commands.Range[int, 0, 1440] | None = None,
        nodata_minutes: app_commands.Range[int, 0, 10080] | None = None,
        manager_role: discord.Role | None = None,
    ):
        member = interaction.user
        assert isinstance(member, discord.Member)
        rule = await self._get_manageable(interaction, rule_id)
        changes: dict = {}
        if name is not None:
            changes["name"] = name
        if metric is not None:
            try:
                changes["metric"] = validate_metric(metric)
            except InvalidReading as e:
                raise UserError(str(e)) from None
        if operator is not None:
            changes["operator"] = operator.value
        if threshold is not None:
            changes["threshold"] = threshold
        if clear_threshold is not None:
            changes["clear_threshold"] = clear_threshold
        elif remove_clear_threshold:
            changes["clear_threshold"] = None
        check_clear_threshold(
            changes.get("operator", rule.operator),
            changes.get("threshold", rule.threshold),
            changes.get("clear_threshold", rule.clear_threshold),
        )
        if kind is not None:
            changes["kind"] = kind.value
        if repeat_minutes is not None:
            changes["repeat_minutes"] = repeat_minutes
        if changes.get("kind", rule.kind) == KIND_NOTIFY:
            changes["repeat_minutes"] = 0
        elif kind is not None and kind.value == KIND_ALARM and repeat_minutes is None and not rule.repeat_minutes:
            changes["repeat_minutes"] = 10
        if for_seconds is not None:
            changes["for_seconds"] = for_seconds
        if nodata_minutes is not None:
            changes["nodata_minutes"] = nodata_minutes
        if role is not None and role.is_default():
            raise UserError("不能提醒 @everyone。")
        if user is not None:
            changes["target_user_id"] = user.id
        elif remove_user:
            changes["target_user_id"] = None
        if role is not None:
            changes["target_role_id"] = role.id
        elif remove_role:
            changes["target_role_id"] = None
        if not changes.get("target_user_id", rule.target_user_id) and not changes.get("target_role_id", rule.target_role_id):
            raise UserError("至少要保留一個提醒對象。")
        if channel is not None:
            self._check_channel(channel, member)
            changes["channel_id"] = channel.id
        if manager_role is not None:
            if not is_admin(member) and member.get_role(manager_role.id) is None:
                raise UserError(f"你不是 {manager_role.mention} 的成員，不能把規則交給它管理。")
            changes["manager_role_id"] = manager_role.id
        if not changes:
            raise UserError("沒有要修改的欄位。")
        async with self.engine.lock:
            # 換了監看的數值，舊的觸發狀態就不適用了
            if "metric" in changes and changes["metric"] != rule.metric:
                await self.engine.close_rule_events(rule, "規則已改為監看其他數值")
                changes.update(state="ok", pending_since=None, event_id=None, nodata_event_id=None)
            await self.bot.db.update_alarm_rule(rule.id, **changes)
        rule = await self.bot.db.get_alarm_rule(rule.id)
        await reply(interaction, "✅ 已更新（下一筆數值進來時套用新條件）", embed=self.rule_embed(rule))  # type: ignore[arg-type]

    @rule.command(name="pause", description="暫停警報規則")
    @app_commands.autocomplete(rule_id=rule_autocomplete)
    @app_commands.rename(rule_id="rule")
    async def rule_pause(self, interaction: discord.Interaction, rule_id: int):
        rule = await self._get_manageable(interaction, rule_id)
        async with self.engine.lock:
            await self.bot.db.update_alarm_rule(rule.id, active=0)
        await reply(interaction, f"⏸️ 已暫停規則 #{rule.id}「{rule.name}」")

    @rule.command(name="resume", description="恢復警報規則")
    @app_commands.autocomplete(rule_id=rule_autocomplete)
    @app_commands.rename(rule_id="rule")
    async def rule_resume(self, interaction: discord.Interaction, rule_id: int):
        rule = await self._get_manageable(interaction, rule_id)
        async with self.engine.lock:
            await self.bot.db.update_alarm_rule(rule.id, active=1)
        await reply(interaction, f"▶️ 已恢復規則 #{rule.id}「{rule.name}」")

    @rule.command(name="delete", description="刪除警報規則")
    @app_commands.autocomplete(rule_id=rule_autocomplete)
    @app_commands.rename(rule_id="rule")
    async def rule_delete(self, interaction: discord.Interaction, rule_id: int):
        rule = await self._get_manageable(interaction, rule_id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.engine.lock:
            await self.engine.close_rule_events(rule, "規則已刪除")
            await self.bot.db.delete_alarm_rule(rule.id)
        await send_guild_log(self.bot, interaction.guild, f"🗑️ {interaction.user.mention} 刪除了警報規則 #{rule.id}「{rule.name}」")  # type: ignore[arg-type]
        await reply(interaction, f"🗑️ 已刪除規則 #{rule.id}「{rule.name}」")

    @rule.command(name="test", description="發一則測試提醒，確認頻道與 @ 對象正確")
    @app_commands.autocomplete(rule_id=rule_autocomplete)
    @app_commands.rename(rule_id="rule")
    async def rule_test(self, interaction: discord.Interaction, rule_id: int):
        rule = await self._get_manageable(interaction, rule_id)
        channel = interaction.guild.get_channel_or_thread(rule.channel_id) if interaction.guild else None  # type: ignore[union-attr]
        if not isinstance(channel, discord.abc.Messageable):
            raise UserError("找不到規則的提醒頻道。")
        try:
            await channel.send(
                f"{targets_text(rule)} 🧪 測試：**{rule.name}**（{condition_text(rule)}）",
                allowed_mentions=target_mentions(rule),
            )
        except discord.HTTPException:
            raise UserError(f"bot 無法在 <#{rule.channel_id}> 發訊息。") from None
        await reply(interaction, "✅ 已發出測試提醒")

    @rule.command(name="list", description="列出警報規則")
    async def rule_list(self, interaction: discord.Interaction):
        rules = await self.bot.db.list_alarm_rules(interaction.guild_id)
        if not rules:
            await reply(interaction, "還沒有警報規則。用 `/alarm rule create` 建立。")
            return
        lines = []
        for r in rules:
            state = STATE_LABEL[r.state] if r.active else "⏸️ 暫停"
            lines.append(
                f"**#{r.id} {r.name}** · {state} · {KIND_LABEL[r.kind]}\n"
                f"　`{r.metric}` {OP_LABEL[r.operator]} {r.threshold:g} → {targets_text(r)}"
            )
        embed = discord.Embed(title="警報規則", description="\n".join(lines)[:4000], color=discord.Color.blurple())
        await reply(interaction, embed=embed)

    @rule.command(name="info", description="查看警報規則詳細資訊")
    @app_commands.autocomplete(rule_id=rule_autocomplete)
    @app_commands.rename(rule_id="rule")
    async def rule_info(self, interaction: discord.Interaction, rule_id: int):
        rule = await self.bot.db.get_alarm_rule(rule_id)
        if rule is None or rule.guild_id != interaction.guild_id:
            raise UserError(f"找不到規則 #{rule_id}。")
        embed = self.rule_embed(rule)
        last = await self.bot.db.last_alarm_event(rule.id)
        if last:
            embed.add_field(name="上次觸發", value=f"<t:{last.started_at}:f>（{fmt(last.value)}）", inline=False)
        await reply(interaction, embed=embed)

    # ---------- /alarm status ----------

    @app_commands.command(name="status", description="各數值的最新值")
    async def status(self, interaction: discord.Interaction):
        metrics = self.engine.metrics_for(interaction.guild_id)  # type: ignore[arg-type]
        if not metrics:
            await reply(interaction, "還沒有收到任何數值。")
            return
        rules = await self.bot.db.list_alarm_rules(interaction.guild_id)
        firing = {r.metric for r in rules if r.active and r.state == "firing"}
        lines = []
        for name in sorted(metrics):
            latest = metrics[name]
            icon = "🔴" if name in firing else "⚪"
            lines.append(f"{icon} `{name}` = **{fmt(latest.value)}** · <t:{latest.ts}:R> · {latest.source or ''}")
        embed = discord.Embed(title="數值狀態", description="\n".join(lines)[:4000], color=discord.Color.blurple())
        await reply(interaction, embed=embed)

    # ---------- 按鈕 ----------

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = str((interaction.data or {}).get("custom_id", ""))
        if not custom_id.startswith(PREFIX):
            return
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            return
        try:
            action, raw_id = custom_id[len(PREFIX):].split(":", 1)
            event_id = int(raw_id)
        except ValueError:
            return
        ev = await self.bot.db.get_alarm_event(event_id)
        rule = await self.bot.db.get_alarm_rule(ev.rule_id) if ev else None
        if ev is None or rule is None:
            await reply(interaction, "⚠️ 這個警報已失效。")
            return
        if not (is_assignee(member, rule) or can_manage(member, rule)):
            await reply(interaction, "⚠️ 只有負責的成員或規則管理者可以操作。")
            return

        if action == "ack":
            rule, ev, ok = await self.engine.ack(event_id, member.id)
            if not ok or rule is None or ev is None:
                await reply(interaction, "⚠️ 這個警報已經有人處理了。")
                return
            await interaction.response.edit_message(embed=event_embed(rule, ev, self.engine.current_value(rule)), view=None)
            await send_guild_log(self.bot, interaction.guild, f"👀 {member.mention} 已知悉警報 #{rule.id}「{rule.name}」")

        elif action == "silence":
            if ev.status != "firing":
                await reply(interaction, "⚠️ 這個警報已經處理過了。")
                return
            select = discord.ui.Select(
                custom_id=f"{PREFIX}mute:{ev.id}",
                placeholder="靜音多久？",
                options=[discord.SelectOption(label=label, value=str(minutes)) for minutes, label in SILENCE_OPTIONS],
            )
            view = discord.ui.View(timeout=None)
            view.add_item(select)
            await reply(interaction, "靜音期間不會重複提醒，恢復時仍會通知：", view=view)

        elif action == "mute":
            values = (interaction.data or {}).get("values") or []
            try:
                minutes = int(values[0])
            except (IndexError, ValueError):
                return
            if minutes not in {m for m, _ in SILENCE_OPTIONS}:
                return
            ev = await self.engine.silence(event_id, minutes)
            if ev is None:
                await interaction.response.edit_message(content="⚠️ 這個警報已經處理過了。", view=None)
                return
            await interaction.response.edit_message(content=f"🔕 已靜音到 <t:{ev.silenced_until}:t>", view=None)
            await self.engine.edit_event_message(rule, ev, self.engine.current_value(rule))
            await send_guild_log(self.bot, interaction.guild, f"🔕 {member.mention} 將警報 #{rule.id}「{rule.name}」靜音 {minutes} 分鐘")

    # ---------- 清理 ----------

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        for rid in await self.bot.db.clear_role_from_alarm_rules(role.guild.id, role.id):
            rule = await self.bot.db.get_alarm_rule(rid)
            if rule and rule.active and not rule.target_user_id and not rule.target_role_id:
                await self.engine.pause_rule(rule, role.guild, f"提醒對象身分組 {role.name} 已被刪除。")


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class AlarmManagers(commands.GroupCog, group_name="alarmmanager", group_description="設定誰可以建立警報規則"):
    def __init__(self, bot: "DcManager"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="add", description="讓這個身分組的成員可以建立 / 管理自己的警報規則")
    async def add(self, interaction: discord.Interaction, role: discord.Role):
        if role.is_default():
            raise UserError("不能使用 @everyone。")
        added = await self.bot.db.add_alarm_manager(interaction.guild_id, role.id)  # type: ignore[arg-type]
        await reply(
            interaction,
            f"✅ {role.mention} 的成員現在可以建立警報規則，並管理屬於 {role.mention} 的規則。"
            if added else f"{role.mention} 已經是警報管理身分組。",
        )

    @app_commands.command(name="remove", description="移除警報管理身分組（既有規則不會刪除）")
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        removed = await self.bot.db.remove_alarm_manager(interaction.guild_id, role.id)  # type: ignore[arg-type]
        await reply(interaction, f"✅ 已移除 {role.mention}" if removed else f"{role.mention} 不是警報管理身分組。")

    @app_commands.command(name="list", description="列出警報管理身分組")
    async def list_(self, interaction: discord.Interaction):
        roles = await self.bot.db.get_alarm_managers(interaction.guild_id)  # type: ignore[arg-type]
        await reply(interaction, "警報管理身分組：" + ("、".join(f"<@&{r}>" for r in roles) if roles else "無（只有伺服器管理員能建立）"))


async def setup(bot: "DcManager"):
    await bot.add_cog(Alarms(bot))
    await bot.add_cog(AlarmManagers(bot))
