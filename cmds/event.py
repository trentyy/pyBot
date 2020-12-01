import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, datetime

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

class  Event(Cog_Extension):
    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.content == 'Server-BOT':
            await  msg.channel.send('As your service!')
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, data):
        # 新增反應貼圖獲取身分組
        if data.message_id == 782810110237868074:
            guild = self.bot.get_guild(data.guild_id)
            flag = False
            if str(data.emoji) == '💫' :           # 華月
                role = guild.get_role(782624351676923945)
                flag = True
            elif str(data.emoji) == '🍽️':          # 夢咲
                role = guild.get_role(782623972344463412)
                flag = True
            elif str(data.emoji) == '🍬':         # 甘ノ星
                role = guild.get_role(782624609882079250)
                flag = True
            elif str(data.emoji) == '🦇':         # 朔桜
                role = guild.get_role(782624818280661012)
                flag = True
            elif str(data.emoji) == '🎐':          # 海月
                role = guild.get_role(782625222968344597)
                flag = True
            if (flag):
                print(f"add {data.member} to role: {role}")
                channel = self.bot.get_channel(783033279166939156) # "機器人操作履歷頻道"
                await data.member.add_roles(role)
                await channel.send(f"Add {data.member} to role: {role}")
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, data):
        #print(guild, role)
        # 移除反應貼圖獲取身分組
        if data.message_id == 782810110237868074:
            guild = self.bot.get_guild(data.guild_id)
            user = await guild.fetch_member(data.user_id)
            flag = False
            if str(data.emoji) == '💫':           # 華月
                role = guild.get_role(782624351676923945)
                flag = True
            elif str(data.emoji) == '🍽️':          # 夢咲
                role = guild.get_role(782623972344463412)
                flag = True
            elif str(data.emoji) == '🍬':         # 甘ノ星
                role = guild.get_role(782624609882079250)
                flag = True
            elif str(data.emoji) == '🦇':         # 朔桜
                role = guild.get_role(782624818280661012)
                flag = True
            elif str(data.emoji) == '🎐':          # 海月
                role = guild.get_role(782625222968344597)
                flag = True
            if (flag): 
                print(f"remove {user} from role: {role}")
                channel = self.bot.get_channel(783033279166939156) # "機器人操作履歷頻道"
                await user.remove_roles(role)
                await channel.send(f"Remove {user} from role: {role}")

def setup(bot):
    bot.add_cog(Event(bot))