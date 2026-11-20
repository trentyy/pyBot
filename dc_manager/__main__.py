"""啟動方式：python -m dc_manager [--sync]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import signal
import sys

import discord

from . import __version__
from .bot import DcManager
from .config import Config
from .metrics import log_counter

EXIT_SIGTERM = 143


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
    root.addHandler(log_counter)  # Prometheus 的 dcbot_log_messages_total
    logging.getLogger("discord.http").setLevel(logging.WARNING)


def install_shutdown_handler() -> None:
    """docker stop / systemctl stop 會送 SIGTERM：比照 Ctrl+C 取消主程式，
    由 async with 正常關閉 bot，讓資料寫回資料庫。"""
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()

    def on_sigterm() -> None:
        logging.getLogger(__name__).info("收到 SIGTERM，正在關閉")
        if main_task is not None:
            main_task.cancel()

    try:
        loop.add_signal_handler(signal.SIGTERM, on_sigterm)
    except (NotImplementedError, RuntimeError):  # Windows 不支援
        pass


async def run(force_sync: bool) -> None:
    config = Config.load()
    setup_logging(config)
    logging.getLogger(__name__).info("dc-manager %s 啟動中（discord.py %s）", __version__, discord.__version__)
    install_shutdown_handler()
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
    except asyncio.CancelledError:
        # 收到 SIGTERM：以 143（128+15）結束，見 docker-compose.yml 的 restart 說明
        sys.exit(EXIT_SIGTERM)


if __name__ == "__main__":
    main()
