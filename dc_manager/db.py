"""SQLite 資料層。所有設定都以 guild_id 區分，一個 bot 可同時服務多個伺服器。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import aiosqlite

KIND_REACTION = "reaction"
KIND_BUTTON = "button"

MODE_NORMAL = "normal"  # 可自由取得 / 移除
MODE_UNIQUE = "unique"  # 同一則訊息只能擁有其中一個身分組
MODE_VERIFY = "verify"  # 只能取得，取消反應不會移除
MODES = (MODE_NORMAL, MODE_UNIQUE, MODE_VERIFY)

SCHEMA_VERSION = 3

# 每個版本的 migration，依序執行；之後改表結構時往後加即可
MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE guild_settings (
        guild_id        INTEGER PRIMARY KEY,
        log_channel_id  INTEGER,
        dm_notify       INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE role_messages (
        message_id  INTEGER PRIMARY KEY,
        guild_id    INTEGER NOT NULL,
        channel_id  INTEGER NOT NULL,
        kind        TEXT NOT NULL CHECK (kind IN ('reaction', 'button')),
        mode        TEXT NOT NULL DEFAULT 'normal' CHECK (mode IN ('normal', 'unique', 'verify')),
        created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX ix_role_messages_guild ON role_messages (guild_id);

    CREATE TABLE role_bindings (
        message_id  INTEGER NOT NULL REFERENCES role_messages (message_id) ON DELETE CASCADE,
        role_id     INTEGER NOT NULL,
        emoji_key   TEXT,     -- 反應比對用：自訂表情存 id，Unicode 表情存字元
        emoji       TEXT,     -- 顯示用的表情字串
        label       TEXT,     -- 按鈕文字
        style       TEXT,     -- 按鈕樣式
        position    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (message_id, role_id)
    );
    CREATE UNIQUE INDEX ux_role_bindings_emoji
        ON role_bindings (message_id, emoji_key) WHERE emoji_key IS NOT NULL;
    CREATE INDEX ix_role_bindings_role ON role_bindings (role_id);

    CREATE TABLE auto_roles (
        guild_id  INTEGER NOT NULL,
        role_id   INTEGER NOT NULL,
        PRIMARY KEY (guild_id, role_id)
    );

    CREATE TABLE kv (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL
    );
    """,
    2: """
    ALTER TABLE guild_settings ADD COLUMN timezone TEXT;

    -- 可以建立 / 管理提醒任務的身分組（例如「A 儀器管理人」）
    CREATE TABLE reminder_managers (
        guild_id  INTEGER NOT NULL,
        role_id   INTEGER NOT NULL,
        PRIMARY KEY (guild_id, role_id)
    );

    CREATE TABLE reminders (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id           INTEGER NOT NULL,
        channel_id         INTEGER NOT NULL,
        title              TEXT NOT NULL,
        description        TEXT,
        target_user_id     INTEGER,
        target_role_id     INTEGER,
        manager_role_id    INTEGER,          -- 擁有此身分組的人可管理這個任務；NULL = 只有伺服器管理員
        interval_unit      TEXT NOT NULL CHECK (interval_unit IN ('day', 'week', 'month')),
        interval_count     INTEGER NOT NULL CHECK (interval_count > 0),
        anchor_local       TEXT NOT NULL,    -- 週期起點（當地時間 ISO 格式），也決定每次提醒的時刻
        timezone           TEXT NOT NULL,
        schedule_mode      TEXT NOT NULL CHECK (schedule_mode IN ('fixed', 'completion')),
        max_postpone_days  INTEGER NOT NULL DEFAULT 0,
        next_due_at        INTEGER,          -- 下次發提醒的 UTC timestamp；NULL = 等待完成後再算
        active             INTEGER NOT NULL DEFAULT 1,
        created_by         INTEGER NOT NULL,
        created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX ix_reminders_due ON reminders (active, next_due_at);
    CREATE INDEX ix_reminders_guild ON reminders (guild_id);

    -- 每一次提醒（一個週期）的狀態
    CREATE TABLE reminder_occurrences (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        reminder_id     INTEGER NOT NULL REFERENCES reminders (id) ON DELETE CASCADE,
        due_at          INTEGER NOT NULL,   -- 原本的到期時間
        remind_at       INTEGER NOT NULL,   -- 延期後的提醒時間
        status          TEXT NOT NULL CHECK (status IN ('pending', 'snoozed', 'done', 'missed')),
        postponed_days  INTEGER NOT NULL DEFAULT 0,
        channel_id      INTEGER,
        message_id      INTEGER,
        completed_by    INTEGER,
        completed_at    INTEGER
    );
    CREATE INDEX ix_occ_reminder ON reminder_occurrences (reminder_id, status);
    CREATE INDEX ix_occ_remind_at ON reminder_occurrences (status, remind_at);
    """,
    3: """
    -- 區網推送來源：每個來源一組 token（只存雜湊），綁定一個 Discord 伺服器
    CREATE TABLE alarm_sources (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id      INTEGER NOT NULL,
        name          TEXT NOT NULL,
        token_hash    TEXT NOT NULL UNIQUE,
        created_by    INTEGER NOT NULL,
        created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at  INTEGER,
        UNIQUE (guild_id, name)
    );

    CREATE TABLE alarm_managers (
        guild_id  INTEGER NOT NULL,
        role_id   INTEGER NOT NULL,
        PRIMARY KEY (guild_id, role_id)
    );

    -- 每個 metric 的最新值（不存歷史，歷史留在原資料庫 / Grafana）
    CREATE TABLE alarm_metrics (
        guild_id  INTEGER NOT NULL,
        metric    TEXT NOT NULL,
        value     REAL NOT NULL,
        ts        INTEGER NOT NULL,
        source    TEXT,
        PRIMARY KEY (guild_id, metric)
    );

    CREATE TABLE alarm_rules (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id         INTEGER NOT NULL,
        name             TEXT NOT NULL,
        metric           TEXT NOT NULL,
        operator         TEXT NOT NULL CHECK (operator IN ('>=', '>', '<=', '<')),
        threshold        REAL NOT NULL,
        clear_threshold  REAL,             -- 恢復門檻（遲滯）；NULL = 不符合條件就算恢復
        for_seconds      INTEGER NOT NULL DEFAULT 0,
        kind             TEXT NOT NULL CHECK (kind IN ('notify', 'alarm')),
        repeat_minutes   INTEGER NOT NULL DEFAULT 0,
        nodata_minutes   INTEGER NOT NULL DEFAULT 0,
        channel_id       INTEGER NOT NULL,
        target_user_id   INTEGER,
        target_role_id   INTEGER,
        manager_role_id  INTEGER,
        active           INTEGER NOT NULL DEFAULT 1,
        created_by       INTEGER NOT NULL,
        created_ts       INTEGER NOT NULL,
        -- 執行狀態
        state            TEXT NOT NULL DEFAULT 'ok' CHECK (state IN ('ok', 'pending', 'firing')),
        pending_since    INTEGER,
        event_id         INTEGER,
        nodata_event_id  INTEGER
    );
    CREATE INDEX ix_alarm_rules_metric ON alarm_rules (guild_id, metric);

    CREATE TABLE alarm_events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id           INTEGER NOT NULL REFERENCES alarm_rules (id) ON DELETE CASCADE,
        kind              TEXT NOT NULL CHECK (kind IN ('threshold', 'nodata')),
        status            TEXT NOT NULL CHECK (status IN ('firing', 'acked', 'resolved')),
        started_at        INTEGER NOT NULL,
        value             REAL,
        peak_value        REAL,
        channel_id        INTEGER,
        message_id        INTEGER,
        last_notified_at  INTEGER,
        acked_by          INTEGER,
        acked_at          INTEGER,
        silenced_until    INTEGER,
        resolved_at       INTEGER
    );
    CREATE INDEX ix_alarm_events_rule ON alarm_events (rule_id, status);
    """,
}

