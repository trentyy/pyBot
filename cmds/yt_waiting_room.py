import discord
from discord.ext import commands
from core.classes import Cog_Extension
import os, json, asyncio, pymysql.cursors
from datetime import datetime, timedelta
import traceback, sys
import dateutil.parser

# my module
import ytTracker

with open('yt_fw_setting.json','r', encoding='utf8') as f:
    yt_fw_setting = json.load(f)
    f.close()
with open('yt_api.json', 'r', encoding='utf8') as f:
    yt_api_setting = json.load(f)
    f.close()
DEVELOPER_KEY = yt_api_setting["DEVELOPER_KEY"]
BOX_MEMBER_ID = yt_fw_setting["BOX_MEMBER_ID"]

with open('db_setting.json', 'r') as f:
    db_setting = json.load(f)
    f.close()
HOST = db_setting['host']
USER = db_setting['user']
PW = db_setting['password']
DB = db_setting['database']

class  ytWaitingRoom(Cog_Extension):
    async def updateMsg(self, target_msg: str, videosDict: dict, msg: discord.Message):
        # dealing with upcoming msg
        time = datetime.now()
        content =   f"**{target_msg.title()} Stream:**\nUpdate at:"
        time_str =  f" {time.strftime('%m-%d %H:%M:%S')}"
        no_result = "\n`There's no result`"
        yt_head_url = "https://www.youtube.com/watch?v="

        if (len(videosDict)==0):
            await msg.edit(content=content + time_str + no_result,embed=None)
        else:
            for key, value in videosDict.items():
                value += timedelta(hours=8)
                time_str = value.strftime("%m-%d %H:%M") + " (UTC+8)"
                content +=  f"\n> url: {yt_head_url}{key}" +\
                            "\n> scheduledStartTime: "+ time_str
        
            print(content)
            await msg.edit(content=content,embed=None)
        #print(f"updateMsg msg_id: {msg.id} to \n{content}")
        content = "**YouTube forwarder update at:**"
        content += f"\n ```{time.strftime('%Y/%m/%d %H:%M')}```"
        #await self.ch_status.edit(name=f"YT {now.strftime('%H:%M')} ✅")
        try:
            await self.msg_status.edit(content=content)
        except Exception as e:
            err_content = traceback.format_exc()
            await self.ch_err.send(f"ERR: \n`{err_content}`")
            print(err_content)
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, data):

        if data.channel_id != 823146960826662912:
            return
        msg_is_upcoming = data.message_id == 826197268055064576
        msg_is_streaming = data.message_id == 826197370353614911
        is_msg = msg_is_upcoming or msg_is_streaming
        if str(data.emoji) == '🔄' and is_msg:
            guild = self.bot.get_guild(data.guild_id)
            channel = self.bot.get_channel(823146960826662912) # waiting room channel
            self.msg_upcoming = await channel.fetch_message(826197268055064576)
            self.msg_live = await channel.fetch_message(826197370353614911)
            message = await channel.fetch_message(data.message_id)
                        
            await message.remove_reaction(data.emoji, data.member)
            select="videoId, scheduledStartTime"
            upcoming_videos = self.tracker.loadDataList(select=select, type="waiting")
            select="videoId, scheduledStartTime"
            upcoming_videos = self.tracker.loadDataList(select=select, type="live")

            await self.updateMsg("upcoming", self.upcoming_videos, self.msg_upcoming)
            await self.updateMsg("live", self.live_videos, self.msg_live)
                
    def __init__(self, bot):
        
        async def interval():
            await bot.wait_until_ready()
            SLEEP_TIME = 60 # 60 seconds
            sleep_minuates = -1
            guild = self.bot.get_guild(782232756238549032)
            channel = self.bot.get_channel(823146960826662912) # waiting room channel
            ch_status_msg = self.bot.get_channel(814226297931694101) # update status in select ch
            self.ch_err = self.bot.get_channel(782232918512107542)
            self.ch_status = self.bot.get_channel(832474478868693032)
            self.msg_status = await ch_status_msg.fetch_message(831658507161567282) # msg to update
            self.msg_upcoming = await channel.fetch_message(826197268055064576)
            self.msg_live = await channel.fetch_message(826197370353614911)
            while not self.bot.is_closed():
                sleep_minuates += 1
                await asyncio.sleep(SLEEP_TIME)

                select="videoId, scheduledStartTime"
                upcoming_videos = self.tracker.loadDataList(select=select, type="waiting")
                select="videoId, scheduledStartTime"
                upcoming_videos = self.tracker.loadDataList(select=select, type="live")
                
                await self.updateMsg("upcoming", self.upcoming_videos, self.msg_upcoming)
                await self.updateMsg("live", self.live_videos, self.msg_live)
                
        self.bot = bot
        self.upcoming_last_search = datetime.now() - timedelta(hours=1)
        self.live_last_search = datetime.now() - timedelta(hours=1)
        self.list_search_time = datetime.now()
        self.live_videos = {}
        self.upcoming_videos = {}

        self.tracker = ytTracker.ytTracker()

        self.bg_task = self.bot.loop.create_task(interval())


        


def setup(bot):
    bot.add_cog(ytWaitingRoom(bot))
