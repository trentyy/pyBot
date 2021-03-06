import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, asyncio, requests, urllib3, socket
import datetime as dt


SLEEP_TIME = 60*60
class TweetForwarder(Cog_Extension):
    def __init__(self, bot):
        self.bot = bot

        async def interval():
            await self.bot.wait_until_ready()
            bot_pg = bot.get_channel(782232918512107542)
            while not self.bot.is_closed():
                curTime = dt.datetime.now().replace(microsecond=0).isoformat(" ")
                msg = f'Bot host by `{socket.gethostname()}` is alive. {curTime}'
                await bot_pg.send(msg)
                await asyncio.sleep(SLEEP_TIME)

        self.bg_task = self.bot.loop.create_task(interval())


def setup(bot):
    bot.add_cog(TweetForwarder(bot))
