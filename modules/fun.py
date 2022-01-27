import logging
import random

import nextcord
from nextcord.ext import commands

logger = logging.getLogger(__name__)

class Fun(commands.Cog):

    COG_EMOJI = "🎱"

    def __init__(self, bot):
        self.bot = bot

    @commands.cooldown(6, 5)
    @commands.command(name='8ball', aliases=['eightball'])
    async def eightball(self, ctx,*,question):
        """
        AKA 8ball, Ask the bot a question that you dont want the answer to.
        """
        responses = ["It is certain.",
                    "It is decidedly so.",
                    "Without a doubt.",
                    "Yes - definitely.",
                    "You may rely on it.",
                    "As I see it, yes.",
                    "Most likely.",
                    "Outlook good.",
                    "Yes.",
                    "Signs point to yes.",
                    "Reply hazy, try again.",
                    "Ask again later.",
                    "Better not tell you now.",
                    "Cannot predict now.",
                    "Concentrate and ask again.",
                    "Don't count on it.",
                    "My reply is no.",
                    "My sources say no.",
                    "Outlook not so good.",
                    "Very doubtful."]
        response = random.choice(responses)
        embed = nextcord.Embed(
            title = response,
            description=f'Question: {question}',
            colour = nextcord.Colour.orange()
        )
        embed.set_footer(text=f'Question by {ctx.author}', icon_url=ctx.author.avatar.url)
        await ctx.channel.send(embed=embed)

def setup(bot):
    bot.add_cog(Fun(bot))
