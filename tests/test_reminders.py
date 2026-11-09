"""用假的 Discord 物件模擬提醒流程：發提醒 → 完成 / 延期 → 下一個週期。"""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from dc_manager.bot import DcManager
from dc_manager.cogs.reminders import PREFIX, Reminders, postpone_options
from dc_manager.config import Config
from dc_manager.db import MIGRATIONS, SCHEMA_VERSION, Database
from dc_manager.schedule import get_zone, to_ts
from dc_manager.utils import UserError

TZ = "Asia/Taipei"
GUILD_ID, CHANNEL_ID = 1, 2
ASSIGNEE_ROLE, MANAGER_ROLE = 500, 600
DAY = 86400


def ts(s: str) -> int:
    return to_ts(datetime.fromisoformat(s), get_zone(TZ))


class Clock:
    def __init__(self, start: str):
        self.t = ts(start)

    def __call__(self):
        return self.t

    def set(self, s: str):
        self.t = ts(s)


class FakeMessage:
    _next_id = 1000

    def __init__(self, channel, content, embed, view):
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id
        self.channel = channel
        self.content, self.embed, self.view = content, embed, view


class FakeChannel(discord.abc.Messageable):
    def __init__(self):
        self.id = CHANNEL_ID
        self.sent: list[FakeMessage] = []
        self.edits: dict[int, list] = {}
        self.fail: Exception | None = None

    async def _get_channel(self):
        return self

    async def send(self, content=None, *, embed=None, view=None, allowed_mentions=None, **k):
        if self.fail:
            raise self.fail
        msg = FakeMessage(self, content, embed, view)
        msg.allowed_mentions = allowed_mentions
        self.sent.append(msg)
        return msg

    def get_partial_message(self, message_id):
        channel = self

        class Partial:
            async def edit(self, *, embed=None, view=None):
                channel.edits.setdefault(message_id, []).append((embed, view))

        return Partial()


class FakeGuild:
    id = GUILD_ID

    def __init__(self):
        self.channel = FakeChannel()

    def get_channel_or_thread(self, cid):
        return self.channel if cid == CHANNEL_ID else None


class FakeMember:
    def __init__(self, guild, uid, roles=(), admin=False):
        self.id = uid
        self.guild = guild
        self.mention = f"<@{uid}>"
        self._roles = set(roles)
        self.guild_permissions = discord.Permissions(manage_guild=admin)

    def get_role(self, rid):
        return object() if rid in self._roles else None


@pytest.fixture
async def env():
    cfg = Config(token="x", db_path=":memory:", log_dir=Path("."), log_level="INFO", members_intent=True, dev_guild_id=None)
    bot = DcManager(cfg)
    await bot.db.connect()
    guild = FakeGuild()
    bot.get_guild = lambda gid: guild if gid == GUILD_ID else None  # type: ignore[method-assign]
    clock = Clock("2026-11-02T08:00")
    cog = Reminders(bot, clock=clock, autostart=False)
    yield SimpleNamespace(bot=bot, guild=guild, clock=clock, cog=cog, db=bot.db)
    await bot.db.close()


async def make_reminder(env, *, mode="fixed", unit="week", count=2, max_postpone=3, anchor="2026-11-02T09:00", **kw):
    fields = dict(
        guild_id=GUILD_ID, channel_id=CHANNEL_ID, title="A 儀器校正", description=None,
        target_user_id=None, target_role_id=ASSIGNEE_ROLE, manager_role_id=MANAGER_ROLE,
        interval_unit=unit, interval_count=count, anchor_local=anchor, timezone=TZ,
        schedule_mode=mode, max_postpone_days=max_postpone, next_due_at=ts(anchor), created_by=1,
    )
    fields.update(kw)
    return await env.db.create_reminder(**fields)


def assignee(env):
    return FakeMember(env.guild, 10, roles=[ASSIGNEE_ROLE])


def last_occ_id(env):
    view = env.guild.channel.sent[-1].view
    return int(view.children[0].custom_id.rsplit(":", 1)[1])


