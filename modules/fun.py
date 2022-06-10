import asyncio
import datetime
import logging
import random
from enum import Enum

import discord
import vacefron
from discord.ext import commands
from discord.ext.commands.cooldowns import BucketType

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

    # TODO: split the two texts as parameters
    # @meme.command(name="npc", usage="npc [text] [text2]")
    # async def meme_npc(self, ctx, *, text: str, text2: str = None):
    #     """
    #     Create npc meme.
    #     """
    #     async with ctx.typing():
    #         npc_image = await self.vac_api.npc(text, text2)
    #         await ctx.send(file=discord.File(await npc_image.read(), filename="npc.png"))
    #     await ctx.message.delete()

    @meme.command(name="stonks", usage="stonks [user] [not_stonks]")
    async def meme_stonks(self, ctx, user: discord.User = None, not_stonks: bool = False):
        """
        Create stonks meme.
        """
        user = user or ctx.author
        async with ctx.typing():
            stonks_image = await self.vac_api.stonks(user.avatar_url, not_stonks)
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
        avatar2 = user2.avatar_url if user2 else None
        async with ctx.typing():
            meme_image = await func(user1.avatar_url, avatar2)
            deletes_at = datetime.datetime.now() + datetime.timedelta(minutes=5)
            await ctx.send(f'Autodestruction <t:{int(deletes_at.timestamp())}:R>', file=discord.File(await meme_image.read(), filename="meme.png"), delete_after=300)
        await ctx.message.delete()

    async def _meme_with_user(self, func, ctx, user: discord.User):
        user = user or ctx.author
        async with ctx.typing():
            meme_image = await func(user.avatar_url)
            deletes_at = datetime.datetime.now() + datetime.timedelta(minutes=5)
            await ctx.send(f'Autodestruction <t:{int(deletes_at.timestamp())}:R>', file=discord.File(await meme_image.read(), filename="meme.png"), delete_after=300)
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
        embed = discord.Embed(
            title = response,
            description=f'Question: {question}',
            colour = discord.Colour.orange()
        )
        embed.set_footer(text=f'Question by {ctx.author}', icon_url=ctx.author.avatar_url)
        await ctx.channel.send(embed=embed)

    
    @commands.command(brief='Fight someone on this server!')
    @commands.max_concurrency(1, per=BucketType.default, wait=True)
    async def fight(self, ctx, member: discord.Member):
        """
        Allows you to fight someone on this server! It's a turn based fighting game. 
        To start the fight, you must use the command `<<fight @member` where @member is the person you want to fight.

        If you and your opponent select a class, the game will start.
        You can choose to punch, defend, or end the fight.

        Classes have different stats and depending on your action, you can either deal more damage or have higher defense.
        
        """
        duel_authors_id = ctx.author.id
        duel_authors_name = ctx.author.display_name
        challenge_member_name = member.display_name
        challenge_member_id = member.id
        bot_id = self.bot.user.id

        # fighting yourself? Loser.
        if duel_authors_id == challenge_member_id:
            replys = ['Dumbass.. You cant challenge yourself! 🤡', 'LOL! IDIOT! 🤣',
                      'Homie... Chillax. Stop beefing with yo self 👊', 'You good bro? 😥', 'REEEEELLLAAAAXXXXXXXXX 😬',
                      'Its gonna be okay 😔']
            await ctx.channel.send(random.choice(replys))

        # fighting the bot? KEKW
        elif challenge_member_id == bot_id:
            replys = ['Simmer down buddy 🔫', 'You dare challenge thy master?! 💪', 'OK homie relax.. 💩',
                      'You aint even worth it dawg 🤏', 'You a one pump chump anyway 🤡', 'HA! Good one. 😂',
                      'You done yet? Pussy.']
            await ctx.channel.send(random.choice(replys))

        # fighting other users
        else:
            embed = discord.Embed(
                title='⚔️ ' + duel_authors_name + ' choose your class.',
                description=ctx.author.mention +
                    'what class do you want to be? `berserker`, `tank` or `wizard`?',
                color=discord.Colour.red()
            )
            await ctx.channel.send(embed=embed)

            warrior1 = await self.createWarrior(ctx, ctx.author)

            embed = discord.Embed(
                title='⚔️ ' + duel_authors_name + ' has challenged ' + challenge_member_name + ' to a fight!',
                description=duel_authors_name + ' chose class ' + warrior1.ClassName + '. ' + member.mention +
                    ', what is your class of choice? `berserker`,`tank`, or `wizard`?\nType your choice out in chat as it is displayed!',
                color=discord.Colour.red()
            )
            await ctx.channel.send(embed=embed)

            warrior2 = await self.createWarrior(ctx, member)
            await ctx.channel.send(
                warrior2.user.mention + ', what would like to do? `punch`,`defend`, or `end`?\nType your choice out in chat as it is displayed!')

            fight_turn = 0

            while warrior1.Health > 0 and warrior2.Health > 0:
                if await self.checkForWinner(warrior2, warrior1, ctx, fight_turn):
                    break
                if await self.checkForWinner(warrior1, warrior2, ctx, fight_turn):
                    break
            if warrior1.Health < 0:
                await self.sendWinnerEmbed(warrior2, ctx)
            elif warrior2.Health < 0:
                await self.sendWinnerEmbed(warrior1, ctx)
            else:
                await ctx.channel.send("Dude, imagine surrendering")

    async def checkForWinner(self, warrior1, warrior2, ctx, fight_turn):
        if await self.getActionResult(warrior1, warrior2, ctx) or warrior1.Health < 0 or warrior2.Health < 0:
            return True
        fight_turn += 1
        await ctx.channel.send(
            warrior2.user.mention + ', what would like to do? `punch`,`defend`, or `end`?\nType your choice out in chat as it is displayed!')


    async def sendWinnerEmbed(self, winner, ctx):
        winEmbedMessage = discord.Embed(
            title='STOP! STOP! STOP! THE FIGHT IS OVER!!!',
            description=f'**{winner.user.display_name}** wins with just `{str(winner.Health)} HP` left!',
            color=discord.Colour.teal())
        await ctx.channel.send(embed=winEmbedMessage)


    def checkClassChooser(self, author):
        def inner_check(message):
            return message.content in ['berserker', 'tank', 'wizard'] and message.author == author

        return inner_check

    def checkAction(self, author):
        def inner_check(message):
            return (message.content == 'punch' or message.content == 'defend' or message.content == 'end' or (
                        message.content == "detroit smash!" and self.bot.is_owner(author))) and message.author == author
        return inner_check

    async def createWarrior(self, ctx, user):
        try:
            msgClass=await self.bot.wait_for('message', check=self.checkClassChooser(user), timeout=40.0)
            warrior=Warrior(msgClass.author)
            if msgClass.content == 'berserker':
                warrior.chooseClass(1)
            elif msgClass.content == 'tank':
                warrior.chooseClass(2)
            elif msgClass.content == 'wizard':
                warrior.chooseClass(3)
            return warrior
        except asyncio.TimeoutError:
            await ctx.channel.send('Time out!')

    async def getActionResult(self, warrior1, warrior2, ctx):
        try:
            action=await self.bot.wait_for("message", check=self.checkAction(warrior1.user), timeout=40.0)
            buff_bonus=20
            if action.content == "punch":
                attack=random.randrange(0, warrior1.AttkMax) + buff_bonus
                defense=random.randrange(0, warrior1.BlckMax)
            elif action.content == "defend":
                attack=random.randrange(0, warrior1.AttkMax)
                defense=random.randrange(0, warrior1.BlckMax) + buff_bonus
            elif action.content == "end":
                return True
            elif action.content == "detroit smash!" and await self.bot.is_owner(action.author):
                attack=random.randrange(0, warrior1.AttkMax) + 2000
                defense=random.randrange(0, warrior1.BlckMax)
            attack_damage=attack - random.randrange(warrior2.BlckMax)
            counter_damage=random.randrange(0, warrior2.AttkMax) - defense

            await self.calculateDamage(ctx, warrior1, warrior2, attack_damage)
            await self.calculateDamage(ctx, warrior2, warrior1, counter_damage)
        except asyncio.TimeoutError:
            await ctx.channel.send('action timed out!')
            return True

    async def calculateDamage(self, ctx, warrior1, warrior2, damage):
        if damage <= 0:
            await ctx.channel.send("**" + warrior2.user.display_name.strip('<>') + "** blocked the attack!")
        else:
            hit_response = ['cRaZyy', 'pOwerful', 'DEADLY', 'dangerous', 'deathly', 'l33t', 'amazing']
            await ctx.channel.send('**' + warrior1.user.display_name.strip('<>') + '** lands a ' + random.choice(
                hit_response) + ' hit on **' + warrior2.user.display_name.strip('<>') + '** dealing `' + str(damage) + '` damage!')
            warrior2.Health -= damage
            await ctx.channel.send(
                '**' + warrior2.user.display_name.strip('<>') + '**  is left with `' + str(warrior2.Health) + '` health!')

    @fight.error
    async def fight_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.channel.send('Who you tryna fight, the air?! Choose someone to fight you pleb! 🤡')


class Warrior:
    def __init__(self, user, health=1, attkMax=1, blckMax=1, mana=1, className=1):
        self.user=user
        self.Health=health
        self.AttkMax=attkMax
        self.BlckMax=blckMax
        self.Mana=mana
        self.ClassName=className

    def chooseClass(self, className):
        if className == 1:
            self.Health=1000
            self.AttkMax=140
            self.BlckMax=30
            self.Mana=30
            self.ClassName=WarriorClasses(1).name
        elif className == 2:
            self.Health=1200
            self.AttkMax=100
            self.BlckMax=60
            self.Mana=20
            self.ClassName=WarriorClasses(2).name
        elif className == 3:
            self.Health=700
            self.AttkMax=200
            self.BlckMax=20
            self.Mana=50
            self.ClassName=WarriorClasses(3).name


class WarriorClasses(Enum):
    BERSERKER=1
    TANK=2
    WIZARD=3


def setup(bot):
    bot.add_cog(Fun(bot))
