"""用假的 Discord 物件模擬反應 / 按鈕事件，驗證身分組發放邏輯。"""
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from dc_manager.bot import DcManager
from dc_manager.cogs.button_roles import CUSTOM_ID_PREFIX, ButtonRoles
from dc_manager.cogs.reaction_roles import ReactionRoles
from dc_manager.config import Config
from dc_manager.db import KIND_BUTTON, KIND_REACTION, MODE_UNIQUE, MODE_VERIFY

GUILD_ID, CHANNEL_ID, MESSAGE_ID, USER_ID = 1, 2, 3, 4


class FakeRole:
    def __init__(self, role_id):
        self.id = role_id
        self.name = f"role{role_id}"
        self.mention = f"<@&{role_id}>"


class FakeMember:
    bot = False

    def __init__(self, guild):
        self.id = USER_ID
        self.guild = guild
        self.mention = f"<@{USER_ID}>"
        self.roles: list[FakeRole] = []

    async def add_roles(self, *roles, reason=None):
        self.roles += [r for r in roles if r not in self.roles]

    async def remove_roles(self, *roles, reason=None):
        self.roles = [r for r in self.roles if r not in roles]

    async def send(self, *a, **k):
        pass


class FakeChannel(discord.abc.Messageable):
    def __init__(self):
        self.removed_reactions: list[str] = []

    async def _get_channel(self):
        return self

    def get_partial_message(self, message_id):
        channel = self

        class Partial:
            async def remove_reaction(self, emoji, member):
                channel.removed_reactions.append(str(emoji))

        return Partial()


class FakeGuild:
    id = GUILD_ID

    def __init__(self):
        self.roles = {i: FakeRole(i) for i in (100, 101, 102)}
        self.channel = FakeChannel()
        self.member = FakeMember(self)

    def get_role(self, role_id):
        return self.roles.get(role_id)

    def get_channel_or_thread(self, channel_id):
        return self.channel if channel_id == CHANNEL_ID else None

    def get_member(self, user_id):
        return self.member

    async def fetch_member(self, user_id):
        return self.member


@pytest.fixture
async def env():
    cfg = Config(token="x", db_path=":memory:", log_dir=Path("."), log_level="INFO", members_intent=True, dev_guild_id=None)
    bot = DcManager(cfg)
    await bot.db.connect()
    guild = FakeGuild()
    bot.get_guild = lambda gid: guild if gid == GUILD_ID else None  # type: ignore[method-assign]
    yield bot, guild
    await bot.db.close()


async def setup_reaction(bot, mode="normal"):
    await bot.db.add_message(MESSAGE_ID, GUILD_ID, CHANNEL_ID, KIND_REACTION, mode)
    await bot.db.add_binding(MESSAGE_ID, 100, emoji_key="🍎", emoji="🍎")
    await bot.db.add_binding(MESSAGE_ID, 101, emoji_key="🍌", emoji="🍌")
    bot.tracked_messages.add(MESSAGE_ID)


def payload(guild, emoji, *, with_member=True):
    return SimpleNamespace(
        guild_id=GUILD_ID,
        channel_id=CHANNEL_ID,
        message_id=MESSAGE_ID,
        user_id=USER_ID,
        emoji=discord.PartialEmoji(name=emoji),
        member=guild.member if with_member else None,
    )


def role_ids(member):
    return sorted(r.id for r in member.roles)


async def test_reaction_normal_add_and_remove(env):
    bot, guild = env
    await setup_reaction(bot)
    cog = ReactionRoles(bot)
    await cog.on_raw_reaction_add(payload(guild, "🍎"))
    await cog.on_raw_reaction_add(payload(guild, "🍌"))
    assert role_ids(guild.member) == [100, 101]
    await cog.on_raw_reaction_remove(payload(guild, "🍎", with_member=False))
    assert role_ids(guild.member) == [101]


