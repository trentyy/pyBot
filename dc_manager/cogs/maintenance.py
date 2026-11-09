"""自動清理失效資料，以及 /help。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import reply
from ..db import KIND_BUTTON

if TYPE_CHECKING:
    from ..bot import DcManager

log = logging.getLogger(__name__)

HELP_TEXT = """\
**按鈕身分組面板**（推薦）
`/buttonrole create` 建立面板 → `/buttonrole add` 加入按鈕
`/buttonrole remove` · `/buttonrole edit` · `/buttonrole mode` · `/buttonrole list`

**反應身分組**（可用在任何訊息）
`/reactionrole add` 訊息 + 表情 + 身分組
`/reactionrole remove` · `/reactionrole mode` · `/reactionrole clear` · `/reactionrole list`

**模式**
一般：可自由取得 / 移除　唯一：同一則訊息只能選一個　驗證：只能取得

**週期提醒**
`/reminder create` 例：每 2 週提醒 @A儀器組 做校正，可按「完成」或「延期」
`/reminder edit|pause|resume|trigger|delete|info|list`
`/remindermanager add` 讓某身分組（例如儀器管理人）可建立並管理自己的任務

**儀器警報**
`/alarm rule create` 例：`furnace1.temp` ≥ 850 時提醒 @A儀器組
`/alarm rule edit|pause|resume|delete|test|info|list` · `/alarm status` 最新數值
`/alarm source create` 產生區網推送 token（管理員）· `/alarmmanager add` 設定可建立規則的身分組

**其他**
`/autorole add|remove|list` 新成員自動身分組
`/settings logchannel` 紀錄頻道 · `/settings dmnotify` 私訊通知 · `/settings timezone` 時區 · `/settings show`

**注意**
• bot 的身分組必須排在要發放的身分組**上方**
• 「唯一」模式的反應身分組需要 bot 有「管理訊息」權限（用來收回表情）
• 刪除面板或訊息後，設定會自動清除
"""


class Maintenance(commands.Cog):
    def __init__(self, bot: "DcManager"):
        self.bot = bot

    @app_commands.command(name="help", description="dc-manager 使用說明")
    async def help_(self, interaction: discord.Interaction):
        embed = discord.Embed(title="dc-manager 身分組管理", description=HELP_TEXT, color=discord.Color.blurple())
        embed.set_footer(text=f"延遲 {round(self.bot.latency * 1000)} ms")
        await reply(interaction, embed=embed)

    async def _forget(self, message_ids: set[int]) -> None:
        tracked = message_ids & self.bot.tracked_messages
        if tracked:
            await self.bot.db.delete_messages(tracked)
            self.bot.tracked_messages -= tracked
            log.info("訊息已刪除，清除 %d 筆身分組設定", len(tracked))

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        await self._forget({payload.message_id})

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        await self._forget(set(payload.message_ids))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        affected = await self.bot.db.remove_role_everywhere(role.guild.id, role.id)
        # 按鈕面板要重畫，把已刪除身分組的按鈕拿掉
        cog = self.bot.get_cog("ButtonRoles")
        for message_id in set(affected):
            record = await self.bot.db.get_message(message_id)
            if record is None or record.kind != KIND_BUTTON or cog is None:
                continue
            channel = role.guild.get_channel_or_thread(record.channel_id)
            if not isinstance(channel, discord.abc.Messageable):
                continue
            try:
                msg = await channel.fetch_message(message_id)
                await cog.refresh_panel(msg, record)  # type: ignore[attr-defined]
            except discord.HTTPException:
                log.warning("無法更新面板 %s", message_id, exc_info=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        log.info("加入伺服器 %s (id=%s)，目前共 %d 個", guild.name, guild.id, len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        ids = await self.bot.db.delete_guild(guild.id)
        self.bot.tracked_messages -= set(ids)
        log.info("離開伺服器 %s (id=%s)，已清除其設定", guild.name, guild.id)


async def setup(bot: "DcManager"):
    await bot.add_cog(Maintenance(bot))
