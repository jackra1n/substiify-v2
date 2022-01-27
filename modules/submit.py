import json
import logging

import nextcord
from nextcord.ext import commands
from nextcord.ext.commands import BucketType

from utils import store

logger = logging.getLogger(__name__)

class Submit(commands.Cog):

    COG_EMOJI = "📝"

    def __init__(self, bot):
        self.bot = bot
        self.bug_channel = bot.get_channel(876412993498398740)
        self.suggestion_channel = bot.get_channel(876413286978031676)
        self.accept_emoji = ':greenTick:876177251832590348'
        self.deny_emoji = ':redCross:876177262813278288'
        with open(store.SETTINGS_PATH, "r") as settings:
            self.settings = json.load(settings)

    async def submission_error(self, ctx, sentence):
        embed = nextcord.Embed(
            title='Submission error',
            description=f'Your message is too short: {len(sentence)} characters',
            colour=nextcord.Colour.red()
        )
        await ctx.send(embed=embed, delete_after=15)

    async def send_submission(self, ctx, channel, sentence, submission_type):
        embed = nextcord.Embed(
            title=f'New {submission_type} submission',
            description=f'```{sentence}```\nSubmitted by: {ctx.author.mention}',
            colour=nextcord.Colour.red()
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
            colour=nextcord.Colour.red()
        )
        await user.send(content=f'Hello {user.name}!\nYour {self.bot.user.mention} {submission_type} submission has been {action}.\n', embed=new_embed)
        await message.delete()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: nextcord.RawReactionActionEvent):
        if payload.guild_id is None or payload.channel_id is None or payload.member is None or payload.message_id is None:
            return
        if payload.member.bot:
            return
        if payload.channel_id == self.bug_channel.id:
            if self.accept_emoji in str(payload.emoji):
                await self.send_accepted_user_reply(payload, 'bug')
            if self.deny_emoji in str(payload.emoji):
                await self.send_denied_user_reply(payload, 'bug')
        if payload.channel_id == self.suggestion_channel.id:
            if self.accept_emoji in str(payload.emoji):
                await self.send_accepted_user_reply(payload, 'suggestion')
            if self.deny_emoji in str(payload.emoji):
                await self.send_denied_user_reply(payload, 'suggestion')

    @commands.group()
    async def submit(self, ctx):
        pass

    @submit.command()
    @commands.cooldown(2, 900, BucketType.user)
    async def bug(self, ctx, *words: str):
        """
        If you find a bug in the bot, use this command to submit it to the developers.
        The best way you can help is by saying what you were doing when the bug happened and what you expected to happen.

        Example:
        `<<submit bug When I used the command `<<help` I expected to see a list of commands. But instead I got a list of bugs.`

        After submitting your bug, you will be able to see if it has been accepted or denied.
        """
        sentence = " ".join(words[:])
        if len(sentence) <= 20:
            await self.submission_error(ctx, sentence)
        else:
            await self.send_submission(ctx, self.bug_channel, sentence, ctx.command.name)
        await ctx.message.delete()

    @submit.command()
    @commands.cooldown(2, 900, BucketType.user)
    async def suggestion(self, ctx, *words: str):
        """
        If you think something doesn't work well or something could be improved use this command to submit it to the developers.
        You can just describe what you want it to do.

        Example:
        `<<submit suggestion I would like to be able to change the bot's prefix.`

        After submitting your suggestion, you will be able to see if it has been accepted or denied.
        """
        sentence = " ".join(words[:])
        if len(sentence) <= 10:
            await self.submission_error(ctx, sentence)
        else:
            await self.send_submission(ctx, self.suggestion_channel, sentence, ctx.command.name)
        await ctx.message.delete()

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

def setup(bot):
    bot.add_cog(Submit(bot))
