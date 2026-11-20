from datetime import date, datetime

from dc_manager.agenda import UPCOMING, build_items, fixed_slots, format_agenda
from dc_manager.db import Occurrence, Reminder
from dc_manager.schedule import get_zone, to_ts

TZ = "Asia/Taipei"
ZONE = get_zone(TZ)
START = date(2026, 11, 2)  # 星期一


def ts(s: str) -> int:
    return to_ts(datetime.fromisoformat(s), ZONE)


def rem(id=1, *, title="A 儀器校正", unit="week", count=2, anchor="2026-11-02T09:00", mode="fixed", active=True, next_due=None):
    return Reminder(
        id=id, guild_id=1, channel_id=2, title=title, description=None, target_user_id=None, target_role_id=500,
        manager_role_id=None, interval_unit=unit, interval_count=count, anchor_local=anchor, timezone=TZ,
        schedule_mode=mode, max_postpone_days=3, next_due_at=next_due or ts(anchor), active=active, created_by=1,
    )


def occ(r, due, status, *, remind_at=None, id=1):
    return Occurrence(id, r.id, ts(due), ts(remind_at or due), status, 0, 2, 3, None, None)


def window(days=14):
    return ts("2026-11-02T00:00"), ts(f"2026-11-{2 + days:02d}T00:00")


def agenda(reminders, occs=(), overdue=(), now="2026-11-02T08:00"):
    s, e = window()
    items, late = build_items(reminders, list(occs), list(overdue), s, e, ts(now))
    return items, late, format_agenda(items, late, ZONE, START, 14, ts(now))


def test_fixed_slots_biweekly():
    r = rem()
    slots = list(fixed_slots(r, ts("2026-11-02T00:00"), ts("2026-12-14T00:00")))
    assert slots == [ts("2026-11-02T09:00"), ts("2026-11-16T09:00"), ts("2026-11-30T09:00")]


def test_empty_days_and_weeks_are_skipped():
    # 每兩週一次：這兩週只有 11/2 一天，第二週完全沒有項目
    _, _, (text, stats) = agenda([rem()])
    assert "週一 11/2" in text
    assert text.count("**週") == 1
    assert "11/9" not in text  # 第二週沒有提醒，連週標題都不顯示
    assert stats["todo"] == 1


def test_nothing_to_show_returns_empty_text():
    _, _, (text, stats) = agenda([rem(unit="month", count=3, anchor="2027-01-05T09:00")])
    assert text == ""
    assert stats == {"todo": 0, "done": 0, "missed": 0, "overdue": 0}


def test_daily_shows_every_day_and_marks_today():
    _, _, (text, _) = agenda([rem(unit="day", count=1)], now="2026-11-04T10:00")
    # 現在是 11/4 10:00：已過去、又沒有紀錄的 11/2–11/4 不顯示，只剩 11/5–11/15 共 11 天
    assert text.count("**週") == 11
    assert "週四 11/5" in text and "週三 11/4" not in text


def test_today_marker():
    _, _, (text, _) = agenda([rem(unit="day", count=1)], now="2026-11-04T08:00")
    assert "週三 11/4** ◀ 今天" in text


def test_records_show_done_missed_pending_snoozed():
    r = rem(unit="day", count=1, anchor="2026-11-02T09:00", next_due=ts("2026-11-06T09:00"))
    occs = [
        occ(r, "2026-11-02T09:00", "done", id=1),
        occ(r, "2026-11-03T09:00", "missed", id=2),
        occ(r, "2026-11-04T09:00", "pending", id=3),
        occ(r, "2026-11-05T09:00", "snoozed", remind_at="2026-11-07T09:00", id=4),
    ]
    items, _, (text, stats) = agenda([r], occs, now="2026-11-05T12:00")
    assert "✅ 已完成" in text and "⌛ 未完成" in text and "🔔 已提醒，等待完成" in text
    assert "💤 延至 11/7 09:00" in text
    assert stats["done"] == 1 and stats["missed"] == 1 and stats["todo"] >= 2
    # 已有紀錄的日子不會再多一筆「待辦」
    assert sum(1 for i in items if i.reminder.id == r.id and i.ts == ts("2026-11-04T09:00")) == 1


