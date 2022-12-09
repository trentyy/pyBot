import discord
from discord.ext import commands
import json
import os, sys, socket
import datetime as dt

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

bot = commands.Bot(command_prefix= '>>')
DEBUG = False
if len(sys.argv) > 1:
    if sys.argv[1] == "DEBUG":
        DEBUG = True
        print("debug mode is on")

@bot.event
async def on_ready():
    print(f"Bot host by `{socket.gethostname()}` is online")
    channel = bot.get_channel(int(jdata['chennel_bot-history']))
    await channel.send(f"`{socket.gethostname()}` As your service!")

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

if DEBUG==False:
    for filename in os.listdir('./cmds'):
        if filename.endswith('.py'):
            print(f'cmds.{filename[:-3]}')
            bot.load_extension(f'cmds.{filename[:-3]}')
    for filename in os.listdir('./msg_listener'):
        if filename.endswith('.py'):
            bot.load_extension(f'msg_listener.{filename[:-3]}')

else:
    for filename in os.listdir('./dev_cmds'):
        if filename.endswith('.py'):
            print(f'dev_cmds.{filename[:-3]}')
            bot.load_extension(f'dev_cmds.{filename[:-3]}')

#@commands.Cog.listener()
#async def on_reaction_add(self, reaction, user):
#    print(reaction)
#    print(user)
if __name__ == '__main__':
    bot.run(jdata['TOKEN'])
