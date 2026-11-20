"""雙週提醒日程：把提醒任務展開成「哪一天要做什麼」的清單（`/reminder week` 使用）。

只處理資料與文字排版，不呼叫 Discord，方便測試。沒有提醒的日子與整週都會略過。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Iterator

from .db import Occurrence, Reminder
from .schedule import SCHEDULE_FIXED, WEEKDAYS, from_ts, get_zone, next_fixed_due, shift, to_ts
from .targets import targets_text

WINDOW_DAYS = 14  # 一次顯示兩週
TEXT_LIMIT = 3800  # Discord 內嵌訊息的說明文字上限是 4096
MAX_SLOTS = 400  # 展開固定週期的安全上限，避免設定異常時無窮迴圈

UPCOMING, PENDING, SNOOZED, DONE, MISSED = "upcoming", "pending", "snoozed", "done", "missed"
OPEN_STATES = (UPCOMING, PENDING, SNOOZED)


@dataclass(frozen=True)
class AgendaItem:
    ts: int
    reminder: Reminder
    state: str
    until: int | None = None  # 延期後的新提醒時間
    estimated: bool = False  # 「完成後起算」任務依原本時程推算的日期，有人按完成後會重新起算


def fixed_slots(r: Reminder, start: int, end: int) -> Iterator[int]:
    """固定週期任務在 [start, end) 內的所有到期時間（UTC timestamp）。"""
    tz = get_zone(r.timezone)
    anchor = datetime.fromisoformat(r.anchor_local)
    due = next_fixed_due(anchor, r.interval_unit, r.interval_count, from_ts(start - 1, tz), r.month_rule)
    for _ in range(MAX_SLOTS):
        t = to_ts(due, tz)
        if t >= end:
            return
        yield t
        due = next_fixed_due(anchor, r.interval_unit, r.interval_count, due, r.month_rule)


def completion_slots(r: Reminder, start: int, end: int) -> Iterator[tuple[int, bool]]:
    """「完成後起算」任務在 [start, end) 內的日期，回傳 (時間, 是否為預估)。

    實際的下次提醒時間（建立時、或上次完成後算出來的）是確定的，不算預估；
    在那之後，以及還在等完成、沒有確定日期時，都依原本的時程往後推算，標示為預估，
    等有人按完成才會重新起算。
    """
    if r.next_due_at is None:
        for t in fixed_slots(r, start, end):
            yield t, True
        return
    tz = get_zone(r.timezone)
    base = from_ts(r.next_due_at, tz)
    for k in range(MAX_SLOTS):
        t = to_ts(shift(base, r.interval_unit, r.interval_count, k, r.month_rule), tz)
        if t >= end:
            return
        if t >= start:
            yield t, k > 0


def _from_occurrence(r: Reminder, o: Occurrence) -> AgendaItem | None:
    if o.status not in (PENDING, SNOOZED, DONE, MISSED):
        return None
    return AgendaItem(
        ts=o.due_at,
        reminder=r,
        state=o.status,
        until=o.remind_at if o.status == SNOOZED else None,
    )


def build_items(
    reminders: list[Reminder],
    occurrences: list[Occurrence],
    overdue: list[Occurrence],
    start: int,
    end: int,
    now: int,
) -> tuple[list[AgendaItem], list[AgendaItem]]:
    """回傳 (期間內的項目, 逾期未完成的項目)。

    - 已經發過的提醒用資料庫紀錄（含完成 / 未完成 / 延期）。
    - 還沒發的未來提醒：固定週期可以事先算出每一次；「完成後起算」只知道下一次。
    """
    by_id = {r.id: r for r in reminders}
    items: list[AgendaItem] = []
    seen = {(o.reminder_id, o.due_at) for o in occurrences}
    for o in occurrences:
        r = by_id.get(o.reminder_id)
        if r and (item := _from_occurrence(r, o)):
            items.append(item)

    lower = max(start, now + 1)
    if lower < end:
        for r in reminders:
            if not r.active:
                continue
            if r.schedule_mode == SCHEDULE_FIXED:
                for t in fixed_slots(r, lower, end):
                    if (r.id, t) not in seen:
                        items.append(AgendaItem(t, r, UPCOMING))
            else:
                for t, estimated in completion_slots(r, lower, end):
                    if (r.id, t) not in seen:
                        items.append(AgendaItem(t, r, UPCOMING, estimated=estimated))
    items.sort(key=lambda i: (i.ts, i.reminder.id))

    late = [item for o in overdue if (r := by_id.get(o.reminder_id)) and (item := _from_occurrence(r, o))]
    return items, late


def _md(d: date | datetime) -> str:
    return f"{d.month}/{d.day}"


def _status(item: AgendaItem, tz: tzinfo) -> str:
    if item.state == DONE:
        return "✅ 已完成"
    if item.state == MISSED:
        return "⌛ 未完成"
    if item.state == SNOOZED and item.until:
        d = from_ts(item.until, tz)
        return f"💤 延至 {_md(d)} {d:%H:%M}"
    if item.state == PENDING:
        return "🔔 已提醒，等待完成"
    return "⏳ 預估（完成後會重新起算）" if item.estimated else "⏳ 待辦"


def _line(item: AgendaItem, tz: tzinfo, *, with_date: bool = False) -> str:
    local = from_ts(item.ts, tz)
    when = f"{_md(local)} {local:%H:%M}" if with_date else f"{local:%H:%M}"
    return f"• {when} **{item.reminder.title}** {targets_text(item.reminder)} {_status(item, tz)}"


def format_agenda(
    items: list[AgendaItem],
    overdue: list[AgendaItem],
    tz: tzinfo,
    start: date,
    days: int,
    now: int,
) -> tuple[str, dict[str, int]]:
    """排成文字。回傳 (內文, 統計)。沒有項目的日子與整週都不會出現。"""
    today = from_ts(now, tz).date()
    blocks: list[tuple[str, int]] = []  # (文字, 其中的項目數)；每一塊是不可拆開的一天或標題

    if overdue:
        body = "\n".join(_line(i, tz, with_date=True) for i in overdue)
        blocks.append((f"⚠️ **已逾期**\n{body}", len(overdue)))

    by_day: dict[date, list[AgendaItem]] = {}
    for item in items:
        by_day.setdefault(from_ts(item.ts, tz).date(), []).append(item)

    for w in range(0, days, 7):
        week_start = start + timedelta(days=w)
        week_days = [week_start + timedelta(days=i) for i in range(7)]
        active = [d for d in week_days if d in by_day]
        if not active:
            continue
        blocks.append((f"\n__**{_md(week_start)}（一）– {_md(week_days[-1])}（日）**__", 0))
        for d in active:
            mark = " ◀ 今天" if d == today else ""
            lines = "\n".join(_line(i, tz) for i in by_day[d])
            blocks.append((f"**週{WEEKDAYS[d.weekday()]} {_md(d)}**{mark}\n{lines}", len(by_day[d])))

    text, used, hidden = "", 0, 0
    for block, count in blocks:
        candidate = f"{text}\n{block}" if text else block
        if len(candidate) > TEXT_LIMIT:
            hidden += count
            continue
        text, used = candidate, used + count
    if hidden:
        text += f"\n\n…還有 {hidden} 項沒有顯示，可用 `scope`、`user`、`role` 縮小範圍。"

    stats = {
        "todo": sum(1 for i in items if i.state in OPEN_STATES),
        "done": sum(1 for i in items if i.state == DONE),
        "missed": sum(1 for i in items if i.state == MISSED),
        "overdue": len(overdue),
    }
    return text, stats


def previous_send_time(now: int, tz: tzinfo, weekday: int, at: time) -> int:
    """「每週 X 的 HH:MM」這個時間點中，最近一個不晚於 now 的（UTC timestamp）。"""
    local = from_ts(now, tz)
    day = local.date() - timedelta(days=(local.weekday() - weekday) % 7)
    when = datetime.combine(day, at)
    if when > local:
        when -= timedelta(days=7)
    return to_ts(when, tz)


def next_send_time(now: int, tz: tzinfo, weekday: int, at: time) -> int:
    """下一次發送時間（嚴格晚於 now）。"""
    local = from_ts(previous_send_time(now, tz, weekday, at), tz)
    return to_ts(local + timedelta(days=7), tz)
