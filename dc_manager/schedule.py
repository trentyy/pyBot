"""提醒週期的時間計算（純函式，方便測試）。

所有日曆運算都在「當地時間」進行（例如每月 15 日 09:00），存進資料庫時才轉成 UTC timestamp。
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UNIT_DAY = "day"
UNIT_WEEK = "week"
UNIT_MONTH = "month"
UNITS = (UNIT_DAY, UNIT_WEEK, UNIT_MONTH)
UNIT_LABEL = {UNIT_DAY: "天", UNIT_WEEK: "週", UNIT_MONTH: "個月"}

SCHEDULE_FIXED = "fixed"  # 依原本週期（例如固定每月 1 號）
SCHEDULE_COMPLETION = "completion"  # 從完成那天重新起算
SCHEDULE_MODES = (SCHEDULE_FIXED, SCHEDULE_COMPLETION)

DEFAULT_TIMEZONE = "Asia/Taipei"
WEEKDAYS = "一二三四五六日"


def get_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def is_valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def add_months(d: datetime, months: int) -> datetime:
    """加 N 個月；目標月份沒有那一天就取月底（1/31 + 1 個月 = 2/28 或 2/29）。"""
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def shift(anchor: datetime, unit: str, count: int, k: int) -> datetime:
    """從 anchor 往後算第 k 個週期。每次都從 anchor 起算，月底日期不會越算越前面。"""
    if unit == UNIT_MONTH:
        return add_months(anchor, count * k)
    days = count * (7 if unit == UNIT_WEEK else 1)
    return anchor + timedelta(days=days * k)


def next_fixed_due(anchor: datetime, unit: str, count: int, after: datetime) -> datetime:
    """固定週期：回傳第一個「嚴格晚於 after」的到期時間（都是當地 naive datetime）。"""
    if anchor > after:
        return anchor
    if unit == UNIT_MONTH:
        months = (after.year - anchor.year) * 12 + after.month - anchor.month
        k = max(0, months // count - 1)
    else:
        step = timedelta(days=count * (7 if unit == UNIT_WEEK else 1))
        k = max(0, (after - anchor) // step)
    due = shift(anchor, unit, count, k)
    while due <= after:
        k += 1
        due = shift(anchor, unit, count, k)
    return due


def next_after_completion(completed: datetime, at: time, unit: str, count: int) -> datetime:
    """完成後重新起算：完成當天 + 一個週期，時間固定在任務設定的時刻。"""
    base = datetime.combine(completed.date(), at)
    return shift(base, unit, count, 1)


def to_ts(local: datetime, tz: ZoneInfo) -> int:
    return int(local.replace(tzinfo=tz).timestamp())


def from_ts(ts: int, tz: ZoneInfo) -> datetime:
    """UTC timestamp → 當地 naive datetime。"""
    return datetime.fromtimestamp(ts, tz).replace(tzinfo=None)


def parse_date(text: str) -> date:
    m = re.fullmatch(r"\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*", text)
    if not m:
        raise ValueError("日期格式請用 YYYY-MM-DD，例如 2026-11-01")
    return date(int(m[1]), int(m[2]), int(m[3]))


def parse_time(text: str) -> time:
    m = re.fullmatch(r"\s*(\d{1,2})[:：](\d{2})\s*", text)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError("時間格式請用 HH:MM（24 小時制），例如 09:00")
    return time(int(m[1]), int(m[2]))


def first_due(start: date | None, at: time, now: datetime) -> datetime:
    """沒指定開始日期時，取下一個 at 時刻（今天還沒過就今天，否則明天）。"""
    if start is not None:
        return datetime.combine(start, at)
    today = datetime.combine(now.date(), at)
    return today if today > now else today + timedelta(days=1)


def describe_interval(unit: str, count: int) -> str:
    if count == 1:
        return {UNIT_DAY: "每天", UNIT_WEEK: "每週", UNIT_MONTH: "每月"}[unit]
    return f"每 {count} {UNIT_LABEL[unit]}"


def fmt_local(d: datetime) -> str:
    return f"{d:%Y-%m-%d}（{WEEKDAYS[d.weekday()]}）{d:%H:%M}"
