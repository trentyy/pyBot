import discord
from discord.ext import commands
import json
import os, sys, socket, asyncio
import datetime as dt

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

intents = discord.Intents.all()

bot = commands.Bot(command_prefix= '>|', intents=intents)
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

    slash = await bot.tree.sync()
    print(f"目前登入身份 --> {bot.user}")
    print(f"載入 {len(slash)} 個斜線指令")

# Core的功能僅開放guild_permissions.administrator使用
@bot.command()
async def load(ctx, extension):
    if ctx.author.guild_permissions.administrator:
        bot.unload_extension(f'cogs.{extension}')
        await ctx.send(f'Loaded extension: {extension}.')
    else:
        msg = "You aren't the `Administrator`"
        await ctx.send(msg)

@bot.command()
async def unload(ctx, extension):
    if ctx.author.guild_permissions.administrator:
        bot.unload_extension(f'cogs.{extension}')
        await ctx.send(f'Unloaded extension: {extension}.')
    else:
        msg = "You aren't the `Administrator`"
        await ctx.send(msg)
@bot.command()
async def reload(ctx, extension):
    if ctx.author.guild_permissions.administrator:
        bot.reload_extension(f'cogs.{extension}')
        await ctx.send(f'Reloaded extension: {extension}.')
    else:
        msg = "You aren't the `Administrator`"
        await ctx.send(msg)

async def load_extensions():
    if DEBUG==False:
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                print(f'cogs.{filename[:-3]}')
                await bot.load_extension(f'cogs.{filename[:-3]}')

    else:
        for filename in os.listdir('./dev_cogs'):
            if filename.endswith('.py'):
                print(f'dev_cogs.{filename[:-3]}')
                await bot.load_extension(f'dev_cogs.{filename[:-3]}')
async def main():
    async with bot:
        await load_extensions()
        await bot.start(jdata['TOKEN'])
#@commands.Cog.listener()
#async def on_reaction_add(self, reaction, user):
#    print(reaction)
#    print(user)
if __name__ == '__main__':
    asyncio.run(main())
