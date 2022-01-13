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
        await ctx.send(embed=embed, delete_after=120)
        await ctx.message.delete()

    @karma.group(name='emote', invoke_without_command=True)
    async def karma_emote(self, ctx):
        karma_emotes = db.session.query(db.karma_emote).filter_by(guild_id=ctx.guild.id).all()
        if len(karma_emotes) == 0:
            return await ctx.send(embed=nextcord.Embed(title='No emotes found.'), delete_after=120)
        embed = nextcord.Embed(title=f'Karma Emotes - {ctx.guild.name}')
        for emote in karma_emotes:
            # get emote by id
            emote_obj = nextcord.Emoji(emote.emote_id)
            embed.description += f'{emote_obj}, '
        await ctx.send(embed=embed, delete_after=120)
        await ctx.message.delete()

    @karma_emote.command(name='add')
    async def karma_emote_add(self, ctx, emote_id: int, action: str = None):
        print(f'{emote_id}, {action}')
        if action == "add":
            action = 0
        elif action == "remove":
            action = 1
        if action not in [0, 1]:
            embed = nextcord.Embed(title='Invalid action parameter.')
            return await ctx.send(embed=embed, delete_after=120)
        # check if emote is already in the db
        existing_emote = db.session.query(db.karma_emote).filter_by(emote_id=emote_id).filter_by(guild_id=ctx.guild.id).first()
        if existing_emote is not None:
            embed = nextcord.Embed(title='That emote is already in the database.')
            return await ctx.send(embed=embed, delete_after=120)
        db.session.add(db.karma_emote(ctx.guild.id, emote_id, ))
        db.session.commit()
        embed = nextcord.Embed(title=f'Emote {nextcord.Emoji(emote_id)} added to the database.')
        await ctx.send(embed=embed, delete_after=120)


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
