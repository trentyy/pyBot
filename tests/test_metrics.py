import logging
from types import SimpleNamespace

import discord
from aiohttp.test_utils import TestClient, TestServer

from dc_manager.bot import DcManager
from dc_manager.config import Config
from dc_manager.metrics import LogCounter, MetricsServer, create_app, render


def make_bot(tmp_path):
    cfg = Config(token="x", db_path=tmp_path / "t.db", log_dir=tmp_path / "logs",
                 log_level="INFO", members_intent=False, dev_guild_id=None, metrics_host="127.0.0.1", metrics_port=0)
    return DcManager(cfg)


def make_logger(counter):
    logger = logging.getLogger("test_metrics")
    logger.handlers = [counter]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def test_handler_counts_each_level():
    c = LogCounter()
    lg = make_logger(c)
    lg.info("ignored")
    lg.debug("ignored")
    lg.warning("w")
    lg.warning("w")
    lg.error("e")
    lg.critical("c")
    assert c.counts == {"warning": 2, "error": 1, "critical": 1}


def test_last_error_only_set_by_error_or_above(tmp_path):
    c = LogCounter()
    lg = make_logger(c)
    lg.warning("w")
    assert c.last_error_ts is None
    lg.error("e")
    assert c.last_error_ts is not None


async def test_guild_count_follows_join_and_remove(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    guilds = []
    monkeypatch.setattr(DcManager, "guilds", property(lambda self: guilds))
    assert "dcbot_guilds 0\n" in render(bot, LogCounter())
    guilds.extend([SimpleNamespace(id=1), SimpleNamespace(id=2)])  # on_guild_join
    assert "dcbot_guilds 2\n" in render(bot, LogCounter())
    guilds.pop()  # on_guild_remove
    assert "dcbot_guilds 1\n" in render(bot, LogCounter())


async def test_render_up_latency_and_last_error_omitted(tmp_path):
    bot = make_bot(tmp_path)
    c = LogCounter()
    out = render(bot, c)
    assert "dcbot_up 0\n" in out
    assert "dcbot_latency_seconds" not in out  # 未連線 latency 為 nan
    assert "dcbot_last_error_timestamp_seconds" not in out
    assert 'dcbot_log_messages_total{level="error"} 0\n' in out
    bot.gateway_connected = True
    assert "dcbot_up 1\n" in render(bot, c)


async def test_metrics_endpoint_serves_text(tmp_path):
    bot = make_bot(tmp_path)
    c = LogCounter()
    make_logger(c).error("boom")
    async with TestClient(TestServer(create_app(bot, c))) as client:
        resp = await client.get("/metrics")
        body = await resp.text()
    assert resp.status == 200
    assert resp.content_type == "text/plain"
    assert 'dcbot_log_messages_total{level="error"} 1' in body
    assert "dcbot_last_error_timestamp_seconds " in body
    assert "dcbot_start_time_seconds " in body


async def test_port_conflict_does_not_raise(tmp_path):
    bot = make_bot(tmp_path)
    first = MetricsServer(bot, "127.0.0.1", 0)
    assert await first.start()
    port = first.runner.addresses[0][1]
    try:
        assert not await MetricsServer(bot, "127.0.0.1", port).start()
    finally:
        await first.stop()
