import asyncio
import json
import logging
import platform
import subprocess
from asyncio import TimeoutError
from datetime import datetime, timedelta
from random import choice, seed, shuffle

import nextcord
import psutil
from nextcord import MessageType
from nextcord.ext import commands, tasks
from nextcord.ext.commands import BucketType
from pytz import timezone

from utils import db, store

logger = logging.getLogger(__name__)

class Util(commands.Cog):

    COG_EMOJI = "📦"

    def __init__(self, bot):
        self.bot = bot
        self.bug_channel = bot.get_channel(876412993498398740)
        self.suggestion_channel = bot.get_channel(876413286978031676)
        self.accept_emoji = ':greenTick:876177251832590348'
        self.deny_emoji = ':redCross:876177262813278288'
        self.giveaway_task.start()

    @commands.group(aliases=["give"], invoke_without_command=True)
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def giveaway(self, ctx):
        """
        Allows you to create giveaways on the server.
        If you want to create a giveaway, check the `giveaway create` command.
        """
        await ctx.send_help(ctx.command)

    @giveaway.command(aliases=["c"], usage="create [hosted_by]")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def create(self, ctx, hosted_by: nextcord.Member = None):
        """
        Allows you to create a giveaway. Requires manage_channels permission.
        After calling this command, you will be asked to enter the prize, the time, and the channel.
        To set the host of the giveaway, specify the optional parameter `hosted_by`.

        Example: 
        `<<giveaway create` or `<<giveaway create @user`
        """
        if hosted_by is None:
            hosted_by = ctx.author
        if hosted_by.bot:
            await ctx.send("Sorry. Bots cannot host giveaways. You will be set as the host", delete_after=15)
            hosted_by = ctx.author

        # Ask Questions
        questions = ["Setting up your giveaway. Choose what channel you want your giveaway in?",
                     "For How long should the Giveaway be hosted ? type number followed (m|h|d). Example: `10m`",
                     "What is the Prize?"]
        answers = []

        # Check Author
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        for i, question in enumerate(questions):
            embed = nextcord.Embed(title=f"Question {i}", description=question)
            question_message = await ctx.send(embed=embed)
            try:
                message = await self.bot.wait_for('message', timeout=45, check=check)
            except TimeoutError:
                await question_message.delete()
                return await ctx.send("You didn't answer the questions in Time", delete_after=60)
            answers.append(message.content)
            await question_message.delete()
            try:
                await message.delete()
            except:
                pass

        # Check if Channel Id is valid
        try:
            channel_id = int(answers[0][2:-1])
        except Exception as e:
            await ctx.send(f"The Channel provided was wrong. The channel should be {ctx.channel.mention}")
            return

        channel = await self.bot.fetch_channel(channel_id)
        if not channel.permissions_for(ctx.me).send_messages:
            return await ctx.send("I don't have permission to send messages in that channel", delete_after=60)
        time = self.convert(answers[1])
        # Check if Time is valid
        if time == -1:
            await ctx.send("The Time format was wrong")
            return
        elif time == -2:
            await ctx.send("The Time was not conventional number")
            return
        prize = answers[2]

        await ctx.send(f"Setup finished. Giveaway for **'{prize}'** will be in {channel.mention}")
        embed = self.create_giveaway_embed(hosted_by, prize)
        end = (datetime.now() + timedelta(seconds=time))
        end_string = end.strftime('%d.%m.%Y %H:%M')
        embed.description += f"\nReact with :tada: to enter!\nEnds <t:{int(end.timestamp())}:R>"
        
        embed.set_footer(text=f"Giveway ends on {end_string}")
        newMsg = await channel.send(embed=embed)
        await newMsg.add_reaction("🎉")
        db.session.add(db.giveaway(hosted_by, end, prize, newMsg))
        db.session.commit()

    @giveaway.command(usage="reroll <message_id>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def reroll(self, ctx, message_id: int):
        """
        Allows you to reroll a giveaway if something went wrong.
        Needs to be executed in the channel the giveaway was posted in.
        """
        try:
            msg = await ctx.fetch_message(message_id)
        except Exception as e:
            await ctx.send("The channel or ID mentioned was incorrect")
            return
        users = await msg.reactions[0].users().flatten()
        users.pop(users.index(self.bot.user))
        prize = await self.get_giveaway_prize(ctx, message_id)
        embed = self.create_giveaway_embed(ctx.author, prize)
        random_seed_value = datetime.now().timestamp()
        if len(users) <= 0:
            embed.set_footer(text="No one won the Giveaway")
        elif len(users) > 0:
            seed(random_seed_value + ctx.message.id)
            winner = choice(users)
            embed.add_field(name=f"Congratulations on winning {prize}", value=winner.mention)
            await msg.channel.send(f'Congratulations {winner.mention}! You won **{prize}**!')
        await msg.edit(embed=embed)

    @giveaway.command(aliases=["cancel"], usage="stop <message_id>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def stop(self, ctx, message_id: int):
        """
        Allows you to stop a giveaway. Takes the ID of the giveaway message as an argument.
        """
        # delete giveaway from db
        giveaway = db.session.query(db.giveaway).filter(db.giveaway.discord_message_id == message_id).first()
        if giveaway is None:
            return await ctx.send("The message ID provided was wrong")
        db.session.delete(giveaway)
        db.session.commit()
        msg = await ctx.fetch_message(message_id)
        newEmbed = nextcord.Embed(title="Giveaway Cancelled", description="The giveaway has been cancelled!!")
        await msg.edit(embed=newEmbed)

    @tasks.loop(seconds=45.0)
    async def giveaway_task(self):
        giveaways = db.session.query(db.giveaway).all()
        random_seed_value = datetime.now().timestamp()
        for giveaway in giveaways:
            if datetime.now() < giveaway.end_date:
                return
            channel = self.bot.get_channel(giveaway.discord_channel_id)
            try:
                message = await channel.fetch_message(giveaway.discord_message_id)
            except nextcord.NotFound as e:
                db.session.delete(giveaway)
                db.session.commit()
                return await channel.send("Could not find the giveaway message! Deleting the giveaway.", delete_after=180)
            users = await message.reactions[0].users().flatten()
            author = await self.bot.fetch_user(giveaway.discord_user_id)
            prize = giveaway.prize
            embed = self.create_giveaway_embed(author, prize)

            users.pop(users.index(self.bot.user))
            # Check if User list is not empty
            if len(users) <= 0:
                embed.remove_field(0)
                embed.set_footer(text="No one won the Giveaway")
                await channel.send('No one won the Giveaway')
            elif len(users) > 0:
                seed(random_seed_value + giveaway.discord_message_id)
                winner = choice(users)
                embed.add_field(name=f"Congratulations on winning {prize}", value=winner.mention)
                await channel.send(f'Congratulations {winner.mention}! You won **{prize}**!')
            await message.edit(embed=embed)
            db.session.query(db.giveaway).filter_by(discord_message_id=message.id).delete()
            db.session.commit()

    async def get_giveaway_prize(self, ctx, message_id: int):
        try:
            msg = await ctx.fetch_message(message_id)
        except Exception as e:
            await ctx.send("The channel or ID mentioned was incorrect")
        return msg.embeds[0].description.split("Win **")[1].split("**!")[0]

    def convert(self, time):
        pos = ["m", "h", "d"]
        time_dict = {"m": 60, "h": 3600, "d": 24*3600}
        unit = time[-1]
        if unit not in pos:
            return -1
        try:
            timeVal = int(time[:-1])
        except Exception as e:
            return -2
        return timeVal*time_dict[unit]

    def create_giveaway_embed(self, author, prize):
        embed = nextcord.Embed(title=":tada: Giveaway :tada:",
                        description=f"Win **{prize}**!",
                        color=0x00FFFF)
        embed.add_field(name="Hosted By:", value=author.mention)
        return embed

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: nextcord.RawReactionActionEvent):
        if payload.guild_id is None or payload.channel_id is None or payload.member is None or payload.message_id is None:
            return
        if payload.member.bot:
            return
        if payload.channel_id == self.bug_channel.id:
            message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
            if message.author != self.bot.user:
                return
            if self.accept_emoji in str(payload.emoji):
                await self.send_accepted_user_reply(payload, 'bug')
            if self.deny_emoji in str(payload.emoji):
                await self.send_denied_user_reply(payload, 'bug')
        if payload.channel_id == self.suggestion_channel.id:
            message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
            if message.author != self.bot.user:
                return
            if self.accept_emoji in str(payload.emoji):
                await self.send_accepted_user_reply(payload, 'suggestion')
            if self.deny_emoji in str(payload.emoji):
                await self.send_denied_user_reply(payload, 'suggestion')

    @commands.group(aliases=['report'], invoke_without_command=True)
    async def submit(self, ctx):
        """
        Allows you to report a bug or suggest a feature or an improvement to the developer team.
        After submitting your bug, you will be able to see if it has been accepted or denied.
        Check out the `bug` and `suggestion` subcommands for more information.
        """
        await ctx.send("Please use the `bug` or `suggestion` subcommands to submit a bug or suggestion.", delete_after=10)

    @submit.command(usage='bug <message>')
    @commands.cooldown(2, 900, BucketType.user)
    async def bug(self, ctx, *words: str):
        """
        If you find a bug in the bot, use this command to submit it to the developers.
        The best way you can help is by saying what you were doing when the bug happened and what you expected to happen.

        Example:
        `<<submit bug When I used the command `<<help` I expected to see a list of commands. But instead I got a list of bugs.`
        """
        sentence = " ".join(words[:])
        if len(sentence) <= 20:
            await self.submission_error(ctx, sentence)
        else:
            await self.send_submission(ctx, self.bug_channel, sentence, ctx.command.name)
        await ctx.message.delete()

    @submit.command(aliases=['improvement','better'], usage='suggestion <message>')
    @commands.cooldown(2, 900, BucketType.user)
    async def suggestion(self, ctx, *words: str):
        """
        If you think something doesn't work well or something could be improved use this command to submit it to the developers.
        You can just describe what you want it to do.

        Example:
        `<<submit suggestion I would like to be able to change the bot's prefix.`
        """
        sentence = " ".join(words[:])
        if len(sentence) <= 10:
            await self.submission_error(ctx, sentence)
        else:
            await self.send_submission(ctx, self.suggestion_channel, sentence, ctx.command.name)
        await ctx.message.delete()

    async def submission_error(self, ctx, sentence):
        embed = nextcord.Embed(
            title='Submission error',
            description=f'Your message is too short: {len(sentence)} characters',
            color=nextcord.Colour.red()
        )
        await ctx.send(embed=embed, delete_after=15)

    async def send_submission(self, ctx, channel, sentence, submission_type):
        embed = nextcord.Embed(
            title=f'New {submission_type} submission',
            description=f'```{sentence}```\nSubmitted by: {ctx.author.mention}',
            color=0x1E9FE3
        )
        embed.set_footer(text=ctx.author.id, icon_url=ctx.author.avatar.url)
        message = await channel.send(embed=embed)
        await ctx.send(f'Thank you for submitting the {submission_type}!', delete_after=30)
        await message.add_reaction(f'<{self.accept_emoji}>')
        await message.add_reaction(f'<{self.deny_emoji}>')

    async def send_accepted_user_reply(self, payload, submission_type):
        await self.send_user_reply(payload, submission_type, f'**accepted** <{self.accept_emoji}>')

    async def send_denied_user_reply(self, payload, submission_type):
        await self.send_user_reply(payload, submission_type, f'**denied** <{self.deny_emoji}>')

    async def send_user_reply(self, payload, submission_type, action):
        channel = await self.bot.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
        embed = message.embeds[0]
        user = await self.bot.fetch_user(int(embed.footer.text))
        new_embed = nextcord.Embed(
            title=f'{submission_type} submission',
            description=embed.description,
            color=nextcord.Colour.red() if 'denied' in action else nextcord.Colour.green()
        )
        await user.send(content=f'Hello {user.name}!\nYour {self.bot.user.mention} {submission_type} submission has been {action}.\n', embed=new_embed)
        embed.color = nextcord.Colour.red() if 'denied' in action else nextcord.Colour.green()
        await message.edit(embed=embed)

    @bug.error
    async def command_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            em = nextcord.Embed(
                title=f"Slow it down!",
                description=f"Try again in {error.retry_after:.2f}s.",
                color=nextcord.Colour.red())
            await ctx.send(embed=em, delete_after=30)
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.channel.send('Missing the bug description', delete_after=30)

    @suggestion.error
    async def command_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            em = nextcord.Embed(
                title=f"Slow it down!",
                description=f"Try again in {error.retry_after:.2f}s.",
                color=nextcord.Colour.red())
            await ctx.send(embed=em, delete_after=30)
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.channel.send('Missing the suggestion description', delete_after=30)
        await ctx.message.delete()

    @commands.cooldown(6, 5)
    @commands.command(aliases=['avatar'])
    async def av(self, ctx, member: nextcord.Member = None):
        """
        Enlarge and view your profile picture or another member
        """
        await ctx.message.delete()
        member = ctx.author if member is None else member
        embed = nextcord.Embed(
            title=f"{str(member.display_name)}'s avatar",
            url=member.avatar.url,
            color=0x1E9FE3
        )
        embed.set_image(url=member.avatar.url)
        await ctx.channel.send(embed=embed)

    @commands.group(aliases=['c'], invoke_without_command = True)
    @commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
    async def clear(self, ctx, amount: int = None):
        """
        Clears messages within the current channel.
        """
        if ctx.message.type == MessageType.reply:
            message = ctx.message.reference.resolved
            if message:
                await message.delete()
                await ctx.message.delete()
            return
        if amount is None:
            return await ctx.send(f'Please specify the amount of messages to delete.', delete_after=15)
        if amount >= 100:
            return await ctx.channel.send('Cannot delete more than 100 messages at a time!')
        await ctx.channel.purge(limit=amount + 1)

    @clear.command(aliases=['bot', 'b'])
    @commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
    async def clear_bot(self, ctx, amount: int):
        """Clears the bot's messages even in DMs"""
        messages = await ctx.channel.history(limit=amount + 1).flatten()
        bots_messages = [m for m in messages if m.author == self.bot.user]

        if len(bots_messages) <= 100 and type(ctx.channel) == nextcord.TextChannel:
            await ctx.channel.delete_messages(bots_messages)

        elif type(ctx.channel) == nextcord.DMChannel:
            for message in bots_messages:
                await message.delete()
                await asyncio.sleep(0.75)
        

    @clear.error
    async def clear_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.channel.send('Please put an amount to clear.')

    @commands.command(aliases=['dink'])
    async def ping(self, ctx):
        """
        Shows the ping of the bot
        """
        title = 'Pong!'
        if 'dink' in ctx.message.content.lower():
            title = 'Donk!'
        embed = nextcord.Embed(title=f'{title} 🏓', description=f'⏱️Ping: `{round(self.bot.latency*1000)}`ms', color=0xE3621E)
        await ctx.message.delete()
        await ctx.send(embed=embed)

    @commands.command(hidden=True)
    async def specialThanks(self, ctx):
        peeople_who_helped = ["<@205704051856244736>", "<@812414532563501077>", "<@299478604809764876>", "<@291291715598286848>", "<@224618877626089483>", "<@231151428167663616>, <@153929916977643521>"]
        shuffle(peeople_who_helped)
        embed = nextcord.Embed(
            title="Special thanks for any help to those people",
            description = f" ".join(peeople_who_helped),
            color=0xE3621E
        )
        await ctx.message.delete()
        await ctx.channel.send(embed=embed, delete_after=120)

    @commands.command()
    async def info(self, ctx):
        """
        Shows different technical information about the bot
        """
        bot_time = time_up((datetime.now() - store.SCRIPT_START).total_seconds()) #uptime of the bot
        last_commit_date = subprocess.check_output(['git', 'log', '-1', '--date=format:"%Y/%m/%d"', '--format=%ad']).decode('utf-8').strip().strip('"')
        cpu_percent = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        ram_used = format_bytes((ram.total - ram.available))
        ram_percent = psutil.virtual_memory().percent
        with open(store.SETTINGS_PATH, "r") as settings:
            self.settings = json.load(settings)
        ver = self.settings['version']
        patch = subprocess.check_output(['git', 'rev-list', f'{self.settings["last_update"]}..HEAD', '--count']).decode('utf-8').strip()
        bot_version = f"{ver['major']}.{ver['minor']}.{patch}"

        content = f'**Instance uptime:** `{bot_time}`\n' \
            f'**Version:** `{bot_version}` | **Updated:** `{last_commit_date}`\n' \
            f'**Python:** `{platform.python_version()}` | **nextcord:** `{nextcord.__version__}`\n\n' \
            f'**CPU:** `{cpu_percent}%` | **RAM:** `{ram_used} ({ram_percent}%)`\n\n' \
            f'**Made by:** <@{self.bot.owner_id}>' 

        embed = nextcord.Embed(
            title=f'Info about {self.bot.user.display_name}',
            description=content, color=0xE3621E,
            timestamp=datetime.now(timezone("Europe/Zurich"))
        )
        embed.set_thumbnail(url=self.bot.user.avatar.url)
        embed.set_footer(text=f"Requested by by {ctx.author.display_name}", icon_url=ctx.author.avatar.url)
        await ctx.channel.send(embed=embed)
        await ctx.message.delete()


def time_up(t):
    if t <= 60:
        return f"<1 minute"
    elif 3600 > t > 60:
        minutes = t // 60
        return f"{int(minutes)} minutes"
    elif t >= 3600:
        hours = t // 3600  # Seconds divided by 3600 gives amount of hours
        minutes = (t % 3600) // 60  # The remaining seconds are looked at to see how many minutes they make up
        if hours >= 24:
            days = hours // 24
            hours = hours % 24
            return f"{int(days)} days, {int(hours)} hours, {int(minutes)} minutes"
        return f"{int(hours)} hours, {int(minutes)} minutes"

def format_bytes(size: int) -> str:
    # 2**10 = 1024
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power:
        size /= power
        n += 1
    return f'{round(size, 2)}{power_labels[n]}'


def setup(bot):
    bot.add_cog(Util(bot))
