"""週期提醒：依週期提醒指定成員 / 身分組執行任務，可按「完成」或「延期」。

- 固定週期（fixed）：依原本的週期提醒，例如每月 1 號；沒完成就在下個週期標記為未完成。
- 完成後起算（completion）：完成那天才開始算下一個週期。
- 延期：按的人自己選天數，同一次提醒累計不超過任務設定的上限。
- 權限：伺服器管理員可管理全部；被設為「提醒管理身分組」的成員可建立任務，
  並管理屬於該身分組的任務（例如「A 儀器管理人」）。
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..bot import reply
from ..db import Occurrence, Reminder
from ..schedule import (
    DEFAULT_TIMEZONE,
    SCHEDULE_COMPLETION,
    SCHEDULE_FIXED,
    UNIT_DAY,
    UNIT_MONTH,
    UNIT_WEEK,
    describe_interval,
    first_due,
    fmt_local,
    from_ts,
    get_zone,
    next_after_completion,
    next_fixed_due,
    parse_date,
    parse_time,
    to_ts,
)
from ..targets import can_manage, is_admin, is_assignee, resolve_manager_role, target_mentions, targets_text
from ..utils import UserError, send_guild_log

if TYPE_CHECKING:
    from ..bot import DcManager

log = logging.getLogger(__name__)

PREFIX = "dcm:rem:"
TICK_SECONDS = 30
RETRY_SECONDS = 300  # Discord 暫時性錯誤時，多久後重試
MAX_SELECT_OPTIONS = 25

UNIT_CHOICES = [
    app_commands.Choice(name="天", value=UNIT_DAY),
    app_commands.Choice(name="週", value=UNIT_WEEK),
    app_commands.Choice(name="月", value=UNIT_MONTH),
]
SCHEDULE_CHOICES = [
    app_commands.Choice(name="依原本週期（例如固定每月 1 號）", value=SCHEDULE_FIXED),
    app_commands.Choice(name="從完成那天重新起算", value=SCHEDULE_COMPLETION),
]
SCHEDULE_LABEL = {SCHEDULE_FIXED: "依原本週期", SCHEDULE_COMPLETION: "完成後重新起算"}


# ---------- 純邏輯（方便測試） ----------


def anchor_of(r: Reminder) -> datetime:
    return datetime.fromisoformat(r.anchor_local)


def compute_next_due(
    *,
    anchor: datetime,
    unit: str,
    count: int,
    mode: str,
    tz_name: str,
    now_ts: int,
    has_open: bool,
    last_completed_ts: int | None,
) -> int | None:
    """依任務設定算出下一次要發提醒的時間（UTC timestamp）。None 表示等完成後再算。"""
    tz = get_zone(tz_name)
    now = from_ts(now_ts, tz)
    if mode == SCHEDULE_FIXED:
        return to_ts(next_fixed_due(anchor, unit, count, now), tz)
    if has_open:
        return None
    if anchor > now:
        return to_ts(anchor, tz)
    if last_completed_ts is not None:
        nxt = next_after_completion(from_ts(last_completed_ts, tz), anchor.time(), unit, count)
        if nxt > now:
            return to_ts(nxt, tz)
    # 已經過期：在下一個提醒時刻發
    return to_ts(first_due(None, anchor.time(), now), tz)


def postpone_day_candidates(remaining: int) -> list[int]:
    """選單最多 25 個選項：前 14 天逐日，之後平均分布到上限。"""
    if remaining <= MAX_SELECT_OPTIONS:
        return list(range(1, remaining + 1))
    head = list(range(1, 15))
    slots = MAX_SELECT_OPTIONS - len(head)
    step = (remaining - 14) / slots
    tail = sorted({14 + round(step * (i + 1)) for i in range(slots)})
    return head + [d for d in tail if d > 14]


def postpone_options(r: Reminder, occ: Occurrence, now_ts: int) -> list[tuple[int, int]]:
    """可選的延期天數 → 新提醒時間。延期以「今天 + N 天」的任務提醒時刻計算。
    固定週期不能延到下一個週期之後。"""
    remaining = r.max_postpone_days - occ.postponed_days
    if remaining <= 0:
        return []
    tz = get_zone(r.timezone)
    now = from_ts(now_ts, tz)
    at = anchor_of(r).time()
    options = []
    for days in postpone_day_candidates(remaining):
        ts = to_ts(datetime.combine(now.date() + timedelta(days=days), at), tz)
        if r.schedule_mode == SCHEDULE_FIXED and r.next_due_at is not None and ts >= r.next_due_at:
            break
        options.append((days, ts))
    return options


# ---------- 訊息外觀 ----------

STATE_COLOR = {
    "pending": discord.Color.orange(),
    "snoozed": discord.Color.light_grey(),
    "done": discord.Color.green(),
    "missed": discord.Color.red(),
    "closed": discord.Color.dark_grey(),
}


def build_embed(r: Reminder, occ: Occurrence, state: str, note: str | None = None) -> discord.Embed:
    embed = discord.Embed(title=f"🔔 {r.title}", description=r.description or None, color=STATE_COLOR[state])
    embed.add_field(name="負責", value=targets_text(r))
    embed.add_field(name="期限", value=f"<t:{occ.due_at}:f>")
    embed.add_field(
        name="週期",
        value=f"{describe_interval(r.interval_unit, r.interval_count)} · {SCHEDULE_LABEL[r.schedule_mode]}",
    )
    if state == "snoozed":
        embed.add_field(name="⏰ 已延期", value=f"將於 <t:{occ.remind_at}:f>（<t:{occ.remind_at}:R>）再次提醒", inline=False)
    if occ.postponed_days and r.max_postpone_days:
        embed.add_field(name="延期", value=f"已延 {occ.postponed_days} / {r.max_postpone_days} 天")
    if note:
        embed.add_field(name="狀態", value=note, inline=False)
    embed.set_footer(text=f"任務 #{r.id}")
    return embed


def build_view(r: Reminder, occ: Occurrence, now_ts: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(style=discord.ButtonStyle.success, label="完成", emoji="✅", custom_id=f"{PREFIX}done:{occ.id}")
    )
    if postpone_options(r, occ, now_ts):
        view.add_item(
            discord.ui.Button(style=discord.ButtonStyle.secondary, label="延期", emoji="⏰", custom_id=f"{PREFIX}snooze:{occ.id}")
        )
    return view


# ---------- Cog ----------


@app_commands.guild_only()
class Reminders(commands.GroupCog, group_name="reminder", group_description="週期提醒任務"):
    def __init__(self, bot: "DcManager", *, clock: Callable[[], float] = _time.time, autostart: bool = True):
        self.bot = bot
        self.clock = clock
        self.autostart = autostart
        # 排程與按鈕操作共用一把鎖，避免同時改到同一筆提醒
        self.lock = asyncio.Lock()
        super().__init__()

    def now(self) -> int:
        return int(self.clock())

    async def cog_load(self) -> None:
        if self.autostart:
            self.scheduler.start()

    async def cog_unload(self) -> None:
        self.scheduler.cancel()

    # ---------- 排程 ----------

    @tasks.loop(seconds=TICK_SECONDS)
    async def scheduler(self) -> None:
        try:
            await self.tick()
        except Exception:
            log.exception("提醒排程錯誤")

    @scheduler.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def tick(self) -> None:
        now = self.now()
        async with self.lock:
            for r in await self.bot.db.due_reminders(now):
                await self.fire(r, now)
            for occ in await self.bot.db.snoozed_due(now):
                await self.refire(occ, now)

    def _channel(self, guild: discord.Guild, channel_id: int | None):
        channel = guild.get_channel_or_thread(channel_id) if channel_id else None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _pause_with_reason(self, r: Reminder, guild: discord.Guild | None, reason: str) -> None:
        await self.bot.db.update_reminder(r.id, active=0)
        log.warning("任務 #%s 已暫停：%s", r.id, reason)
        if guild is not None:
            await send_guild_log(self.bot, guild, f"⚠️ 提醒任務 #{r.id}「{r.title}」已暫停：{reason}")

    async def _edit_occ_message(self, guild: discord.Guild, occ: Occurrence, embed: discord.Embed, view=None) -> None:
        channel = self._channel(guild, occ.channel_id)
        if channel is None or occ.message_id is None:
            return
        try:
            await channel.get_partial_message(occ.message_id).edit(embed=embed, view=view)  # type: ignore[union-attr]
        except discord.HTTPException:
            log.info("無法更新提醒訊息 %s", occ.message_id)

    async def _send(self, r: Reminder, channel, occ: Occurrence, now: int, prefix: str = "") -> discord.Message:
        content = f"{prefix}{targets_text(r)} 提醒：**{r.title}**"
        return await channel.send(
            content=content,
            embed=build_embed(r, occ, "pending"),
            view=build_view(r, occ, now),
            allowed_mentions=target_mentions(r),
        )

    async def fire(self, r: Reminder, now: int, *, manual: bool = False) -> Occurrence | None:
        """發出一次新的提醒。manual=True 為手動觸發，不影響固定週期的排程。"""
        guild = self.bot.get_guild(r.guild_id)
        if guild is None:
            return None  # bot 暫時看不到這個伺服器，下次再試
        channel = self._channel(guild, r.channel_id)
        if channel is None:
            await self._pause_with_reason(r, guild, "找不到提醒頻道，請用 `/reminder edit` 重新設定頻道後 `/reminder resume`。")
            return None

        # 上一次還沒完成的提醒標記為未完成
        for old in await self.bot.db.open_occurrences(r.id):
            if await self.bot.db.miss_occurrence(old.id):
                old = await self.bot.db.get_occurrence(old.id)  # type: ignore[assignment]
                await self._edit_occ_message(guild, old, build_embed(r, old, "missed", "⌛ 未完成，已進入下一個週期"))
                await send_guild_log(self.bot, guild, f"⌛ 提醒任務 #{r.id}「{r.title}」上一期未完成")

        due = now if manual or r.next_due_at is None else r.next_due_at
        # 先算好下一次時間：延期選項不能超過下一個週期，按鈕要依此決定
        if r.schedule_mode == SCHEDULE_FIXED:
            if manual:
                next_due = r.next_due_at
            else:
                tz = get_zone(r.timezone)
                after = max(from_ts(due, tz), from_ts(now, tz))
                next_due = to_ts(next_fixed_due(anchor_of(r), r.interval_unit, r.interval_count, after), tz)
        else:
            next_due = None
        upcoming = replace(r, next_due_at=next_due)

        occ = await self.bot.db.create_occurrence(r.id, due, None, None)
        try:
            msg = await self._send(upcoming, channel, occ, now)
        except (discord.Forbidden, discord.NotFound):
            await self.bot.db.delete_occurrence(occ.id)
            await self._pause_with_reason(r, guild, f"bot 無法在 <#{r.channel_id}> 發訊息，請檢查權限後 `/reminder resume`。")
            return None
        except discord.HTTPException:
            await self.bot.db.delete_occurrence(occ.id)
            if not manual:
                await self.bot.db.update_reminder(r.id, next_due_at=now + RETRY_SECONDS)
            log.warning("任務 #%s 發送失敗，稍後重試", r.id, exc_info=True)
            return None
        await self.bot.db.set_occurrence_message(occ.id, msg.channel.id, msg.id)
        await self.bot.db.update_reminder(r.id, next_due_at=next_due)
        log.info("guild=%s 發出提醒 #%s occ=%s", r.guild_id, r.id, occ.id)
        return await self.bot.db.get_occurrence(occ.id)

    async def refire(self, occ: Occurrence, now: int) -> None:
        """延期時間到，重新提醒一次。"""
        r = await self.bot.db.get_reminder(occ.reminder_id)
        guild = self.bot.get_guild(r.guild_id) if r else None
        if r is None or guild is None:
            return
        channel = self._channel(guild, r.channel_id)
        if channel is None:
            await self._pause_with_reason(r, guild, "找不到提醒頻道。")
            return
        reopened = Occurrence(**{**occ.__dict__, "status": "pending"})
        try:
            msg = await self._send(r, channel, reopened, now, prefix="（延期後再次提醒）")
        except (discord.Forbidden, discord.NotFound):
            await self._pause_with_reason(r, guild, f"bot 無法在 <#{r.channel_id}> 發訊息。")
            return
        except discord.HTTPException:
            log.warning("任務 #%s 延期提醒發送失敗，稍後重試", r.id, exc_info=True)
            return
        await self._edit_occ_message(guild, occ, build_embed(r, occ, "closed", "已延期，請看新的提醒訊息"))
        await self.bot.db.reopen_occurrence(occ.id, msg.channel.id, msg.id)

    # ---------- 完成 / 延期 ----------

    async def complete(self, occ_id: int, member: discord.Member) -> tuple[bool, str, Reminder | None, Occurrence | None]:
        """回傳 (成功與否, 訊息, 任務, 更新後的 occurrence)。"""
        now = self.now()
        async with self.lock:
            occ = await self.bot.db.get_occurrence(occ_id)
            r = await self.bot.db.get_reminder(occ.reminder_id) if occ else None
            if occ is None or r is None:
                return False, "這個提醒已失效。", None, None
            if not (is_assignee(member, r) or can_manage(member, r)):
                return False, "只有負責的成員或任務管理者可以按完成。", r, occ
            if not await self.bot.db.complete_occurrence(occ.id, member.id, now):
                return False, "這個提醒已經處理過了。", r, occ
            if r.schedule_mode == SCHEDULE_COMPLETION and not await self.bot.db.open_occurrences(r.id):
                tz = get_zone(r.timezone)
                nxt = next_after_completion(from_ts(now, tz), anchor_of(r).time(), r.interval_unit, r.interval_count)
                await self.bot.db.update_reminder(r.id, next_due_at=to_ts(nxt, tz))
            r = await self.bot.db.get_reminder(r.id)
            occ = await self.bot.db.get_occurrence(occ.id)
        return True, "已完成", r, occ

    def done_embed(self, r: Reminder, occ: Occurrence) -> discord.Embed:
        note = f"✅ <@{occ.completed_by}> 於 <t:{occ.completed_at}:f> 完成"
        if r.active and r.next_due_at:
            note += f"\n下次提醒：<t:{r.next_due_at}:f>"
        return build_embed(r, occ, "done", note)

    async def snooze(self, occ_id: int, member: discord.Member, days: int) -> tuple[bool, str, Reminder | None, Occurrence | None]:
        now = self.now()
        async with self.lock:
            occ = await self.bot.db.get_occurrence(occ_id)
            r = await self.bot.db.get_reminder(occ.reminder_id) if occ else None
            if occ is None or r is None or not occ.is_open:
                return False, "這個提醒已經處理過了。", r, occ
            if not (is_assignee(member, r) or can_manage(member, r)):
                return False, "只有負責的成員或任務管理者可以延期。", r, occ
            options = dict(postpone_options(r, occ, now))
            if days not in options:
                return False, "無法延期這麼多天（超過上限或會超過下一個週期）。", r, occ
            await self.bot.db.snooze_occurrence(occ.id, days, options[days])
            occ = await self.bot.db.get_occurrence(occ.id)
        return True, f"已延期 {days} 天，將於 <t:{occ.remind_at}:f> 再次提醒。", r, occ  # type: ignore[union-attr]

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
            occ_id = int(raw_id)
        except ValueError:
            return

        if action == "done":
            ok, text, r, occ = await self.complete(occ_id, member)
            if ok and r and occ:
                # 先回應互動（3 秒內），再寫紀錄頻道
                await interaction.response.edit_message(embed=self.done_embed(r, occ), view=None)
                await send_guild_log(self.bot, interaction.guild, f"✅ {member.mention} 完成了提醒任務 #{r.id}「{r.title}」")
            else:
                await reply(interaction, f"⚠️ {text}")

        elif action == "snooze":
            occ = await self.bot.db.get_occurrence(occ_id)
            r = await self.bot.db.get_reminder(occ.reminder_id) if occ else None
            if occ is None or r is None or not occ.is_open:
                await reply(interaction, "⚠️ 這個提醒已經處理過了。")
                return
            if not (is_assignee(member, r) or can_manage(member, r)):
                await reply(interaction, "⚠️ 只有負責的成員或任務管理者可以延期。")
                return
            options = postpone_options(r, occ, self.now())
            if not options:
                await reply(interaction, "⚠️ 已達延期上限，或延期會超過下一個週期。")
                return
            tz = get_zone(r.timezone)
            select = discord.ui.Select(
                custom_id=f"{PREFIX}pick:{occ.id}",
                placeholder="要延期幾天？",
                options=[
                    discord.SelectOption(label=f"{d} 天", value=str(d), description=f"到 {fmt_local(from_ts(ts, tz))}")
                    for d, ts in options
                ],
            )
            view = discord.ui.View(timeout=None)
            view.add_item(select)
            remaining = r.max_postpone_days - occ.postponed_days
            await reply(interaction, f"選擇延期天數（這次提醒最多還能延 {remaining} 天）：", view=view)

        elif action == "pick":
            values = (interaction.data or {}).get("values") or []
            try:
                days = int(values[0])
            except (IndexError, ValueError):
                return
            ok, text, r, occ = await self.snooze(occ_id, member, days)
            await interaction.response.edit_message(content=("✅ " if ok else "⚠️ ") + text, view=None)
            if ok and r and occ:
                await self._edit_occ_message(
                    interaction.guild, occ, build_embed(r, occ, "snoozed", f"由 {member.mention} 延期 {days} 天"),
                    build_view(r, occ, self.now()),
                )
                await send_guild_log(self.bot, interaction.guild, f"⏰ {member.mention} 將提醒任務 #{r.id}「{r.title}」延期 {days} 天")

    # ---------- 權限 ----------

    async def _resolve_manager_role(
        self, member: discord.Member, requested: discord.Role | None
    ) -> int | None:
        managers = [] if is_admin(member) else await self.bot.db.get_reminder_managers(member.guild.id)
        return resolve_manager_role(member, requested, managers, "/remindermanager add")

    async def _get_manageable(self, interaction: discord.Interaction, task: int) -> Reminder:
        r = await self.bot.db.get_reminder(task)
        if r is None or r.guild_id != interaction.guild_id:
            raise UserError(f"找不到任務 #{task}。")
        if not can_manage(interaction.user, r):  # type: ignore[arg-type]
            raise UserError("你沒有管理這個任務的權限。")
        return r

    async def task_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        member = interaction.user
        if interaction.guild_id is None or not isinstance(member, discord.Member):
            return []
        current = current.strip().lower().lstrip("#")
        choices = []
        for r in await self.bot.db.list_reminders(interaction.guild_id):
            if not can_manage(member, r):
                continue
            if current and current not in r.title.lower() and current != str(r.id):
                continue
            state = "" if r.active else "（暫停）"
            choices.append(app_commands.Choice(name=f"#{r.id} {r.title}{state}"[:100], value=r.id))
        return choices[:25]

    def _check_channel(self, channel, member: discord.Member) -> None:
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise UserError("請選擇文字頻道。")
        perms = channel.permissions_for(channel.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise UserError(f"bot 需要在 {channel.mention} 有「檢視頻道、傳送訊息、嵌入連結」權限。")
        # 非管理員只能把提醒發到自己能發言的頻道
        if not is_admin(member) and not channel.permissions_for(member).send_messages:
            raise UserError(f"你沒有在 {channel.mention} 發言的權限。")

    def _mention_warning(self, channel, role: discord.Role | None) -> str:
        if role is None or role.mentionable:
            return ""
        if channel.permissions_for(channel.guild.me).mention_everyone:
            return ""
        return f"\n⚠️ {role.mention} 不允許被提及，提醒時不會通知到成員。請開啟「允許任何人 @提及此身分組」或給 bot「提及所有身分組」權限。"

    # ---------- 指令 ----------

    @app_commands.command(name="create", description="建立週期提醒任務")
    @app_commands.describe(
        title="任務名稱，例如「A 儀器校正」",
        every="週期數字，例如 2（搭配單位「週」= 兩週一次）",
        unit="週期單位",
        user="要提醒的成員",
        role="要提醒的身分組",
        channel="提醒發在哪個頻道（預設目前頻道）",
        start_date="第一次提醒日期 YYYY-MM-DD（預設今天或明天）",
        at="提醒時間 HH:MM（預設 09:00）",
        schedule="下次提醒怎麼算",
        max_postpone="每次提醒最多可延期幾天（0 = 不能延期）",
        manager_role="這個任務屬於哪個管理身分組（擁有者可以管理此任務）",
        description="任務說明（可用 \\n 換行）",
    )
    @app_commands.choices(unit=UNIT_CHOICES, schedule=SCHEDULE_CHOICES)
    async def create(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 1, 100],
        every: app_commands.Range[int, 1, 365],
        unit: app_commands.Choice[str],
        user: discord.Member | None = None,
        role: discord.Role | None = None,
        channel: discord.TextChannel | None = None,
        start_date: str | None = None,
        at: str = "09:00",
        schedule: app_commands.Choice[str] | None = None,
        max_postpone: app_commands.Range[int, 0, 365] = 7,
        manager_role: discord.Role | None = None,
        description: app_commands.Range[str, 1, 1000] | None = None,
    ):
        member = interaction.user
        assert isinstance(member, discord.Member) and interaction.guild is not None
        manager_role_id = await self._resolve_manager_role(member, manager_role)
        if user is None and role is None:
            raise UserError("請至少指定 `user` 或 `role` 其中一個提醒對象。")
        if role is not None and role.is_default():
            raise UserError("不能提醒 @everyone。")
        target_channel = channel or interaction.channel
        self._check_channel(target_channel, member)

        settings = await self.bot.db.get_settings(interaction.guild.id)
        tz_name = settings.timezone or DEFAULT_TIMEZONE
        tz = get_zone(tz_name)
        now = self.now()
        try:
            at_time = parse_time(at)
            start = parse_date(start_date) if start_date else None
        except ValueError as e:
            raise UserError(str(e)) from None
        anchor = first_due(start, at_time, from_ts(now, tz))
        mode = schedule.value if schedule else SCHEDULE_FIXED
        if mode == SCHEDULE_COMPLETION and to_ts(anchor, tz) <= now:
            raise UserError("第一次提醒時間已經過了，請指定未來的日期或時間。")

        next_due = compute_next_due(
            anchor=anchor, unit=unit.value, count=every, mode=mode, tz_name=tz_name,
            now_ts=now, has_open=False, last_completed_ts=None,
        )
        r = await self.bot.db.create_reminder(
            guild_id=interaction.guild.id,
            channel_id=target_channel.id,  # type: ignore[union-attr]
            title=title,
            description=description.replace("\\n", "\n") if description else None,
            target_user_id=user.id if user else None,
            target_role_id=role.id if role else None,
            manager_role_id=manager_role_id,
            interval_unit=unit.value,
            interval_count=every,
            anchor_local=anchor.isoformat(timespec="minutes"),
            timezone=tz_name,
            schedule_mode=mode,
            max_postpone_days=max_postpone,
            next_due_at=next_due,
            created_by=member.id,
        )
        await send_guild_log(self.bot, interaction.guild, f"🆕 {member.mention} 建立了提醒任務 #{r.id}「{r.title}」")
        await reply(
            interaction,
            f"✅ 已建立任務 #{r.id}，第一次提醒：<t:{next_due}:F>" + self._mention_warning(target_channel, role),
            embed=self.info_embed(r),
        )

    def info_embed(self, r: Reminder) -> discord.Embed:
        embed = discord.Embed(title=f"#{r.id} {r.title}", description=r.description or None, color=discord.Color.blurple())
        embed.add_field(name="負責", value=targets_text(r))
        embed.add_field(name="頻道", value=f"<#{r.channel_id}>")
        embed.add_field(name="管理身分組", value=f"<@&{r.manager_role_id}>" if r.manager_role_id else "僅伺服器管理員")
        embed.add_field(
            name="週期",
            value=f"{describe_interval(r.interval_unit, r.interval_count)} · {SCHEDULE_LABEL[r.schedule_mode]}\n"
            f"起點 {fmt_local(anchor_of(r))}（{r.timezone}）",
        )
        embed.add_field(name="延期上限", value=f"{r.max_postpone_days} 天" if r.max_postpone_days else "不可延期")
        if not r.active:
            status = "⏸️ 已暫停"
        elif r.next_due_at:
            status = f"下次提醒 <t:{r.next_due_at}:f>（<t:{r.next_due_at}:R>）"
        else:
            status = "等待完成後重新起算"
        embed.add_field(name="狀態", value=status, inline=False)
        return embed

    @app_commands.command(name="edit", description="修改提醒任務（只填要改的欄位）")
    @app_commands.describe(
        task="任務",
        title="任務名稱",
        description="任務說明（可用 \\n 換行）",
        every="週期數字",
        unit="週期單位",
        user="改成提醒這個成員",
        role="改成提醒這個身分組",
        remove_user="不再提醒個人",
        remove_role="不再提醒身分組",
        channel="提醒頻道",
        start_date="新的週期起點日期 YYYY-MM-DD",
        at="提醒時間 HH:MM",
        schedule="下次提醒怎麼算",
        max_postpone="每次提醒最多可延期幾天",
        manager_role="管理身分組",
    )
    @app_commands.choices(unit=UNIT_CHOICES, schedule=SCHEDULE_CHOICES)
    @app_commands.autocomplete(task=task_autocomplete)
    async def edit(
        self,
        interaction: discord.Interaction,
        task: int,
        title: app_commands.Range[str, 1, 100] | None = None,
        description: app_commands.Range[str, 1, 1000] | None = None,
        every: app_commands.Range[int, 1, 365] | None = None,
        unit: app_commands.Choice[str] | None = None,
        user: discord.Member | None = None,
        role: discord.Role | None = None,
        remove_user: bool = False,
        remove_role: bool = False,
        channel: discord.TextChannel | None = None,
        start_date: str | None = None,
        at: str | None = None,
        schedule: app_commands.Choice[str] | None = None,
        max_postpone: app_commands.Range[int, 0, 365] | None = None,
        manager_role: discord.Role | None = None,
    ):
        member = interaction.user
        assert isinstance(member, discord.Member)
        r = await self._get_manageable(interaction, task)
        changes: dict = {}
        if title is not None:
            changes["title"] = title
        if description is not None:
            changes["description"] = description.replace("\\n", "\n")
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
        if not changes.get("target_user_id", r.target_user_id) and not changes.get("target_role_id", r.target_role_id):
            raise UserError("至少要保留一個提醒對象。")
        if channel is not None:
            self._check_channel(channel, member)
            changes["channel_id"] = channel.id
        if max_postpone is not None:
            changes["max_postpone_days"] = max_postpone
        if manager_role is not None:
            if not is_admin(member) and member.get_role(manager_role.id) is None:
                raise UserError(f"你不是 {manager_role.mention} 的成員，不能把任務交給它管理。")
            changes["manager_role_id"] = manager_role.id

        timing_changed = any(v is not None for v in (every, unit, start_date, at, schedule))
        if timing_changed:
            old_anchor = anchor_of(r)
            try:
                at_time = parse_time(at) if at else old_anchor.time()
                start = parse_date(start_date) if start_date else old_anchor.date()
            except ValueError as e:
                raise UserError(str(e)) from None
            anchor = datetime.combine(start, at_time)
            changes.update(
                interval_count=every or r.interval_count,
                interval_unit=unit.value if unit else r.interval_unit,
                anchor_local=anchor.isoformat(timespec="minutes"),
                schedule_mode=schedule.value if schedule else r.schedule_mode,
            )
            last = await self.bot.db.last_completed(r.id)
            changes["next_due_at"] = compute_next_due(
                anchor=anchor,
                unit=changes["interval_unit"],
                count=changes["interval_count"],
                mode=changes["schedule_mode"],
                tz_name=r.timezone,
                now_ts=self.now(),
                has_open=bool(await self.bot.db.open_occurrences(r.id)),
                last_completed_ts=last.completed_at if last else None,
            )
        if not changes:
            raise UserError("沒有要修改的欄位。")
        async with self.lock:
            await self.bot.db.update_reminder(r.id, **changes)
        r = await self.bot.db.get_reminder(r.id)
        await reply(interaction, "✅ 已更新", embed=self.info_embed(r))  # type: ignore[arg-type]

    @app_commands.command(name="pause", description="暫停提醒任務")
    @app_commands.autocomplete(task=task_autocomplete)
    async def pause(self, interaction: discord.Interaction, task: int):
        r = await self._get_manageable(interaction, task)
        async with self.lock:
            await self.bot.db.update_reminder(r.id, active=0)
        await reply(interaction, f"⏸️ 已暫停任務 #{r.id}「{r.title}」")

    @app_commands.command(name="resume", description="恢復提醒任務")
    @app_commands.autocomplete(task=task_autocomplete)
    async def resume(self, interaction: discord.Interaction, task: int):
        r = await self._get_manageable(interaction, task)
        last = await self.bot.db.last_completed(r.id)
        next_due = compute_next_due(
            anchor=anchor_of(r), unit=r.interval_unit, count=r.interval_count, mode=r.schedule_mode,
            tz_name=r.timezone, now_ts=self.now(),
            has_open=bool(await self.bot.db.open_occurrences(r.id)),
            last_completed_ts=last.completed_at if last else None,
        )
        async with self.lock:
            await self.bot.db.update_reminder(r.id, active=1, next_due_at=next_due)
        when = f"下次提醒 <t:{next_due}:f>" if next_due else "目前的提醒完成後重新起算"
        await reply(interaction, f"▶️ 已恢復任務 #{r.id}「{r.title}」，{when}")

    @app_commands.command(name="trigger", description="立刻發出一次提醒（不影響固定週期）")
    @app_commands.autocomplete(task=task_autocomplete)
    async def trigger(self, interaction: discord.Interaction, task: int):
        r = await self._get_manageable(interaction, task)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            occ = await self.fire(r, self.now(), manual=True)
        await reply(interaction, "✅ 已發出提醒" if occ else "❌ 發送失敗，請檢查頻道權限（紀錄頻道會有詳細原因）。")

    @app_commands.command(name="delete", description="刪除提醒任務")
    @app_commands.autocomplete(task=task_autocomplete)
    async def delete(self, interaction: discord.Interaction, task: int):
        r = await self._get_manageable(interaction, task)
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            for occ in await self.bot.db.open_occurrences(r.id):
                await self._edit_occ_message(interaction.guild, occ, build_embed(r, occ, "closed", "任務已刪除"))  # type: ignore[arg-type]
            await self.bot.db.delete_reminder(r.id)
        await send_guild_log(self.bot, interaction.guild, f"🗑️ {interaction.user.mention} 刪除了提醒任務 #{r.id}「{r.title}」")  # type: ignore[arg-type]
        await reply(interaction, f"🗑️ 已刪除任務 #{r.id}「{r.title}」")

    @app_commands.command(name="info", description="查看提醒任務詳細資訊")
    @app_commands.autocomplete(task=task_autocomplete)
    async def info(self, interaction: discord.Interaction, task: int):
        r = await self.bot.db.get_reminder(task)
        if r is None or r.guild_id != interaction.guild_id:
            raise UserError(f"找不到任務 #{task}。")
        embed = self.info_embed(r)
        last = await self.bot.db.last_completed(r.id)
        if last:
            embed.add_field(name="上次完成", value=f"<@{last.completed_by}> · <t:{last.completed_at}:f>", inline=False)
        await reply(interaction, embed=embed)

    @app_commands.command(name="list", description="列出這個伺服器的提醒任務")
    async def list_(self, interaction: discord.Interaction):
        reminders = await self.bot.db.list_reminders(interaction.guild_id)  # type: ignore[arg-type]
        if not reminders:
            await reply(interaction, "還沒有提醒任務。用 `/reminder create` 建立。")
            return
        lines = []
        for r in reminders:
            if not r.active:
                when = "⏸️ 暫停"
            elif r.next_due_at:
                when = f"<t:{r.next_due_at}:R>"
            else:
                when = "等待完成"
            lines.append(
                f"**#{r.id} {r.title}** · {describe_interval(r.interval_unit, r.interval_count)} · {when}\n"
                f"　負責 {targets_text(r)}" + (f" · 管理 <@&{r.manager_role_id}>" if r.manager_role_id else "")
            )
        embed = discord.Embed(title="提醒任務", description="\n".join(lines)[:4000], color=discord.Color.blurple())
        await reply(interaction, embed=embed)

    # ---------- 清理 ----------

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        for rid in await self.bot.db.clear_role_from_reminders(role.guild.id, role.id):
            r = await self.bot.db.get_reminder(rid)
            if r and r.active and not r.target_user_id and not r.target_role_id:
                await self._pause_with_reason(r, role.guild, f"提醒對象身分組 {role.name} 已被刪除。")


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class ReminderManagers(commands.GroupCog, group_name="remindermanager", group_description="設定誰可以建立提醒任務"):
    def __init__(self, bot: "DcManager"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="add", description="讓這個身分組的成員可以建立 / 管理自己的提醒任務")
    async def add(self, interaction: discord.Interaction, role: discord.Role):
        if role.is_default():
            raise UserError("不能使用 @everyone。")
        added = await self.bot.db.add_reminder_manager(interaction.guild_id, role.id)  # type: ignore[arg-type]
        await reply(
            interaction,
            f"✅ {role.mention} 的成員現在可以建立提醒任務，並管理屬於 {role.mention} 的任務。"
            if added else f"{role.mention} 已經是提醒管理身分組。",
        )

    @app_commands.command(name="remove", description="移除提醒管理身分組（既有任務不會刪除）")
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        removed = await self.bot.db.remove_reminder_manager(interaction.guild_id, role.id)  # type: ignore[arg-type]
        await reply(interaction, f"✅ 已移除 {role.mention}" if removed else f"{role.mention} 不是提醒管理身分組。")

    @app_commands.command(name="list", description="列出提醒管理身分組")
    async def list_(self, interaction: discord.Interaction):
        roles = await self.bot.db.get_reminder_managers(interaction.guild_id)  # type: ignore[arg-type]
        await reply(interaction, "提醒管理身分組：" + ("、".join(f"<@&{r}>" for r in roles) if roles else "無（只有伺服器管理員能建立）"))


async def setup(bot: "DcManager"):
    await bot.add_cog(Reminders(bot))
    await bot.add_cog(ReminderManagers(bot))
