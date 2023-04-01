import discord
from discord import app_commands
from discord.ext import commands
from core.classes import Cog_Extension
import json, datetime
from typing import Optional

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)
class  AddReaction(Cog_Extension):
    @app_commands.command(name="add_reaction", description="Add reaction")
    @app_commands.describe(msg_id="message id in this channel", text="reactions")
    async def add_reaction(self, interaction: discord.Interaction, msg_id: str, text: Optional[str] = None):
        try:
            message = await interaction.channel.fetch_message(int(msg_id))
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

async def setup(bot):
    await bot.add_cog(AddReaction(bot))