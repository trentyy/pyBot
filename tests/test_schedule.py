from datetime import date, datetime, time

import pytest

from dc_manager.cogs.reminders import compute_next_due, postpone_day_candidates
from dc_manager.schedule import (
    add_months,
    first_due,
    from_ts,
    get_zone,
    next_after_completion,
    next_fixed_due,
    parse_date,
    parse_time,
    to_ts,
)

TZ = "Asia/Taipei"


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def test_add_months_clamps_to_month_end():
    assert add_months(dt("2026-01-31T09:00"), 1) == dt("2026-02-28T09:00")
    assert add_months(dt("2028-01-31T09:00"), 1) == dt("2028-02-29T09:00")
    assert add_months(dt("2026-11-15T09:00"), 3) == dt("2027-02-15T09:00")


def test_monthly_fixed_does_not_drift():
    anchor = dt("2026-01-31T09:00")
    # 2 月只到 28 號，但 3 月要回到 31 號
    assert next_fixed_due(anchor, "month", 1, dt("2026-02-01T00:00")) == dt("2026-02-28T09:00")
    assert next_fixed_due(anchor, "month", 1, dt("2026-02-28T09:00")) == dt("2026-03-31T09:00")


def test_biweekly_fixed():
    anchor = dt("2026-11-02T09:00")  # 週一
    assert next_fixed_due(anchor, "week", 2, dt("2026-10-01T00:00")) == anchor
    assert next_fixed_due(anchor, "week", 2, anchor) == dt("2026-11-16T09:00")
    assert next_fixed_due(anchor, "week", 2, dt("2027-01-01T00:00")) == dt("2027-01-11T09:00")


def test_next_fixed_due_strictly_after():
    anchor = dt("2026-01-01T09:00")
    for unit, count in [("day", 3), ("week", 1), ("month", 2)]:
        after = dt("2026-06-15T12:34")
        due = next_fixed_due(anchor, unit, count, after)
        assert due > after
        # 前一個週期一定 <= after
        assert next_fixed_due(anchor, unit, count, after) == due


def test_after_completion_uses_completion_day():
    nxt = next_after_completion(dt("2026-11-05T17:30"), time(9, 0), "week", 2)
    assert nxt == dt("2026-11-19T09:00")
    assert next_after_completion(dt("2026-01-31T10:00"), time(9, 0), "month", 1) == dt("2026-02-28T09:00")


def test_parse_inputs():
    assert parse_date("2026/11/1") == date(2026, 11, 1)
    assert parse_time("9:05") == time(9, 5)
    assert parse_time("09：30") == time(9, 30)
    for bad in ["25:00", "9", "09:60"]:
        with pytest.raises(ValueError):
            parse_time(bad)
    with pytest.raises(ValueError):
        parse_date("11/01")


def test_first_due_defaults_to_next_occurrence_of_time():
    assert first_due(None, time(9, 0), dt("2026-11-01T08:00")) == dt("2026-11-01T09:00")
    assert first_due(None, time(9, 0), dt("2026-11-01T10:00")) == dt("2026-11-02T09:00")


def test_timestamps_round_trip_in_taipei():
    tz = get_zone(TZ)
    local = dt("2026-11-01T09:00")
    ts = to_ts(local, tz)
    assert ts == int(datetime.fromisoformat("2026-11-01T01:00+00:00").timestamp())
    assert from_ts(ts, tz) == local


def test_postpone_candidates():
    assert postpone_day_candidates(3) == [1, 2, 3]
    big = postpone_day_candidates(90)
    assert len(big) <= 25 and big[:14] == list(range(1, 15)) and big[-1] == 90
    assert big == sorted(set(big))


def ts(s: str) -> int:
    return to_ts(dt(s), get_zone(TZ))


def test_compute_next_due():
    anchor = dt("2026-11-02T09:00")
    common = dict(anchor=anchor, unit="week", count=2, tz_name=TZ)
    now = ts("2026-11-20T12:00")
    assert compute_next_due(**common, mode="fixed", now_ts=now, has_open=True, last_completed_ts=None) == ts("2026-11-30T09:00")
    # 完成後起算：還有未完成的提醒 → 等完成
    assert compute_next_due(**common, mode="completion", now_ts=now, has_open=True, last_completed_ts=None) is None
    # 上次 11/10 完成 → 11/24
    assert compute_next_due(
        **common, mode="completion", now_ts=now, has_open=False, last_completed_ts=ts("2026-11-10T15:00")
    ) == ts("2026-11-24T09:00")
    # 上次完成算出來已過期 → 下一個 09:00
    assert compute_next_due(
        **common, mode="completion", now_ts=now, has_open=False, last_completed_ts=ts("2026-10-01T15:00")
    ) == ts("2026-11-21T09:00")
