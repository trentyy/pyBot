from types import SimpleNamespace

import discord
import pytest

from dc_manager.utils import UserError, emoji_key, parse_message_ref, role_assign_problem


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://discord.com/channels/1/2/3", (1, 2, 3)),
        ("https://ptb.discord.com/channels/1/2/3", (1, 2, 3)),
        ("https://canary.discordapp.com/channels/1/2/3", (1, 2, 3)),
        (" 123456 ", (None, None, 123456)),
    ],
)
def test_parse_message_ref(text, expected):
    assert parse_message_ref(text) == expected


def test_parse_message_ref_invalid():
    with pytest.raises(UserError):
        parse_message_ref("hello")


def test_emoji_key():
    assert emoji_key("<:blob:112233445566778899>") == "112233445566778899"
    assert emoji_key("<a:dance:998877665544332211>") == "998877665544332211"
    # 改名後的自訂表情仍對應同一個 key
    assert emoji_key(discord.PartialEmoji(name="renamed", id=112233445566778899)) == "112233445566778899"
    # 有無變體選擇符視為同一個
    assert emoji_key("❤️") == emoji_key("❤") == "❤"


class FakeRole:
    def __init__(self, position, *, default=False, managed=False, perms=0):
        self.position = position
        self.managed = managed
        self.permissions = discord.Permissions(perms)
        self._default = default
        self.mention = f"<@&{position}>"

    def is_default(self):
        return self._default

    def __ge__(self, other):
        return self.position >= other.position


def make_guild(bot_top=10, manage_roles=True, owner_id=1):
    me = SimpleNamespace(
        top_role=FakeRole(bot_top),
        guild_permissions=discord.Permissions(manage_roles=manage_roles),
    )
    return SimpleNamespace(me=me, owner_id=owner_id)


def role_in(guild, position, **kw):
    r = FakeRole(position, **kw)
    r.guild = guild
    return r


def test_role_ok():
    g = make_guild()
    assert role_assign_problem(role_in(g, 5)) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"position": 0, "default": True},
        {"position": 5, "managed": True},
        {"position": 10},  # 與 bot 同位置
        {"position": 15},  # 高於 bot
        {"position": 5, "perms": discord.Permissions(administrator=True).value},
        {"position": 5, "perms": discord.Permissions(ban_members=True).value},
    ],
)
def test_role_rejected(kwargs):
    g = make_guild()
    assert role_assign_problem(role_in(g, **kwargs)) is not None


def test_bot_without_manage_roles():
    g = make_guild(manage_roles=False)
    assert role_assign_problem(role_in(g, 5)) is not None


def test_actor_hierarchy():
    g = make_guild(owner_id=1)
    role = role_in(g, 5)
    low_mod = SimpleNamespace(id=2, top_role=FakeRole(5))
    high_mod = SimpleNamespace(id=3, top_role=FakeRole(6))
    owner = SimpleNamespace(id=1, top_role=FakeRole(1))
    assert role_assign_problem(role, low_mod) is not None
    assert role_assign_problem(role, high_mod) is None
    assert role_assign_problem(role, owner) is None