async def test_fixed_cycle_send_complete_next(env):
    r = await make_reminder(env)
    await env.cog.tick()  # 08:00 還沒到
    assert env.guild.channel.sent == []

    env.clock.set("2026-11-02T09:00")
    await env.cog.tick()
    msg = env.guild.channel.sent[-1]
    assert f"<@&{ASSIGNEE_ROLE}>" in msg.content
    assert [b.label for b in msg.view.children] == ["完成", "延期"]
    assert (await env.db.get_reminder(r.id)).next_due_at == ts("2026-11-16T09:00")

    ok, _, r2, occ = await env.cog.complete(last_occ_id(env), assignee(env))
    assert ok and occ.status == "done"
    # 固定週期：完成不影響下一次時間
    assert r2.next_due_at == ts("2026-11-16T09:00")
    # 重複按完成
    ok, text, *_ = await env.cog.complete(occ.id, assignee(env))
    assert not ok


async def test_fixed_cycle_marks_previous_missed(env):
    await make_reminder(env)
    env.clock.set("2026-11-02T09:00")
    await env.cog.tick()
    first = last_occ_id(env)
    first_msg = env.guild.channel.sent[-1].id

    env.clock.set("2026-11-16T09:00")
    await env.cog.tick()
    assert (await env.db.get_occurrence(first)).status == "missed"
    assert first_msg in env.guild.channel.edits
    assert len(env.guild.channel.sent) == 2


async def test_catch_up_after_downtime_sends_once(env):
    r = await make_reminder(env, unit="day", count=1)
    env.clock.set("2026-11-10T12:00")  # 樹莓派關機好幾天
    await env.cog.tick()
    await env.cog.tick()
    assert len(env.guild.channel.sent) == 1
    assert (await env.db.get_reminder(r.id)).next_due_at == ts("2026-11-11T09:00")


async def test_completion_mode_restarts_from_completion_day(env):
    r = await make_reminder(env, mode="completion")
    env.clock.set("2026-11-02T09:00")
    await env.cog.tick()
    assert (await env.db.get_reminder(r.id)).next_due_at is None

    env.clock.set("2026-11-05T17:30")
    ok, _, r2, _ = await env.cog.complete(last_occ_id(env), assignee(env))
    assert ok
    assert r2.next_due_at == ts("2026-11-19T09:00")

    # 等待期間不會再發
    env.clock.set("2026-11-16T09:00")
    await env.cog.tick()
    assert len(env.guild.channel.sent) == 1


async def test_postpone_limit_and_refire(env):
    r = await make_reminder(env, mode="completion", max_postpone=3)
    env.clock.set("2026-11-02T09:00")
    await env.cog.tick()
    occ_id = last_occ_id(env)

    occ = await env.db.get_occurrence(occ_id)
    assert [d for d, _ in postpone_options(r, occ, env.clock())] == [1, 2, 3]

    ok, _, _, occ = await env.cog.snooze(occ_id, assignee(env), 2)
    assert ok and occ.status == "snoozed" and occ.remind_at == ts("2026-11-04T09:00")
    ok, text, *_ = await env.cog.snooze(occ_id, assignee(env), 2)
    assert not ok  # 累計超過 3 天
    ok, *_ = await env.cog.snooze(occ_id, assignee(env), 1)
    assert ok
    occ = await env.db.get_occurrence(occ_id)
    assert occ.postponed_days == 3 and occ.remind_at == ts("2026-11-03T09:00")
    assert postpone_options(r, occ, env.clock()) == []

    env.clock.set("2026-11-03T09:00")
    await env.cog.tick()
    assert len(env.guild.channel.sent) == 2
    assert "延期後再次提醒" in env.guild.channel.sent[-1].content
    occ = await env.db.get_occurrence(occ_id)
    assert occ.status == "pending" and occ.message_id == env.guild.channel.sent[-1].id
    # 已用完延期額度 → 新訊息只有「完成」按鈕
    assert [b.label for b in env.guild.channel.sent[-1].view.children] == ["完成"]


async def test_fixed_postpone_cannot_pass_next_cycle(env):
    r = await make_reminder(env, unit="week", count=1, max_postpone=30)
    env.clock.set("2026-11-02T09:00")
    await env.cog.tick()
    r = await env.db.get_reminder(r.id)
    occ = await env.db.get_occurrence(last_occ_id(env))
    days = [d for d, _ in postpone_options(r, occ, env.clock())]
    assert days == [1, 2, 3, 4, 5, 6]


