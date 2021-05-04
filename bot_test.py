import discord
from discord.ext import commands
import json
import os, sys, socket
import datetime as dt

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

bot = commands.Bot(command_prefix= '>|')

"""
if len(sys.argv) == 1:
    print("please add folder names as argument")
    exit
else:
    test_folder = (sys.argv[1])
"""

@bot.event
async def on_ready():
    print(f"Test Bot host by `{socket.gethostname()}` is online")
    channel = bot.get_channel(int(jdata['chennel_bot-playground']))
    await channel.send(f"Test Bot `{socket.gethostname()}` As your service!")

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

extension_name = 'cmds.tweet_forwarder_sql'
bot.load_extension(extension_name)
bot.load_extension('cmds.yt_forwarder')
"""
for filename in os.listdir(f'./cmds'):
    if filename.endswith('.py'):
        extension_name = f'cmds.{filename[:-3]}'
        print(extension_name)
        bot.load_extension(extension_name)
"""
if __name__ == '__main__':
    bot.run(jdata['TOKEN'])
