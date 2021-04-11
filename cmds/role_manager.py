import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, datetime

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

class  RoleManager(Cog_Extension):
    def __init__(self, bot):
        self.booting = True
        self.bot = bot
        self.roles = {
            '🐑': (785051702176645130),
            '💫': (782624351676923945),
            '🍽️': (782623972344463412),
            '🍬': (782624609882079250),
            '🦇': (782624818280661012),
            '🎐': (782625222968344597),
            '💗': (817743717217206273),
            '🌺': (817743846514884649),
            '🐭': (830013401413058580),
            '👑': (830013399181688892)
        }
    
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, data):
        if self.booting:
            self.booting = False
            self.guild = self.bot.get_guild(782232756238549032)
            
            self.follow_roles = {}
            for key, value in self.roles.items():
                self.follow_roles[key] = self.guild.get_role(value)
            print(f"Cog role_manager load roles: {self.follow_roles}")
        if data.channel_id != 782790264795299870:
            return
        

        # 新增反應貼圖獲取身分組-接收訊息身分組
        if data.message_id == 782810110237868074:

            # 通知身分組
            follow_roles = self.follow_roles

            if str(data.emoji) in follow_roles.keys():
                role = follow_roles[str(data.emoji)]

                print(f"add {data.member.mention} to role: {role}")
                channel = self.bot.get_channel(783033279166939156) # "機器人操作履歷頻道"
                await data.member.add_roles(role)
                await data.member.send(f"Add {data.member.mention} to {self.guild}'s role: {role}")
                await channel.send(f"Add {data.member.mention} to role: {role}")
        # 新增反應貼圖獲取身分組-選顏色身分組
        if data.message_id == 790101308140027934:
            guild = self.guild
            channel = self.guild.get_channel(data.channel_id)
            bot_ch = self.bot.get_channel(783033279166939156) # "機器人操作履歷頻道"
            message = await channel.fetch_message(data.message_id)
            emoji = str(data.emoji)
            # 顏色身分組
            color_roles = {'💫': guild.get_role(790095058133188670),
                      '🍽️': guild.get_role(790095058971000832),
                      '🍬': guild.get_role(790095064835293225),
                      '🦇': guild.get_role(790095067716648961),
                      '🎐': guild.get_role(790095069997826049),
                      '💗': guild.get_role(820013990217252895),
                      '🌺': guild.get_role(820013965827637319)}

            if emoji in color_roles.keys():
                role = color_roles[emoji]

                # remove other reaction
                for item in color_roles.keys():
                    if emoji == item:
                        continue
                    else:
                        await message.remove_reaction(item, data.member)
                await data.member.remove_roles(*color_roles.values())

                operation = f"Change {self.guild}'s member: {data.member.mention} to {role}'s color"
                print(operation)
                await data.member.add_roles(role)
                await data.member.send(operation)
                await bot_ch.send(operation)
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, data):
        if self.booting:
            self.booting = False
            self.guild = self.bot.get_guild(782232756238549032)
            
            self.follow_roles = {}
            for key, value in self.roles.items():
                self.follow_roles[key] = self.guild.get_role(value)
            print(f"Cog role_manager load roles: {self.follow_roles}")
        #print(guild, role)
        # 移除反應貼圖移除身分組
        if data.message_id == 782810110237868074:
            guild = self.guild
            follow_roles = self.follow_roles
            user = await guild.fetch_member(data.user_id)
            emoji = str(data.emoji)

            if emoji in follow_roles.keys():           # 公式羊
                role = follow_roles[emoji]
                
                print(f"remove {user.mention} from role: {role}")
                channel = self.bot.get_channel(783033279166939156) # "機器人操作履歷頻道"
                await user.remove_roles(role)
                await user.send(f"Remove {user.mention} from {guild}'s role: {role}")
                await channel.send(f"Remove {user.mention} from role: {role}")

def setup(bot):
    bot.add_cog(RoleManager(bot))
