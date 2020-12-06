import discord
from discord.ext import commands
from core.classes import Cog_Extension
import json, asyncio, requests
import datetime as dt

# load data and set variables
with open('twitter_forward_setting.json','r', encoding='utf8') as f:
    twitter_setting = json.load(f)
    f.close()
twitter_url = twitter_setting['twitter_url']
twitter_icon_url = twitter_setting['twitter_icon_url']
proproduction = twitter_setting['proproduction']
mikuru = twitter_setting['mikuru']
mia = twitter_setting['mia']
chiroru = twitter_setting['chiroru']
isumi = twitter_setting['isumi']
yuru = twitter_setting['yuru']

with open('twitter_api.json', mode='r', encoding='utf8') as jfile:
    jdata = json.load(jfile)
class Task(Cog_Extension):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.url = jdata['url']
        self.payload = jdata['payload']
        self.headers = jdata['headers']
        #self.follower = '<@658596195995877376>'    #<@user_id>
        self.follow_role = '<@&782875710545068062>' #<@&role_id>

        async def interval():
            await self.bot.wait_until_ready()
            self.channel = self.bot.get_channel(782232918512107542)
            while not self.bot.is_closed():
                await self.channel.send('Bot is alive :wink:')
                await asyncio.sleep(60*10) # unit: second
        self.bg_task = self.bot.loop.create_task(interval())
    @commands.command()
    async def set_channel(self, ctx, ch: int):
        self.channel = self.bot.get_channel(ch)
        await ctx.send(f'Set Channel: {self.channel.mention}')
    @commands.command()
    async def get_tweets(self, ctx):
        await ctx.channel.send(self.follow_role)
        target = chiroru
        tweets = get_tweets(chiroru)
        for item in tweets['data']:
            # found tweet by target[account_id], and get tweet url=twitter_url+target['account_id']+'/status/'+tweet_id
            tweet_url = twitter_url+target['account_id']+'/status/'+item['id']
            embed=discord.Embed(title=target['name'] + " just tweeted this:", url=tweet_url, description=item['text'], color=0xd2f57f)
            embed.set_author(name=target['name']+"( @amachiroru)", url=twitter_url+target['account_id'], icon_url=target['icon_url'])
            embed.set_thumbnail(url=target['icon_url'])
            embed.set_footer(text=item['created_at'])
            await ctx.send(embed=embed)

def setup(bot):
    bot.add_cog(Task(bot))

def get_tweets(target:dict):
    with open('twitter_api.json', 'r', encoding='utf8') as f:
        jdata = json.load(f)
        f.close()
    # set time
    time = dt.datetime.utcnow()
    time += dt.timedelta(days=-1)
    time = time.isoformat('T') + 'Z'
    # set for request
    url = "https://api.twitter.com/2/tweets/search/recent?tweet.fields=attachments,created_at,entities&expansions=&media.fields=&user.fields=&query=(from:amachiroru)&start_time="+time
    payload = jdata['payload']
    headers= jdata['headers']

    res = requests.request("GET", url, headers=headers, data = payload)
    jdata = res.json()
    return jdata
    
    