async def test_reaction_unknown_emoji_and_untracked_message_ignored(env):
    bot, guild = env
    await setup_reaction(bot)
    cog = ReactionRoles(bot)
    await cog.on_raw_reaction_add(payload(guild, "🍇"))
    bot.tracked_messages.clear()
    await cog.on_raw_reaction_add(payload(guild, "🍎"))
    assert guild.member.roles == []


async def test_reaction_unique_swaps_role_and_reaction(env):
    bot, guild = env
    await setup_reaction(bot, MODE_UNIQUE)
    cog = ReactionRoles(bot)
    await cog.on_raw_reaction_add(payload(guild, "🍎"))
    await cog.on_raw_reaction_add(payload(guild, "🍌"))
    assert role_ids(guild.member) == [101]
    assert guild.channel.removed_reactions == ["🍎"]


async def test_reaction_verify_keeps_role(env):
    bot, guild = env
    await setup_reaction(bot, MODE_VERIFY)
    cog = ReactionRoles(bot)
    await cog.on_raw_reaction_add(payload(guild, "🍎"))
    await cog.on_raw_reaction_remove(payload(guild, "🍎", with_member=False))
    assert role_ids(guild.member) == [100]


class FakeResponse:
    def __init__(self):
        self.done = False

    def is_done(self):
        return self.done

    async def defer(self, **k):
        self.done = True

    async def send_message(self, text=None, **k):
        self.done = True


class FakeInteraction:
    type = discord.InteractionType.component

    def __init__(self, guild, role_id):
        self.guild = guild
        self.user = guild.member
        self.data = {"custom_id": f"{CUSTOM_ID_PREFIX}{role_id}"}
        self.message = SimpleNamespace(id=MESSAGE_ID)
        self.response = FakeResponse()
        self.sent: list[str] = []
        outer = self

        class Followup:
            async def send(self, text=None, **k):
                outer.sent.append(text)

        self.followup = Followup()


async def click(cog, guild, role_id, monkeypatch):
    monkeypatch.setattr(discord, "Member", FakeMember)  # 讓 isinstance 檢查通過
    inter = FakeInteraction(guild, role_id)
    await cog.on_interaction(inter)
    return inter


async def setup_panel(bot, mode="normal"):
    await bot.db.add_message(MESSAGE_ID, GUILD_ID, CHANNEL_ID, KIND_BUTTON, mode)
    for rid in (100, 101):
        await bot.db.add_binding(MESSAGE_ID, rid, label=f"r{rid}")


async def test_button_toggle(env, monkeypatch):
    bot, guild = env
    await setup_panel(bot)
    cog = ButtonRoles(bot)
    await click(cog, guild, 100, monkeypatch)
    assert role_ids(guild.member) == [100]
    await click(cog, guild, 100, monkeypatch)
    assert role_ids(guild.member) == []


async def test_button_unique(env, monkeypatch):
    bot, guild = env
    await setup_panel(bot, MODE_UNIQUE)
    cog = ButtonRoles(bot)
    await click(cog, guild, 100, monkeypatch)
    inter = await click(cog, guild, 101, monkeypatch)
    assert role_ids(guild.member) == [101]
    assert "移除" in inter.sent[-1]


async def test_button_verify_does_not_remove(env, monkeypatch):
    bot, guild = env
    await setup_panel(bot, MODE_VERIFY)
    cog = ButtonRoles(bot)
    await click(cog, guild, 100, monkeypatch)
    await click(cog, guild, 100, monkeypatch)
    assert role_ids(guild.member) == [100]


async def test_button_for_unbound_role_is_rejected(env, monkeypatch):
    bot, guild = env
    await setup_panel(bot)
    cog = ButtonRoles(bot)
    # 有人偽造 custom_id 想拿面板上沒有的身分組
    inter = await click(cog, guild, 102, monkeypatch)
    assert guild.member.roles == []
    assert inter.response.done
