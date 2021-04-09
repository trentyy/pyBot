import discord
from discord.ext import commands
from core.classes import Cog_Extension

import random

class Sup(Cog_Extension):
    @commands.Cog.listener()
    async def on_message(self, msg):
        if '怕.jpg' in msg.content:
            sel = random.randint(0,2)
            if sel == 0:
                await  msg.channel.send('https://tenor.com/view/funny-animals-dog-scared-shocked-gif-12315251')
            elif sel == 1:
                await  msg.channel.send('https://tenor.com/view/scared-cat-alarmed-nervous-startled-shook-gif-13065287')
            elif sel == 2:
                await  msg.channel.send('https://tenor.com/view/see-it-like-final-hamster-scared-gif-14498643')
        elif '圖呢' in msg.content:
            await  msg.channel.send('https://i.imgur.com/TsPkY4Q.gif')
        elif  '修但' in msg.content:
            await  msg.channel.send('https://memes.tw/wtf/356325')
        elif '蘿莉控' in msg.content or 'FBI open up' in msg.content:
            await  msg.channel.send('https://tenor.com/view/fbi-open-up-open-up-fbi-raid-gif-17397897')
        elif '看了三小' in msg.content:
            await  msg.channel.send('https://c2.staticflickr.com/6/5647/23875668111_df9d520c4a_b.jpg')
def setup(bot):
    bot.add_cog(Sup(bot))
