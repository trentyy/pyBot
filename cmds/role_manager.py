import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, datetime

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

class  RoleMamager(Cog_Extension):
    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.content == 'Server-BOT':
            await  msg.channel.send('As your service!')
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, data):
        if data.channel_id != 782790264795299870:
            return
        

        # 新增反應貼圖獲取身分組-接收訊息身分組
        if data.message_id == 782810110237868074:
            guild = self.bot.get_guild(data.guild_id)

            # 通知身分組
            target = {  '🐑': guild.get_role(785051702176645130),
                        '💫': guild.get_role(782624351676923945),
                        '🍽️': guild.get_role(782623972344463412),
                        '🍬': guild.get_role(782624609882079250),
                        '🦇': guild.get_role(782624818280661012),
                        '🎐': guild.get_role(782625222968344597),
                        '💎': guild.get_role(817743717217206273),
                        '🌼': guild.get_role(817743846514884649)}

            if str(data.emoji) in target.keys():
                role = target[str(data.emoji)]

                print(f"add {data.member} to role: {role}")
                channel = self.bot.get_channel(783033279166939156) # "機器人操作履歷頻道"
                await data.member.add_roles(role)
                await data.member.send(f"Add {data.member} to {guild}'s role: {role}")
                await channel.send(f"Add {data.member} to role: {role}")
        # 新增反應貼圖獲取身分組-選顏色身分組
        if data.message_id == 790101308140027934:
            guild = self.bot.get_guild(data.guild_id)
            channel = guild.get_channel(data.channel_id)
            bot_ch = self.bot.get_channel(783033279166939156) # "機器人操作履歷頻道"
            message = await channel.fetch_message(data.message_id)

            # 顏色身分組
            target = {'💫': guild.get_role(790095058133188670),
                      '🍽️': guild.get_role(790095058971000832),
                      '🍬': guild.get_role(790095064835293225),
                      '🦇': guild.get_role(790095067716648961),
                      '🎐': guild.get_role(790095069997826049),
                      '💎': guild.get_role(820013990217252895),
                      '🌼': guild.get_role(820013965827637319)}

            if str(data.emoji) in target.keys():
                role = target[str(data.emoji)]

                # remove other reaction
                for item in target.keys():
                    if str(data.emoji) == item:
                        continue
                    else:
                        await message.remove_reaction(item, data.member)
                await data.member.remove_roles(*target.values())

                operation = f"Change {guild}'s member: {data.member} to {role}'s color"
                print(operation)
                await data.member.add_roles(role)
                await data.member.send(operation)
                await bot_ch.send(operation)
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, data):
        #print(guild, role)
        # 移除反應貼圖獲取身分組
        if data.message_id == 782810110237868074:
            guild = self.bot.get_guild(data.guild_id)
            user = await guild.fetch_member(data.user_id)
            flag = False
            if str(data.emoji) == '🐑':           # 公式羊
                role = guild.get_role(785051702176645130)
                flag = True
            # generation-1
            elif str(data.emoji) == '💫':           # 華月
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
            # generation-2
            elif str(data.emoji) == '💎':         # 恋乃夜
                role = guild.get_role(817743717217206273)
                flag = True
            elif str(data.emoji) == '🌼':          # 花雲
                role = guild.get_role(817743846514884649)
                flag = True
            
            if (flag): 
                print(f"remove {user} from role: {role}")
                channel = self.bot.get_channel(783033279166939156) # "機器人操作履歷頻道"
                await user.remove_roles(role)
                await user.send(f"Remove {user} from {guild}'s role: {role}")
                await channel.send(f"Remove {user} from role: {role}")

def setup(bot):
    bot.add_cog(RoleMamager(bot))