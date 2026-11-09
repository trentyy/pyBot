"""儀器警報：規則引擎、區網接收器、PostgreSQL 設定與轉換、按鈕。"""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from dc_manager.alarms.engine import PREFIX, AlarmEngine, InvalidReading, cleared, triggered, validate_metric
from dc_manager.alarms.http_api import RateLimiter, create_app, hash_token, new_token, parse_readings, parse_ts
from dc_manager.alarms.pg import ConfigError, PgMetric, PgPoller, PgSource, load_config, rows_to_readings
from dc_manager.bot import DcManager
from dc_manager.cogs.alarms import Alarms, check_clear_threshold
from dc_manager.config import Config
from dc_manager.schedule import get_zone
from dc_manager.utils import UserError

GUILD_ID, CHANNEL_ID, ROLE_ID, MANAGER_ROLE = 1, 2, 500, 600
T0 = 1_800_000_000


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


class FakeMessage:
    _next = 5000

    def __init__(self, channel, content, embed, view, reference):
        FakeMessage._next += 1
        self.id = FakeMessage._next
        self.channel = channel
        self.content, self.embed, self.view, self.reference = content, embed, view, reference


class FakeChannel(discord.abc.Messageable):
    def __init__(self):
        self.id = CHANNEL_ID
        self.sent: list[FakeMessage] = []
        self.edits: dict[int, list] = {}
        self.fail: Exception | None = None

    async def _get_channel(self):
        return self

    async def send(self, content=None, *, embed=None, view=None, allowed_mentions=None, reference=None, **k):
        if self.fail:
            raise self.fail
        msg = FakeMessage(self, content, embed, view, reference)
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
    def __init__(self, uid, roles=(), admin=False):
        self.id = uid
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
    clock = Clock()
    engine = AlarmEngine(bot, clock=clock)
    await engine.load()
    yield SimpleNamespace(bot=bot, db=bot.db, guild=guild, clock=clock, engine=engine, sent=guild.channel.sent)
    await bot.db.close()


async def make_rule(env, **kw):
    fields = dict(
        guild_id=GUILD_ID, name="A 爐溫度", metric="furnace1.temp", operator=">=", threshold=850.0,
        clear_threshold=None, for_seconds=0, kind="notify", repeat_minutes=0, nodata_minutes=0,
        channel_id=CHANNEL_ID, target_user_id=None, target_role_id=ROLE_ID, manager_role_id=MANAGER_ROLE,
        created_by=1, created_ts=env.clock(),
    )
    fields.update(kw)
    return await env.db.create_alarm_rule(**fields)


async def feed(env, value, metric="furnace1.temp", advance=10):
    env.clock.t += advance
    await env.engine.ingest(GUILD_ID, metric, value)


def labels(msg):
    return [b.label for b in msg.view.children] if msg.view else []


# ---------- 判斷邏輯 ----------


def test_validate_metric():
    assert validate_metric(" furnace1.temp ") == "furnace1.temp"
    for bad in ["", "有中文", "a b", "x" * 65, "drop;table"]:
        with pytest.raises(InvalidReading):
            validate_metric(bad)


def test_triggered_and_hysteresis():
    high = SimpleNamespace(operator=">=", threshold=850, clear_threshold=840)
    assert triggered(high, 850) and not triggered(high, 849.9)
    assert not cleared(high, 845) and cleared(high, 840)
    low = SimpleNamespace(operator="<=", threshold=50, clear_threshold=None)
    assert triggered(low, 50) and cleared(low, 50.1)


def test_clear_threshold_direction():
    check_clear_threshold(">=", 850, 840)
    check_clear_threshold("<=", 50, 60)
    with pytest.raises(UserError):
        check_clear_threshold(">=", 850, 900)
    with pytest.raises(UserError):
        check_clear_threshold("<", 50, 40)


# ---------- 引擎 ----------


async def test_notify_once_then_rearm(env):
    rule = await make_rule(env)
    await feed(env, 800)
    assert env.sent == []
    await feed(env, 851)
    assert len(env.sent) == 1 and f"<@&{ROLE_ID}>" in env.sent[0].content
    assert labels(env.sent[0]) == ["已知悉"]
    await feed(env, 860)  # 仍在條件內，不重複
    assert len(env.sent) == 1
    await feed(env, 700)  # 恢復 → 編輯原訊息
    assert env.sent[0].id in env.guild.channel.edits
    assert (await env.db.get_alarm_rule(rule.id)).state == "ok"
    await feed(env, 855)  # 再次達到 → 再通知
    assert len(env.sent) == 2


