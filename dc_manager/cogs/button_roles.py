"""按鈕身分組面板：bot 發一則訊息，成員按按鈕取得 / 移除身分組。
按鈕的 custom_id 帶有身分組 id，bot 重開後不需要重新註冊 View 也能運作。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import reply
from ..db import KIND_BUTTON, MODE_NORMAL, MODE_UNIQUE, MODE_VERIFY, Binding, RoleMessage
from ..roles import give_roles, take_roles
from ..utils import UserError, parse_emoji, resolve_message, role_assign_problem
from .reaction_roles import MODE_CHOICES, MODE_LABEL, message_url

if TYPE_CHECKING:
    from ..bot import DcManager

log = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = "dcm:role:"
MAX_BUTTONS = 25  # Discord 限制：每則訊息 5 列 × 5 個
STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}
STYLE_CHOICES = [
    app_commands.Choice(name="藍色", value="primary"),
    app_commands.Choice(name="灰色", value="secondary"),
    app_commands.Choice(name="綠色", value="success"),
    app_commands.Choice(name="紅色", value="danger"),
]


def build_view(bindings: list[Binding]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for b in bindings[:MAX_BUTTONS]:
        view.add_item(
            discord.ui.Button(
                style=STYLES.get(b.style or "", discord.ButtonStyle.secondary),
                label=b.label or None,
                emoji=b.emoji or None,
                custom_id=f"{CUSTOM_ID_PREFIX}{b.role_id}",
            )
        )
    return view


def footer_text(mode: str) -> str:
    return f"模式：{MODE_LABEL.get(mode, mode)} · 點按鈕取得或移除身分組"


class PanelModal(discord.ui.Modal):
    def __init__(
        self,
        callback: Callable[[discord.Interaction, str, str], Awaitable[None]],
        *,
        title: str = "",
        description: str = "",
    ):
        super().__init__(title="身分組面板內容")
        self._callback = callback
        self.panel_title = discord.ui.TextInput(label="標題", max_length=256, default=title or None)
        self.panel_description = discord.ui.TextInput(
            label="說明（可換行）",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000,
            default=description or None,
        )
        self.add_item(self.panel_title)
        self.add_item(self.panel_description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction, self.panel_title.value, self.panel_description.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if isinstance(error, UserError):
            await reply(interaction, f"⚠️ {error}")
        else:
            log.exception("面板表單錯誤", exc_info=error)
            await reply(interaction, "❌ 發生未預期的錯誤，已記錄在 log。")


@app_commands.guild_only()
@app_commands.default_permissions(manage_roles=True)
class ButtonRoles(commands.GroupCog, group_name="buttonrole", group_description="按鈕身分組面板設定"):
    def __init__(self, bot: "DcManager"):
        self.bot = bot
        super().__init__()

    # ---------- 共用 ----------

    async def _get_panel(self, interaction: discord.Interaction, message: str) -> tuple[discord.Message, RoleMessage]:
        msg = await resolve_message(interaction, message)
        record = await self.bot.db.get_message(msg.id)
        if record is None or record.kind != KIND_BUTTON or msg.author.id != self.bot.user.id:  # type: ignore[union-attr]
            raise UserError("這不是按鈕身分組面板。請先用 `/buttonrole create` 建立面板。")
        return msg, record

    async def refresh_panel(self, msg: discord.Message, record: RoleMessage) -> None:
        bindings = await self.bot.db.get_bindings(msg.id)
        embed = msg.embeds[0] if msg.embeds else discord.Embed()
        embed.set_footer(text=footer_text(record.mode))
        await msg.edit(embed=embed, view=build_view(bindings) if bindings else None)

    # ---------- 指令 ----------

    @app_commands.command(name="create", description="建立新的按鈕身分組面板")
    @app_commands.describe(channel="面板要發在哪個頻道（預設目前頻道）", mode="領取模式")
    @app_commands.choices(mode=MODE_CHOICES)
    async def create(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        mode: app_commands.Choice[str] | None = None,
    ):
        target = channel or interaction.channel
        if not isinstance(target, (discord.TextChannel, discord.Thread)):
            raise UserError("請選擇文字頻道。")
        perms = target.permissions_for(interaction.guild.me)  # type: ignore[union-attr]
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise UserError(f"bot 需要在 {target.mention} 有「檢視頻道、傳送訊息、嵌入連結」權限。")
        mode_value = mode.value if mode else MODE_NORMAL

        async def submit(modal_inter: discord.Interaction, title: str, description: str) -> None:
            embed = discord.Embed(title=title, description=description or None, color=discord.Color.blurple())
            embed.set_footer(text=footer_text(mode_value))
            try:
                sent = await target.send(embed=embed)
            except discord.HTTPException:
                raise UserError(f"無法在 {target.mention} 發送訊息。") from None
            await self.bot.db.add_message(sent.id, sent.guild.id, sent.channel.id, KIND_BUTTON, mode_value)  # type: ignore[union-attr]
            self.bot.tracked_messages.add(sent.id)
            await reply(
                modal_inter,
                f"✅ 面板已建立：{sent.jump_url}\n接著用 `/buttonrole add message:{sent.jump_url} role:...` 加入按鈕。",
            )

        await interaction.response.send_modal(PanelModal(submit))

    @app_commands.command(name="add", description="在面板加入（或更新）一個身分組按鈕")
    @app_commands.describe(
        message="面板的訊息連結或 ID",
        role="按鈕要發放的身分組",
        label="按鈕文字（預設為身分組名稱）",
        emoji="按鈕表情（選填）",
        style="按鈕顏色",
    )
    @app_commands.choices(style=STYLE_CHOICES)
    async def add(
        self,
        interaction: discord.Interaction,
        message: str,
        role: discord.Role,
        label: app_commands.Range[str, 1, 80] | None = None,
        emoji: str | None = None,
        style: app_commands.Choice[str] | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg, record = await self._get_panel(interaction, message)
        if problem := role_assign_problem(role, interaction.user):  # type: ignore[arg-type]
            raise UserError(problem)

        previous = await self.bot.db.get_binding_by_role(msg.id, role.id)
        if previous is None and len(await self.bot.db.get_bindings(msg.id)) >= MAX_BUTTONS:
            raise UserError(f"一個面板最多 {MAX_BUTTONS} 個按鈕，請另外建立新面板。")
        emoji_str = str(parse_emoji(emoji)) if emoji else None
        if not label and not emoji_str:
            label = role.name[:80]

        await self.bot.db.add_binding(
            msg.id, role.id, emoji=emoji_str, label=label, style=style.value if style else "secondary"
        )
        try:
            await self.refresh_panel(msg, record)
        except discord.HTTPException:
            # 多半是表情無效，還原設定
            if previous:
                await self.bot.db.add_binding(
                    msg.id, role.id, emoji=previous.emoji, label=previous.label, style=previous.style
                )
            else:
                await self.bot.db.remove_binding(msg.id, role.id)
            raise UserError("更新面板失敗，請確認表情有效（自訂表情必須來自 bot 也在的伺服器）。") from None
        await reply(interaction, f"✅ 已{'更新' if previous else '加入'} {role.mention} 按鈕。")

    @app_commands.command(name="remove", description="從面板移除某個身分組按鈕")
    @app_commands.describe(message="面板的訊息連結或 ID", role="要移除的身分組")
    async def remove(self, interaction: discord.Interaction, message: str, role: discord.Role):
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg, record = await self._get_panel(interaction, message)
        if not await self.bot.db.remove_binding(msg.id, role.id):
            raise UserError(f"這個面板沒有 {role.mention} 按鈕。")
        await self.refresh_panel(msg, record)
        await reply(interaction, f"✅ 已移除 {role.mention} 按鈕（已經拿到的成員不受影響）。")

    @app_commands.command(name="edit", description="修改面板的標題與說明")
    @app_commands.describe(message="面板的訊息連結或 ID")
    async def edit(self, interaction: discord.Interaction, message: str):
        msg, record = await self._get_panel(interaction, message)
        old = msg.embeds[0] if msg.embeds else discord.Embed()

        async def submit(modal_inter: discord.Interaction, title: str, description: str) -> None:
            embed = discord.Embed(title=title, description=description or None, color=old.color or discord.Color.blurple())
            embed.set_footer(text=footer_text(record.mode))
            await msg.edit(embed=embed)
            await reply(modal_inter, f"✅ 面板已更新：{msg.jump_url}")

        await interaction.response.send_modal(
            PanelModal(submit, title=old.title or "", description=old.description or "")
        )

    @app_commands.command(name="mode", description="設定面板的領取模式")
    @app_commands.describe(message="面板的訊息連結或 ID", mode="模式")
    @app_commands.choices(mode=MODE_CHOICES)
    async def mode(self, interaction: discord.Interaction, message: str, mode: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg, record = await self._get_panel(interaction, message)
        await self.bot.db.set_mode(msg.id, mode.value)
        record = await self.bot.db.get_message(msg.id)
        await self.refresh_panel(msg, record)  # type: ignore[arg-type]
        await reply(interaction, f"✅ 模式已設為「{mode.name}」")

    @app_commands.command(name="list", description="列出這個伺服器的按鈕身分組面板")
    async def list_(self, interaction: discord.Interaction):
        records = await self.bot.db.list_messages(interaction.guild_id, KIND_BUTTON)  # type: ignore[arg-type]
        if not records:
            await reply(interaction, "這個伺服器還沒有按鈕面板。用 `/buttonrole create` 建立。")
            return
        embed = discord.Embed(title="按鈕身分組面板", color=discord.Color.blurple())
        for record in records[:25]:
            bindings = await self.bot.db.get_bindings(record.message_id)
            lines = [f"{b.emoji or ''} {b.label or ''} → <@&{b.role_id}>".strip() for b in bindings] or ["（還沒有按鈕）"]
            url = message_url(record.guild_id, record.channel_id, record.message_id)
            embed.add_field(
                name=f"<#{record.channel_id}> · {MODE_LABEL.get(record.mode, record.mode)}",
                value=f"[跳到面板]({url})\n" + "\n".join(lines)[:900],
                inline=False,
            )
        await reply(interaction, embed=embed)

    # ---------- 按鈕事件 ----------

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = str((interaction.data or {}).get("custom_id", ""))
        if not custom_id.startswith(CUSTOM_ID_PREFIX) or interaction.message is None:
            return
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return

        try:
            role_id = int(custom_id[len(CUSTOM_ID_PREFIX):])
        except ValueError:
            return
        record = await self.bot.db.get_message(interaction.message.id)
        binding = await self.bot.db.get_binding_by_role(interaction.message.id, role_id)
        if record is None or record.kind != KIND_BUTTON or binding is None:
            await reply(interaction, "這個按鈕已失效，請聯絡管理員。")
            return
        role = guild.get_role(role_id)
        if role is None:
            await reply(interaction, "這個身分組已經不存在，請聯絡管理員。")
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.bot.member_lock(guild.id, member.id):
            has_role = role in member.roles
            if record.mode == MODE_VERIFY:
                if has_role:
                    text = f"你已經有 {role.mention} 了。"
                else:
                    ok = await give_roles(self.bot, member, [role], "按鈕身分組")
                    text = f"✅ 已取得 {role.mention}" if ok else "❌ 無法發放身分組，請通知管理員檢查 bot 權限。"
            elif has_role:
                ok = await take_roles(self.bot, member, [role], "按鈕身分組")
                text = f"已移除 {role.mention}" if ok else "❌ 無法移除身分組，請通知管理員檢查 bot 權限。"
            else:
                removed: list[discord.Role] = []
                if record.mode == MODE_UNIQUE:
                    other_ids = {b.role_id for b in await self.bot.db.get_bindings(record.message_id)} - {role.id}
                    removed = await take_roles(
                        self.bot, member, [r for r in member.roles if r.id in other_ids], "按鈕身分組（唯一）"
                    )
                ok = await give_roles(self.bot, member, [role], "按鈕身分組")
                if ok:
                    text = f"✅ 已取得 {role.mention}"
                    if removed:
                        text += f"，並移除 {'、'.join(r.mention for r in removed)}"
                else:
                    text = "❌ 無法發放身分組，請通知管理員檢查 bot 權限。"
        await reply(interaction, text)


async def setup(bot: "DcManager"):
    await bot.add_cog(ButtonRoles(bot))
