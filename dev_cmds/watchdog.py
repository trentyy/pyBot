import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, asyncio, sys, socket
import requests, urllib3
from datetime import datetime, timedelta
import dateutil.parser

SLEEP_TIME = 300

class Watchdog(Cog_Extension):
    def twi_status_update(self, edit_at, now):
        old_status = self.twi_fw_service_alive
        dm = (edit_at - now).seconds / 60
        new_status = True if dm < 5 else False
        self.twi_fw_service_alive = new_status  # update status
        need_report = (old_status) and (not new_status)
        
        return True if need_report else False
    def yt_status_update(self, edit_at, now):
        old_status = self.yt_fw_service_alive
        dm = (edit_at - now).seconds / 60
        new_status = True if dm < 5 else False
        self.yt_fw_service_alive = new_status   # update status
        need_report = (old_status) and (not new_status)

        return True if need_report else False
    
            
    def __init__(self, bot):
        self.bot = bot
        
        self.yt_fw_service_alive = True
        self.twi_fw_service_alive = True

        async def interval():
            await self.default_setting(bot)
            while not self.bot.is_closed():
                now = dt.datetime.now()
                
                msg_twi = self.ch.fetch_message(self.msg_twi_id)
                edit_at = msg_twi.edit_at
                need_report = self.twi_status_update(edit_at, now)
                if (twi_status_update):
                    mention = self.role.mention
                    self.report_ch.send(f"{mention} twitter轉發掛了")
                    self.ch.send(f"{mention} :Cat_Yuru: 抱歉，twitter轉發出了點問題，有時間來看看嗎?")

                msg_yt = self.ch.fetch_message(self.msg_yt_id)
                edit_at = msg_y.edit_at
                need_report = self.yt_status_update(edit_at, now)
                if (need_report):
                    mention = self.role.mention
                    self.report_ch.send(f"{mention} YT轉發掛了")
                    self.ch.send(f"{mention}:mia_mem05: 糟糕，YT轉發有點不對勁")

                # wait
                await asyncio.sleep(SLEEP_TIME) # unit: second
        self.bg_task = self.bot.loop.create_task(interval())

    async def default_setting(self, bot):
        await bot.wait_until_ready()

        self.guild =  bot.get_guild(782232756238549032)
        print("TweetForwarder: working at guild=", self.guild)
        self.report_ch = self.bot.get_channel(782232918512107542) # infrom manager
        self.role = self.guild.get_role(785503910818218025) # bot manager role
        
        self.ch = self.bot.get_channel(814226297931694101)
        self.msg_twi_id = 829113456195797092
        self.msg_yt_id = 831658507161567282
        

        self.count = int(0)






def setup(bot):
    bot.add_cog(Watchdog(bot))
