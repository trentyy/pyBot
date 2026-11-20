"""Prometheus 指標：GET /metrics 輸出 text exposition format。

用 aiohttp（discord.py 已依賴）自行輸出文字格式，不額外引入 prometheus_client：
只有 6 個指標，不需要它的 registry／多程序功能，也少一個要固定版本的相依套件。
"""
from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from .bot import DcManager

log = logging.getLogger(__name__)

LEVELS = {logging.WARNING: "warning", logging.ERROR: "error", logging.CRITICAL: "critical"}
START_TIME = time.time()


class LogCounter(logging.Handler):
    """掛在 root logger：累計 warning／error／critical 筆數與最後一次 ERROR 以上的時間。"""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.counts = {name: 0 for name in LEVELS.values()}
        self.last_error_ts: float | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno >= logging.CRITICAL:
                level = "critical"
            elif record.levelno >= logging.ERROR:
                level = "error"
            elif record.levelno >= logging.WARNING:
                level = "warning"
            else:
                return
            self.counts[level] += 1
            if level != "warning":
                self.last_error_ts = record.created
        except Exception:  # 計數永遠不能影響 logging 本身
            pass


# 全域唯一的計數器；setup_logging 把它掛到 root logger
log_counter = LogCounter()


def _fmt(value: float) -> str:
    return repr(float(value)) if not float(value).is_integer() else str(int(value))


def render(bot: "DcManager", counter: LogCounter = log_counter) -> str:
    lines: list[str] = []

    def metric(name: str, kind: str, help_: str, samples: list[tuple[str, float]]) -> None:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            lines.append(f"{name}{labels} {_fmt(value)}")

    metric("dcbot_up", "gauge", "1 if the bot is logged in and connected to the Discord Gateway.",
           [("", 1 if bot.gateway_connected else 0)])
    metric("dcbot_guilds", "gauge", "Number of guilds the bot is serving.", [("", len(bot.guilds))])
    latency = bot.latency
    if math.isfinite(latency):  # 未連線時 discord.py 回傳 nan／inf，直接不輸出
        metric("dcbot_latency_seconds", "gauge", "Discord Gateway heartbeat latency.", [("", latency)])
    metric("dcbot_start_time_seconds", "gauge", "Process start time as a Unix timestamp.", [("", START_TIME)])
    metric("dcbot_log_messages_total", "counter", "Log records at warning level or above.",
           [(f'{{level="{lvl}"}}', counter.counts[lvl]) for lvl in ("warning", "error", "critical")])
    if counter.last_error_ts is not None:
        metric("dcbot_last_error_timestamp_seconds", "gauge",
               "Unix time of the last ERROR-or-above log record.", [("", counter.last_error_ts)])
    return "\n".join(lines) + "\n"


def create_app(bot: "DcManager", counter: LogCounter = log_counter) -> web.Application:
    async def metrics(request: web.Request) -> web.Response:
        return web.Response(
            text=render(bot, counter), content_type="text/plain", charset="utf-8",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    app = web.Application()
    app.router.add_get("/metrics", metrics)
    return app


class MetricsServer:
    def __init__(self, bot: "DcManager", host: str, port: int):
        self.bot = bot
        self.host = host
        self.port = port
        self.runner: web.AppRunner | None = None

    async def start(self) -> bool:
        """啟動失敗只記錄，不影響 bot。"""
        try:
            self.runner = web.AppRunner(create_app(self.bot), access_log=None)
            await self.runner.setup()
            await web.TCPSite(self.runner, self.host, self.port).start()
        except Exception as e:
            log.error("無法啟動 metrics 端點 %s:%s（%s），指標輸出停用", self.host, self.port, e)
            if self.runner is not None:
                await self.runner.cleanup()
                self.runner = None
            return False
        log.info("metrics 端點已啟動：http://%s:%s/metrics", self.host, self.port)
        return True

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
