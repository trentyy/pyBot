"""反應身分組：對任一訊息加上表情，成員點表情就能取得身分組。"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import reply
from ..db import KIND_BUTTON, KIND_REACTION, MODE_UNIQUE, MODE_VERIFY
from ..roles import give_roles, take_roles
from ..utils import UserError, emoji_key, parse_emoji, resolve_message, role_assign_problem

if TYPE_CHECKING:
    from ..bot import DcManager

MODE_CHOICES = [
    app_commands.Choice(name="一般：點表情取得，取消表情移除", value="normal"),
    app_commands.Choice(name="唯一：同一則訊息只能選一個（例如顏色）", value="unique"),
    app_commands.Choice(name="驗證：只能取得，取消表情不會移除", value="verify"),
]
MODE_LABEL = {c.value: c.name.split("：")[0] for c in MODE_CHOICES}


def message_url(guild_id: int, channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


@app_commands.guild_only()
@app_commands.default_permissions(manage_roles=True)
class ReactionRoles(commands.GroupCog, group_name="reactionrole", group_description="反應身分組設定"):
    def __init__(self, bot: "DcManager"):
        self.bot = bot
        super().__init__()

    # ---------- 指令 ----------

    @app_commands.command(name="add", description="在訊息上新增「表情 → 身分組」")
    @app_commands.describe(message="訊息連結或 ID（ID 只限目前頻道）", emoji="表情符號", role="要發放的身分組")
    async def add(self, interaction: discord.Interaction, message: str, emoji: str, role: discord.Role):
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await resolve_message(interaction, message)
        existing = await self.bot.db.get_message(msg.id)
        if existing and existing.kind == KIND_BUTTON:
            raise UserError("這則訊息是按鈕身分組面板，請用 `/buttonrole add`。")
        if problem := role_assign_problem(role, interaction.user):  # type: ignore[arg-type]
            raise UserError(problem)

        partial = parse_emoji(emoji)
        key = emoji_key(partial)
        same = await self.bot.db.get_binding_by_emoji(msg.id, key)
        if same and same.role_id != role.id:
            raise UserError(f"這個表情已經綁定 <@&{same.role_id}>，請先移除。")

        try:
            await msg.add_reaction(partial)
        except discord.Forbidden:
            raise UserError("bot 在該頻道沒有「新增反應」權限。") from None
        except discord.HTTPException:
            raise UserError("無法使用這個表情。自訂表情必須來自 bot 也在的伺服器。") from None

        await self.bot.db.add_message(msg.id, interaction.guild_id, msg.channel.id, KIND_REACTION)  # type: ignore[arg-type]
        try:
            await self.bot.db.add_binding(msg.id, role.id, emoji_key=key, emoji=str(partial))
        except sqlite3.IntegrityError:
            raise UserError("這個表情已經被使用了。") from None
        self.bot.tracked_messages.add(msg.id)
        await reply(interaction, f"✅ 已設定：{partial} → {role.mention}\n{msg.jump_url}")

    @app_commands.command(name="remove", description="移除訊息上某個身分組的反應設定")
    @app_commands.describe(message="訊息連結或 ID", role="要移除設定的身分組")
    async def remove(self, interaction: discord.Interaction, message: str, role: discord.Role):
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await resolve_message(interaction, message)
        binding = await self.bot.db.get_binding_by_role(msg.id, role.id)
        if binding is None:
            raise UserError(f"這則訊息沒有設定 {role.mention}。")
        await self.bot.db.remove_binding(msg.id, role.id)
        if binding.emoji:
            try:
                await msg.remove_reaction(discord.PartialEmoji.from_str(binding.emoji), interaction.guild.me)  # type: ignore[union-attr]
            except discord.HTTPException:
                pass
        if not await self.bot.db.get_bindings(msg.id):
            await self.bot.db.delete_messages([msg.id])
            self.bot.tracked_messages.discard(msg.id)
        await reply(interaction, f"✅ 已移除 {binding.emoji} → {role.mention}（已經拿到的成員不受影響）")

    @app_commands.command(name="mode", description="設定訊息的領取模式")
    @app_commands.describe(message="訊息連結或 ID", mode="模式")
    @app_commands.choices(mode=MODE_CHOICES)
    async def mode(self, interaction: discord.Interaction, message: str, mode: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await resolve_message(interaction, message)
        record = await self.bot.db.get_message(msg.id)
        if record is None or record.kind != KIND_REACTION:
            raise UserError("這則訊息還沒有設定反應身分組。")
        await self.bot.db.set_mode(msg.id, mode.value)
        await reply(interaction, f"✅ 模式已設為「{mode.name}」")

    @app_commands.command(name="clear", description="清除訊息上所有反應身分組設定")
    @app_commands.describe(message="訊息連結或 ID")
    async def clear(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await resolve_message(interaction, message)
        record = await self.bot.db.get_message(msg.id)
        if record is None or record.kind != KIND_REACTION:
            raise UserError("這則訊息沒有反應身分組設定。")
        bindings = await self.bot.db.get_bindings(msg.id)
        await self.bot.db.delete_messages([msg.id])
        self.bot.tracked_messages.discard(msg.id)
        for b in bindings:
            if b.emoji:
                try:
                    await msg.remove_reaction(discord.PartialEmoji.from_str(b.emoji), interaction.guild.me)  # type: ignore[union-attr]
                except discord.HTTPException:
                    pass
        await reply(interaction, f"✅ 已清除 {len(bindings)} 個設定。")

    @app_commands.command(name="list", description="列出這個伺服器的反應身分組")
    async def list_(self, interaction: discord.Interaction):
        records = await self.bot.db.list_messages(interaction.guild_id, KIND_REACTION)  # type: ignore[arg-type]
        if not records:
            await reply(interaction, "這個伺服器還沒有反應身分組。用 `/reactionrole add` 新增。")
            return
        embed = discord.Embed(title="反應身分組", color=discord.Color.blurple())
        for record in records[:25]:
            bindings = await self.bot.db.get_bindings(record.message_id)
            lines = [f"{b.emoji} → <@&{b.role_id}>" for b in bindings] or ["（無）"]
            url = message_url(record.guild_id, record.channel_id, record.message_id)
            embed.add_field(
                name=f"<#{record.channel_id}> · {MODE_LABEL.get(record.mode, record.mode)}",
                value=f"[跳到訊息]({url})\n" + "\n".join(lines)[:900],
                inline=False,
            )
        if len(records) > 25:
            embed.set_footer(text=f"只顯示前 25 則，共 {len(records)} 則")
        await reply(interaction, embed=embed)

    # ---------- 事件 ----------

    async def _lookup(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.message_id not in self.bot.tracked_messages:
            return None
        if self.bot.user and payload.user_id == self.bot.user.id:
            return None
        record = await self.bot.db.get_message(payload.message_id)
        if record is None or record.kind != KIND_REACTION:
            return None
        binding = await self.bot.db.get_binding_by_emoji(payload.message_id, emoji_key(payload.emoji))
        if binding is None:
            return None
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return None
        role = guild.get_role(binding.role_id)
        if role is None:
            return None
        return guild, record, binding, role

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        found = await self._lookup(payload)
        if found is None or payload.member is None or payload.member.bot:
            return
        guild, record, binding, role = found
        member = payload.member
        async with self.bot.member_lock(guild.id, member.id):
            if record.mode == MODE_UNIQUE:
                others = [b for b in await self.bot.db.get_bindings(record.message_id) if b.role_id != role.id]
                other_roles = {b.role_id: b for b in others}
                had = [r for r in member.roles if r.id in other_roles]
                # 先移除身分組再收回表情，這樣收回表情觸發的移除事件不會重複處理
                await take_roles(self.bot, member, had, "反應身分組（唯一）")
                channel = guild.get_channel_or_thread(record.channel_id)
                if channel is not None and isinstance(channel, discord.abc.Messageable):
                    partial_msg = channel.get_partial_message(record.message_id)  # type: ignore[union-attr]
                    for r in had:
                        emoji = other_roles[r.id].emoji
                        if emoji:
                            try:
                                await partial_msg.remove_reaction(discord.PartialEmoji.from_str(emoji), member)
                            except discord.HTTPException:
                                pass
            await give_roles(self.bot, member, [role], "反應身分組")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        found = await self._lookup(payload)
        if found is None:
            return
        guild, record, binding, role = found
        if record.mode == MODE_VERIFY:
            return
        # 沒有 members intent 時快取的成員資料可能過期，直接向 API 取最新的
        member = guild.get_member(payload.user_id) if self.bot.intents.members else None
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        if member.bot:
            return
        async with self.bot.member_lock(guild.id, member.id):
            await take_roles(self.bot, member, [role], "反應身分組")


async def setup(bot: "DcManager"):
    await bot.add_cog(ReactionRoles(bot))