OPEN_STATUSES = ("pending", "snoozed")


@dataclass(frozen=True)
class GuildSettings:
    guild_id: int
    log_channel_id: int | None = None
    dm_notify: bool = False
    timezone: str | None = None


@dataclass(frozen=True)
class RoleMessage:
    message_id: int
    guild_id: int
    channel_id: int
    kind: str
    mode: str


@dataclass(frozen=True)
class Reminder:
    id: int
    guild_id: int
    channel_id: int
    title: str
    description: str | None
    target_user_id: int | None
    target_role_id: int | None
    manager_role_id: int | None
    interval_unit: str
    interval_count: int
    anchor_local: str
    timezone: str
    schedule_mode: str
    max_postpone_days: int
    next_due_at: int | None
    active: bool
    created_by: int


@dataclass(frozen=True)
class Occurrence:
    id: int
    reminder_id: int
    due_at: int
    remind_at: int
    status: str
    postponed_days: int
    channel_id: int | None
    message_id: int | None
    completed_by: int | None
    completed_at: int | None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES


REMINDER_COLUMNS = (
    "id, guild_id, channel_id, title, description, target_user_id, target_role_id, manager_role_id, "
    "interval_unit, interval_count, anchor_local, timezone, schedule_mode, max_postpone_days, "
    "next_due_at, active, created_by"
)
OCCURRENCE_COLUMNS = (
    "id, reminder_id, due_at, remind_at, status, postponed_days, channel_id, message_id, "
    "completed_by, completed_at"
)
# update_reminder 允許修改的欄位（欄位名會拼進 SQL，所以一定要白名單）
REMINDER_EDITABLE = {
    "channel_id", "title", "description", "target_user_id", "target_role_id", "manager_role_id",
    "interval_unit", "interval_count", "anchor_local", "timezone", "schedule_mode",
    "max_postpone_days", "next_due_at", "active",
}


