import discord
from discord.ext import commands
from core.classes import Cog_Extension
import os, json, asyncio, pymysql.cursors
from datetime import datetime, timedelta
import dateutil.parser

import googleapiclient.discovery

with open('yt_api.json','r', encoding='utf8') as f:
    yt_api_setting = json.load(f)
    f.close()
with open('yt_fw_setting.json','r', encoding='utf8') as f:
    yt_fw_setting = json.load(f)
    f.close()

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

api_service_name = "youtube"
api_version = "v3"
DEVELOPER_KEY = yt_api_setting["DEVELOPER_KEY"]
BOX_MEMBER_ID = yt_fw_setting["BOX_MEMBER_ID"]

youtube = googleapiclient.discovery.build(
    api_service_name, api_version, developerKey = DEVELOPER_KEY)
class  YTForwarder(Cog_Extension):
    def ytSearchAPI(self, eventType="upcoming"):
        # Disable OAuthlib's HTTPS verification when running locally.
        # *DO NOT* leave this option enabled in production.

        request = youtube.search().list(
            part="snippet",
            #channelId="",
            eventType=eventType,
            maxResults=20,
            q="プロプロ",
            type="video"
        )
        response = request.execute()    
        self.list_search_time = datetime.now()
        return response
    def ytVideosAPI(self, videoId):
        request = youtube.videos().list(
            part="snippet,liveStreamingDetails,status",
            id=videoId
        )
        response = request.execute()
        response = response["items"][0]

        liveBroadcastContent = response["snippet"]["liveBroadcastContent"]
        scheduledStartTime = response["liveStreamingDetails"]["scheduledStartTime"]
        scheduledStartTime = dateutil.parser.isoparse(scheduledStartTime)  
        return liveBroadcastContent, scheduledStartTime
    def videosListFilter(self, response, member_id_dict):
        # filter and refresh live, upcoming list
        for item in response['items']:
            if item['snippet']['channelId'] in member_id_dict.values():
                # get scheduledStartTime
                videoId = item['id']['videoId']
                liveStatus = item['snippet']['liveBroadcastContent']
                if (liveStatus == "upcoming"):
                    self.upcoming_videos[videoId] = datetime.utcnow()
                elif (liveStatus == "live"):
                    self.live_videos[videoId] = datetime.utcnow()
            else:
                unrelated.append(item)
    def videosListUpdate(self, upcoming_videos, live_videos):
        # check videos stream status
        all_videos = list(upcoming_videos.keys()) + list(live_videos.keys())
        for videoId in all_videos:
            liveBroadcastContent, scheduledStartTime = self.ytVideosAPI(videoId)
            if (liveBroadcastContent=="none"):
                print("already publish: ", videoId)
                if (videoId in upcoming_videos.keys()):
                    upcoming_videos.pop(videoId)
                elif (videoId in live_videos.keys()):
                    live_videos.pop(videoId)
                continue
            elif (liveBroadcastContent == "upcoming"):
                print("liveBroadcastContent == upcoming")
                upcoming_videos[videoId] = scheduledStartTime
            elif (liveBroadcastContent == "live"):
                print("liveBroadcastContent == live")
                if (videoId in upcoming_videos.keys()):
                    print(upcoming_videos.pop(videoId))
                live_videos[videoId] = scheduledStartTime
        return upcoming_videos, live_videos
    async def updateMsg(self, target_msg: str, videosDict: dict, msg: discord.Message, last_search):
        # dealing with upcoming msg
        time = datetime.now()
        content = f"**{target_msg.title()} Search:**\n*Status Update at: {time.strftime('%m-%d %H:%M:%S')}*" +\
            f"\nList Search time: {self.list_search_time.strftime('%m-%d %H:%M:%S')}"
        no_result = "\n`There's no result`"
        yt_head_url = "https://www.youtube.com/watch?v="

        if (len(videosDict)==0):
            await msg.edit(content=content+no_result,embed=None)
        else:
            for key, value in videosDict.items():
                value += timedelta(hours=8)
                time_str = value.strftime("%m-%d %H:%M") + "UTC+8"
                content += f"\n> url: {yt_head_url}{key}" +\
                "\n> scheduledStartTime: "+ time_str
        
            print(content)
            last_search = datetime.now()
            await msg.edit(content=content,embed=None)
        print(f"updateMsg msg_id: {msg.id} to {content}")
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, data):
        
        if data.channel_id != 823146960826662912:
            return
        if str(data.emoji) == '🔄':
            guild = self.bot.get_guild(data.guild_id)
            channel = self.bot.get_channel(823146960826662912) # waiting room channel
            self.msg_upcoming = await channel.fetch_message(826197268055064576)
            self.msg_live = await channel.fetch_message(826197370353614911)
            message = await channel.fetch_message(data.message_id)
            
            print(channel)
            # upcoming
            
            if data.message_id == 826197268055064576:
                await message.remove_reaction('🔄', data.member)
                
                time = datetime.now()
                interval_time = (time - self.upcoming_last_search).seconds
                if interval_time < 600:
                    msg = f"Update interval: 10 minutes, current: {str(int(interval_time/60))}"
                    await channel.send(content=msg, delete_after=20)
                    return
                time += timedelta(seconds=-time.second, microseconds=-time.microsecond)

                result = self.ytSearchAPI()
                self.videosListFilter(result, BOX_MEMBER_ID) # parse
                result = self.ytSearchAPI("live")
                self.videosListFilter(result, BOX_MEMBER_ID)

                self.upcoming_videos, self.live_videos = self.videosListUpdate(self.upcoming_videos, self.live_videos)
                await self.updateMsg("upcoming", self.upcoming_videos, self.msg_upcoming, self.upcoming_last_search)
                await self.updateMsg("live", self.live_videos, self.msg_live, self.upcoming_last_search)
                
                
            if data.message_id == 826197370353614911:
                await message.remove_reaction('🔄', data.member)
                
                time = datetime.now()
                interval_time = (time - self.upcoming_last_search).seconds
                if interval_time < 300:
                    msg = f"Update interval: 5 minutes, current: {str(int(interval_time/60))}"
                    await channel.send(content=msg, delete_after=20)
                    return
                time += timedelta(seconds=-time.second, microseconds=-time.microsecond)

                self.upcoming_videos, self.live_videos = self.videosListUpdate(self.upcoming_videos, self.live_videos)
                await self.updateMsg("upcoming", self.upcoming_videos, self.msg_upcoming, self.upcoming_last_search)
                await self.updateMsg("live", self.live_videos, self.msg_live, self.upcoming_last_search)
    def __init__(self, bot):
        
        async def interval():
            await bot.wait_until_ready()
            SLEEP_TIME = 60 # 60 seconds
            sleep_minuates = -1
            guild = self.bot.get_guild(782232756238549032)
            channel = self.bot.get_channel(823146960826662912) # waiting room channel
            self.msg_upcoming = await channel.fetch_message(826197268055064576)
            self.msg_live = await channel.fetch_message(826197370353614911)
            while not self.bot.is_closed():
                sleep_minuates += 1
                await asyncio.sleep(SLEEP_TIME)

                if (sleep_minuates % 2 != 0):
                    continue
                if (sleep_minuates % 30 == 0):
                    result = self.ytSearchAPI()
                    self.videosListFilter(result, BOX_MEMBER_ID)
                    if (sleep_minuates == 0):
                        result = self.ytSearchAPI("live")
                        self.videosListFilter(result, BOX_MEMBER_ID)
                    print("get new list at: ", datetime.now())
                self.upcoming_videos, self.live_videos = self.videosListUpdate(self.upcoming_videos, self.live_videos)
                await self.updateMsg("upcoming", self.upcoming_videos, self.msg_upcoming, self.upcoming_last_search)
                await self.updateMsg("live", self.live_videos, self.msg_live, self.upcoming_last_search)
                
        self.bot = bot
        self.upcoming_last_search = datetime.now() - timedelta(hours=1)
        self.live_last_search = datetime.now() - timedelta(hours=1)
        self.list_search_time = datetime.now()
        self.live_videos = {}
        self.upcoming_videos = {}
        self.bg_task = self.bot.loop.create_task(interval())


        


def setup(bot):
    bot.add_cog(YTForwarder(bot))
