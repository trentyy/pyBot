"""伺服器設定（紀錄頻道、私訊通知）與新成員自動身分組。"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING
from zoneinfo import available_timezones

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import reply
from ..roles import give_roles
from ..schedule import DEFAULT_TIMEZONE, is_valid_zone
from ..utils import UserError, role_assign_problem

if TYPE_CHECKING:
    from ..bot import DcManager


@lru_cache(maxsize=1)
def all_zones() -> list[str]:
    # 常用的放前面
    common = ["Asia/Taipei", "Asia/Tokyo", "Asia/Hong_Kong", "Asia/Shanghai", "UTC"]
    rest = sorted(z for z in available_timezones() if z not in common and "/" in z)
    return common + rest


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Settings(commands.GroupCog, group_name="settings", group_description="dc-manager 伺服器設定"):
    def __init__(self, bot: "DcManager"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="logchannel", description="設定身分組變動的紀錄頻道（不填則關閉）")
    @app_commands.describe(channel="紀錄頻道")
    async def logchannel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        if channel is not None:
            perms = channel.permissions_for(interaction.guild.me)  # type: ignore[union-attr]
            if not (perms.view_channel and perms.send_messages):
                raise UserError(f"bot 需要在 {channel.mention} 有「檢視頻道、傳送訊息」權限。")
        await self.bot.db.set_log_channel(interaction.guild_id, channel.id if channel else None)  # type: ignore[arg-type]
        await reply(interaction, f"✅ 紀錄頻道已設為 {channel.mention}" if channel else "✅ 已關閉紀錄頻道")

    @app_commands.command(name="dmnotify", description="成員取得 / 失去身分組時是否私訊通知")
    @app_commands.describe(enabled="是否開啟")
    async def dmnotify(self, interaction: discord.Interaction, enabled: bool):
        await self.bot.db.set_dm_notify(interaction.guild_id, enabled)  # type: ignore[arg-type]
        await reply(interaction, f"✅ 私訊通知已{'開啟' if enabled else '關閉'}")

    @app_commands.command(name="timezone", description="設定提醒使用的時區（只影響之後建立的提醒）")
    @app_commands.describe(name="時區名稱，例如 Asia/Taipei")
    async def timezone(self, interaction: discord.Interaction, name: str):
        if not is_valid_zone(name):
            raise UserError(f"找不到時區 `{name}`，請從選單挑選，例如 Asia/Taipei。")
        await self.bot.db.set_timezone(interaction.guild_id, name)  # type: ignore[arg-type]
        await reply(interaction, f"✅ 時區已設為 `{name}`（既有的提醒仍使用建立時的時區）")

    @timezone.autocomplete("name")
    async def timezone_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        matches = [z for z in all_zones() if current in z.lower()]
        return [app_commands.Choice(name=z, value=z) for z in matches[:25]]

    @app_commands.command(name="show", description="顯示目前設定")
    async def show(self, interaction: discord.Interaction):
        guild = interaction.guild
        assert guild is not None
        s = await self.bot.db.get_settings(guild.id)
        auto = await self.bot.db.get_auto_roles(guild.id)
        embed = discord.Embed(title=f"{guild.name} 的 dc-manager 設定", color=discord.Color.blurple())
        embed.add_field(name="紀錄頻道", value=f"<#{s.log_channel_id}>" if s.log_channel_id else "未設定")
        embed.add_field(name="私訊通知", value="開啟" if s.dm_notify else "關閉")
        embed.add_field(name="時區", value=s.timezone or f"{DEFAULT_TIMEZONE}（預設）")
        embed.add_field(
            name="自動身分組",
            value="、".join(f"<@&{r}>" for r in auto) if auto else "無",
            inline=False,
        )
        me = guild.me
        embed.add_field(
            name="bot 狀態",
            value=(
                f"最高身分組：{me.top_role.mention}\n"
                f"管理身分組權限：{'✅' if me.guild_permissions.manage_roles else '❌'}\n"
                f"Members intent：{'✅' if self.bot.intents.members else '❌（自動身分組無法使用）'}"
            ),
            inline=False,
        )
        await reply(interaction, embed=embed)


@app_commands.guild_only()
@app_commands.default_permissions(manage_roles=True)
class AutoRoles(commands.GroupCog, group_name="autorole", group_description="新成員自動取得的身分組"):
    def __init__(self, bot: "DcManager"):
        self.bot = bot
        super().__init__()

    def _require_intent(self) -> None:
        if not self.bot.intents.members:
            raise UserError("bot 主機沒有開啟 SERVER MEMBERS INTENT，自動身分組無法使用。請聯絡 bot 管理者。")

    @app_commands.command(name="add", description="新增新成員自動取得的身分組")
    async def add(self, interaction: discord.Interaction, role: discord.Role):
        self._require_intent()
        if problem := role_assign_problem(role, interaction.user):  # type: ignore[arg-type]
            raise UserError(problem)
        added = await self.bot.db.add_auto_role(interaction.guild_id, role.id)  # type: ignore[arg-type]
        await reply(interaction, f"✅ 新成員會自動取得 {role.mention}" if added else f"{role.mention} 已經在清單中。")

    @app_commands.command(name="remove", description="移除自動身分組")
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        removed = await self.bot.db.remove_auto_role(interaction.guild_id, role.id)  # type: ignore[arg-type]
        await reply(interaction, f"✅ 已移除 {role.mention}" if removed else f"{role.mention} 不在清單中。")

    @app_commands.command(name="list", description="列出自動身分組")
    async def list_(self, interaction: discord.Interaction):
        roles = await self.bot.db.get_auto_roles(interaction.guild_id)  # type: ignore[arg-type]
        await reply(interaction, "自動身分組：" + ("、".join(f"<@&{r}>" for r in roles) if roles else "無"))

    async def _apply(self, member: discord.Member) -> None:
        if member.bot:
            return
        role_ids = await self.bot.db.get_auto_roles(member.guild.id)
        roles = [r for r in (member.guild.get_role(i) for i in role_ids) if r and not role_assign_problem(r)]
        if roles:
            await give_roles(self.bot, member, roles, "自動身分組")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # 有成員驗證（規則畫面）的伺服器，等成員通過後再給
        if not member.pending:
            await self._apply(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.pending and not after.pending:
            await self._apply(after)


async def setup(bot: "DcManager"):
    await bot.add_cog(Settings(bot))
    await bot.add_cog(AutoRoles(bot))
