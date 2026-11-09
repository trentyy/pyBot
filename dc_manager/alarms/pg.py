"""PostgreSQL 輪詢：依樹莓派上的設定檔定期查詢數值。

查詢語句只寫在設定檔（由 bot 主機管理者維護），不能從 Discord 輸入，
避免任何伺服器管理員都能對實驗室資料庫下 SQL。範例見 alarm_sources.example.toml。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from ..schedule import DEFAULT_TIMEZONE, get_zone
from .engine import InvalidReading, validate_metric

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from .engine import AlarmEngine

log = logging.getLogger(__name__)

MIN_INTERVAL = 5
MAX_BACKOFF = 300
QUERY_TIMEOUT = 15


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PgMetric:
    query: str
    interval: int
    guilds: tuple[int, ...]
    key: str | None = None  # 單一數值：取第一列的 value
    prefix: str = ""  # 多個數值：每一列要有 metric 欄位，名稱前面加上 prefix


@dataclass(frozen=True)
class PgSource:
    name: str
    dsn: str
    timezone: str
    metrics: tuple[PgMetric, ...] = field(default_factory=tuple)


def load_config(path: Path) -> list[PgSource]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    sources = []
    for i, raw in enumerate(data.get("postgres", [])):
        name = str(raw.get("name") or f"postgres{i + 1}")
        dsn = raw.get("dsn")
        if raw.get("dsn_env"):
            dsn = os.getenv(raw["dsn_env"])
            if not dsn:
                raise ConfigError(f"[{name}] 環境變數 {raw['dsn_env']} 沒有設定")
        if not dsn:
            raise ConfigError(f"[{name}] 需要 dsn 或 dsn_env")
        tz = str(raw.get("timezone") or DEFAULT_TIMEZONE)
        metrics = []
        for j, m in enumerate(raw.get("metric", [])):
            where = f"[{name}] metric #{j + 1}"
            query = str(m.get("query") or "").strip()
            if not query:
                raise ConfigError(f"{where} 缺少 query")
            guilds = m.get("guilds")
            if not guilds or not all(isinstance(g, int) for g in guilds):
                raise ConfigError(f"{where} 需要 guilds = [Discord 伺服器 ID, ...]")
            key = m.get("key")
            prefix = str(m.get("prefix") or "")
            try:
                if key:
                    key = validate_metric(str(key))
                elif prefix:
                    validate_metric(prefix + "x")
            except InvalidReading as e:
                raise ConfigError(f"{where}：{e}") from None
            interval = max(MIN_INTERVAL, int(m.get("interval", 30)))
            metrics.append(PgMetric(query=query, interval=interval, guilds=tuple(guilds), key=key, prefix=prefix))
        if not metrics:
            raise ConfigError(f"[{name}] 沒有任何 [[postgres.metric]]")
        sources.append(PgSource(name=name, dsn=dsn, timezone=tz, metrics=tuple(metrics)))
    return sources


def to_epoch(value: Any, tz: "ZoneInfo", now: int) -> int:
    if value is None:
        return now
    if isinstance(value, datetime):
        if value.tzinfo is None:  # timestamp without time zone → 視為設定的時區
            value = value.replace(tzinfo=tz)
        return int(value.timestamp())
    if isinstance(value, (int, float, Decimal)):
        return int(value)
    raise InvalidReading(f"無法解析 ts 欄位：{value!r}")


def to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    raise InvalidReading(f"value 欄位不是數字：{value!r}")


def rows_to_readings(rows: list[Mapping[str, Any]], metric: PgMetric, tz: "ZoneInfo", now: int) -> list[tuple[str, float, int]]:
    """把查詢結果轉成 (metric, value, ts)。value 為 NULL 的列略過。"""
    if metric.key:
        rows = rows[:1]
    out = []
    for row in rows:
        if "value" not in row:
            raise InvalidReading("查詢結果需要 value 欄位（可用 AS value）")
        value = to_number(row["value"])
        if value is None:
            continue
        if metric.key:
            name = metric.key
        else:
            if "metric" not in row:
                raise InvalidReading("沒有設定 key 時，查詢結果需要 metric 欄位")
            name = validate_metric(metric.prefix + str(row["metric"]))
        out.append((name, value, to_epoch(row.get("ts"), tz, now)))
    return out


class PgPoller:
    def __init__(self, engine: "AlarmEngine", sources: list[PgSource]):
        self.engine = engine
        self.sources = sources
        self.tasks: list[asyncio.Task] = []
        self.pools: dict[str, Any] = {}
        self._pool_locks: dict[str, asyncio.Lock] = {}

    def known_metrics(self, guild_id: int) -> list[str]:
        """設定檔中已知（固定 key）的 metric，給 Discord 自動完成用。"""
        return sorted({m.key for s in self.sources for m in s.metrics if m.key and guild_id in m.guilds})

    def start(self) -> None:
        for source in self.sources:
            self._pool_locks[source.name] = asyncio.Lock()
            for metric in source.metrics:
                self.tasks.append(asyncio.create_task(self._run(source, metric)))
        log.info("PostgreSQL 輪詢已啟動：%d 個來源、%d 個查詢", len(self.sources), len(self.tasks))

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        for pool in self.pools.values():
            await pool.close()
        self.pools.clear()

    async def _pool(self, source: PgSource):
        async with self._pool_locks[source.name]:
            if source.name not in self.pools:
                import asyncpg  # 只有設定了 PostgreSQL 才需要

                self.pools[source.name] = await asyncpg.create_pool(
                    source.dsn,
                    min_size=1,
                    max_size=2,
                    command_timeout=QUERY_TIMEOUT,
                    server_settings={
                        "application_name": "dc-manager",
                        # 連線層級強制唯讀，就算帳號有寫入權限也不會改到資料
                        "default_transaction_read_only": "on",
                        "statement_timeout": str(QUERY_TIMEOUT * 1000),
                    },
                )
            return self.pools[source.name]

    async def poll_once(self, source: PgSource, metric: PgMetric) -> int:
        pool = await self._pool(source)
        async with pool.acquire() as conn:
            rows = await conn.fetch(metric.query)
        readings = rows_to_readings([dict(r) for r in rows], metric, get_zone(source.timezone), self.engine.now())
        for guild_id in metric.guilds:
            for name, value, ts in readings:
                await self.engine.ingest(guild_id, name, value, ts, source=f"pg:{source.name}")
        return len(readings)

    async def _run(self, source: PgSource, metric: PgMetric) -> None:
        label = metric.key or f"{metric.prefix}*"
        failures = 0
        while True:
            try:
                await self.poll_once(source, metric)
                if failures:
                    log.info("PostgreSQL [%s] %s 已恢復", source.name, label)
                failures = 0
                delay = metric.interval
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failures += 1
                if failures == 1 or failures % 20 == 0:  # 避免 log 洗版
                    log.error("PostgreSQL [%s] %s 查詢失敗（第 %d 次）：%s", source.name, label, failures, e)
                if source.name in self.pools and failures >= 3:
                    pool = self.pools.pop(source.name)
                    try:
                        await pool.close()
                    except Exception:
                        pass
                delay = min(MAX_BACKOFF, metric.interval * (2 ** min(failures, 5)))
            await asyncio.sleep(delay)
