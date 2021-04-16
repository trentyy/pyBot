import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, asyncio, sys, socket
import requests, urllib3
import datetime as dt

SLEEP_TIME = 300

class Watchdog(Cog_Extension):
    def yt_status_update(new_status):
        old_status = self.yt_fw_service_down
        self.yt_fw_service_down = new_status
        statement = (old_status==False) and (new_status==True)

        return True if statement else False
    def twi_status_update(new_status):
        old_status = self.twi_fw_service_down
        self.twi_fw_service_down = new_status
        statement = (old_status==False) and (new_status==True)
        
        return True if statement else False
            
    def __init__(self, bot):
        self.bot = bot
        
        self.yt_fw_service_down = False
        self.twi_fw_service_down = False

        async def interval():
            await self.default_setting(bot)
            while not self.bot.is_closed():
                now = dt.datetime.now()
                


                # wait
                await asyncio.sleep(SLEEP_TIME) # unit: second
        self.bg_task = self.bot.loop.create_task(interval())

    async def default_setting(self, bot):
        await bot.wait_until_ready()

        self.guild =  bot.get_guild(782232756238549032)
        print("TweetForwarder: working at guild=", self.guild)
        self.report_ch = self.bot.get_channel(782232918512107542)
        self.announce_ch = self.bot.get_channel(814226297931694101)
        
        self.yt_status = self.bot.get_channel(832474478868693032)
        self.twi_status = self.bot.get_channel(832496669752819743)

        self.count = int(0)






def setup(bot):
    bot.add_cog(Watchdog(bot))
