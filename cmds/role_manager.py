import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, datetime
import pymysql
from loguru import logger

trace = logger.add("./log/role_manager.log", rotation="monthly", encoding="utf-8", enqueue=True, retention="1 year")
with open('setting.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

class  RoleManager(Cog_Extension):
    def __init__(self, bot):
        self.booting = True
        self.bot = bot
        self.connection = pymysql.connect(
            host = jdata["mysql"]["host"],
            database = jdata["mysql"]["database"],
            user = jdata["mysql"]["user"],
            password = jdata["mysql"]["password"]
        )
    def update_connection(self):
        try:
            self.connection.ping()
        except:
            logger.info("db reconnect")
            self.connection = pymysql.connect(
                host = jdata["mysql"]["host"],
                database = jdata["mysql"]["database"],
                user = jdata["mysql"]["user"],
                password = jdata["mysql"]["password"]
            )

            
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, data):
        self.update_connection()
        try:
            with self.connection.cursor() as cursor:
                sql = (
                    "SELECT `role_id` FROM `reaction_role`"
                    f"WHERE `guild_id`={data.guild_id} AND `channel_id`={data.channel_id} "
                    f"AND `message_id`={data.message_id} AND `emoji`='{data.emoji}'"
                    )
                if (cursor.execute(sql)!=0):
                    logger.info(sql)
                    print(sql)
                    # 新增反應貼圖獲取身分組-接收訊息身分組
                    # 通知身分組
                    role_id = cursor.fetchall()[0][0]
                    guild = self.bot.get_guild(data.guild_id)
                    role = guild.get_role(role_id)
                    
                    logger.info(f"Add {data.member.mention} to role: {role}")
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
                if (cursor.execute(sql)!=0):
                    logger.info(sql)
                    print(sql)
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
                            logger.info(operation)
                            print(operation)
                            await data.member.send(operation)
                            await bot_channel.send(f"Change {data.member.mention} to role: {role}")
                        else:
                            role = guild.get_role(item[0])
                            await message.remove_reaction(item[1], data.member)
                            await data.member.remove_roles(role)
        except Exception as e:
            logger.exception(e)
            await bot_channel.send(e)
                
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, data):
        self.update_connection()
        try:
            with self.connection.cursor() as cursor:
            
                sql = (
                    "SELECT `role_id` FROM `reaction_role`"
                    f"WHERE `guild_id`={data.guild_id} AND `channel_id`={data.channel_id} "
                    f"AND `message_id`={data.message_id} AND `emoji`='{data.emoji}'"
                    )
                if (cursor.execute(sql)!=0):
                    logger.info(sql)
                    print(sql)
                    role_id = cursor.fetchall()[0][0]
                    guild = self.bot.get_guild(data.guild_id)
                    role = guild.get_role(role_id)
                    member = await guild.fetch_member(data.user_id)
                    
                    logger.info(f"Remove {member.mention} from role: {role}")
                    print(f"Remove {member.mention} from role: {role}")
                    bot_channel = guild.get_channel(jdata["chennel_bot-history"]) # "機器人操作履歷頻道"
                    await member.remove_roles(role)
                    await member.send(f"Remove {member.mention} from {guild}'s role: {role}")
                    await bot_channel.send(f"Remove {member.mention} from role: {role}")
        except Exception as e:
            logger.exception(e)
            await bot_channel.send(e)


def setup(bot):
    bot.add_cog(RoleManager(bot))