@dataclass(frozen=True)
class AlarmSource:
    id: int
    guild_id: int
    name: str
    created_by: int
    last_seen_at: int | None


@dataclass(frozen=True)
class AlarmRule:
    id: int
    guild_id: int
    name: str
    metric: str
    operator: str
    threshold: float
    clear_threshold: float | None
    for_seconds: int
    kind: str
    repeat_minutes: int
    nodata_minutes: int
    channel_id: int
    target_user_id: int | None
    target_role_id: int | None
    manager_role_id: int | None
    active: bool
    created_by: int
    created_ts: int
    state: str
    pending_since: int | None
    event_id: int | None
    nodata_event_id: int | None


@dataclass(frozen=True)
class AlarmEvent:
    id: int
    rule_id: int
    kind: str
    status: str
    started_at: int
    value: float | None
    peak_value: float | None
    channel_id: int | None
    message_id: int | None
    last_notified_at: int | None
    acked_by: int | None
    acked_at: int | None
    silenced_until: int | None
    resolved_at: int | None


ALARM_RULE_COLUMNS = (
    "id, guild_id, name, metric, operator, threshold, clear_threshold, for_seconds, kind, "
    "repeat_minutes, nodata_minutes, channel_id, target_user_id, target_role_id, manager_role_id, "
    "active, created_by, created_ts, state, pending_since, event_id, nodata_event_id"
)
# 欄位名會拼進 SQL，所以只允許白名單
ALARM_RULE_EDITABLE = set(ALARM_RULE_COLUMNS.replace(" ", "").split(",")) - {"id", "guild_id", "created_by", "created_ts"}
ALARM_EVENT_COLUMNS = (
    "id, rule_id, kind, status, started_at, value, peak_value, channel_id, message_id, "
    "last_notified_at, acked_by, acked_at, silenced_until, resolved_at"
)
ALARM_EVENT_EDITABLE = set(ALARM_EVENT_COLUMNS.replace(" ", "").split(",")) - {"id", "rule_id", "kind", "started_at"}


def _alarm_rule(row) -> AlarmRule:
    values = list(row)
    values[15] = bool(values[15])  # active
    return AlarmRule(*values)


def _reminder(row) -> Reminder:
    values = list(row)
    values[15] = bool(values[15])  # active
    return Reminder(*values)


@dataclass(frozen=True)
class Binding:
    message_id: int
    role_id: int
    emoji_key: str | None
    emoji: str | None
    label: str | None
    style: str | None
    position: int


