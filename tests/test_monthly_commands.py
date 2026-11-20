"""實際呼叫 /reminder create 與 /reminder edit，確認每月規則從輸入到資料庫都正確。"""
from types import SimpleNamespace

import discord
import pytest

from dc_manager.utils import UserError

from test_reminders import ASSIGNEE_ROLE, CHANNEL_ID, GUILD_ID, FakeChannel, FakeMember, env, ts  # noqa: F401


class FakeResponse:
    def __init__(self):
        self.done = False

    def is_done(self):
        return self.done

    async def send_message(self, text=None, **k):
        self.done = True


class FakeTextChannel(FakeChannel):
    """通過 isinstance(channel, discord.TextChannel) 檢查的假頻道。"""

    mention = "<#2>"

    def __init__(self, guild):
        super().__init__()
        self.guild = SimpleNamespace(me=None)
        self.real_guild = guild

    def permissions_for(self, who):
        return discord.Permissions(view_channel=True, send_messages=True, embed_links=True)


def choice(value):
    return SimpleNamespace(value=value)


@pytest.fixture
def cmd(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(discord, "Member", FakeMember)
    monkeypatch.setattr(discord, "TextChannel", FakeTextChannel)
    admin = FakeMember(env.guild, 1, admin=True)
    admin.guild = SimpleNamespace(id=GUILD_ID)
    role = SimpleNamespace(id=ASSIGNEE_ROLE, mention=f"<@&{ASSIGNEE_ROLE}>", mentionable=True, is_default=lambda: False)
    channel = FakeTextChannel(env.guild)
    inter = SimpleNamespace(
        user=admin,
        guild=SimpleNamespace(id=GUILD_ID),
        guild_id=GUILD_ID,
        channel=channel,
        response=FakeResponse(),
    )
    return SimpleNamespace(env=env, inter=inter, role=role, channel=channel)


def new_inter(cmd):
    inter = SimpleNamespace(**cmd.inter.__dict__)
    inter.response = FakeResponse()
    return inter


async def create(cmd, **kw):
    args = dict(
        title="月會", every=1, unit=choice("month"), user=None, role=cmd.role, channel=cmd.channel,
        start_date=None, at="09:00", schedule=None, max_postpone=7, manager_role=None, description=None,
        month_day=None, nth=None, weekday=None,
    )
    args.update(kw)
    cog = cmd.env.cog
    await cog.create.callback(cog, new_inter(cmd), **args)
    reminders = await cmd.env.db.list_reminders(GUILD_ID)
    return reminders[-1]


async def test_create_nth_weekday(cmd):
    # 現在是 2026/11/2（星期一）08:00：每月第 2 個星期四 → 第一次是 11/12
    r = await create(cmd, nth=choice(2), weekday=choice(3))
    assert r.month_rule == "nth:2:3" and r.interval_unit == "month"
    assert r.anchor_local == "2026-11-12T09:00"
    assert r.next_due_at == ts("2026-11-12T09:00")


async def test_create_last_friday_with_start_date(cmd):
    r = await create(cmd, nth=choice(-1), weekday=choice(4), start_date="2026-11-28", at="17:30")
    assert r.month_rule == "nth:-1:4"
    assert r.anchor_local == "2026-12-25T17:30"  # 11/28 之後的第一個「最後一個星期五」是 12/25
    assert r.next_due_at == ts("2026-12-25T17:30")


async def test_create_month_day(cmd):
    r = await create(cmd, month_day=15)
    assert r.month_rule == "day:15"
    assert r.next_due_at == ts("2026-11-15T09:00")
    r = await create(cmd, month_day=31, every=2)  # 每 2 個月的 31 號
    assert r.next_due_at == ts("2026-11-30T09:00")  # 11 月沒有 31 號，取月底


async def test_create_without_rule_keeps_old_behaviour(cmd):
    r = await create(cmd, every=2, unit=choice("week"), start_date="2026-11-09")
    assert r.month_rule is None and r.next_due_at == ts("2026-11-09T09:00")


async def test_create_rejects_bad_combinations(cmd):
    for kw in (
        dict(unit=choice("week"), month_day=15),
        dict(nth=choice(2)),
        dict(weekday=choice(3)),
        dict(month_day=15, nth=choice(2), weekday=choice(3)),
    ):
        with pytest.raises(UserError):
            await create(cmd, **kw)
    assert await cmd.env.db.list_reminders(GUILD_ID) == []


async def test_edit_changes_and_clears_rule(cmd):
    cog = cmd.env.cog
    r = await create(cmd, nth=choice(2), weekday=choice(3))  # 每月第 2 個星期四

    async def edit(**kw):
        args = dict(
            title=None, description=None, every=None, unit=None, user=None, role=None, remove_user=False,
            remove_role=False, channel=None, start_date=None, at=None, schedule=None, max_postpone=None,
            manager_role=None, month_day=None, nth=None, weekday=None,
        )
        args.update(kw)
        await cog.edit.callback(cog, new_inter(cmd), r.id, **args)
        return await cmd.env.db.get_reminder(r.id)

    # 換成每月第 1 個星期一：從現在（11/2 08:00）往後找，今天 09:00 就符合
    r2 = await edit(nth=choice(1), weekday=choice(0))
    assert r2.month_rule == "nth:1:0" and r2.next_due_at == ts("2026-11-02T09:00")

    # 只改時間：規則和起點都保留
    r3 = await edit(at="18:00")
    assert r3.month_rule == "nth:1:0" and r3.anchor_local == "2026-11-02T18:00"

    # 換成每月 20 號：不會跳過這個月還沒到的 11/20
    r4 = await edit(month_day=20)
    assert r4.month_rule == "day:20" and r4.next_due_at == ts("2026-11-20T18:00")

    # 指定起點：取該日期（含）之後的第一個符合日期
    r4b = await edit(month_day=5, start_date="2026-11-06")
    assert r4b.next_due_at == ts("2026-12-05T18:00")

    # 單位改成週：規則清掉，不再套用每月規則
    r5 = await edit(unit=choice("week"), every=2, start_date="2026-11-16")
    assert r5.month_rule is None and r5.interval_unit == "week"

    # 單位是週時不能設每月規則
    with pytest.raises(UserError):
        await edit(month_day=15)
