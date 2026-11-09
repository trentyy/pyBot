"""共用工具：訊息連結解析、表情正規化、身分組安全檢查。"""
from __future__ import annotations

import re

import discord

MESSAGE_LINK_RE = re.compile(
    r"https?://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild>\d+|@me)/(?P<channel>\d+)/(?P<message>\d+)"
)


class UserError(Exception):
    """可以直接顯示給使用者看的錯誤訊息。"""


def parse_message_ref(text: str) -> tuple[int | None, int | None, int]:
    """解析訊息連結或訊息 ID，回傳 (guild_id, channel_id, message_id)。"""
    text = text.strip()
    m = MESSAGE_LINK_RE.search(text)
    if m:
        guild = None if m["guild"] == "@me" else int(m["guild"])
        return guild, int(m["channel"]), int(m["message"])
    if text.isdigit():
        return None, None, int(text)
    raise UserError("請輸入訊息連結（右鍵訊息 → 複製訊息連結）或訊息 ID。")


async def resolve_message(interaction: discord.Interaction, text: str) -> discord.Message:
    """依使用者輸入取得訊息，只允許同一個伺服器內的訊息。"""
    guild = interaction.guild
    assert guild is not None
    guild_id, channel_id, message_id = parse_message_ref(text)
    if guild_id is not None and guild_id != guild.id:
        raise UserError("只能使用這個伺服器內的訊息。")

    channel = guild.get_channel_or_thread(channel_id) if channel_id else interaction.channel
    if channel is None or not isinstance(channel, discord.abc.Messageable):
        raise UserError("找不到該頻道，或 bot 看不到它。")
    try:
        return await channel.fetch_message(message_id)
    except discord.NotFound:
        raise UserError("找不到這則訊息。若訊息在其他頻道，請改用訊息連結。") from None
    except discord.Forbidden:
        raise UserError(f"bot 沒有在 {channel.mention} 讀取訊息歷史的權限。") from None


def emoji_key(emoji: discord.PartialEmoji | discord.Emoji | str) -> str:
    """把表情轉成穩定的比對 key：自訂表情用 id（改名也不受影響），Unicode 表情去掉變體選擇符。"""
    if isinstance(emoji, str):
        emoji = discord.PartialEmoji.from_str(emoji.strip())
    if emoji.id:
        return str(emoji.id)
    return (emoji.name or "").replace("️", "")


def parse_emoji(text: str) -> discord.PartialEmoji:
    text = text.strip()
    if not text:
        raise UserError("請輸入表情符號。")
    return discord.PartialEmoji.from_str(text)


def role_assign_problem(role: discord.Role, actor: discord.Member | None = None) -> str | None:
    """檢查身分組能不能讓 bot 發放；actor 是設定的人（防止拿到比自己更高的身分組）。
    回傳 None 表示沒問題，否則回傳原因。"""
    guild = role.guild
    me = guild.me
    if role.is_default():
        return "不能使用 @everyone。"
    if role.managed:
        return f"{role.mention} 由整合服務或 bot 管理，無法手動發放。"
    if not me.guild_permissions.manage_roles:
        return "bot 缺少「管理身分組」權限。"
    if role >= me.top_role:
        return f"{role.mention} 的位置不低於 bot 的最高身分組，請把 bot 的身分組拉到它上面。"
    if actor is not None and actor.id != guild.owner_id and role >= actor.top_role:
        return f"你不能設定位置不低於你自己最高身分組的 {role.mention}。"
    if role.permissions.value & discord.Permissions.elevated().value:
        return f"{role.mention} 含有管理類權限，基於安全不允許自助領取。"
    return None


async def send_guild_log(bot: "discord.Client", guild: discord.Guild, text: str) -> None:
    """寫到該伺服器設定的紀錄頻道；沒設定或沒權限就略過。"""
    db = getattr(bot, "db", None)
    if db is None:
        return
    settings = await db.get_settings(guild.id)
    if not settings.log_channel_id:
        return
    channel = guild.get_channel_or_thread(settings.log_channel_id)
    if channel is None or not isinstance(channel, discord.abc.Messageable):
        return
    try:
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


async def notify_member(bot: "discord.Client", member: discord.Member, text: str) -> None:
    """若伺服器開啟私訊通知，私訊成員；對方關閉私訊時略過。"""
    settings = await bot.db.get_settings(member.guild.id)  # type: ignore[attr-defined]
    if not settings.dm_notify:
        return
    try:
        await member.send(f"**{member.guild.name}**：{text}")
    except discord.HTTPException:
        pass
