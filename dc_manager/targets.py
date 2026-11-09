"""提醒與警報共用：提醒對象、管理權限。

任務 / 規則物件只要有 target_user_id、target_role_id、manager_role_id 三個欄位就能用。
"""
from __future__ import annotations

from typing import Protocol

import discord

from .utils import UserError


class HasTargets(Protocol):
    target_user_id: int | None
    target_role_id: int | None
    manager_role_id: int | None


def is_admin(member: discord.Member) -> bool:
    return member.guild_permissions.manage_guild


def can_manage(member: discord.Member, item: HasTargets) -> bool:
    """伺服器管理員，或擁有該項目管理身分組的人。"""
    return is_admin(member) or (
        item.manager_role_id is not None and member.get_role(item.manager_role_id) is not None
    )


def is_assignee(member: discord.Member, item: HasTargets) -> bool:
    if item.target_user_id == member.id:
        return True
    return item.target_role_id is not None and member.get_role(item.target_role_id) is not None


def targets_text(item: HasTargets) -> str:
    parts = []
    if item.target_user_id:
        parts.append(f"<@{item.target_user_id}>")
    if item.target_role_id:
        parts.append(f"<@&{item.target_role_id}>")
    return " ".join(parts) or "（無）"


def target_mentions(item: HasTargets) -> discord.AllowedMentions:
    """只 @ 提醒對象，其他提及一律不通知。"""
    return discord.AllowedMentions(
        everyone=False,
        users=[discord.Object(item.target_user_id)] if item.target_user_id else False,
        roles=[discord.Object(item.target_role_id)] if item.target_role_id else False,
    )


def resolve_manager_role(
    member: discord.Member, requested: discord.Role | None, managers: list[int], setup_command: str
) -> int | None:
    """決定新項目屬於哪個管理身分組。
    managers：這個功能登記的管理身分組；setup_command：提示管理員用哪個指令登記。"""
    if is_admin(member):
        return requested.id if requested else None
    mine = [rid for rid in managers if member.get_role(rid)]
    if requested is not None:
        if requested.id not in mine:
            raise UserError(f"你不是 {requested.mention} 的成員，或它不是管理身分組。")
        return requested.id
    if len(mine) == 1:
        return mine[0]
    if not mine:
        raise UserError(f"你沒有建立的權限。請伺服器管理員用 `{setup_command}` 設定管理身分組。")
    raise UserError("你有多個管理身分組，請用 `manager_role` 指定這個項目屬於哪一個。")
