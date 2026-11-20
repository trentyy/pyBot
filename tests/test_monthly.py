"""每月規則：每月幾號 / 每月第幾個星期幾。"""
from datetime import date, datetime, time
from types import SimpleNamespace

import pytest

from dc_manager.agenda import fixed_slots
from dc_manager.cogs.reminders import AgendaQuery, Reminders
from dc_manager.db import Database
from dc_manager.schedule import (
    describe_interval,
    first_rule_due,
    get_zone,
    make_month_rule,
    month_rule_date,
    next_after_completion,
    next_fixed_due,
    shift,
    to_ts,
)
from dc_manager.utils import UserError

# 重用提醒測試的假物件與 fixture
from test_reminders import GUILD_ID, TZ, assignee, env, last_occ_id, make_reminder, ts  # noqa: F401


def test_make_month_rule_validation():
    assert make_month_rule(None, None, None) is None
    assert make_month_rule(15, None, None) == "day:15"
    assert make_month_rule(None, 2, 3) == "nth:2:3"
    for bad in [(15, 2, 3), (None, 2, None), (None, None, 3), (32, None, None), (None, 5, 3), (None, 2, 7)]:
        with pytest.raises(ValueError):
            make_month_rule(*bad)


def test_month_rule_date_nth_weekday():
    # 2026/11/1 是星期日：星期四是 5、12、19、26
    assert month_rule_date(2026, 11, "nth:2:3") == date(2026, 11, 12)
    assert month_rule_date(2026, 11, "nth:1:0") == date(2026, 11, 2)  # 第 1 個星期一
    assert month_rule_date(2026, 11, "nth:-1:4") == date(2026, 11, 27)  # 最後一個星期五
    # 2026/12：星期四是 3、10、17、24、31，最後一個是 31
    assert month_rule_date(2026, 12, "nth:-1:3") == date(2026, 12, 31)
    assert month_rule_date(2026, 12, "nth:4:3") == date(2026, 12, 24)
    # 第 1～4 個在每個月都存在，包含 28 天的 2 月
    for month in range(1, 13):
        for nth in (1, 2, 3, 4):
            d = month_rule_date(2027, month, f"nth:{nth}:6")
            assert d.month == month and d.weekday() == 6


def test_month_rule_date_day_clamps_to_month_end():
    assert month_rule_date(2027, 2, "day:31") == date(2027, 2, 28)
    assert month_rule_date(2028, 2, "day:31") == date(2028, 2, 29)
    assert month_rule_date(2026, 11, "day:15") == date(2026, 11, 15)


def test_first_rule_due():
    at = time(9, 0)
    rule = "nth:2:3"
    assert first_rule_due(None, at, datetime(2026, 11, 2, 8, 0), rule) == datetime(2026, 11, 12, 9, 0)
    assert first_rule_due(None, at, datetime(2026, 11, 12, 10, 0), rule) == datetime(2026, 12, 10, 9, 0)  # 今天的已過
    assert first_rule_due(date(2026, 11, 12), at, datetime(2026, 11, 1), rule) == datetime(2026, 11, 12, 9, 0)  # 含當天
    assert first_rule_due(date(2026, 11, 13), at, datetime(2026, 11, 1), rule) == datetime(2026, 12, 10, 9, 0)
    assert first_rule_due(None, at, datetime(2026, 11, 20, 8, 0), "day:15") == datetime(2026, 12, 15, 9, 0)


def test_fixed_schedule_with_rule_across_months_and_years():
    anchor = datetime(2026, 11, 12, 9, 0)  # 第 2 個星期四
    rule = "nth:2:3"
    assert next_fixed_due(anchor, "month", 1, datetime(2026, 11, 12, 9, 0), rule) == datetime(2026, 12, 10, 9, 0)
    assert next_fixed_due(anchor, "month", 1, datetime(2026, 12, 10, 9, 0), rule) == datetime(2027, 1, 14, 9, 0)
    # 每 2 個月：11 → 1 → 3
    assert next_fixed_due(anchor, "month", 2, datetime(2026, 11, 12, 9, 0), rule) == datetime(2027, 1, 14, 9, 0)
    assert shift(anchor, "month", 2, 2, rule) == datetime(2027, 3, 11, 9, 0)
    # 離很久之後也算得對（例如 bot 關機很久）
    assert next_fixed_due(anchor, "month", 1, datetime(2028, 5, 1), rule) == datetime(2028, 5, 11, 9, 0)
    # 每月幾號：31 號遇到小月取月底，下個月再回到 31
    anchor31 = datetime(2026, 12, 31, 9, 0)
    assert next_fixed_due(anchor31, "month", 1, anchor31, "day:31") == datetime(2027, 1, 31, 9, 0)
    assert next_fixed_due(anchor31, "month", 1, datetime(2027, 1, 31, 9, 0), "day:31") == datetime(2027, 2, 28, 9, 0)