async def test_for_seconds_requires_duration(env):
    rule = await make_rule(env, for_seconds=60)
    await feed(env, 900)
    assert env.sent == [] and (await env.db.get_alarm_rule(rule.id)).state == "pending"
    await feed(env, 800, advance=10)  # 中途掉下去 → 重新計算
    assert (await env.db.get_alarm_rule(rule.id)).state == "ok"
    await feed(env, 900)
    env.clock.t += 61
    await env.engine.tick()  # 沒有新數值，靠排程觸發
    assert len(env.sent) == 1


async def test_hysteresis_prevents_flapping(env):
    rule = await make_rule(env, clear_threshold=840)
    await feed(env, 851)
    await feed(env, 845)  # 低於門檻但高於恢復門檻 → 仍是觸發中
    await feed(env, 852)
    assert len(env.sent) == 1
    assert (await env.db.get_alarm_rule(rule.id)).state == "firing"
    await feed(env, 839)
    assert (await env.db.get_alarm_rule(rule.id)).state == "ok"


async def test_alarm_repeats_until_ack(env):
    rule = await make_rule(env, kind="alarm", repeat_minutes=10, operator="<=", threshold=5.0)
    await feed(env, 3)
    assert labels(env.sent[0]) == ["已知悉", "靜音"]
    env.clock.t += 9 * 60
    await env.engine.tick()
    assert len(env.sent) == 1
    env.clock.t += 61
    await env.engine.tick()
    assert len(env.sent) == 2 and env.sent[1].reference is not None
    ev_id = (await env.db.get_alarm_rule(rule.id)).event_id
    _, ev, ok = await env.engine.ack(ev_id, 42)
    assert ok and ev.status == "acked"
    env.clock.t += 3600
    await env.engine.tick()
    assert len(env.sent) == 2
    # 恢復時發「已恢復」（不 @ 人）
    await feed(env, 20)
    assert "已恢復" in env.sent[-1].content
    assert (await env.db.get_alarm_event(ev_id)).status == "resolved"


async def test_silence_pauses_repeats(env):
    rule = await make_rule(env, kind="alarm", repeat_minutes=5)
    await feed(env, 900)
    ev_id = (await env.db.get_alarm_rule(rule.id)).event_id
    await env.engine.silence(ev_id, 30)
    env.clock.t += 20 * 60
    await env.engine.tick()
    assert len(env.sent) == 1
    env.clock.t += 11 * 60
    await env.engine.tick()
    assert len(env.sent) == 2


async def test_nodata_alarm_and_recovery(env):
    rule = await make_rule(env, nodata_minutes=5, kind="alarm", repeat_minutes=0)
    await feed(env, 100)
    env.clock.t += 4 * 60
    await env.engine.tick()
    assert env.sent == []
    env.clock.t += 61
    await env.engine.tick()
    assert len(env.sent) == 1 and "沒有收到資料" in env.sent[0].content
    env.clock.t += 600
    await env.engine.tick()
    assert len(env.sent) == 1  # 不重複發
    await feed(env, 100)
    assert (await env.db.get_alarm_rule(rule.id)).nodata_event_id is None
    assert "已收到資料" in env.sent[-1].content


async def test_nodata_not_fired_right_after_restart(env):
    await env.db.save_alarm_metrics([(GUILD_ID, "furnace1.temp", 100.0, T0 - 86400, "lan:x")])
    await make_rule(env, nodata_minutes=5)
    await env.engine.load()  # 模擬重開機
    env.clock.t += 60
    await env.engine.tick()
    assert env.sent == []


async def test_guilds_are_isolated(env):
    await make_rule(env)
    await env.engine.ingest(999, "furnace1.temp", 900)
    assert env.sent == []
    assert "furnace1.temp" not in env.engine.metrics_for(GUILD_ID)


async def test_out_of_order_reading_ignored(env):
    await env.engine.ingest(GUILD_ID, "x", 1, ts=T0 + 100)
    await env.engine.ingest(GUILD_ID, "x", 2, ts=T0 + 50)
    assert env.engine.latest[(GUILD_ID, "x")].value == 1


async def test_missing_channel_pauses_rule(env):
    rule = await make_rule(env, channel_id=12345)
    await feed(env, 900)
    assert (await env.db.get_alarm_rule(rule.id)).active is False


