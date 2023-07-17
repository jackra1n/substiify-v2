import datetime
import logging
import random

import discord
import vacefron
from core.bot import Substiify
from discord.ext import commands

logger = logging.getLogger(__name__)


class Fun(commands.Cog):

    COG_EMOJI = "🎱"

    def __init__(self, bot: Substiify):
        self.bot = bot
        self.vac_api = vacefron.Client()

    @commands.group(name="meme", usage="meme <meme_type> ...")
    async def meme(self, ctx):
        """
        Allows you to create a meme from the list.
        """
        if ctx.invoked_subcommand is None:
            await ctx.send("Invalid subcommand passed.")

    @meme.command(name="adios", usage="adios [user]")
    async def meme_adios(self, ctx, user: discord.User = None):
        """
        Create a meme with the adios meme.
        """
        await self._meme_with_user(self.vac_api.adios, ctx, user)

    @meme.command(name="eject", aliases=['ejected'], usage="eject [user] [is_impostor]")
    async def meme_eject(self, ctx, user: discord.User = None, is_impostor: bool = False):
        """
        Create amogus eject meme. Kinda sus.
        """
        user = user or ctx.author
        async with ctx.typing():
            eject_image = await self.vac_api.ejected(user.name, impostor=is_impostor)
            await ctx.send(file=discord.File(await eject_image.read(), filename="ejected.png"))
        await ctx.message.delete()

    @meme.command(name="drip", usage="drip [user]")
    async def meme_drip(self, ctx, user: discord.User = None):
        """
        Goku drip meme.
        """
        await self._meme_with_user(self.vac_api.drip, ctx, user)

    @meme.command(name="cmm", usage="cmm [text]")
    async def meme_change_my_mind(self, ctx, *, text: str):
        """
        Create change my mind meme.
        """
        await self._meme_with_text(self.vac_api.change_my_mind, ctx, text)

    @meme.command(name="emergency", usage="emergency [text]")
    async def meme_emergency(self, ctx, *, text: str):
        """
        Create emergency meme from amongus.
        """
        await self._meme_with_text(self.vac_api.emergency_meeting, ctx, text)

    @meme.command(name="firstTime", aliases=['ft'], usage="firstTime [user]")
    async def meme_first_time(self, ctx, user: discord.User = None):
        """
        Create first time meme.
        """
        await self._meme_with_user(self.vac_api.first_time, ctx, user)

    @meme.command(name="grave", usage="grave [user]")
    async def meme_grave(self, ctx, user: discord.User = None):
        """
        When someon got destroyed 💀.
        """
        await self._meme_with_user(self.vac_api.grave, ctx, user)

    @meme.command(name="speed", aliases=['iAmSpeed'], usage="speed [user]")
    async def meme_speed(self, ctx, user: discord.User = None):
        """
        Creates cars i am speed meme.
        """
        await self._meme_with_user(self.vac_api.iam_speed, ctx, user)

    @meme.command(name="milk", usage="milk <user1> [user2]")
    async def meme_milk(self, ctx, user1: discord.User, user2: discord.User = None):
        """
        Generate that "I can milk you" meme from Markiplier with someone's avatar.
        """
        await self._meme_with_two_users(self.vac_api.i_can_milk_you, ctx, user1, user2)

    @meme.command(name="heaven", aliases=['hv', 'hvn'], usage="heaven [user]")
    async def meme_heaven(self, ctx, user: discord.User = None):
        """
        Create heaven meme.
        """
        await self._meme_with_user(self.vac_api.heaven, ctx, user)

    @meme.command(name="stonks", usage="stonks [user] [not_stonks]")
    async def meme_stonks(self, ctx, user: discord.User = None, not_stonks: bool = False):
        """
        Create stonks meme.
        """
        user = user or ctx.author
        async with ctx.typing():
            stonks_image = await self.vac_api.stonks(user.display_avatar.url, stonks=not_stonks)
            await ctx.send(file=discord.File(await stonks_image.read(), filename="stonks.png"))
        await ctx.message.delete()

    @meme.command(name="wolverine", aliases=['wolverin', 'wvn', 'wolv'], usage="wolverine [user]")
    async def meme_wolverine(self, ctx, user: discord.User = None):
        """
        Create wolverine meme.
        """
        await self._meme_with_user(self.vac_api.wolverine, ctx, user)

    @meme.command(name="womanYellingCat", aliases=['wyc'], usage="womanYellingCat <woman> <cat>")
    async def meme_woman_yelling_at_cat(self, ctx, woman: discord.User, cat: discord.User):
        """
        Generate that "woman yelling at cat" meme.
        """
        await self._meme_with_two_users(self.vac_api.woman_yelling_at_cat, ctx, woman, cat)

    async def _meme_with_text(self, func, ctx, text):
        async with ctx.typing():
            meme_image = await func(text)
            deletes_at = datetime.datetime.now() + datetime.timedelta(minutes=5)
            await ctx.send(f'Autodestruction <t:{int(deletes_at.timestamp())}:R>', file=discord.File(await meme_image.read(), filename="meme.png"), delete_after=300)
        await ctx.message.delete()

    async def _meme_with_two_users(self, func, ctx, user1, user2):
        avatar2 = user2.display_avatar.url if user2 else None
        async with ctx.typing():
            meme_image = await func(user1.display_avatar.url, avatar2)
            deletes_at = datetime.datetime.now() + datetime.timedelta(minutes=5)
            await ctx.send(f'Autodestruction <t:{int(deletes_at.timestamp())}:R>', file=discord.File(await meme_image.read(), filename="meme.png"), delete_after=300)
        await ctx.message.delete()

    async def _meme_with_user(self, func, ctx, user: discord.User):
        user = user or ctx.author
        async with ctx.typing():
            meme_image = await func(user.display_avatar.url)
            deletes_at = datetime.datetime.now() + datetime.timedelta(minutes=5)
            await ctx.send(f'Autodestruction <t:{int(deletes_at.timestamp())}:R>', file=discord.File(await meme_image.read(), filename="meme.png"), delete_after=300)
        await ctx.message.delete()

    @commands.cooldown(6, 5)
    @commands.command(name='8ball', aliases=['eightball'], usage='8ball <question>')
    async def eightball(self, ctx, *, question):
        """
        AKA 8ball, Ask the bot a question that you dont want the answer to.
        """
        responses = [
            "It is certain.",
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
            "Very doubtful."
        ]
        response = random.choice(responses)
        embed = discord.Embed(
            title=response,
            description=f'Question: {question}',
            colour=discord.Colour.orange()
        )
        embed.set_footer(text=f'Question by {ctx.author}', icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Fun(bot))
