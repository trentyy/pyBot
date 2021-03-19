import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, asyncio, requests, urllib3, socket
import datetime as dt

DEBUG = False
DEBUG_NO_REPLY = False
DEBUG_NO_BOT_PG = False
DEBUG_HOUR = 0
if DEBUG: DEBUG_HOUR = 1 
DEBUG_LOG = "./tweet_forwarder.log"

# load data and set variables
with open('twitter_forward_setting.json','r', encoding='utf8') as f:
    twitter_setting = json.load(f)
    f.close()
t_url = twitter_setting['twitter_url']
twitter_icon_url = twitter_setting['twitter_icon_url']
koinoya = twitter_setting['koinoya']
hanakumo = twitter_setting['hanakumo']


TARGETS = koinoya, hanakumo      # here is TARGETS list
TARGETS_ACCOUNT_ID =  [x['account_id'] for x in TARGETS]
TARGETS_ID = [x['id'] for x in TARGETS]

#TARGETS_ROLES = [self.guild.get_role(int(x['dc_role'])) for x in TARGETS]

SLEEP_TIME = 5


with open('twitter_api.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)
class TweetForwarderGen2(Cog_Extension):
    def __init__(self, bot):
        self.bot = bot

        async def interval():
            await self.bot.wait_until_ready()

            self.guild =  bot.get_guild(782232756238549032)
            print("TweetForwarder: working at guild=", self.guild)
            self.bot_pg = self.bot.get_channel(782232918512107542)
            
            self.channel = self.bot.get_channel(twitter_setting['dc_ch_id_general']) #default channel
            self.reply_ch = self.bot.get_channel(twitter_setting['dc_ch_id_reply'])

            if DEBUG:
                self.channel = self.bot_pg
                self.reply_ch = self.bot_pg

            self.last_st_t = dt.datetime.utcnow()
            self.last_ed_t = dt.datetime.utcnow() + dt.timedelta(hours=-DEBUG_HOUR-1,  seconds=-SLEEP_TIME*5)
            self.cur_st_t = dt.datetime.utcnow() 
            self.cur_ed_t = dt.datetime.utcnow()
            self.count = int(0)
            self.new_t_all = int(0)     # all new tweet number
            self.new_t_vis = int(0)     # visiable new tweet number    

            

            while not self.bot.is_closed():
                self.count += 1
                print(f"{dt.datetime.now()} interval: loop time: {self.count}")

                # send message to bot_pg show that it's alive
                """
                curTime = dt.datetime.now().replace(microsecond=0).isoformat(" ")
                msg = f'Bot host by `{socket.gethostname()}` is alive. {curTime}'
                await self.bot_pg.send(msg)
                """

                # set search time
                self.cur_st_t = self.last_ed_t
                self.cur_ed_t = dt.datetime.utcnow() + dt.timedelta(days=0, hours=0, minutes=0, seconds=-15)
                # get embed message and send to speticular channel
                for tg in TARGETS:
                    self.channel = self.bot.get_channel(int(tg['twi_fw_ch']))
                    role = self.guild.get_role(int(tg['dc_role']))
                    
                    try:
                        tweets = await self.get_tweets(tg, self.cur_st_t, self.cur_ed_t)
                    except Exception as e:
                        bug_msg = f"Failed to get tweets from: {tg['account_id']}"
                        print(bug_msg)
                        await self.bot_pg.send(bug_msg)
                    
                    # if no tweet in time interval, continue

                    for i in range(tweets['meta']['result_count']):
                        tweet = tweets['data'][i]
                        # skip situation

                        ## need to add a whitelist

                        # found tweet by target[account_id], and get tweet url=twitter_url+target['account_id']+'/status/'+tweet_id
                        tweet_url = t_url + tg['account_id'] + '/status/' + tweet['id']
                        tweet_time = tweet['created_at'].replace("T"," ")[:-1]
                        try:
                            tweet_time = dt.datetime.fromisoformat(tweet_time)# + dt.timedelta(hours=8)
                        except Exception as e:
                            print("Exception in tweet time transform: ", e)
                            tweet_time = dt.datetime.now()
                        
                        # Embed setting 
                        embed=discord.Embed(url=tweet_url, description=tweet['text'], color=tg['embed_color'], timestamp=tweet_time)
                        embed.set_author(name=f"{tg['name']} ( @{tg['account_id']})", url=t_url+tg['account_id'])#, icon_url=tg['icon_url'])
                        embed.set_thumbnail(url=tg['icon_url']) # bigger photo at top right
                        embed.set_footer(text="Twitter", icon_url="https://upload.wikimedia.org/wikipedia/zh/thumb/9/9f/Twitter_bird_logo_2012.svg/590px-Twitter_bird_logo_2012.svg.png")
                        
                        # invisiable forward to bot_pg
                        
                        self.new_t_all += 1
                        # bot_pg 監控
                        if DEBUG_NO_BOT_PG==False:
                            msg = "TO BOT CH:" + f"{tg['nickname']} 發/回覆了一篇推特:\n{tweet_url}"
                            debug_msg = f'tweet id {tweet["id"]} from {tg["account_id"]} forward to {self.bot_pg.name}'
                            await self.bot_pg.send(msg, embed=embed)
                            print(debug_msg)

                        # tweet in reply to user
                        if "in_reply_to_user_id" in tweet.keys():
                            msg = f"{tg['nickname']} just reply a tweet:\n{tweet_url}"
                            

                            if DEBUG_NO_REPLY:
                                continue
                            if (tweet["in_reply_to_user_id"] in TARGETS_ID):
                                msg = f"{tg['nickname']} tete!\n{tweet_url}"
                                if (tweet["in_reply_to_user_id"]==tg["account_id"]):
                                    msg = f"{tg['nickname']} reply to herself\n{tweet_url}"
                                debug_msg = f'{tg["account_id"]} reply to id: {tweet["in_reply_to_user_id"]} in TARGETS_ID , message forward to {self.channel.name}'
                                await self.channel.send(msg)
                                print(debug_msg)
                            else:
                                debug_msg = f'{tg["account_id"]} reply to id: {tweet["in_reply_to_user_id"]}, message forward to {self.reply_ch.name}'
                                await self.reply_ch.send(msg)
                                print(debug_msg)
                            continue

                        # visiable forward to channel
                        self.new_t_vis += 1
                        msg = f"{role.mention} {tg['nickname']} just post a tweet:\n{tweet_url}"
                        debug_msg = f'{tg["account_id"]} post a tweet, message forward to {tg["twi_fw_ch"]}'
                        await self.bot.get_channel(int(tg['twi_fw_ch'])).send(msg)
                        print(debug_msg)
                    await asyncio.sleep(1) 

                # update last search range by current search range
                self.last_st_t = self.cur_st_t
                self.last_ed_t = self.cur_ed_t
                
                # print how many tweet are detect
                debug_msg = "from {} to {}, I detect new tweet: {}, visible forward: {}".format(
                    self.cur_st_t, self.cur_ed_t, self.new_t_all, self.new_t_vis)
                print(debug_msg)
                self.new_t_all = int(0)
                self.new_t_vis = int(0) 

                # wait
                await asyncio.sleep(SLEEP_TIME) # unit: second
        self.bg_task = self.bot.loop.create_task(interval())
    

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
            f"&query=(from:{target['account_id']})"+\
            f"&start_time={start_time}&end_time={end_time}"
        payload = jdata['payload']
        headers= jdata['headers']

        count = 0
        while (count < 5):
            try:
                res = requests.request("GET", url, headers=headers, data = payload)
            except Exception as e:
                print("Except:", e)
                count += 1

            if res.status_code == requests.codes.ok:
                break
            else:
                print("FAIL to request, res.status_code=", res.status_code)
            await asyncio.sleep(5)
        
        if res.status_code != requests.codes.ok:
            print("request fail, status_code: ", res.status_code)
            print("get_tweets : url=", url)
            await self.bot_pg.send("request fail, status_code: ", res.status_code)
            await self.bot_pg.send("get_tweets : url=", url)
            

        jdata = res.json()
        return jdata


def setup(bot):
    bot.add_cog(TweetForwarderGen2(bot))