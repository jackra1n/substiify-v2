import logging

import nextcord
from nextcord.ext import commands

from utils import db, store

logger = logging.getLogger(__name__)

class Karma(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.member.bot:
            return
        if payload.emoji == store.UPVOTE_EMOTE:
            # get author of the reacted message
            message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
            user = message.author
            if user.bot:
                return
            self.add_karama(user.id, 1)

            
        elif payload.emoji == store.DOWNVOTE_EMOTE:
            await self.vote(payload, -1)


    def add_karma(self, user_id, karma):
        if user_id not in self.karma:
            self.karma[user_id] = 0
        self.karma[user_id] += karma