def test_overdue_section():
    r = rem(unit="day", count=1)
    old = occ(r, "2026-10-30T09:00", "pending")
    _, late, (text, stats) = agenda([r], overdue=[old], now="2026-11-02T08:00")
    assert len(late) == 1
    assert text.startswith("⚠️ **已逾期**")
    assert "10/30 09:00" in text
    assert stats["overdue"] == 1


def test_completion_mode_projects_from_next_due_and_marks_estimates():
    # 確定的下次提醒是 11/9；之後依週期（每 3 天）推算，標示預估
    r = rem(mode="completion", unit="day", count=3, next_due=ts("2026-11-09T09:00"))
    items, _, (text, _) = agenda([r])
    assert [(i.ts, i.estimated) for i in items] == [
        (ts("2026-11-09T09:00"), False),
        (ts("2026-11-12T09:00"), True),
        (ts("2026-11-15T09:00"), True),
    ]
    assert text.count("預估（完成後會重新起算）") == 2 and text.count("⏳ 待辦") == 1


def test_completion_mode_waiting_for_completion_uses_original_schedule():
    # 11/2 的提醒已發出、還沒完成（沒有確定的下次日期）：依原本起點的時程推算，全部是預估
    base = rem(mode="completion", unit="day", count=4, anchor="2026-11-02T09:00")
    r = Reminder(**{**base.__dict__, "next_due_at": None})
    items, _, _ = agenda([r], now="2026-11-02T10:00")
    assert [(i.ts, i.estimated) for i in items] == [
        (ts("2026-11-06T09:00"), True),
        (ts("2026-11-10T09:00"), True),
        (ts("2026-11-14T09:00"), True),
    ]


def test_completion_mode_projection_follows_new_date_after_completion():
    # 11/5 完成後下次改為 11/19：原本的時程（11/2 起每 2 週）不再適用，改以 11/19 為準
    r = rem(mode="completion", unit="week", count=2, anchor="2026-11-02T09:00", next_due=ts("2026-11-19T09:00"))
    s, e = ts("2026-11-16T00:00"), ts("2026-11-30T00:00")
    items, _ = build_items([r], [], [], s, e, ts("2026-11-05T17:30"))
    assert [(i.ts, i.estimated) for i in items] == [(ts("2026-11-19T09:00"), False)]


def test_inactive_reminders_have_no_future_items():
    items, _, (text, _) = agenda([rem(unit="day", count=1, active=False)])
    assert items == [] and text == ""


def test_long_agenda_is_truncated_under_embed_limit():
    reminders = [rem(id=i, title=f"任務{i:03d}" + "長" * 30, unit="day", count=1) for i in range(1, 80)]
    _, _, (text, _) = agenda(reminders)
    assert len(text) <= 4096
    assert "還有" in text and "項沒有顯示" in text


def test_upcoming_state():
    items, _, _ = agenda([rem()])
    assert items[0].state == UPCOMING


def test_previous_and_next_send_time():
    from datetime import time

    from dc_manager.agenda import next_send_time, previous_send_time

    at = time(9, 0)
    # 週三 10:00：最近一次是本週一 09:00，下一次是下週一
    now = ts("2026-11-04T10:00")
    assert previous_send_time(now, ZONE, 0, at) == ts("2026-11-02T09:00")
    assert next_send_time(now, ZONE, 0, at) == ts("2026-11-09T09:00")
    # 週一 08:00 還沒到：最近一次是上週一，下一次是今天 09:00
    now = ts("2026-11-02T08:00")
    assert previous_send_time(now, ZONE, 0, at) == ts("2026-10-26T09:00")
    assert next_send_time(now, ZONE, 0, at) == ts("2026-11-02T09:00")
    # 剛好在發送時間：算「已經到了」，下一次是下週
    now = ts("2026-11-02T09:00")
    assert previous_send_time(now, ZONE, 0, at) == now
    assert next_send_time(now, ZONE, 0, at) == ts("2026-11-09T09:00")
    # 其他星期：週五 17:30
    assert previous_send_time(ts("2026-11-04T10:00"), ZONE, 4, time(17, 30)) == ts("2026-10-30T17:30")