async def test_transient_failure_retries_on_next_reading(env):
    rule = await make_rule(env)
    env.guild.channel.fail = discord.HTTPException(SimpleNamespace(status=503, reason="x"), "down")
    await feed(env, 900)
    assert (await env.db.get_alarm_rule(rule.id)).state == "ok"
    env.guild.channel.fail = None
    await feed(env, 901)
    assert len(env.sent) == 1


async def test_metrics_flush_to_db(env):
    await feed(env, 123.5)
    await env.engine.flush(force=True)
    rows = await env.db.load_alarm_metrics()
    assert rows == [(GUILD_ID, "furnace1.temp", 123.5, env.clock(), None)]


# ---------- 區網接收器 ----------


def test_parse_readings_and_ts():
    now = T0
    assert parse_readings({"metric": "a", "value": 1}, now) == [("a", 1.0, now)]
    batch = parse_readings({"readings": [{"metric": "a", "value": 1, "ts": now - 5}, {"metric": "b", "value": 2.5}]}, now)
    assert batch == [("a", 1.0, now - 5), ("b", 2.5, now)]
    assert parse_ts((now - 1) * 1000, now) == now - 1  # 毫秒
    iso = datetime.fromtimestamp(now - 60, timezone.utc).isoformat()
    assert parse_ts(iso, now) == now - 60
    for bad in [{"metric": "a", "value": "1"}, {"metric": "a", "value": True}, {"metric": "a b", "value": 1},
                {"metric": "a", "value": 1, "ts": now + 3600}, {"metric": "a", "value": 1, "ts": "2026-11-01T09:00:00"}]:
        with pytest.raises(InvalidReading):
            parse_readings(bad, now)


def test_rate_limiter():
    t = [0.0]
    rl = RateLimiter(2, clock=lambda: t[0])
    assert rl.allow(1) and rl.allow(1) and not rl.allow(1)
    assert rl.allow(2)
    t[0] = 61
    assert rl.allow(1)


async def test_http_api(env):
    await make_rule(env)
    token = new_token()
    await env.db.create_alarm_source(GUILD_ID, "furnace-pc", hash_token(token), 1)
    async with TestClient(TestServer(create_app(env.engine))) as client:
        r = await client.get("/api/v1/health")
        assert r.status == 200
        r = await client.post("/api/v1/readings", json={"metric": "furnace1.temp", "value": 900})
        assert r.status == 401
        r = await client.post("/api/v1/readings", json={"metric": "furnace1.temp", "value": 900},
                              headers={"Authorization": "Bearer dcm_wrong"})
        assert r.status == 401
        auth = {"Authorization": f"Bearer {token}"}
        r = await client.post("/api/v1/readings", data="not json", headers=auth)
        assert r.status == 400
        r = await client.post("/api/v1/readings", json={"metric": "furnace1.temp", "value": "hot"}, headers=auth)
        assert r.status == 400 and "數字" in (await r.json())["error"]
        r = await client.post("/api/v1/readings", json={"readings": [
            {"metric": "furnace1.temp", "value": 900}, {"metric": "furnace2.temp", "value": 25}]}, headers=auth)
        assert r.status == 200 and (await r.json())["accepted"] == 2
        r = await client.post("/api/v1/readings", data="x" * 70_000, headers=auth)
        assert r.status == 413
    assert len(env.sent) == 1
    assert env.engine.latest[(GUILD_ID, "furnace2.temp")].source == "lan:furnace-pc"


# ---------- PostgreSQL ----------