def test_after_completion_with_rule_uses_month_after_completion():
    # 11/5 完成、每月第 2 個星期四 → 12 月的第 2 個星期四（不是完成日 + 1 個月）
    done = datetime(2026, 11, 5, 17, 30)
    assert next_after_completion(done, time(9, 0), "month", 1, "nth:2:3") == datetime(2026, 12, 10, 9, 0)
    assert next_after_completion(datetime(2026, 12, 20, 8, 0), time(9, 0), "month", 2, "day:15") == datetime(2027, 2, 15, 9, 0)
    # 沒有規則時維持原本行為
    assert next_after_completion(done, time(9, 0), "month", 1) == datetime(2026, 12, 5, 9, 0)


def test_describe_interval_with_rule():
    assert describe_interval("month", 1, "nth:2:3") == "每月第 2 個週四"
    assert describe_interval("month", 1, "nth:-1:4") == "每月最後一個週五"
    assert describe_interval("month", 2, "nth:1:0") == "每 2 個月的第 1 個週一"
    assert describe_interval("month", 1, "day:15") == "每月 15 號"
    assert describe_interval("month", 3, "day:1") == "每 3 個月的 1 號"
    assert describe_interval("month", 1) == "每月"
    assert describe_interval("week", 2, "day:15") == "每 2 週"  # 規則只對「月」有效


def test_month_rule_option_validation():
    def choice(v):
        return SimpleNamespace(value=v)

    check = Reminders._month_rule
    assert check("month", None, None, None) is None
    assert check("month", 15, None, None) == "day:15"
    assert check("month", None, choice(2), choice(3)) == "nth:2:3"
    for args in [
        ("week", 15, None, None),  # 單位不是月
        ("day", None, choice(2), choice(3)),
        ("month", 15, choice(2), choice(3)),  # 兩種規則只能擇一
        ("month", None, choice(2), None),  # 缺星期幾
        ("month", None, None, choice(3)),  # 缺第幾個
    ]:
        with pytest.raises(UserError):
            check(*args)


async def test_monthly_nth_weekday_cycle(env):  # noqa: F811
    # 每月第 2 個星期四 09:00（2026/11/12 是第一次）
    r = await make_reminder(env, unit="month", count=1, anchor="2026-11-12T09:00", month_rule="nth:2:3")
    env.clock.set("2026-11-12T09:00")
    await env.cog.tick()
    assert len(env.guild.channel.sent) == 1
    assert (await env.db.get_reminder(r.id)).next_due_at == ts("2026-12-10T09:00")  # 12 月第 2 個星期四

    env.clock.set("2026-12-10T09:00")
    await env.cog.tick()
    assert (await env.db.get_reminder(r.id)).next_due_at == ts("2027-01-14T09:00")


async def test_monthly_rule_completion_mode(env):  # noqa: F811
    r = await make_reminder(
        env, mode="completion", unit="month", count=1, anchor="2026-11-12T09:00", month_rule="nth:2:3"
    )
    env.clock.set("2026-11-12T09:00")
    await env.cog.tick()
    env.clock.set("2026-11-20T10:00")  # 晚幾天才完成
    ok, *_ = await env.cog.complete(last_occ_id(env), assignee(env))
    assert ok
    assert (await env.db.get_reminder(r.id)).next_due_at == ts("2026-12-10T09:00")  # 下個月的第 2 個星期四


async def test_monthly_rule_in_info_and_agenda_and_slots(env):  # noqa: F811
    r = await make_reminder(env, unit="month", count=1, anchor="2026-11-12T09:00", month_rule="nth:2:3")
    assert "每月第 2 個週四" in env.cog.info_embed(r).fields[3].value

    embed = await env.cog.agenda_embed(AgendaQuery(GUILD_ID, assignee(env)), date(2026, 11, 9))
    assert "週四 11/12" in embed.description
    embed = await env.cog.agenda_embed(AgendaQuery(GUILD_ID, assignee(env)), date(2026, 12, 7))
    assert "週四 12/10" in embed.description

    zone = get_zone(TZ)
    start, end = to_ts(datetime(2026, 11, 1), zone), to_ts(datetime(2027, 3, 1), zone)
    assert list(fixed_slots(r, start, end)) == [
        ts("2026-11-12T09:00"), ts("2026-12-10T09:00"), ts("2027-01-14T09:00"), ts("2027-02-11T09:00"),
    ]


async def test_reminder_month_rule_persisted_and_migrated():
    db = Database(":memory:")
    await db.connect()
    try:
        r = await db.create_reminder(
            guild_id=1, channel_id=2, title="月會", description=None, target_user_id=None, target_role_id=5,
            manager_role_id=None, interval_unit="month", interval_count=1, anchor_local="2026-11-12T09:00",
            timezone="Asia/Taipei", schedule_mode="fixed", max_postpone_days=0, next_due_at=1, created_by=1,
            month_rule="nth:2:3",
        )
        assert (await db.get_reminder(r.id)).month_rule == "nth:2:3"
        await db.update_reminder(r.id, month_rule=None)
        assert (await db.get_reminder(r.id)).month_rule is None

        # 從舊版（schema 4，沒有 month_rule 欄位）升級：既有任務保留，規則為空
        await db.conn.execute("ALTER TABLE reminders DROP COLUMN month_rule")
        await db.conn.execute("PRAGMA user_version = 4")
        await db.conn.commit()
        await db._migrate()
        assert (await db.get_reminder(r.id)).month_rule is None
    finally:
        await db.close()
