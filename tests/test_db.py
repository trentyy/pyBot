import sqlite3

import pytest

from dc_manager.db import KIND_BUTTON, KIND_REACTION, MODE_UNIQUE, SCHEMA_VERSION, Database


@pytest.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


async def test_migration_sets_version(db):
    async with db.conn.execute("PRAGMA user_version") as cur:
        assert (await cur.fetchone())[0] == SCHEMA_VERSION
    # 重跑 migration 不應出錯
    await db._migrate()


async def test_settings_defaults_and_update(db):
    s = await db.get_settings(1)
    assert s.log_channel_id is None and s.dm_notify is False
    await db.set_log_channel(1, 99)
    await db.set_dm_notify(1, True)
    s = await db.get_settings(1)
    assert s.log_channel_id == 99 and s.dm_notify is True
    await db.set_log_channel(1, None)
    assert (await db.get_settings(1)).log_channel_id is None
    assert (await db.get_settings(1)).dm_notify is True


async def test_bindings_lifecycle(db):
    await db.add_message(10, guild_id=1, channel_id=2, kind=KIND_REACTION)
    await db.add_binding(10, 100, emoji_key="😀", emoji="😀")
    await db.add_binding(10, 101, emoji_key="123", emoji="<:x:123>")
    assert [b.role_id for b in await db.get_bindings(10)] == [100, 101]
    assert (await db.get_binding_by_emoji(10, "123")).role_id == 101

    # 同一表情不能綁兩個身分組
    with pytest.raises(sqlite3.IntegrityError):
        await db.add_binding(10, 102, emoji_key="😀", emoji="😀")

    # 同身分組再設一次 = 更新表情
    await db.add_binding(10, 100, emoji_key="🎉", emoji="🎉")
    assert (await db.get_binding_by_role(10, 100)).emoji == "🎉"
    assert await db.get_binding_by_emoji(10, "😀") is None

    assert await db.remove_binding(10, 100)
    assert not await db.remove_binding(10, 100)

    await db.set_mode(10, MODE_UNIQUE)
    assert (await db.get_message(10)).mode == MODE_UNIQUE


async def test_delete_message_cascades(db):
    await db.add_message(10, 1, 2, KIND_BUTTON)
    await db.add_binding(10, 100, label="A")
    assert await db.all_message_ids() == {10}
    await db.delete_messages([10])
    assert await db.get_bindings(10) == []
    assert await db.all_message_ids() == set()


async def test_guilds_are_isolated(db):
    await db.add_message(10, 1, 2, KIND_REACTION)
    await db.add_message(20, 2, 3, KIND_REACTION)
    await db.add_binding(10, 100, emoji_key="a", emoji="a")
    await db.add_binding(20, 100, emoji_key="a", emoji="a")
    await db.add_auto_role(1, 100)
    await db.add_auto_role(2, 100)

    # 刪除 guild 1 的身分組不能影響 guild 2
    assert await db.remove_role_everywhere(1, 100) == [10]
    assert await db.get_bindings(10) == []
    assert len(await db.get_bindings(20)) == 1
    assert await db.get_auto_roles(2) == [100]

    assert await db.delete_guild(2) == [20]
    assert await db.all_message_ids() == {10}
    assert await db.get_auto_roles(2) == []


async def test_auto_roles(db):
    assert await db.add_auto_role(1, 5)
    assert not await db.add_auto_role(1, 5)
    assert await db.get_auto_roles(1) == [5]
    assert await db.remove_auto_role(1, 5)
    assert await db.get_auto_roles(1) == []


async def test_kv(db):
    assert await db.get_kv("k") is None
    await db.set_kv("k", "v1")
    await db.set_kv("k", "v2")
    assert await db.get_kv("k") == "v2"