def test_load_config(tmp_path, monkeypatch):
    path = tmp_path / "alarm_sources.toml"
    path.write_text(
        '''
[[postgres]]
name = "lab"
dsn_env = "LAB_PG"
timezone = "Asia/Taipei"

[[postgres.metric]]
key = "furnace1.temp"
query = "SELECT value, ts FROM t ORDER BY ts DESC LIMIT 1"
interval = 1
guilds = [1]

[[postgres.metric]]
prefix = "temp."
query = "SELECT instrument AS metric, value, ts FROM latest"
guilds = [1, 2]
''',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(path)  # 環境變數還沒設
    monkeypatch.setenv("LAB_PG", "postgresql://u:p@h/db")
    (src,) = load_config(path)
    assert src.dsn == "postgresql://u:p@h/db"
    assert src.metrics[0].key == "furnace1.temp" and src.metrics[0].interval == 5  # 最短 5 秒
    assert src.metrics[1].prefix == "temp." and src.metrics[1].guilds == (1, 2)

    path.write_text('[[postgres]]\ndsn = "x"\n[[postgres.metric]]\nquery = "SELECT 1"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)  # 沒有 guilds


def test_rows_to_readings():
    tz = get_zone("Asia/Taipei")
    naive = datetime(2026, 11, 1, 9, 0)  # timestamp without time zone → 視為台北時間
    single = PgMetric(query="q", interval=30, guilds=(1,), key="f1")
    out = rows_to_readings([{"value": Decimal("850.5"), "ts": naive}, {"value": 1, "ts": naive}], single, tz, T0)
    assert out == [("f1", 850.5, int(datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc).timestamp()))]

    multi = PgMetric(query="q", interval=30, guilds=(1,), prefix="temp.")
    rows = [{"metric": "F1", "value": 1.5, "ts": None}, {"metric": "F2", "value": None}]
    assert rows_to_readings(rows, multi, tz, T0) == [("temp.F1", 1.5, T0)]
    with pytest.raises(InvalidReading):
        rows_to_readings([{"value": 1}], multi, tz, T0)
    with pytest.raises(InvalidReading):
        rows_to_readings([{"val": 1}], single, tz, T0)


async def test_poll_once_with_fake_pool(env):
    await make_rule(env, metric="temp.F1")
    source = PgSource(name="lab", dsn="x", timezone="Asia/Taipei", metrics=())
    metric = PgMetric(query="SELECT ...", interval=30, guilds=(GUILD_ID,), prefix="temp.")
    poller = PgPoller(env.engine, [source])

    class Conn:
        async def fetch(self, query):
            assert query == "SELECT ..."
            return [{"metric": "F1", "value": 900, "ts": None}]

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *a):
            return False

    poller.pools["lab"] = SimpleNamespace(acquire=lambda: Acquire())
    poller._pool_locks["lab"] = __import__("asyncio").Lock()
    assert await poller.poll_once(source, metric) == 1
    assert len(env.sent) == 1


# ---------- Discord 指令與按鈕 ----------


def test_command_structure():
    cfg = Config(token="x", db_path=":memory:", log_dir=Path("."), log_level="INFO", members_intent=True, dev_guild_id=None)
    cog = Alarms(DcManager(cfg), autostart=False)
    cmds = {c.name: c for c in cog.__cog_app_commands_group__.commands}
    assert set(cmds) == {"source", "rule", "status"}
    assert {c.name for c in cmds["rule"].commands} == {"create", "edit", "pause", "resume", "delete", "test", "list", "info"}
    assert {c.name for c in cmds["source"].commands} == {"create", "rotate", "list", "delete"}


async def test_buttons(env, monkeypatch):
    monkeypatch.setattr(discord, "Member", FakeMember)
    rule = await make_rule(env, kind="alarm", repeat_minutes=10)
    await feed(env, 900)
    ev_id = (await env.db.get_alarm_rule(rule.id)).event_id
    cog = Alarms(env.bot, engine=env.engine, autostart=False)

    class Resp:
        def __init__(self):
            self.edited = self.sent = None

        def is_done(self):
            return self.edited is not None or self.sent is not None

        async def edit_message(self, **k):
            self.edited = k

        async def send_message(self, text=None, **k):
            self.sent = (text, k)

    def inter(custom_id, member, values=None):
        data = {"custom_id": custom_id, **({"values": values} if values else {})}
        return SimpleNamespace(type=discord.InteractionType.component, data=data, guild=env.guild,
                               user=member, response=Resp(), followup=None)

    stranger = FakeMember(7)
    i = inter(f"{PREFIX}ack:{ev_id}", stranger)
    await cog.on_interaction(i)
    assert "只有負責" in i.response.sent[0]

    member = FakeMember(8, roles=[ROLE_ID])
    i = inter(f"{PREFIX}silence:{ev_id}", member)
    await cog.on_interaction(i)
    assert [o.value for o in i.response.sent[1]["view"].children[0].options][0] == "30"
    i = inter(f"{PREFIX}mute:{ev_id}", member, values=["60"])
    await cog.on_interaction(i)
    assert (await env.db.get_alarm_event(ev_id)).silenced_until == env.clock() + 3600

    i = inter(f"{PREFIX}ack:{ev_id}", member)
    await cog.on_interaction(i)
    assert i.response.edited["view"] is None
    assert (await env.db.get_alarm_event(ev_id)).acked_by == 8
