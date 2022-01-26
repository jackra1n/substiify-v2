import json
import logging

import nextcord
import numpy as np
from nextcord.ext import commands

from utils import db, store

logger = logging.getLogger(__name__)

class Votes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.vote_channels = np.array(self.load_vote_channels())
        with open(store.SETTINGS_PATH, "r") as settings:
            self.settings = json.load(settings)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id in self.vote_channels and not message.author.bot:
            await message.add_reaction(self.get_upvote_emote())
            await message.add_reaction(self.get_downvote_emote())

    @commands.group(invoke_without_command=True)
    async def votes(self, ctx):
        await ctx.message.delete()
        if ctx.channel.id in self.vote_channels:
            embed = nextcord.Embed(description=f'Votes are **ALREADY enabled** in {ctx.channel.mention}!', colour=0x23b40c)
            await ctx.send(embed=embed, delete_after=10)
        else:
            embed = nextcord.Embed(description=f'Votes are **NOT enabled** in {ctx.channel.mention}!', colour=0xf66045)
            await ctx.send(embed=embed, delete_after=10)

    @votes.command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def setup(self, ctx, channel: nextcord.TextChannel = None):
        await ctx.message.delete()
        channel = ctx.channel if channel is None else channel
        if channel.id not in self.vote_channels:
            self.vote_channels = np.append(self.vote_channels, channel.id)
        if db.session.query(db.vote_channels).filter_by(server_id=ctx.guild.id).filter_by(channel_id=channel.id).first() is None:
            db.session.add(db.vote_channels(ctx.guild.id, channel.id))
            db.session.commit()
        else:
            embed = nextcord.Embed(
                description=f'Votes are **already active** in {ctx.channel.mention}!',
                colour=0x23b40c
            )
            await ctx.send(embed=embed, delete_after=20)
            return
        embed = nextcord.Embed(
            description=f'Votes **enabled** in {channel.mention}!',
            colour=0x23b40c
        )
        await ctx.send(embed=embed)

    @votes.command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def stop(self, ctx, channel: nextcord.TextChannel = None):
        channel = ctx.channel if channel is None else channel
        db.session.query(db.vote_channels).filter_by(server_id=ctx.guild.id).filter_by(channel_id=channel.id).delete()
        db.session.commit()
        if channel.id in self.vote_channels:
            index = np.argwhere(self.vote_channels==channel.id)
            self.vote_channels = np.delete(self.vote_channels, index)
        await ctx.message.delete()
        await ctx.channel.send(embed=nextcord.Embed(description=f'Votes has been stopped in {channel.mention}!', colour=0xf66045))

    def load_vote_channels(self) -> list:
        channel_array = []
        for entry in db.session.query(db.vote_channels).all():
            channel_array = np.append(channel_array, entry.channel_id)
        return channel_array

    def get_upvote_emote(self):
        return self.bot.get_emoji(store.UPVOTE_EMOTE_ID)

    def get_downvote_emote(self):
        return self.bot.get_emoji(store.DOWNVOTE_EMOTE_ID)


def setup(bot):
    bot.add_cog(Votes(bot))
