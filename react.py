import discord
from discord.ext import commands
from core.classes import Cog_Extension

class React(Cog_Extension)

def setup(bot):
    bot.add_cog(Main(bot))