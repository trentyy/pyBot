"""啟動方式：python -m dc_manager [--sync]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import sys

import discord

from . import __version__
from .bot import DcManager
from .config import Config


def setup_logging(config: Config) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    # 每檔 2MB、保留 5 份，避免塞滿樹莓派的 SD 卡
    file_handler = logging.handlers.RotatingFileHandler(
        config.log_dir / "dc-manager.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    # 不使用語音功能，略過「voice will NOT be supported」警告
    logging.getLogger("discord.client").addFilter(lambda r: "voice will NOT be supported" not in r.getMessage())

    root = logging.getLogger()
    root.setLevel(config.log_level)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


async def run(force_sync: bool) -> None:
    config = Config.load()
    setup_logging(config)
    logging.getLogger(__name__).info("dc-manager %s 啟動中（discord.py %s）", __version__, discord.__version__)
    bot = DcManager(config, force_sync=force_sync)
    async with bot:
        try:
            await bot.start(config.token)
        except discord.PrivilegedIntentsRequired:
            raise SystemExit(
                "Discord 拒絕連線：請到 Developer Portal → Bot 開啟 SERVER MEMBERS INTENT，"
                "或在 .env 設定 ENABLE_MEMBERS_INTENT=false。"
            ) from None
        except discord.LoginFailure:
            raise SystemExit("Bot Token 無效，請檢查 .env 的 DISCORD_TOKEN。") from None


def main() -> None:
    # Windows 主控台預設不是 UTF-8，中文會變亂碼
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="dc-manager", description="Discord 身分組管理 bot")
    parser.add_argument("--sync", action="store_true", help="強制重新同步斜線指令")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.sync))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
