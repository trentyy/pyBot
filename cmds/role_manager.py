import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, datetime
import pymysql

with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

class  RoleManager(Cog_Extension):
    def __init__(self, bot):
        self.booting = True
        self.bot = bot
        self.db = pymysql.connect(
            host = jdata["mysql"]["host"],
            database = jdata["mysql"]["database"],
            user = jdata["mysql"]["user"],
            password = jdata["mysql"]["password"]
        )
            
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, data):
        cursor = self.db.cursor()
        sql = (
            "SELECT `role_id` FROM `reaction_role`"
            f"WHERE `guild_id`={data.guild_id} AND `channel_id`={data.channel_id} "
            f"AND `message_id`={data.message_id} AND `emoji`='{data.emoji}'"
            )
        print(sql)
        if (cursor.execute(sql)!=0):
            print("====data====")
            print(data)
            # 新增反應貼圖獲取身分組-接收訊息身分組
            # 通知身分組
            role_id = cursor.fetchall()[0][0]
            guild = self.bot.get_guild(data.guild_id)
            role = guild.get_role(role_id)
            
            print(f"Add {data.member.mention} to role: {role}")
            channel = guild.get_channel(jdata["chennel_bot-history"]) # "機器人操作履歷頻道"
            await data.member.add_roles(role)
            await data.member.send(f"Add {data.member.mention} to {guild}'s role: {role}")
            await channel.send(f"Add {data.member.mention} to role: {role}")
        sql = (
            "SELECT `role_id`, `emoji` FROM `reaction_role_exclusive`"
            f"WHERE `guild_id`={data.guild_id} AND `channel_id`={data.channel_id} "
            f"AND `message_id`={data.message_id}"
            )
        print(sql)
        if (cursor.execute(sql)!=0):
            # 新增反應貼圖獲取身分組-選顏色身分組
            result = cursor.fetchall()
            guild = self.bot.get_guild(data.guild_id)
            channel = guild.get_channel(data.channel_id)
            bot_channel = guild.get_channel(jdata["chennel_bot-history"])
            message = await channel.fetch_message(data.message_id)
            emoji = str(data.emoji)

            for item in result:
                if emoji == item[1]:
                    role = guild.get_role(item[0])
                    await data.member.add_roles(role)
                    operation = f"Change {guild}'s member: {data.member.mention} to {role}'s color"
                    print(operation)
                    await data.member.send(operation)
                    await bot_channel.send(f"Change {data.member.mention} to role: {role}")
                else:
                    role = guild.get_role(item[0])
                    await message.remove_reaction(item[1], data.member)
                    await data.member.remove_roles(role)
                
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, data):
        cursor = self.db.cursor()
        sql = (
            "SELECT `role_id` FROM `reaction_role`"
            f"WHERE `guild_id`={data.guild_id} AND `channel_id`={data.channel_id} "
            f"AND `message_id`={data.message_id} AND `emoji`='{data.emoji}'"
            )
        if (cursor.execute(sql)!=0):
            role_id = cursor.fetchall()[0][0]
            guild = self.bot.get_guild(data.guild_id)
            role = guild.get_role(role_id)
            member = await guild.fetch_member(data.user_id)
            
            
            print(f"Remove {member.mention} from role: {role}")
            bot_channel = guild.get_channel(jdata["chennel_bot-history"]) # "機器人操作履歷頻道"
            await member.remove_roles(role)
            await member.send(f"Remove {member.mention} from {guild}'s role: {role}")
            await bot_channel.send(f"Remove {member.mention} from role: {role}")


def setup(bot):
    bot.add_cog(RoleManager(bot))
