"""區網接收器：儀器電腦用 HTTP POST 把數值送給 bot。

    POST /api/v1/readings
    Authorization: Bearer <token>        （/alarm source create 產生）
    {"metric": "furnace1.temp", "value": 852.3}
    或批次：{"readings": [{"metric": "...", "value": 1.2, "ts": 1761300000}, ...]}

    GET /api/v1/health                   連線測試，不需要 token
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import TYPE_CHECKING

from aiohttp import web

from .engine import InvalidReading, validate_metric, validate_value

if TYPE_CHECKING:
    from .engine import AlarmEngine

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
MAX_READINGS = 200
RATE_LIMIT_PER_MINUTE = 120
MAX_FUTURE_SECONDS = 300
TOKEN_PREFIX = "dcm_"


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_ts(raw, now: int) -> int:
    """接受 Unix 秒數或 ISO 8601 字串（需含時區），沒給就用收到的時間。"""
    if raw is None:
        return now
    if isinstance(raw, bool):
        raise InvalidReading("ts 格式錯誤")
    if isinstance(raw, (int, float)):
        ts = int(raw / 1000) if raw > 1e12 else int(raw)  # 也接受毫秒
    elif isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidReading("ts 必須是 Unix 秒數或 ISO 8601 時間") from None
        if dt.tzinfo is None:
            raise InvalidReading("ISO 時間請帶時區，例如 2026-11-01T09:00:00+08:00")
        ts = int(dt.timestamp())
    else:
        raise InvalidReading("ts 格式錯誤")
    if ts > now + MAX_FUTURE_SECONDS:
        raise InvalidReading("ts 比現在晚太多，請檢查儀器電腦的時鐘")
    return ts


def parse_readings(body, now: int) -> list[tuple[str, float, int]]:
    if isinstance(body, dict) and "readings" in body:
        items = body["readings"]
    elif isinstance(body, list):
        items = body
    else:
        items = [body]
    if not isinstance(items, list) or not items:
        raise InvalidReading("沒有任何數值")
    if len(items) > MAX_READINGS:
        raise InvalidReading(f"一次最多 {MAX_READINGS} 筆")
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise InvalidReading("每筆資料必須是 JSON 物件")
        out.append((validate_metric(item.get("metric", "")), validate_value(item.get("value")), parse_ts(item.get("ts"), now)))
    return out


class RateLimiter:
    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self.hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, key: int) -> bool:
        now = self.clock()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def create_app(engine: "AlarmEngine") -> web.Application:
    limiter = RateLimiter(RATE_LIMIT_PER_MINUTE)

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "dc-manager"})

    async def readings(request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-API-Key", "").strip()
        if not token:
            return _error(401, "缺少 token（Authorization: Bearer <token>）")
        source = await engine.db.get_alarm_source_by_hash(hash_token(token))
        if source is None:
            return _error(401, "token 無效")
        if not limiter.allow(source.id):
            return _error(429, f"太頻繁，每分鐘最多 {RATE_LIMIT_PER_MINUTE} 次請求（可改用批次送出）")
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "內容必須是 JSON")
        now = engine.now()
        try:
            items = parse_readings(body, now)
        except InvalidReading as e:
            return _error(400, str(e))
        for metric, value, ts in items:
            await engine.ingest(source.guild_id, metric, value, ts, source=f"lan:{source.name}")
        engine.mark_source_seen(source.id, now)
        return web.json_response({"ok": True, "accepted": len(items)})

    app = web.Application(client_max_size=MAX_BODY_BYTES)
    app.router.add_get("/api/v1/health", health)
    app.router.add_post("/api/v1/readings", readings)
    return app


class HttpReceiver:
    def __init__(self, engine: "AlarmEngine", host: str, port: int):
        self.engine = engine
        self.host = host
        self.port = port
        self.runner: web.AppRunner | None = None

    async def start(self) -> bool:
        self.runner = web.AppRunner(create_app(self.engine), access_log=None)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, self.host, self.port).start()
        except OSError as e:
            log.error("無法啟動警報接收器 %s:%s（%s），區網推送功能停用", self.host, self.port, e)
            await self.runner.cleanup()
            self.runner = None
            return False
        log.info("警報接收器已啟動：http://%s:%s/api/v1/readings", self.host, self.port)
        return True

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
