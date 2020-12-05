import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, asyncio, datetime, requests

with open('twitter_api.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)
class Task(Cog_Extension):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.url = jdata['url']
        self.payload = jdata['payload']
        self.headers = jdata['headers']
        #self.follower = '<@658596195995877376>'    #<@user_id>
        self.follow_role = '<@&782875710545068062>' #<@&role_id>

        async def interval():
            await self.bot.wait_until_ready()
            self.channel = self.bot.get_channel(782232918512107542)
            while not self.bot.is_closed():
                await self.channel.send('Bot is alive :wink:')
                await asyncio.sleep(60*10) # unit: second
        self.bg_task = self.bot.loop.create_task(interval())
    @commands.command()
    async def set_channel(self, ctx, ch: int):
        self.channel = self.bot.get_channel(ch)
        await ctx.send(f'Set Channel: {self.channel.mention}')
    @commands.command()
    async def get_tweets(self, ctx):
        await ctx.channel.send(self.follower)
def setup(bot):
    bot.add_cog(Task(bot))