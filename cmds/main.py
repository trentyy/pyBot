import discord
import datetime as dt
from discord.ext import commands
from core.classes import Cog_Extension


class Main(Cog_Extension):
    @commands.command()
    async def ping(self, ctx):
        await ctx.send(f'{round(self.bot.latency*1000)} (ms)')
    @commands.command()
    async def botsay(self, ctx, *, msg):
        await ctx.message.delete()
        await ctx.send(msg)
    @commands.command()
    async def curtime(self, ctx):
        await ctx.send(dt.datetime.now())
    @commands.command()
    async def purge(self, ctx, num:int=1):
        await ctx.channel.purge(limit=num+1)
    @commands.command()
    async def purgetoday(self, ctx, 
                        h: int= dt.datetime.now().hour,
                        m: int= dt.datetime.now().minute, 
                        s: int= dt.datetime.now().second):
        today = dt.datetime.today() - dt.timedelta(hours=-24)
        # from utc
        tz_from = dt.timezone(dt.timedelta(0))
        # to utc+8
        tz_to = dt.timezone(dt.timedelta(hours=8))
        # from local time
        time_loc = today.replace(hour=h, minute=m, second=s, tzinfo=tz_to)
        # to utc time, tzinfo　need to remove
        time_utc = time.astimezone(tz_from).replace(tzinfo=None)

        #print("local time: ", time)
        #print("utc time: ", time_utc)
        await ctx.channel.purge(after=time_utc)

def setup(bot):
    bot.add_cog(Main(bot))