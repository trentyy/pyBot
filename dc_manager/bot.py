from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import weakref

import discord
from discord import app_commands
from discord.ext import commands

from .config import Config
from .db import Database
from .utils import UserError

log = logging.getLogger(__name__)

EXTENSIONS = (
    "dc_manager.cogs.reaction_roles",
    "dc_manager.cogs.button_roles",
    "dc_manager.cogs.settings",
    "dc_manager.cogs.reminders",
    "dc_manager.cogs.alarms",
    "dc_manager.cogs.maintenance",
)


class DcManager(commands.Bot):
    def __init__(self, config: Config, *, force_sync: bool = False):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_reactions = True
        intents.members = config.members_intent
        super().__init__(
            # 只用斜線指令，不需要 Message Content 特權 intent
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            # 預設不 @ 任何人，避免紀錄訊息誤 ping
            allowed_mentions=discord.AllowedMentions.none(),
            member_cache_flags=discord.MemberCacheFlags.from_intents(intents),
            chunk_guilds_at_startup=False,  # 樹莓派省記憶體，需要時才抓成員
            proxy=config.proxy,
        )
        self.config = config
        self.force_sync = force_sync
        self.db = Database(config.db_path)
        # 有設定身分組的訊息 id，反應事件先用它過濾，避免每個反應都查資料庫
        self.tracked_messages: set[int] = set()
        self._member_locks: weakref.WeakValueDictionary[tuple[int, int], asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self.tree.on_error = self.on_app_command_error

    def member_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        """同一個成員的身分組操作排隊執行，避免連點造成「唯一」模式錯亂。"""
        key = (guild_id, user_id)
        lock = self._member_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._member_locks[key] = lock
        return lock

    async def setup_hook(self) -> None:
        await self.db.connect()
        self.tracked_messages = await self.db.all_message_ids()
        log.info("資料庫已載入，追蹤 %d 則身分組訊息", len(self.tracked_messages))
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        await self.sync_commands()

    async def sync_commands(self) -> None:
        if self.config.dev_guild_id:
            guild = discord.Object(self.config.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("已同步 %d 個指令到開發伺服器 %s", len(synced), self.config.dev_guild_id)
            return

        # 全域同步有頻率限制，只在指令內容改變時才同步
        payload = [cmd.to_dict(self.tree) for cmd in self.tree.get_commands()]
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        key = f"command_hash:{self.application_id}"
        if not self.force_sync and await self.db.get_kv(key) == digest:
            log.info("斜線指令沒有變更，略過同步")
            return
        synced = await self.tree.sync()
        await self.db.set_kv(key, digest)
        log.info("已全域同步 %d 個斜線指令（Discord 端可能需要幾分鐘生效）", len(synced))

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("已登入 %s (id=%s)，服務 %d 個伺服器", self.user, self.user.id, len(self.guilds))

    async def close(self) -> None:
        await super().close()
        await self.db.close()

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, UserError):
            text = f"⚠️ {original}"
        elif isinstance(error, app_commands.MissingPermissions):
            text = "⚠️ 你沒有使用這個指令的權限。"
        elif isinstance(error, app_commands.BotMissingPermissions):
            text = f"⚠️ bot 缺少權限：{', '.join(error.missing_permissions)}"
        elif isinstance(error, app_commands.NoPrivateMessage):
            text = "⚠️ 這個指令只能在伺服器中使用。"
        elif isinstance(original, discord.Forbidden):
            text = "⚠️ bot 權限不足，請確認 bot 的身分組位置與頻道權限。"
        else:
            log.exception("指令執行錯誤", exc_info=original)
            text = "❌ 發生未預期的錯誤，已記錄在 log。"
        await reply(interaction, text)


async def reply(interaction: discord.Interaction, text: str | None = None, **kwargs) -> None:
    """回覆互動；不管是否已經 defer 過都能用，預設只有本人看得到。"""
    kwargs.setdefault("ephemeral", True)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, **kwargs)
        else:
            await interaction.response.send_message(text, **kwargs)
    except discord.HTTPException:
        log.warning("無法回覆互動", exc_info=True)
