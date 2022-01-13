import logging

import nextcord
from nextcord.ext import commands

from utils import db, store

logger = logging.getLogger(__name__)

class Karma(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def karma(self, ctx, user: nextcord.User = None):
        if user is None:
            user = ctx.author
        if user.bot:
            return
        user_karma = db.session.query(db.karma).filter_by(user_id=user.id).filter_by(guild_id=ctx.guild.id).first()
        if user_karma is None:
            user_karma = 0
        else:
            user_karma = user_karma.amount
        embed = nextcord.Embed(
            title=f'Karma - {ctx.guild.name}',
            description=f'{user.mention} has {user_karma} karma.',
            colour=0x23b40c
        )
        await ctx.send(embed=embed, delete_after=20)
        await ctx.message.delete()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        user = await self.check_payload(payload)
        if str(payload.emoji) == store.UPVOTE_EMOTE:
            self.change_karma(user.id, payload.guild_id, 1) 
        elif str(payload.emoji) == store.DOWNVOTE_EMOTE:
            self.change_karma(user.id, payload.guild_id, -1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        user = await self.check_payload(payload)
        if str(payload.emoji) == store.UPVOTE_EMOTE:
            self.change_karma(user.id, payload.guild_id, -1) 
        elif str(payload.emoji) == store.DOWNVOTE_EMOTE:
            self.change_karma(user.id, payload.guild_id, 1)

    async def check_payload(self, payload):
        if payload.event_type == 'REACTION_ADD' and payload.member.bot:
            return
        message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
        user = message.author
        if user.bot:
            return
        return user

    def change_karma(self, user_id, guild_id, amount):
        # check if user and guild are in the db
        existing_user = db.session.query(db.karma).filter_by(user_id=user_id).filter_by(guild_id=guild_id).first()
        if existing_user is None:
            db.session.add(db.karma(user_id=user_id, guild_id=guild_id, amount=amount))
        else:
            existing_user.amount += amount
        db.session.commit()



def setup(bot):
    bot.add_cog(Karma(bot))