class Database:
    def __init__(self, path: Path | str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database 尚未連線")
        return self._conn

    async def connect(self) -> None:
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA synchronous = NORMAL")  # 減少 SD 卡寫入
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _migrate(self) -> None:
        async with self.conn.execute("PRAGMA user_version") as cur:
            current = (await cur.fetchone())[0]
        for version in range(current + 1, SCHEMA_VERSION + 1):
            await self.conn.executescript(
                f"BEGIN;\n{MIGRATIONS[version]}\nPRAGMA user_version = {version};\nCOMMIT;"
            )

    # ---------- guild settings ----------

    async def get_settings(self, guild_id: int) -> GuildSettings:
        async with self.conn.execute(
            "SELECT guild_id, log_channel_id, dm_notify, timezone FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return GuildSettings(guild_id)
        return GuildSettings(row["guild_id"], row["log_channel_id"], bool(row["dm_notify"]), row["timezone"])

    async def set_log_channel(self, guild_id: int, channel_id: int | None) -> None:
        await self.conn.execute(
            "INSERT INTO guild_settings (guild_id, log_channel_id) VALUES (?, ?) "
            "ON CONFLICT (guild_id) DO UPDATE SET log_channel_id = excluded.log_channel_id",
            (guild_id, channel_id),
        )
        await self.conn.commit()

    async def set_timezone(self, guild_id: int, timezone: str) -> None:
        await self.conn.execute(
            "INSERT INTO guild_settings (guild_id, timezone) VALUES (?, ?) "
            "ON CONFLICT (guild_id) DO UPDATE SET timezone = excluded.timezone",
            (guild_id, timezone),
        )
        await self.conn.commit()

    async def set_dm_notify(self, guild_id: int, enabled: bool) -> None:
        await self.conn.execute(
            "INSERT INTO guild_settings (guild_id, dm_notify) VALUES (?, ?) "
            "ON CONFLICT (guild_id) DO UPDATE SET dm_notify = excluded.dm_notify",
            (guild_id, int(enabled)),
        )
        await self.conn.commit()

    # ---------- role messages ----------

    async def all_message_ids(self) -> set[int]:
        async with self.conn.execute("SELECT message_id FROM role_messages") as cur:
            return {row[0] for row in await cur.fetchall()}

    async def get_message(self, message_id: int) -> RoleMessage | None:
        async with self.conn.execute(
            "SELECT message_id, guild_id, channel_id, kind, mode FROM role_messages WHERE message_id = ?",
            (message_id,),
        ) as cur:
            row = await cur.fetchone()
        return RoleMessage(*row) if row else None

    async def list_messages(self, guild_id: int, kind: str | None = None) -> list[RoleMessage]:
        sql = "SELECT message_id, guild_id, channel_id, kind, mode FROM role_messages WHERE guild_id = ?"
        params: list = [guild_id]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY created_at"
        async with self.conn.execute(sql, params) as cur:
            return [RoleMessage(*row) for row in await cur.fetchall()]

    async def add_message(
        self, message_id: int, guild_id: int, channel_id: int, kind: str, mode: str = MODE_NORMAL
    ) -> RoleMessage:
        await self.conn.execute(
            "INSERT OR IGNORE INTO role_messages (message_id, guild_id, channel_id, kind, mode) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, guild_id, channel_id, kind, mode),
        )
        await self.conn.commit()
        msg = await self.get_message(message_id)
        assert msg is not None
        return msg

    async def set_mode(self, message_id: int, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        await self.conn.execute("UPDATE role_messages SET mode = ? WHERE message_id = ?", (mode, message_id))
        await self.conn.commit()

    async def delete_messages(self, message_ids: Iterable[int]) -> int:
        ids = list(message_ids)
        if not ids:
            return 0
        cur = await self.conn.executemany(
            "DELETE FROM role_messages WHERE message_id = ?", [(i,) for i in ids]
        )
        await self.conn.commit()
        return cur.rowcount

    # ---------- bindings ----------

    async def get_bindings(self, message_id: int) -> list[Binding]:
        async with self.conn.execute(
            "SELECT message_id, role_id, emoji_key, emoji, label, style, position "
            "FROM role_bindings WHERE message_id = ? ORDER BY position, rowid",
            (message_id,),
        ) as cur:
            return [Binding(*row) for row in await cur.fetchall()]

    async def get_binding_by_emoji(self, message_id: int, emoji_key: str) -> Binding | None:
        async with self.conn.execute(
            "SELECT message_id, role_id, emoji_key, emoji, label, style, position "
            "FROM role_bindings WHERE message_id = ? AND emoji_key = ?",
            (message_id, emoji_key),
        ) as cur:
            row = await cur.fetchone()
        return Binding(*row) if row else None

    async def get_binding_by_role(self, message_id: int, role_id: int) -> Binding | None:
        async with self.conn.execute(
            "SELECT message_id, role_id, emoji_key, emoji, label, style, position "
            "FROM role_bindings WHERE message_id = ? AND role_id = ?",
            (message_id, role_id),
        ) as cur:
            row = await cur.fetchone()
        return Binding(*row) if row else None

    async def add_binding(
        self,
        message_id: int,
        role_id: int,
        *,
        emoji_key: str | None = None,
        emoji: str | None = None,
        label: str | None = None,
        style: str | None = None,
    ) -> None:
        """新增或覆寫同一身分組的綁定。emoji_key 重複時會丟出 IntegrityError。"""
        async with self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM role_bindings WHERE message_id = ?",
            (message_id,),
        ) as cur:
            position = (await cur.fetchone())[0]
        await self.conn.execute(
            "INSERT INTO role_bindings (message_id, role_id, emoji_key, emoji, label, style, position) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (message_id, role_id) DO UPDATE SET emoji_key = excluded.emoji_key, "
            "emoji = excluded.emoji, label = excluded.label, style = excluded.style",
            (message_id, role_id, emoji_key, emoji, label, style, position),
        )
        await self.conn.commit()

    async def remove_binding(self, message_id: int, role_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM role_bindings WHERE message_id = ? AND role_id = ?", (message_id, role_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def remove_role_everywhere(self, guild_id: int, role_id: int) -> list[int]:
        """身分組被刪除時清掉所有綁定，回傳受影響的訊息 id。"""
        async with self.conn.execute(
            "SELECT b.message_id FROM role_bindings b JOIN role_messages m USING (message_id) "
            "WHERE m.guild_id = ? AND b.role_id = ?",
            (guild_id, role_id),
        ) as cur:
            affected = [row[0] for row in await cur.fetchall()]
        await self.conn.execute(
            "DELETE FROM role_bindings WHERE role_id = ? AND message_id IN "
            "(SELECT message_id FROM role_messages WHERE guild_id = ?)",
            (role_id, guild_id),
        )
        await self.conn.execute(
            "DELETE FROM auto_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)
        )
        await self.conn.commit()
        return affected

    # ---------- auto roles ----------

    async def get_auto_roles(self, guild_id: int) -> list[int]:
        async with self.conn.execute(
            "SELECT role_id FROM auto_roles WHERE guild_id = ?", (guild_id,)
        ) as cur:
            return [row[0] for row in await cur.fetchall()]

    async def add_auto_role(self, guild_id: int, role_id: int) -> bool:
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO auto_roles (guild_id, role_id) VALUES (?, ?)", (guild_id, role_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def remove_auto_role(self, guild_id: int, role_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM auto_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # ---------- guild cleanup ----------

    async def delete_guild(self, guild_id: int) -> list[int]:
        """bot 被移出伺服器時清掉該伺服器的所有資料，回傳被刪除的訊息 id。"""
        async with self.conn.execute(
            "SELECT message_id FROM role_messages WHERE guild_id = ?", (guild_id,)
        ) as cur:
            ids = [row[0] for row in await cur.fetchall()]
        await self.conn.execute("DELETE FROM role_messages WHERE guild_id = ?", (guild_id,))
        await self.conn.execute("DELETE FROM auto_roles WHERE guild_id = ?", (guild_id,))
        await self.conn.execute("DELETE FROM guild_settings WHERE guild_id = ?", (guild_id,))
        await self.conn.execute("DELETE FROM reminders WHERE guild_id = ?", (guild_id,))
        await self.conn.execute("DELETE FROM reminder_managers WHERE guild_id = ?", (guild_id,))
        for table in ("alarm_rules", "alarm_sources", "alarm_managers", "alarm_metrics"):
            await self.conn.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        await self.conn.commit()
        return ids

    # ---------- key/value ----------

    async def get_kv(self, key: str) -> str | None:
        async with self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def set_kv(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()

    # ---------- reminder managers ----------

    async def get_reminder_managers(self, guild_id: int) -> list[int]:
        async with self.conn.execute(
            "SELECT role_id FROM reminder_managers WHERE guild_id = ?", (guild_id,)
        ) as cur:
            return [row[0] for row in await cur.fetchall()]

    async def add_reminder_manager(self, guild_id: int, role_id: int) -> bool:
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO reminder_managers (guild_id, role_id) VALUES (?, ?)", (guild_id, role_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def remove_reminder_manager(self, guild_id: int, role_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM reminder_managers WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # ---------- reminders ----------

    async def create_reminder(
        self,
        *,
        guild_id: int,
        channel_id: int,
        title: str,
        description: str | None,
        target_user_id: int | None,
        target_role_id: int | None,
        manager_role_id: int | None,
        interval_unit: str,
        interval_count: int,
        anchor_local: str,
        timezone: str,
        schedule_mode: str,
        max_postpone_days: int,
        next_due_at: int | None,
        created_by: int,
    ) -> Reminder:
        cur = await self.conn.execute(
            "INSERT INTO reminders (guild_id, channel_id, title, description, target_user_id, target_role_id, "
            "manager_role_id, interval_unit, interval_count, anchor_local, timezone, schedule_mode, "
            "max_postpone_days, next_due_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id, channel_id, title, description, target_user_id, target_role_id, manager_role_id,
                interval_unit, interval_count, anchor_local, timezone, schedule_mode, max_postpone_days,
                next_due_at, created_by,
            ),
        )
        await self.conn.commit()
        reminder = await self.get_reminder(cur.lastrowid)  # type: ignore[arg-type]
        assert reminder is not None
        return reminder

    async def get_reminder(self, reminder_id: int) -> Reminder | None:
        async with self.conn.execute(
            f"SELECT {REMINDER_COLUMNS} FROM reminders WHERE id = ?", (reminder_id,)
        ) as cur:
            row = await cur.fetchone()
        return _reminder(row) if row else None

    async def list_reminders(self, guild_id: int) -> list[Reminder]:
        async with self.conn.execute(
            f"SELECT {REMINDER_COLUMNS} FROM reminders WHERE guild_id = ? ORDER BY id", (guild_id,)
        ) as cur:
            return [_reminder(row) for row in await cur.fetchall()]

    async def update_reminder(self, reminder_id: int, **fields) -> None:
        if not fields:
            return
        unknown = set(fields) - REMINDER_EDITABLE
        if unknown:
            raise ValueError(f"不能修改欄位：{unknown}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE reminders SET {sets} WHERE id = ?", (*fields.values(), reminder_id)
        )
        await self.conn.commit()

    async def delete_reminder(self, reminder_id: int) -> None:
        await self.conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await self.conn.commit()

    async def due_reminders(self, now: int) -> list[Reminder]:
        async with self.conn.execute(
            f"SELECT {REMINDER_COLUMNS} FROM reminders "
            "WHERE active = 1 AND next_due_at IS NOT NULL AND next_due_at <= ? ORDER BY next_due_at",
            (now,),
        ) as cur:
            return [_reminder(row) for row in await cur.fetchall()]

    async def clear_role_from_reminders(self, guild_id: int, role_id: int) -> list[int]:
        """身分組被刪除時，從任務的對象 / 管理者中移除，回傳受影響的任務 id。"""
        async with self.conn.execute(
            "SELECT id FROM reminders WHERE guild_id = ? AND (target_role_id = ? OR manager_role_id = ?)",
            (guild_id, role_id, role_id),
        ) as cur:
            ids = [row[0] for row in await cur.fetchall()]
        await self.conn.execute(
            "UPDATE reminders SET target_role_id = NULL WHERE guild_id = ? AND target_role_id = ?",
            (guild_id, role_id),
        )
        await self.conn.execute(
            "UPDATE reminders SET manager_role_id = NULL WHERE guild_id = ? AND manager_role_id = ?",
            (guild_id, role_id),
        )
        await self.conn.execute(
            "DELETE FROM reminder_managers WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)
        )
        await self.conn.commit()
        return ids

    # ---------- occurrences ----------

    async def create_occurrence(
        self, reminder_id: int, due_at: int, channel_id: int | None, message_id: int | None
    ) -> Occurrence:
        cur = await self.conn.execute(
            "INSERT INTO reminder_occurrences (reminder_id, due_at, remind_at, status, channel_id, message_id) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (reminder_id, due_at, due_at, channel_id, message_id),
        )
        await self.conn.commit()
        occ = await self.get_occurrence(cur.lastrowid)  # type: ignore[arg-type]
        assert occ is not None
        return occ

    async def delete_occurrence(self, occurrence_id: int) -> None:
        await self.conn.execute("DELETE FROM reminder_occurrences WHERE id = ?", (occurrence_id,))
        await self.conn.commit()

    async def set_occurrence_message(self, occurrence_id: int, channel_id: int, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE reminder_occurrences SET channel_id = ?, message_id = ? WHERE id = ?",
            (channel_id, message_id, occurrence_id),
        )
        await self.conn.commit()

    async def get_occurrence(self, occurrence_id: int) -> Occurrence | None:
        async with self.conn.execute(
            f"SELECT {OCCURRENCE_COLUMNS} FROM reminder_occurrences WHERE id = ?", (occurrence_id,)
        ) as cur:
            row = await cur.fetchone()
        return Occurrence(*row) if row else None

    async def open_occurrences(self, reminder_id: int) -> list[Occurrence]:
        async with self.conn.execute(
            f"SELECT {OCCURRENCE_COLUMNS} FROM reminder_occurrences "
            "WHERE reminder_id = ? AND status IN ('pending', 'snoozed') ORDER BY id",
            (reminder_id,),
        ) as cur:
            return [Occurrence(*row) for row in await cur.fetchall()]

    async def last_completed(self, reminder_id: int) -> Occurrence | None:
        async with self.conn.execute(
            f"SELECT {OCCURRENCE_COLUMNS} FROM reminder_occurrences "
            "WHERE reminder_id = ? AND status = 'done' ORDER BY completed_at DESC LIMIT 1",
            (reminder_id,),
        ) as cur:
            row = await cur.fetchone()
        return Occurrence(*row) if row else None

    async def snoozed_due(self, now: int) -> list[Occurrence]:
        """延期時間已到、需要再提醒一次的項目（只算啟用中的任務）。"""
        cols = ", ".join("o." + c.strip() for c in OCCURRENCE_COLUMNS.split(","))
        async with self.conn.execute(
            f"SELECT {cols} FROM reminder_occurrences o JOIN reminders r ON r.id = o.reminder_id "
            "WHERE o.status = 'snoozed' AND o.remind_at <= ? AND r.active = 1 ORDER BY o.remind_at",
            (now,),
        ) as cur:
            return [Occurrence(*row) for row in await cur.fetchall()]

    async def complete_occurrence(self, occurrence_id: int, user_id: int, at: int) -> bool:
        """標記完成；已經被別人完成時回傳 False（避免兩人同時按）。"""
        cur = await self.conn.execute(
            "UPDATE reminder_occurrences SET status = 'done', completed_by = ?, completed_at = ? "
            "WHERE id = ? AND status IN ('pending', 'snoozed')",
            (user_id, at, occurrence_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def snooze_occurrence(self, occurrence_id: int, days: int, remind_at: int) -> bool:
        cur = await self.conn.execute(
            "UPDATE reminder_occurrences SET status = 'snoozed', remind_at = ?, "
            "postponed_days = postponed_days + ? WHERE id = ? AND status IN ('pending', 'snoozed')",
            (remind_at, days, occurrence_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def reopen_occurrence(self, occurrence_id: int, channel_id: int, message_id: int) -> None:
        """延期時間到、重新發提醒後改回 pending。"""
        await self.conn.execute(
            "UPDATE reminder_occurrences SET status = 'pending', channel_id = ?, message_id = ? "
            "WHERE id = ? AND status = 'snoozed'",
            (channel_id, message_id, occurrence_id),
        )
        await self.conn.commit()

    async def miss_occurrence(self, occurrence_id: int) -> bool:
        cur = await self.conn.execute(
            "UPDATE reminder_occurrences SET status = 'missed' "
            "WHERE id = ? AND status IN ('pending', 'snoozed')",
            (occurrence_id,),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # ---------- alarm sources ----------

    async def create_alarm_source(self, guild_id: int, name: str, token_hash: str, created_by: int) -> AlarmSource:
        cur = await self.conn.execute(
            "INSERT INTO alarm_sources (guild_id, name, token_hash, created_by) VALUES (?, ?, ?, ?)",
            (guild_id, name, token_hash, created_by),
        )
        await self.conn.commit()
        return AlarmSource(cur.lastrowid, guild_id, name, created_by, None)  # type: ignore[arg-type]

    async def get_alarm_source_by_hash(self, token_hash: str) -> AlarmSource | None:
        async with self.conn.execute(
            "SELECT id, guild_id, name, created_by, last_seen_at FROM alarm_sources WHERE token_hash = ?",
            (token_hash,),
        ) as cur:
            row = await cur.fetchone()
        return AlarmSource(*row) if row else None

    async def list_alarm_sources(self, guild_id: int) -> list[AlarmSource]:
        async with self.conn.execute(
            "SELECT id, guild_id, name, created_by, last_seen_at FROM alarm_sources WHERE guild_id = ? ORDER BY name",
            (guild_id,),
        ) as cur:
            return [AlarmSource(*row) for row in await cur.fetchall()]

    async def rotate_alarm_source(self, guild_id: int, name: str, token_hash: str) -> bool:
        cur = await self.conn.execute(
            "UPDATE alarm_sources SET token_hash = ? WHERE guild_id = ? AND name = ?", (token_hash, guild_id, name)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def delete_alarm_source(self, guild_id: int, name: str) -> bool:
        cur = await self.conn.execute("DELETE FROM alarm_sources WHERE guild_id = ? AND name = ?", (guild_id, name))
        await self.conn.commit()
        return cur.rowcount > 0

    async def touch_alarm_sources(self, seen: dict[int, int]) -> None:
        if seen:
            await self.conn.executemany(
                "UPDATE alarm_sources SET last_seen_at = ? WHERE id = ?", [(ts, sid) for sid, ts in seen.items()]
            )
            await self.conn.commit()

    # ---------- alarm managers ----------

    async def get_alarm_managers(self, guild_id: int) -> list[int]:
        async with self.conn.execute("SELECT role_id FROM alarm_managers WHERE guild_id = ?", (guild_id,)) as cur:
            return [row[0] for row in await cur.fetchall()]

    async def add_alarm_manager(self, guild_id: int, role_id: int) -> bool:
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO alarm_managers (guild_id, role_id) VALUES (?, ?)", (guild_id, role_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def remove_alarm_manager(self, guild_id: int, role_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM alarm_managers WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # ---------- alarm metrics（最新值） ----------

    async def load_alarm_metrics(self) -> list[tuple[int, str, float, int, str | None]]:
        async with self.conn.execute("SELECT guild_id, metric, value, ts, source FROM alarm_metrics") as cur:
            return [tuple(row) for row in await cur.fetchall()]  # type: ignore[misc]

    async def save_alarm_metrics(self, rows: list[tuple[int, str, float, int, str | None]]) -> None:
        if rows:
            await self.conn.executemany(
                "INSERT INTO alarm_metrics (guild_id, metric, value, ts, source) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (guild_id, metric) DO UPDATE SET value = excluded.value, ts = excluded.ts, "
                "source = excluded.source",
                rows,
            )
            await self.conn.commit()

    async def delete_alarm_metric(self, guild_id: int, metric: str) -> None:
        await self.conn.execute("DELETE FROM alarm_metrics WHERE guild_id = ? AND metric = ?", (guild_id, metric))
        await self.conn.commit()

    # ---------- alarm rules ----------

    async def create_alarm_rule(self, **fields) -> AlarmRule:
        unknown = set(fields) - (ALARM_RULE_EDITABLE | {"guild_id", "created_by", "created_ts"})
        if unknown:
            raise ValueError(f"未知欄位：{unknown}")
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cur = await self.conn.execute(f"INSERT INTO alarm_rules ({cols}) VALUES ({marks})", tuple(fields.values()))
        await self.conn.commit()
        rule = await self.get_alarm_rule(cur.lastrowid)  # type: ignore[arg-type]
        assert rule is not None
        return rule

    async def get_alarm_rule(self, rule_id: int) -> AlarmRule | None:
        async with self.conn.execute(f"SELECT {ALARM_RULE_COLUMNS} FROM alarm_rules WHERE id = ?", (rule_id,)) as cur:
            row = await cur.fetchone()
        return _alarm_rule(row) if row else None

    async def list_alarm_rules(
        self, guild_id: int | None = None, *, metric: str | None = None, active_only: bool = False
    ) -> list[AlarmRule]:
        sql = f"SELECT {ALARM_RULE_COLUMNS} FROM alarm_rules WHERE 1 = 1"
        params: list = []
        if guild_id is not None:
            sql += " AND guild_id = ?"
            params.append(guild_id)
        if metric is not None:
            sql += " AND metric = ?"
            params.append(metric)
        if active_only:
            sql += " AND active = 1"
        async with self.conn.execute(sql + " ORDER BY id", params) as cur:
            return [_alarm_rule(row) for row in await cur.fetchall()]

    async def update_alarm_rule(self, rule_id: int, **fields) -> None:
        unknown = set(fields) - ALARM_RULE_EDITABLE
        if unknown:
            raise ValueError(f"不能修改欄位：{unknown}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            await self.conn.execute(f"UPDATE alarm_rules SET {sets} WHERE id = ?", (*fields.values(), rule_id))
            await self.conn.commit()

    async def delete_alarm_rule(self, rule_id: int) -> None:
        await self.conn.execute("DELETE FROM alarm_rules WHERE id = ?", (rule_id,))
        await self.conn.commit()

    async def clear_role_from_alarm_rules(self, guild_id: int, role_id: int) -> list[int]:
        """身分組被刪除時，從規則的對象 / 管理者中移除，回傳受影響的規則 id。"""
        async with self.conn.execute(
            "SELECT id FROM alarm_rules WHERE guild_id = ? AND (target_role_id = ? OR manager_role_id = ?)",
            (guild_id, role_id, role_id),
        ) as cur:
            ids = [row[0] for row in await cur.fetchall()]
        await self.conn.execute(
            "UPDATE alarm_rules SET target_role_id = NULL WHERE guild_id = ? AND target_role_id = ?", (guild_id, role_id)
        )
        await self.conn.execute(
            "UPDATE alarm_rules SET manager_role_id = NULL WHERE guild_id = ? AND manager_role_id = ?", (guild_id, role_id)
        )
        await self.conn.execute("DELETE FROM alarm_managers WHERE guild_id = ? AND role_id = ?", (guild_id, role_id))
        await self.conn.commit()
        return ids

    # ---------- alarm events ----------

    async def create_alarm_event(self, rule_id: int, kind: str, started_at: int, value: float | None) -> AlarmEvent:
        cur = await self.conn.execute(
            "INSERT INTO alarm_events (rule_id, kind, status, started_at, value, peak_value, last_notified_at) "
            "VALUES (?, ?, 'firing', ?, ?, ?, ?)",
            (rule_id, kind, started_at, value, value, started_at),
        )
        await self.conn.commit()
        event = await self.get_alarm_event(cur.lastrowid)  # type: ignore[arg-type]
        assert event is not None
        return event

    async def get_alarm_event(self, event_id: int) -> AlarmEvent | None:
        async with self.conn.execute(f"SELECT {ALARM_EVENT_COLUMNS} FROM alarm_events WHERE id = ?", (event_id,)) as cur:
            row = await cur.fetchone()
        return AlarmEvent(*row) if row else None

    async def update_alarm_event(self, event_id: int, **fields) -> None:
        unknown = set(fields) - ALARM_EVENT_EDITABLE
        if unknown:
            raise ValueError(f"不能修改欄位：{unknown}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            await self.conn.execute(f"UPDATE alarm_events SET {sets} WHERE id = ?", (*fields.values(), event_id))
            await self.conn.commit()

    async def ack_alarm_event(self, event_id: int, user_id: int, at: int) -> bool:
        """標記已知悉；已經被別人按過時回傳 False。"""
        cur = await self.conn.execute(
            "UPDATE alarm_events SET status = 'acked', acked_by = ?, acked_at = ? WHERE id = ? AND status = 'firing'",
            (user_id, at, event_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def firing_alarm_events(self) -> list[AlarmEvent]:
        async with self.conn.execute(
            f"SELECT {ALARM_EVENT_COLUMNS} FROM alarm_events WHERE status = 'firing' ORDER BY id"
        ) as cur:
            return [AlarmEvent(*row) for row in await cur.fetchall()]

    async def last_alarm_event(self, rule_id: int) -> AlarmEvent | None:
        async with self.conn.execute(
            f"SELECT {ALARM_EVENT_COLUMNS} FROM alarm_events WHERE rule_id = ? ORDER BY id DESC LIMIT 1", (rule_id,)
        ) as cur:
            row = await cur.fetchone()
        return AlarmEvent(*row) if row else None
