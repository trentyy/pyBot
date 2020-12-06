import discord
from discord.ext import commands
import json
import os, socket

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

bot = commands.Bot(command_prefix= '>>')

@bot.event
async def on_ready():
    print(f"Bot host by `{socket.gethostname()}` is online")
    channel = bot.get_channel(int(jdata['chennel_bot-playground']))
    await channel.send("As your service!")

@bot.command()
async def load(ctx, extension):
    bot.unload_extension(f'cmds.{extension}')
    await ctx.send(f'Loaded extension: {extension}.')

@bot.command()
async def unload(ctx, extension):
    bot.unload_extension(f'cmds.{extension}')
    await ctx.send(f'Unloaded extension: {extension}.')

@bot.command()
async def reload(ctx, extension):
    bot.reload_extension(f'cmds.{extension}')
    await ctx.send(f'Reloaded extension: {extension}.')

for filename in os.listdir('./cmds'):
    if filename.endswith('.py'):
        bot.load_extension(f'cmds.{filename[:-3]}')
#@commands.Cog.listener()
#async def on_reaction_add(self, reaction, user):
#    print(reaction)
#    print(user)
if __name__ == '__main__':
    bot.run(jdata['TOKEN'])

