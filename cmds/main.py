import discord
import datetime as dt
from discord.ext import commands
from core.classes import Cog_Extension


class Main(Cog_Extension):
    @commands.command()
    async def ping(self, ctx):
        await ctx.send(f'{round(self.bot.latency*1000)} (ms)')
    @commands.command()
    async def botsay(self, ctx, target_ch_id, *, msg):
        print(ctx.author.roles)
        target_ch = self.bot.get_channel(int(target_ch_id))
        if (ctx.guild.get_role(785503910818218025) in ctx.author.roles):
            await ctx.message.delete()
            await target_ch.send(msg)
        else:
            await ctx.send("你誰啊?")
    @commands.command()
    async def curtime(self, ctx):
        await ctx.send(dt.datetime.now())
    @commands.command()
    async def purge(self, ctx, num:int=1):
        if ctx.author.permissions_in(ctx.channel).manage_messages:
            await ctx.channel.purge(limit=num+1)
        else:
            msg =   f"您沒有在 {ctx.channel} `管理訊息`的權限\n"+\
                    f"You don't have the permission to `Manage Message` at {ctx.channel}"
            await ctx.channel.send(msg)
    @commands.command()
    async def purgefromDT(self, ctx, 
                        Y: int= dt.datetime.now().year,
                        M: int= dt.datetime.now().month, 
                        D: int= dt.datetime.now().day,
                        *hms: int):
        if ctx.author.permissions_in(ctx.channel).manage_messages:
            time = dt.datetime(Y, M, D, *hms)
            # from utc
            tz_from = dt.timezone(dt.timedelta(0))
            # to utc+8
            tz_to = dt.timezone(dt.timedelta(hours=8))
            # from local time
            time_loc = time.replace(tzinfo=tz_to)
            # to utc time, tzinfo　need to remove
            time_utc = time_loc.astimezone(tz_from).replace(tzinfo=None)

            await ctx.channel.purge(after=time_utc)
        else:
            msg =   f"您沒有在 {ctx.channel} `管理訊息`的權限\n"+\
                    f"You don't have the permission to `Manage Message` at {ctx.channel}"
            await ctx.channel.send(msg)

def setup(bot):
    bot.add_cog(Main(bot))