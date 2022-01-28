import logging
import random

import vacefron
import nextcord
from nextcord.ext import commands

logger = logging.getLogger(__name__)

class Fun(commands.Cog):

    COG_EMOJI = "🎱"

    def __init__(self, bot):
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
    async def meme_adios(self, ctx, user: nextcord.User = None):
        """
        Create a meme with the adios meme.
        """
        user = user or ctx.author
        async with ctx.typing():
            adios_image = await self.vac_api.adios(user.avatar.url)
            await ctx.send(file=nextcord.File(await adios_image.read(), filename="adios.png"))
        await ctx.message.delete()

    @meme.command(name="eject", aliases=['ejected'], usage="eject [user] [is_impostor]")
    async def meme_eject(self, ctx, user: nextcord.User = None, is_impostor: bool = False):
        """
        Create amogus eject meme. Kinda sus.
        """
        user = user or ctx.author
        async with ctx.typing():
            eject_image = await self.vac_api.ejected(user.name, impostor=is_impostor)
            await ctx.send(file=nextcord.File(await eject_image.read(), filename="ejected.png"))
        await ctx.message.delete()


    @meme.command(name="drip", usage="drip [user]")
    async def meme_drip(self, ctx, user: nextcord.User = None):
        """
        Goku drip meme.
        """
        user = user or ctx.author
        async with ctx.typing():
            drip_image = await self.vac_api.drip(user.avatar.url)
            await ctx.send(file=nextcord.File(await drip_image.read(), filename="drip.png"))
        await ctx.message.delete()

    @meme.command(name="cmm", usage="cmm [text]")
    async def meme_change_my_mind(self, ctx, *, text: str):
        """
        Create change my mind meme.
        """
        async with ctx.typing():
            cmm_image = await self.vac_api.change_my_mind(text)
            await ctx.send(file=nextcord.File(await cmm_image.read(), filename="cmm.png"))
        await ctx.message.delete()

    @meme.command(name="emergency", usage="emergency [text]")
    async def meme_emergency(self, ctx, *, text: str):
        """
        Create emergency meme from amongus.
        """
        async with ctx.typing():
            emergency_image = await self.vac_api.emergency_meeting(text)
            await ctx.send(file=nextcord.File(await emergency_image.read(), filename="emergency.png"))
        await ctx.message.delete()

    @meme.command(name="firstTime", aliases=['ft'], usage="firstTime [user]")
    async def meme_first_time(self, ctx, user: nextcord.User = None):
        """
        Create first time meme.
        """
        user = user or ctx.author
        async with ctx.typing():
            first_time_image = await self.vac_api.first_time(user.avatar.url)
            await ctx.send(file=nextcord.File(await first_time_image.read(), filename="first_time.png"))
        await ctx.message.delete()

    @meme.command(name="grave", usage="grave [user]")
    async def meme_grave(self, ctx, user: nextcord.User = None):
        """
        When someon got destroyed 💀.
        """
        user = user or ctx.author
        async with ctx.typing():
            grave_image = await self.vac_api.grave(user.avatar.url)
            await ctx.send(file=nextcord.File(await grave_image.read(), filename="grave.png"))
        await ctx.message.delete()

    @meme.command(name="speed", aliases=['iAmSpeed'], usage="speed [user]")
    async def meme_speed(self, ctx, user: nextcord.User = None):
        """
        Creates cars i am speed meme.
        """
        user = user or ctx.author
        async with ctx.typing():
            speed_image = await self.vac_api.iam_speed(user.avatar.url)
            await ctx.send(file=nextcord.File(await speed_image.read(), filename="speed.png"))
        await ctx.message.delete()

    @meme.command(name="milk", usage="milk [user] [user2]")
    async def meme_milk(self, ctx, user: nextcord.User = None, user2: nextcord.User = None):
        """
        Generate that "I can milk you" meme from Markiplier with someone's avatar.
        """
        user = user or ctx.author
        async with ctx.typing():
            if user2 is not None:
                milk_image = await self.vac_api.i_can_milk_you(user.avatar.url, user2.avatar.url)
            else:
                milk_image = await self.vac_api.i_can_milk_you(user.avatar.url)
            await ctx.send(file=nextcord.File(await milk_image.read(), filename="milk.png"))
        await ctx.message.delete()

    @meme.command(name="heaven", aliases=['hv', 'hvn'], usage="heaven [user]")
    async def meme_heaven(self, ctx, user: nextcord.User = None):
        """
        Create heaven meme.
        """
        user = user or ctx.author
        async with ctx.typing():
            heaven_image = await self.vac_api.heaven(user.avatar.url)
            await ctx.send(file=nextcord.File(await heaven_image.read(), filename="heaven.png"))
        await ctx.message.delete()

    # TODO: split the two texts as parameters
    # @meme.command(name="npc", usage="npc [text] [text2]")
    # async def meme_npc(self, ctx, *, text: str, text2: str = None):
    #     """
    #     Create npc meme.
    #     """
    #     async with ctx.typing():
    #         npc_image = await self.vac_api.npc(text, text2)
    #         await ctx.send(file=nextcord.File(await npc_image.read(), filename="npc.png"))
    #     await ctx.message.delete()

    @meme.command(name="stonks", usage="stonks [user] [not_stonks]")
    async def meme_stonks(self, ctx, user: nextcord.User = None, not_stonks: bool = False):
        """
        Create stonks meme.
        """
        user = user or ctx.author
        async with ctx.typing():
            stonks_image = await self.vac_api.stonks(user.avatar.url, not_stonks)
            await ctx.send(file=nextcord.File(await stonks_image.read(), filename="stonks.png"))
        await ctx.message.delete()

    @meme.command(name="wolverine", aliases=['wolverin', 'wvn', 'wolv'], usage="wolverine [user]")
    async def meme_wolverine(self, ctx, user: nextcord.User = None):
        """
        Create wolverine meme.
        """
        user = user or ctx.author
        async with ctx.typing():
            wolverine_image = await self.vac_api.wolverine(user.avatar.url)
            await ctx.send(file=nextcord.File(await wolverine_image.read(), filename="wolverine.png"))
        await ctx.message.delete()

    @meme.command(name="womanYellingCat", aliases=['wyc'], usage="womanYellingCat [user]")
    async def meme_woman_yelling_at_cat(self, ctx, woman: nextcord.User, cat: nextcord.User = None):
        """
        Generate that "woman yelling at cat" meme.
        """
        cat = cat or ctx.author
        async with ctx.typing():
            womanCat_image = await self.vac_api.woman_yelling_at_cat(woman.avatar.url, cat.avatar.url)
            await ctx.send(file=nextcord.File(await womanCat_image.read(), filename="womanCat.png"))
        await ctx.message.delete()


    @commands.cooldown(6, 5)
    @commands.command(name='8ball', aliases=['eightball'], usage='8ball <question>')
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
