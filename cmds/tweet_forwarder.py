import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, asyncio, requests, urllib3, socket
import datetime as dt

DEBUG=False
DEBUG_HOUR=0
if DEBUG: DEBUG_HOUR=3

# load data and set variables
with open('twitter_forward_setting.json','r', encoding='utf8') as f:
    twitter_setting = json.load(f)
    f.close()
t_url = twitter_setting['twitter_url']
twitter_icon_url = twitter_setting['twitter_icon_url']

# gen1 with staff
proproduction = twitter_setting['proproduction']
mikuru = twitter_setting['mikuru']
mia = twitter_setting['mia']
chiroru = twitter_setting['chiroru']
isumi = twitter_setting['isumi']
yuru = twitter_setting['yuru']
# gen2
mai = twitter_setting['mai']
rin = twitter_setting['rin']

BOX_MEMBER = (proproduction, 
        mikuru, mia, chiroru, isumi, yuru, 
        mai, rin)
TARGETS_GEN1 = mikuru, mia, chiroru, isumi, yuru
TARGETS_GEN2 = mai, rin      # here is TARGETS list
BOX_MEMBER_ID = [x['id'] for x in BOX_MEMBER]
SLEEP_TIME = 10

with open('twitter_api.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)

class TweetForwarder(Cog_Extension):
    @commands.command()
    async def set_target(self, ctx, msg):
        if ("all" in msg):
            self.TARGETS = BOX_MEMBER
            await ctx.send('tweet_forwarder set target to all box member')
        elif ("gen1" in msg):
            self.TARGETS = TARGETS_GEN1
            await ctx.send('tweet_forwarder set target to gen1')
        elif ("gen2" in msg):
            self.TARGETS = TARGETS_GEN2
            await ctx.send('tweet_forwarder set target to gen2')
        
    def __init__(self, bot):
        self.bot = bot
        self.TARGETS = TARGETS_GEN2
        
        async def interval():
            await self.default_satting(bot)
            await self.guild.get
            while not self.bot.is_closed():
                self.new_t_all = int(0)     # all new tweet number
                self.new_t_vis = int(0)
                self.count += 1
                now = dt.datetime.now()
                print(f"{now} interval: loop time: {self.count}")

                # report update time
                self.report_msg.edit(content=self.report_content+f"*{now.strftime('%m-%d %H:%M')}*")


                # set search time
                self.cur_st_t = self.last_ed_t
                self.cur_ed_t = dt.datetime.utcnow() + dt.timedelta(days=0, hours=0, minutes=0, seconds=-15)
                # get embed message and send to speticular channel
                for tg in self.TARGETS:
                    self.channel = self.bot.get_channel(int(tg['twi_fw_ch']))
                    role = self.guild.get_role(int(tg['dc_role']))
                    

                    res = await self.get_tweets(tg, self.cur_st_t, self.cur_ed_t)
                    if (res.status_code!=200): 
                        print(f"fail to get_tweets from {tg['username']}")
                        continue
                    tweets = res.json()

                    

                    
                    # if no tweet in time interval, continue

                    for i in range(tweets['meta']['result_count']):
                        tweet = tweets['data'][i]

                        # found tweet by target[username], and get tweet url=twitter_url+target['username']+'/status/'+tweet_id
                        tweet_url = t_url + tg['username'] + '/status/' + tweet['id']
                        tweet_time = tweet['created_at'].replace("T"," ")[:-1]
                        try:
                            tweet_time = dt.datetime.fromisoformat(tweet_time)# + dt.timedelta(hours=8)
                        except Exception as e:
                            print("Exception in tweet time transform: ", e)
                            tweet_time = dt.datetime.now()
                        """
                        # Embed setting 
                        embed=discord.Embed(url=tweet_url, description=tweet['text'], color=tg['embed_color'], timestamp=tweet_time)
                        embed.set_author(name=f"{tg['name']} ( @{tg['username']})", url=t_url+tg['username'])#, icon_url=tg['icon_url'])
                        embed.set_thumbnail(url=tg['icon_url']) # bigger photo at top right
                        embed.set_footer(text="Twitter", icon_url="https://upload.wikimedia.org/wikipedia/zh/thumb/9/9f/Twitter_bird_logo_2012.svg/590px-Twitter_bird_logo_2012.svg.png")
                        """
                        
                        self.new_t_all += 1
                        msg_mention = f"{role.mention} "
                        msg_S = f"{tg['nickname']} "
                        msg_V = ""
                        msg_O = ""
                        msg_Link = f"\n{tweet_url}"
                        debug_msg = f'{tg["username"]} '
                        
                        # tweet in reply to user
                        if "in_reply_to_user_id" in tweet.keys():
                            
                            msg_V = "just reply a tweet:"
                            debug_msg += f'reply to id: {tweet["in_reply_to_user_id"]}, '
                            if (not (tweet["in_reply_to_user_id"] in BOX_MEMBER_ID)):
                                debug_msg += f'message forward to {self.reply_ch.name}'
                                if (DEBUG):
                                    await self.debug_ch.send(msg_S + msg_V + msg_O + msg_Link)
                                else:
                                    await self.reply_ch.send(msg_S + msg_V + msg_O + msg_Link)
                                print(debug_msg)
                                continue
                            else:
                                msg_V = "tete!"
                                if (tweet["in_reply_to_user_id"] == tg["username"]):
                                    msg_V = "reply to herself"
                                debug_msg += f'is relative, message forward to {self.channel.name}'
                                if (DEBUG):
                                    await self.debug_ch.send(msg_mention + msg_S + msg_V + msg_O + msg_Link)
                                else:
                                    await self.channel.send(msg_mention + msg_S + msg_V + msg_O + msg_Link)
                                print(debug_msg)
                                continue
                        elif (tweet["text"][:2] == "RT"):
                            msg_V = "just retweet this:"
                            debug_msg += f'retweet, message forward to {self.channel.name}'
                            if (DEBUG):
                                await self.debug_ch.send(msg_mention + msg_S + msg_V + msg_O + msg_Link)
                            else:
                                await self.channel.send(msg_mention + msg_S + msg_V + msg_O + msg_Link)
                            print(debug_msg)
                            continue

                        # visiable forward to channel
                        self.new_t_vis += 1
                        msg_V = "just post a tweet:"
                        debug_msg += f'post a tweet, message forward to {tg["twi_fw_ch"]}'
                        if (DEBUG):
                            await self.debug_ch.send(msg_mention + msg_S + msg_V + msg_O + msg_Link)
                        else:
                            await self.bot.get_channel(int(tg['twi_fw_ch'])).send(
                                msg_mention + msg_S + msg_V + msg_O + msg_Link)
                        print(debug_msg)
                    await asyncio.sleep(1) 

                # update last search range by current search range
                self.last_st_t = self.cur_st_t
                self.last_ed_t = self.cur_ed_t
                
                # print how many tweet are detect
                debug_msg = "{} -> {}, TwitterForwarderGen2 detect new tweet: {}, visible forward: {}".format(
                    self.cur_st_t, self.cur_ed_t, self.new_t_all, self.new_t_vis)
                print(debug_msg)


                # wait
                await asyncio.sleep(SLEEP_TIME) # unit: second
        self.bg_task = self.bot.loop.create_task(interval())

    async def default_satting(self, bot):
        await bot.wait_until_ready()

        self.guild =  bot.get_guild(782232756238549032)
        print("TweetForwarder: working at guild=", self.guild)
        self.debug_ch = self.bot.get_channel(782232918512107542)
        
        self.channel = self.bot.get_channel(twitter_setting['dc_ch_id_general']) #default channel
        self.reply_ch = self.bot.get_channel(twitter_setting['dc_ch_id_reply'])

        if DEBUG:
            self.channel = self.debug_ch
            self.reply_ch = self.debug_ch

        self.last_st_t = dt.datetime.utcnow()
        self.last_ed_t = dt.datetime.utcnow() + dt.timedelta(hours=-DEBUG_HOUR,  seconds=-SLEEP_TIME*5)
        self.cur_st_t = dt.datetime.utcnow() 
        self.cur_ed_t = dt.datetime.utcnow()
        self.count = int(0)

        # for report update
        self.report_ch = self.bot.get_channel(814226297931694101)
        self.report_msg = await channel.fetch_message(829113456195797092)
        self.report_content = "Tweet forwarder update at: "


    async def get_tweets(self, target:dict, start_t, end_t):
        with open('twitter_api.json', 'r', encoding='utf8') as f:
            jdata = json.load(f)
            f.close()
        # set time, assume both of them is utc time
        
        start_time = start_t.isoformat('T') + 'Z'
        end_time = end_t.isoformat('T') + 'Z'
        # set for request
        url = "https://api.twitter.com/2/tweets/search/recent?"+\
            "tweet.fields=attachments,created_at,entities"+\
            "&max_results=50"+\
            "&expansions=author_id,in_reply_to_user_id"+\
            "&media.fields=&user.fields="+\
            f"&query=(from:{target['username']})"+\
            f"&start_time={start_time}&end_time={end_time}"
        payload = jdata['payload']
        headers= jdata['headers']

        # deal with error
        for i in range(5):
            try:
                res = requests.request("GET", url, headers=headers, data = payload)
            except Exception as e:
                print("Except: ", e, "status_code: ", res.status_code, "count: ", count)
                count += 1
                await asyncio.sleep(5)

            if res.status_code == requests.codes.ok:
                break
            else:
                print("FAIL to request, res.status_code=", res.status_code)
            await asyncio.sleep(5)
        
        if res.status_code != requests.codes.ok:
            print("request fail, status_code: ", res.status_code)
            print("res.content: ", res.content)
            print("get_tweets : url=", url)
            await self.debug_ch.send("request fail, status_code: ", res.status_code)
            await self.debug_ch.send("get_tweets : url=", url)
            

        
        return res


def setup(bot):
    bot.add_cog(TweetForwarder(bot))
