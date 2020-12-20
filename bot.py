import discord
from discord.ext import commands
import json
import os, socket
import datetime as dt

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

bot = commands.Bot(command_prefix= '>>')

@bot.event
async def on_ready():
    print(f"Bot host by `{socket.gethostname()}` is online")
    channel = bot.get_channel(int(jdata['chennel_bot-playground']))
    await channel.send("As your service!")


# Core的功能僅開放guild_permissions.administrator使用
@bot.command()
async def load(ctx, extension):
    if ctx.author.guild_permissions.administrator:
        bot.unload_extension(f'cmds.{extension}')
        await ctx.send(f'Loaded extension: {extension}.')
    else:
        msg = "You aren't the `Administrator`"
        await ctx.send(msg)

@bot.command()
async def unload(ctx, extension):
    if ctx.author.guild_permissions.administrator:
        bot.unload_extension(f'cmds.{extension}')
        await ctx.send(f'Unloaded extension: {extension}.')
    else:
        msg = "You aren't the `Administrator`"
        await ctx.send(msg)
@bot.command()
async def reload(ctx, extension):
    if ctx.author.guild_permissions.administrator:
        bot.reload_extension(f'cmds.{extension}')
        await ctx.send(f'Reloaded extension: {extension}.')
    else:
        msg = "You aren't the `Administrator`"
        await ctx.send(msg)

for filename in os.listdir('./cmds'):
    if filename.endswith('.py'):
        bot.load_extension(f'cmds.{filename[:-3]}')
for filename in os.listdir('./msg_listener'):
    if filename.endswith('.py'):
        bot.load_extension(f'msg_listener.{filename[:-3]}')
#@commands.Cog.listener()
#async def on_reaction_add(self, reaction, user):
#    print(reaction)
#    print(user)
if __name__ == '__main__':
    bot.run(jdata['TOKEN'])
