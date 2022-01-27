import logging

import nextcord
from nextcord.ext import commands

from utils import db, store

logger = logging.getLogger(__name__)

class Karma(commands.Cog):

    COG_EMOJI = "☯️"

    def __init__(self, bot):
        self.bot = bot

    @commands.group(invoke_without_command=True)
    async def karma(self, ctx, user: nextcord.User = None):
        """
        Shows the karma of a user
        """
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
        """
        Shows the karma emotes of the server
        """
        karma_emotes = db.session.query(db.karma_emote).filter_by(guild_id=ctx.guild.id).order_by(db.karma_emote.action).all()
        if len(karma_emotes) == 0:
            return await ctx.send(embed=nextcord.Embed(title='No emotes found.'), delete_after=120)
        embed_string = ''
        last_action = ''
        for emote in karma_emotes:
            if emote.action != last_action:
                embed_string += f'\n`{emote.action}:` '
                last_action = emote.action
            embed_string += f'{self.bot.get_emoji(emote.emote_id)} '
        embed = nextcord.Embed(title=f'Karma Emotes - {ctx.guild.name}', description=embed_string)
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @karma_emotes.command(name='add')
    async def karma_emote_add(self, ctx, emote: nextcord.Emoji, emote_action: int):
        """
        Add an emote to the karma emotes for this server. Takes an emoji and an action (0 for increase, 1 for reduce karma)
        The votes from this bots Votes module automatically add karma to the user. No need to add those emotes to the emote list.

        Example:
        `<<karma emotes add :upvote: 0` - adds the upvote emote to list as karma increasing emote
        `<<karma emotes add :downvote: 1` - adds the downvote emote to list as karma decreasing emote
        """
        if not await self.has_permissions(ctx):
            return
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
        """
        Remove an emote from the karma emotes for this server.
        """
        if not await self.has_permissions(ctx):
            return
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
        """
        Shows users with the most karma on the server.
        """
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
        if user is None:
            return
        if payload.emoji.id in self.get_query_karma_add(payload.guild_id):
            self.change_user_karma(user.id, payload.guild_id, 1)
            await self.change_post_upvotes(payload, 1)
        elif payload.emoji.id in self.get_query_karma_remove(payload.guild_id):
            self.change_user_karma(user.id, payload.guild_id, -1)
            await self.change_post_downvotes(payload, 1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        user = await self.check_payload(payload)
        if user is None:
            return
        if payload.emoji.id in self.get_query_karma_add(payload.guild_id):
            self.change_user_karma(user.id, payload.guild_id, -1) 
            await self.change_post_upvotes(payload, -1)
        elif payload.emoji.id in self.get_query_karma_remove(payload.guild_id):
            self.change_user_karma(user.id, payload.guild_id, 1)
            await self.change_post_downvotes(payload, -1)

    def get_query_karma_add(self, guild_id):
        query = db.session.query(db.karma_emote.emote_id).filter_by(guild_id=guild_id).filter_by(action=0).all()
        list = [ x[0] for x in query ] if query is not None else []
        list.append(int(store.UPVOTE_EMOTE_ID))
        return list

    def get_query_karma_remove(self, guild_id):
        query = db.session.query(db.karma_emote.emote_id).filter_by(guild_id=guild_id).filter_by(action=1).all()
        list = [ x[0] for x in query ] if query is not None else []
        list.append(int(store.DOWNVOTE_EMOTE_ID))
        return list

    async def check_payload(self, payload):
        if payload.event_type == 'REACTION_ADD' and payload.member.bot:
            return None
        message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
        if message.author.bot:
            return None
        user = await self.bot.fetch_user(payload.user_id)
        if user == message.author:
            return None
        return message.author

    def change_user_karma(self, user_id, guild_id, amount):
        # check if user and guild are in the db
        existing_user = db.session.query(db.karma).filter_by(user_id=user_id).filter_by(guild_id=guild_id).first()
        if existing_user is None:
            db.session.add(db.karma(user_id=user_id, guild_id=guild_id, amount=amount))
        else:
            existing_user.amount += amount
        db.session.commit()

    async def change_post_upvotes(self, payload, amount):
        message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
        existing_post = db.session.query(db.post).filter_by(message_id=message.id).first()
        if existing_post is None:
            db.session.add(db.post(message=message, upvotes=amount, downvotes=0))
        else:
            existing_post.upvotes += amount
        db.session.commit()

    async def change_post_downvotes(self, payload, amount):
        message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
        existing_post = db.session.query(db.post).filter_by(message_id=message.id).first()
        if existing_post is None:
            db.session.add(db.post(message=message, upvotes=0, downvotes=amount))
        else:
            existing_post.downvotes += amount
        db.session.commit()

    async def has_permissions(self, ctx):
        if not ctx.channel.permissions_for(ctx.author).manage_channels and not await self.bot.is_owner(ctx.author):
            await ctx.send("You don't have permissions to do that", delete_after=10)
            return False
        return True

def setup(bot):
    bot.add_cog(Karma(bot))
