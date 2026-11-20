import logging
from unittest.mock import AsyncMock, MagicMock

import discord

from dc_manager.bot import DcManager
from dc_manager.config import Config


def make_bot(tmp_path):
    cfg = Config(
        token="x",
        db_path=tmp_path / "t.db",
        log_dir=tmp_path / "logs",
        log_level="INFO",
        members_intent=False,
        dev_guild_id=123,
    )
    return DcManager(cfg)


async def test_sync_failure_is_logged_and_does_not_stop_startup(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr("dc_manager.bot.EXTENSIONS", ())  # 只測同步，不載入 cog
    bot = make_bot(tmp_path)
    response = MagicMock(status=403, reason="Forbidden")
    bot.sync_commands = AsyncMock(side_effect=discord.Forbidden(response, {"code": 50001, "message": "Missing Access"}))
    await bot.db.connect()
    try:
        with caplog.at_level(logging.ERROR, logger="dc_manager.bot"):
            await bot.setup_hook()  # 不應該丟出例外
    finally:
        await bot.db.close()
    assert "斜線指令同步失敗" in caplog.text
    assert "50001" in caplog.text
    assert "DEV_GUILD_ID" in caplog.text
