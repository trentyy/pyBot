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

# 每月規則（只用於單位「月」），存成字串：
#   "day:15"   每月 15 號（小月沒有那一天就取月底）
#   "nth:2:3"  每月第 2 個星期四（星期從 0 = 一算起；第幾個用 -1 表示最後一個）
NTH_LABEL = {1: "第 1 個", 2: "第 2 個", 3: "第 3 個", 4: "第 4 個", -1: "最後一個"}


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


def make_month_rule(month_day: int | None, nth: int | None, weekday: int | None) -> str | None:
    """把使用者填的選項轉成每月規則；都沒填就是 None（沿用起點日期的「幾號」）。"""
    if nth is not None or weekday is not None:
        if month_day is not None:
            raise ValueError("「每月幾號」和「每月第幾個星期幾」只能擇一。")
        if nth is None or weekday is None:
            raise ValueError("每月第幾個星期幾要同時填 `nth`（第幾個）和 `weekday`（星期幾）。")
        if nth not in NTH_LABEL or not 0 <= weekday <= 6:
            raise ValueError("第幾個只能是 1～4 或最後一個。")
        return f"nth:{nth}:{weekday}"
    if month_day is not None:
        if not 1 <= month_day <= 31:
            raise ValueError("每月幾號請填 1～31。")
        return f"day:{month_day}"
    return None


def month_rule_date(year: int, month: int, rule: str) -> date:
    """套用每月規則，算出該月的日期。"""
    kind, first, second = (rule.split(":") + ["0"])[:3]
    last_day = calendar.monthrange(year, month)[1]
    if kind == "day":
        return date(year, month, min(int(first), last_day))
    nth, weekday = int(first), int(second)
    if nth > 0:
        # 第 1～4 個一定落在 1～28 號，每個月都存在
        offset = (weekday - date(year, month, 1).weekday()) % 7
        return date(year, month, 1 + offset + 7 * (nth - 1))
    back = (date(year, month, last_day).weekday() - weekday) % 7
    return date(year, month, last_day - back)


def _month_of(d: date | datetime, plus: int) -> tuple[int, int]:
    total = d.month - 1 + plus
    return d.year + total // 12, total % 12 + 1


def first_rule_due(start: date | None, at: time, now: datetime, rule: str) -> datetime:
    """每月規則的第一次提醒：有指定開始日期就取那天（含）之後的第一次，否則取現在之後的第一次。"""
    base = start or now.date()
    for i in range(36):
        year, month = _month_of(base, i)
        due = datetime.combine(month_rule_date(year, month, rule), at)
        if (due.date() >= start) if start else (due > now):
            return due
    raise ValueError("找不到符合的日期")


def shift(anchor: datetime, unit: str, count: int, k: int, rule: str | None = None) -> datetime:
    """從 anchor 往後算第 k 個週期。每次都從 anchor 起算，月底日期不會越算越前面。"""
    if unit == UNIT_MONTH and rule and k:
        year, month = _month_of(anchor, count * k)
        return datetime.combine(month_rule_date(year, month, rule), anchor.time())
    if unit == UNIT_MONTH:
        return add_months(anchor, count * k)
    days = count * (7 if unit == UNIT_WEEK else 1)
    return anchor + timedelta(days=days * k)


def next_fixed_due(anchor: datetime, unit: str, count: int, after: datetime, rule: str | None = None) -> datetime:
    """固定週期：回傳第一個「嚴格晚於 after」的到期時間（都是當地 naive datetime）。"""
    if anchor > after:
        return anchor
    if unit == UNIT_MONTH:
        months = (after.year - anchor.year) * 12 + after.month - anchor.month
        k = max(0, months // count - 1)
    else:
        step = timedelta(days=count * (7 if unit == UNIT_WEEK else 1))
        k = max(0, (after - anchor) // step)
    due = shift(anchor, unit, count, k, rule)
    while due <= after:
        k += 1
        due = shift(anchor, unit, count, k, rule)
    return due


def next_after_completion(completed: datetime, at: time, unit: str, count: int, rule: str | None = None) -> datetime:
    """完成後重新起算：完成當天 + 一個週期，時間固定在任務設定的時刻。
    有每月規則時，改取「完成那個月再過 count 個月」依規則算出的日期。"""
    if unit == UNIT_MONTH and rule:
        year, month = _month_of(completed, count)
        return datetime.combine(month_rule_date(year, month, rule), at)
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


def describe_interval(unit: str, count: int, rule: str | None = None) -> str:
    if unit == UNIT_MONTH and rule:
        kind, first, second = (rule.split(":") + ["0"])[:3]
        prefix = "每月" if count == 1 else f"每 {count} 個月的"
        if kind == "day":
            return f"{prefix} {first} 號"
        return f"{prefix}{NTH_LABEL[int(first)]}週{WEEKDAYS[int(second)]}"
    if count == 1:
        return {UNIT_DAY: "每天", UNIT_WEEK: "每週", UNIT_MONTH: "每月"}[unit]
    return f"每 {count} {UNIT_LABEL[unit]}"


def fmt_local(d: datetime) -> str:
    return f"{d:%Y-%m-%d}（{WEEKDAYS[d.weekday()]}）{d:%H:%M}"
