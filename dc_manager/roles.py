"""實際發放 / 移除身分組的共用邏輯（反應與按鈕共用）。"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Sequence

import discord

from .utils import notify_member, send_guild_log

if TYPE_CHECKING:
    from .bot import DcManager

log = logging.getLogger(__name__)


async def _report_failure(bot: "DcManager", member: discord.Member, roles: Sequence[discord.Role], action: str, err: Exception) -> None:
    names = "、".join(r.mention for r in roles)
    log.warning("guild=%s 無法%s %s 給 %s：%s", member.guild.id, action, names, member.id, err)
    await send_guild_log(
        bot,
        member.guild,
        f"⚠️ 無法{action} {names}（{member.mention}）：bot 權限不足或身分組位置太高。",
    )


async def give_roles(bot: "DcManager", member: discord.Member, roles: Sequence[discord.Role], reason: str) -> list[discord.Role]:
    """發放成員還沒有的身分組，回傳實際新增的。"""
    todo = [r for r in roles if r not in member.roles]
    if not todo:
        return []
    try:
        await member.add_roles(*todo, reason=reason)
    except discord.HTTPException as err:
        await _report_failure(bot, member, todo, "發放", err)
        return []
    names = "、".join(r.mention for r in todo)
    log.info("guild=%s 發放 %s 給 %s", member.guild.id, [r.id for r in todo], member.id)
    await send_guild_log(bot, member.guild, f"➕ {member.mention} 取得 {names}（{reason}）")
    await notify_member(bot, member, f"你取得了身分組 {'、'.join(r.name for r in todo)}")
    return todo


async def take_roles(bot: "DcManager", member: discord.Member, roles: Sequence[discord.Role], reason: str) -> list[discord.Role]:
    """移除成員擁有的身分組，回傳實際移除的。"""
    todo = [r for r in roles if r in member.roles]
    if not todo:
        return []
    try:
        await member.remove_roles(*todo, reason=reason)
    except discord.HTTPException as err:
        await _report_failure(bot, member, todo, "移除", err)
        return []
    names = "、".join(r.mention for r in todo)
    log.info("guild=%s 移除 %s 的 %s", member.guild.id, member.id, [r.id for r in todo])
    await send_guild_log(bot, member.guild, f"➖ {member.mention} 移除 {names}（{reason}）")
    await notify_member(bot, member, f"你被移除了身分組 {'、'.join(r.name for r in todo)}")
    return todo
