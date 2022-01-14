import logging

import nextcord
from nextcord.ext import commands

from utils import db, store

logger = logging.getLogger(__name__)

class Karma(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(invoke_without_command=True)
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
        embed = nextcord.Embed(title=f'Karma - {ctx.guild.name}', description=f'{user.mention} has {user_karma} karma.')
        await ctx.send(embed=embed, delete_after=20)
        await ctx.message.delete()

    @karma.group(name='emotes', invoke_without_command=True)
    async def karma_emotes(self, ctx):
        karma_emotes = db.session.query(db.karma_emote).filter_by(guild_id=ctx.guild.id).group_by(db.karma_emote.action).all()
        if len(karma_emotes) == 0:
            return await ctx.send(embed=nextcord.Embed(title='No emotes found.'), delete_after=120)
        embed_string = ''
        last_action = ''
        for emote in karma_emotes:
            if emote.action != last_action:
                embed_string += f'\n{emote.action}: '
                last_action = emote.action
            embed_string += f'{self.bot.get_emoji(emote.emote_id)} '
        embed = nextcord.Embed(title=f'Karma Emotes - {ctx.guild.name}', description=embed_string)
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @karma_emotes.command(name='add')
    async def karma_emote_add(self, ctx, emote: nextcord.Emoji, emote_action: int):
        if emote_action not in [0, 1]:
            embed = nextcord.Embed(title='Invalid action parameter.')
            return await ctx.send(embed=embed, delete_after=20)
        # check if emote is already in the db
        existing_emote = db.session.query(db.karma_emote).filter_by(emote_id=emote.id).filter_by(guild_id=ctx.guild.id).first()
        if existing_emote is not None:
            embed = nextcord.Embed(title='That emote is already added.')
            return await ctx.send(embed=embed, delete_after=20)
        max_emotes = db.session.query(db.karma_emote).filter_by(guild_id=ctx.guild.id).count()
        if max_emotes >= 10:
            embed = nextcord.Embed(title='You can only have 10 emotes.')
            return await ctx.send(embed=embed, delete_after=20)
        db.session.add(db.karma_emote(ctx.guild.id, emote.id, emote_action))
        db.session.commit()
        embed = nextcord.Embed(title=f'Emote {emote} added to the list.')
        await ctx.send(embed=embed, delete_after=20)
        await ctx.message.delete()

    @karma_emotes.command(name='remove')
    async def karma_emote_remove(self, ctx, emote: nextcord.Emoji):
        existing_emote = db.session.query(db.karma_emote).filter_by(emote_id=emote.id).filter_by(guild_id=ctx.guild.id).first()
        if existing_emote is None:
            embed = nextcord.Embed(title='That emote is not in the list.')
            return await ctx.send(embed=embed, delete_after=20)
        db.session.delete(existing_emote)
        db.session.commit()
        embed = nextcord.Embed(title=f'Emote {emote} removed from the list.')
        await ctx.send(embed=embed, delete_after=20)
        await ctx.message.delete()

    @karma.command(name='leaderboard', aliases=['lb'])
    async def karma_leaderboard(self, ctx, global_leaderboard: str = None):
        embed = nextcord.Embed(title='Karma Leaderboard')
        if global_leaderboard is None:
            query = db.session.query(db.karma).filter_by(guild_id=ctx.guild.id).order_by(db.karma.amount.desc()).limit(15)
        elif global_leaderboard == 'global': 
            query = db.session.query(db.karma).order_by(db.karma.amount.desc()).limit(15)
        if len(query.all()) == 0:
            embed.description = 'No users have any karma.'
        embed_users = ''
        embed_karma = ''
        for entry in query:
            user = await self.bot.fetch_user(entry.user_id)
            embed_users += f'{user.mention}\n'
            embed_karma += f'{entry.amount}\n'
        embed.add_field(name='Users', value=embed_users)
        embed.add_field(name='Karma', value=embed_karma)        
        await ctx.send(embed=embed)
        await ctx.message.delete()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        user = await self.check_payload(payload)
        if str(payload.emoji) == store.UPVOTE_EMOTE:
            self.change_karma(user.id, payload.guild_id, 1) 
        elif str(payload.emoji) == store.DOWNVOTE_EMOTE:
            self.change_karma(user.id, payload.guild_id, -1)
        elif db.session.query(db.karma_emote.emote_id).filter_by(guild_id=payload.guild_id).filter_by(action=0).first() is not None:
            self.change_karma(user.id, payload.guild_id, 1) 
        elif db.session.query(db.karma_emote.emote_id).filter_by(guild_id=payload.guild_id).filter_by(action=1).first() is not None:
            self.change_karma(user.id, payload.guild_id, -1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        user = await self.check_payload(payload)
        if str(payload.emoji) == store.UPVOTE_EMOTE:
            self.change_karma(user.id, payload.guild_id, -1) 
        elif str(payload.emoji) == store.DOWNVOTE_EMOTE:
            self.change_karma(user.id, payload.guild_id, 1)
        elif db.session.query(db.karma_emote.emote_id).filter_by(guild_id=payload.guild_id).filter_by(action=0).first() is not None:
            self.change_karma(user.id, payload.guild_id, -1) 
        elif db.session.query(db.karma_emote.emote_id).filter_by(guild_id=payload.guild_id).filter_by(action=1).first() is not None:
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
