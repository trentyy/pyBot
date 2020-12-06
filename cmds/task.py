import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, asyncio, requests, socket
import datetime as dt

# load data and set variables
with open('twitter_forward_setting.json','r', encoding='utf8') as f:
    twitter_setting = json.load(f)
    f.close()
t_url = twitter_setting['twitter_url']
twitter_icon_url = twitter_setting['twitter_icon_url']
proproduction = twitter_setting['proproduction']
mikuru = twitter_setting['mikuru']
mia = twitter_setting['mia']
chiroru = twitter_setting['chiroru']
isumi = twitter_setting['isumi']
yuru = twitter_setting['yuru']
SLEEP_TIME = 2*60

with open('twitter_api.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)
class Task(Cog_Extension):
    def __init__(self, bot):
        self.bot = bot
        async def interval():
            await self.bot.wait_until_ready()

            #self.bot = bot
            print("task - interval loaded: bot=", bot)
            self.guild =  bot.get_guild(782232756238549032)
            print("interval: self.guild=", self.guild)
            self.channel = self.bot.get_channel(782232918512107542) #default channel
            self.bot_ch = self.bot.get_channel(782232918512107542)
            self.last_st_t = dt.datetime.utcnow()
            self.last_ed_t = dt.datetime.utcnow() + dt.timedelta(hours=-1, seconds=-SLEEP_TIME*5)
            self.cur_st_t = dt.datetime.utcnow() 
            self.cur_ed_t = dt.datetime.utcnow()
            self.history = dict()
            self.count = int(0)

            targets = proproduction, mikuru, mia, chiroru, isumi, yuru

            while not self.bot.is_closed():
                self.count += 1
                print("interval: loop time:", self.count)
                # send message to bot_ch show that it's alive
                curTime = dt.datetime.now().replace(microsecond=0).isoformat(" ")
                await self.bot_ch.send(f'Bot host by `{socket.gethostname()}` is alive. {curTime}')
                

                # set search time
                self.cur_st_t = self.last_ed_t
                self.cur_ed_t = dt.datetime.utcnow() + dt.timedelta(days=0, hours=0, minutes=0, seconds=-10)
                # get embed message and send to speticular channel
                for tg in targets:
                    role = self.guild.get_role(int(tg['dc_role']))
                    
                    tweets = get_tweets(tg, self.cur_st_t, self.cur_ed_t)
                    
                    # if no tweet in time interval, continue

                    for i in range(tweets['meta']['result_count']):
                        tweet = tweets['data'][i]
                        # skip situation
                        ## need to add a whitelist
                        
                        if int(tweet['id']) in self.history.keys():
                            print(f'tweet id {tweet["id"]} already posted, hostory: {self.history.keys()}')
                            continue
                        else:
                            # add tweet to history
                            self.history.update({int(tweet['id']): tweet['created_at']})

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
                        #embed.set_author(name=f"{tg['name']} ( @{tg['account_id']})", url=t_url+tg['account_id'])#, icon_url=tg['icon_url'])
                        #embed.set_thumbnail(url=tg['icon_url']) # bigger photo at top right
                        #embed.set_footer(text="Twitter", icon_url="https://upload.wikimedia.org/wikipedia/zh/thumb/9/9f/Twitter_bird_logo_2012.svg/590px-Twitter_bird_logo_2012.svg.png")
                        
                        
                        await self.bot_ch.send(f"{role.mention} {tg['nickname']} 發了一篇推特:\n{tweet_url}", embed=embed)
                        if "in_reply_to_user_id" in tweet.keys():
                            print(f'{tg["account_id"]} reply to id: {tweet["in_reply_to_user_id"]}, message forward to {bot_ch.mention}')
                            continue
                        await self.channel.send(f"{role.mention} {tg['nickname']} just tweeted this:\n{tweet_url}")
                # update last search range by current search range
                self.last_st_t = self.cur_st_t
                self.last_ed_t = self.cur_ed_t
                # wait
                await asyncio.sleep(SLEEP_TIME) # unit: second
        self.bg_task = self.bot.loop.create_task(interval())
    
    @commands.command()
    async def set_channel(self, ctx, ch: int):
        try:
            self.channel = self.bot.get_channel(ch)
            print(f"I'll start to forward tweet to channel: #{self.channel}")
            await ctx.send(f'Starting to forward tweet to channel: {self.channel.mention}')
        except AttributeError:
            await ctx.send(f'Failed to forward tweet to channel: {self.channel.mention}, check channel ID')
        


def get_tweets(target:dict, start_t, end_t):
    with open('twitter_api.json', 'r', encoding='utf8') as f:
        jdata = json.load(f)
        f.close()
    # set time, assume both of them is utc time
    
    start_time = start_t.isoformat('T') + 'Z'
    end_time = end_t.isoformat('T') + 'Z'
    # set for request
    url = "https://api.twitter.com/2/tweets/search/recent?tweet.fields="+\
        "attachments,created_at,entities&expansions=in_reply_to_user_id&media.fields=&user.fields="+\
        f"&query=(from:{target['account_id']})"+\
        f"&start_time={start_time}&end_time={end_time}"
    payload = jdata['payload']
    headers= jdata['headers']

    res = requests.request("GET", url, headers=headers, data = payload)
    if res.status_code != requests.codes.ok:
        print("request fail, status_code: ", res.status_code)
        print("get_tweets : url=", url)
        

    jdata = res.json()
    return jdata


def setup(bot):
    bot.add_cog(Task(bot))