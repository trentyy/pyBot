import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, datetime

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)
class  AddReaction(Cog_Extension):
    @commands.command()
    async def add_reaction(self, ctx, msg_id: int, *text):
        try:
            message = await ctx.fetch_message(msg_id)
        except Exception as e:
            print("Failed to fetch message, except: ", e)
            return
        for i in range(len(text)):
            try:
                await message.add_reaction(text[i])
            except discord.NotFound:
                continue
            except discord.InvalidArgument:
                continue

def setup(bot):
    bot.add_cog(AddReaction(bot))