async def test_permissions(env):
    await make_reminder(env)
    env.clock.set("2026-11-02T09:00")
    await env.cog.tick()
    occ_id = last_occ_id(env)
    stranger = FakeMember(env.guild, 11)
    ok, text, *_ = await env.cog.complete(occ_id, stranger)
    assert not ok and "負責" in text
    ok, *_ = await env.cog.snooze(occ_id, stranger, 1)
    assert not ok
    manager = FakeMember(env.guild, 12, roles=[MANAGER_ROLE])
    ok, *_ = await env.cog.complete(occ_id, manager)
    assert ok


async def test_resolve_manager_role(env):
    admin = FakeMember(env.guild, 1, admin=True)
    assert await env.cog._resolve_manager_role(admin, None) is None
    plain = FakeMember(env.guild, 2, roles=[MANAGER_ROLE])
    with pytest.raises(UserError):
        await env.cog._resolve_manager_role(plain, None)  # 還沒登記為管理身分組
    await env.db.add_reminder_manager(GUILD_ID, MANAGER_ROLE)
    assert await env.cog._resolve_manager_role(plain, None) == MANAGER_ROLE
    other = SimpleNamespace(id=999, mention="<@&999>")
    with pytest.raises(UserError):
        await env.cog._resolve_manager_role(plain, other)


async def test_missing_channel_pauses_task(env):
    r = await make_reminder(env, channel_id=12345)
    env.clock.set("2026-11-02T09:00")
    await env.cog.tick()
    assert (await env.db.get_reminder(r.id)).active is False


async def test_transient_send_error_retries(env):
    r = await make_reminder(env)
    env.clock.set("2026-11-02T09:00")
    env.guild.channel.fail = discord.HTTPException(SimpleNamespace(status=503, reason="x"), "down")
    await env.cog.tick()
    r = await env.db.get_reminder(r.id)
    assert r.active and r.next_due_at == env.clock() + 300
    assert await env.db.open_occurrences(r.id) == []
    env.guild.channel.fail = None
    env.clock.t += 300
    await env.cog.tick()
    assert len(env.guild.channel.sent) == 1


async def test_button_interactions(env, monkeypatch):
    monkeypatch.setattr(discord, "Member", FakeMember)
    await make_reminder(env, mode="completion")
    env.clock.set("2026-11-02T09:00")
    await env.cog.tick()
    occ_id = last_occ_id(env)

    class Resp:
        def __init__(self):
            self.edited = None
            self.sent = None

        def is_done(self):
            return self.edited is not None or self.sent is not None

        async def edit_message(self, **k):
            self.edited = k

        async def send_message(self, text=None, **k):
            self.sent = (text, k)

    def inter(custom_id, values=None, member=None):
        data = {"custom_id": custom_id}
        if values:
            data["values"] = values
        return SimpleNamespace(
            type=discord.InteractionType.component, data=data, guild=env.guild,
            user=member or assignee(env), response=Resp(), followup=None,
        )

    # 按延期 → 出現選單
    i = inter(f"{PREFIX}snooze:{occ_id}")
    await env.cog.on_interaction(i)
    select = i.response.sent[1]["view"].children[0]
    assert [o.value for o in select.options] == ["1", "2", "3"]

    # 選 2 天
    i = inter(f"{PREFIX}pick:{occ_id}", values=["2"])
    await env.cog.on_interaction(i)
    assert i.response.edited["content"].startswith("✅")
    assert (await env.db.get_occurrence(occ_id)).status == "snoozed"

    # 按完成 → 訊息變成已完成、按鈕移除
    i = inter(f"{PREFIX}done:{occ_id}")
    await env.cog.on_interaction(i)
    assert i.response.edited["view"] is None
    assert i.response.edited["embed"].color == discord.Color.green()


async def test_migration_from_v1(tmp_path):
    import aiosqlite

    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(MIGRATIONS[1] + "\nPRAGMA user_version = 1;")
        await conn.execute("INSERT INTO guild_settings (guild_id, log_channel_id) VALUES (1, 99)")
        await conn.commit()
    db = Database(path)
    await db.connect()
    async with db.conn.execute("PRAGMA user_version") as cur:
        assert (await cur.fetchone())[0] == SCHEMA_VERSION
    s = await db.get_settings(1)
    assert s.log_channel_id == 99 and s.timezone is None
    await db.close()
