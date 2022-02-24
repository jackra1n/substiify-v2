import logging
import numpy as np
from sqlalchemy.sql import func

import nextcord
from nextcord.ext import commands

from utils import db, store

logger = logging.getLogger(__name__)

class Karma(commands.Cog):

    COG_EMOJI = "☯️"

    def __init__(self, bot):
        self.bot = bot
        self.vote_channels = np.array(self.load_vote_channels())

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id in self.vote_channels and not message.author.bot:
            await message.add_reaction(self.get_upvote_emote())
            await message.add_reaction(self.get_downvote_emote())

    @commands.group(invoke_without_command=True)
    async def votes(self, ctx):
        """
        Shows if votes are enabled in the current channel
        """
        await ctx.message.delete()
        if ctx.channel.id in self.vote_channels:
            embed = nextcord.Embed(description=f'Votes are **ALREADY enabled** in {ctx.channel.mention}!', color=0x23b40c)
            await ctx.send(embed=embed, delete_after=10)
        else:
            embed = nextcord.Embed(description=f'Votes are **NOT enabled** in {ctx.channel.mention}!', color=0xf66045)
            await ctx.send(embed=embed, delete_after=10)

    @votes.command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def setup(self, ctx, channel: nextcord.TextChannel = None):
        """
        Enables votes in the current channel. Requires Manage Channels permission.
        After enabling votes, the bot will add the upvote and downvote emojis to every message in the channel.
        This is good for something like a meme channel if you want to give upvotes and downvotes to the messages.

        If users click the reactions user karma will be updated.
        """
        await ctx.message.delete()
        channel = ctx.channel if channel is None else channel
        if channel.id not in self.vote_channels:
            self.vote_channels = np.append(self.vote_channels, channel.id)
        vote_channel = db.session.query(db.discord_channel).filter_by(discord_channel_id=channel.id).filter_by(upvote=True).first()
        if vote_channel is None:
            vote_channel.upvote = True
            db.session.commit()
        else:
            embed = nextcord.Embed(
                description=f'Votes are **already active** in {ctx.channel.mention}!',
                color=0x23b40c
            )
            await ctx.send(embed=embed, delete_after=20)
            return
        embed = nextcord.Embed(
            description=f'Votes **enabled** in {channel.mention}!',
            color=0x23b40c
        )
        await ctx.send(embed=embed)

    @votes.command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def stop(self, ctx, channel: nextcord.TextChannel = None):
        """
        Disables votes in the current channel. Requires Manage Channels permission.
        """
        channel = ctx.channel if channel is None else channel
        db.session.query(db.discord_channel).filter_by(discord_channel_id=channel.id).filter_by(upvote=True).delete()
        db.session.commit()
        if channel.id in self.vote_channels:
            index = np.argwhere(self.vote_channels==channel.id)
            self.vote_channels = np.delete(self.vote_channels, index)
        await ctx.message.delete()
        await ctx.channel.send(embed=nextcord.Embed(description=f'Votes has been stopped in {channel.mention}!', color=0xf66045))

    def load_vote_channels(self) -> list:
        channel_array = []
        for entry in db.session.query(db.discord_channel).filter_by(upvote=True).all():
            channel_array = np.append(channel_array, entry.channel_id)
        return channel_array

    def get_upvote_emote(self):
        return self.bot.get_emoji(store.UPVOTE_EMOTE_ID)

    def get_downvote_emote(self):
        return self.bot.get_emoji(store.DOWNVOTE_EMOTE_ID)


    @commands.group(aliases=["k"], usage="karma [user]", invoke_without_command=True,)
    async def karma(self, ctx, user: nextcord.User = None):
        """
        Shows the karma of a user. If you dont specify a user, it will show your own.
        If you want to know what emote reactions are used for karma, use the subcommand `karma emotes`
        """
        if user is None:
            user = ctx.author
        if user.bot:
            return
        user_karma = db.session.query(db.karma).filter_by(discord_user_id=user.id).filter_by(discord_server_id=ctx.guild.id).first()
        if user_karma is None:
            user_karma = 0
        else:
            user_karma = user_karma.amount
        embed = nextcord.Embed(title=f'Karma - {ctx.guild.name}', description=f'{user.mention} has {user_karma} karma.')
        await ctx.send(embed=embed, delete_after=120)
        await ctx.message.delete()

    @karma.group(name='emotes', aliases=['emote'], usage="emotes", invoke_without_command=True)
    async def karma_emotes(self, ctx):
        """
        Shows the karma emotes of the server. Emotes in the `add` category increase karma, while emotes in the `remove` category decrease karma.
        If you want to add or remove an emote from the karma system, check the subcommand `karma emotes add` or `karma emotes remove`
        """
        karma_emotes = db.session.query(db.karma_emote).filter_by(discord_server_id=ctx.guild.id).order_by(db.karma_emote.action).all()
        if len(karma_emotes) == 0:
            return await ctx.send(embed=nextcord.Embed(title='No emotes found.'), delete_after=60)
        embed_string = ''
        last_action = ''
        for emote in karma_emotes:
            if emote.action != last_action:
                embed_string += f'\n`{"add" if emote.action == 0 else "remove"}:` '
                last_action = emote.action
            embed_string += f'{self.bot.get_emoji(emote.discord_emote_id)} '
        embed = nextcord.Embed(title=f'Karma Emotes - {ctx.guild.name}', description=embed_string)
        await ctx.send(embed=embed, delete_after=60)
        await ctx.message.delete()

    @karma_emotes.command(name='add', usage="add <emote> <action>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def karma_emote_add(self, ctx, emote: nextcord.Emoji, emote_action: int):
        """
        Add an emote to the karma emotes for this server. Takes an emoji and an action (0 for add, 1 for remove karma)
        The votes from this bots Votes module automatically add karma to the user. No need to add those emotes to the emote list.

        Example:
        `<<karma emotes add :upvote: 0` - adds the upvote emote to list as karma increasing emote
        `<<karma emotes add :downvote: 1` - adds the downvote emote to list as karma decreasing emote
        """
        if emote_action not in [0, 1]:
            embed = nextcord.Embed(title='Invalid action parameter.')
            return await ctx.send(embed=embed, delete_after=30)
        # check if emote is already in the db
        existing_emote = db.session.query(db.karma_emote).filter_by(discord_emote_id=emote.id).filter_by(discord_server_id=ctx.guild.id).first()
        if existing_emote is not None:
            embed = nextcord.Embed(title='That emote is already added.')
            return await ctx.send(embed=embed, delete_after=30)
        max_emotes = db.session.query(db.karma_emote).filter_by(discord_server_id=ctx.guild.id).count()
        if max_emotes >= 10:
            embed = nextcord.Embed(title='You can only have 10 emotes.')
            return await ctx.send(embed=embed, delete_after=30)
        db.session.add(db.karma_emote(emote, emote_action))
        db.session.commit()
        embed = nextcord.Embed(title=f'Emote {emote} added to the list.')
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()


    @karma_emotes.command(name='remove', aliases=['delete'], usage="remove <emote>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def karma_emote_remove(self, ctx, emote: nextcord.Emoji):
        """
        Remove an emote from the karma emotes for this server.
        """
        existing_emote = db.session.query(db.karma_emote).filter_by(discord_emote_id=emote.id).filter_by(discord_server_id=ctx.guild.id).first()
        if existing_emote is None:
            embed = nextcord.Embed(title='That emote is not in the list.')
            return await ctx.send(embed=embed, delete_after=20)
        db.session.delete(existing_emote)
        db.session.commit()
        embed = nextcord.Embed(title=f'Emote {emote} removed from the list.')
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @karma.command(name='leaderboard', aliases=['lb'], usage="leaderboard")
    async def karma_leaderboard(self, ctx, global_leaderboard: str = None):
        """
        Shows users with the most karma on the server.
        """
        embed = nextcord.Embed(title='Karma Leaderboard')
        if global_leaderboard is None:
            query = db.session.query(db.karma).filter_by(discord_server_id=ctx.guild.id).order_by(db.karma.amount.desc()).limit(15)
        elif global_leaderboard == 'global': 
            query = db.session.query(db.karma).order_by(db.karma.amount.desc()).limit(15)
        if len(query.all()) == 0:
            embed.description = 'No users have any karma.'
        embed_users = ''
        embed_karma = ''
        for entry in query:
            user = await self.bot.fetch_user(entry.discord_user_id)
            embed_users += f'{user.mention}\n'
            embed_karma += f'{entry.amount}\n'
        embed.add_field(name='Users', value=embed_users)
        embed.add_field(name='Karma', value=embed_karma)        
        await ctx.send(embed=embed)
        await ctx.message.delete()

    @commands.group(name='casino', aliases=['cas'], invoke_without_command=True)
    async def casino(self, ctx):
        await ctx.send_help(ctx.command)

    @casino.command(name='open', aliases=['o'], usage="open <question> <option1> <option2>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def casino_open(self, ctx, question, op_a, op_b):

        casino = await self.add_casino(ctx, question, op_a, op_b)
        await self.update_casino(casino.id)
        await ctx.message.delete()

    @casino.command(name='close', usage="close <casino_id> <winning_option>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def casino_close(self, ctx, casino_id: int, winner: int):
        if db.session.query(db.casino).filter_by(id=casino_id).first().locked:
            return await ctx.send(f'Casino with ID {casino_id} is already closed.')

        


    @casino.command(name='bet', usage="close <casino_id>")
    async def casino_bet(self, ctx, casino_id: int, amount: int, option: int):
        pass
        


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
        query = db.session.query(db.karma_emote.discord_emote_id).filter_by(discord_server_id=guild_id).filter_by(action=0).all()
        list = [ x[0] for x in query ] if query is not None else []
        list.append(int(store.UPVOTE_EMOTE_ID))
        return list

    def get_query_karma_remove(self, guild_id):
        query = db.session.query(db.karma_emote.discord_emote_id).filter_by(discord_server_id=guild_id).filter_by(action=1).all()
        list = [ x[0] for x in query ] if query is not None else []
        list.append(int(store.DOWNVOTE_EMOTE_ID))
        return list

    async def check_payload(self, payload):
        if payload.event_type == 'REACTION_ADD' and payload.member.bot:
            return None
        try:
            message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
        except nextcord.errors.NotFound:
            return None
        if message.author.bot:
            return None
        user = await self.bot.fetch_user(payload.user_id)
        if user == message.author:
            return None
        return message.author

    def change_user_karma(self, user_id, guild_id, amount):
        # check if user and guild are in the db
        existing_user = db.session.query(db.karma).filter_by(discord_user_id=user_id).filter_by(discord_server_id=guild_id).first()
        if existing_user is None:
            db.session.add(db.karma(user_id, guild_id, amount))
        else:
            existing_user.amount += amount
        db.session.commit()

    async def change_post_upvotes(self, payload, amount):
        message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
        existing_post = db.session.query(db.post).filter_by(discord_message_id=message.id).first()
        if existing_post is None:
            db.session.add(db.post(message=message, upvotes=amount, downvotes=0))
        else:
            existing_post.upvotes += amount
        db.session.commit()

    async def change_post_downvotes(self, payload, amount):
        message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
        existing_post = db.session.query(db.post).filter_by(discord_message_id=message.id).first()
        if existing_post is None:
            db.session.add(db.post(message=message, upvotes=0, downvotes=amount))
        else:
            existing_post.downvotes += amount
        db.session.commit()

    async def add_casino(self, ctx, question, option_1, option_2):
        if db.session.query(db.discord_server).filter_by(discord_server_id=ctx.guild.id).first() is None:
            db.session.add(db.discord_server(ctx.guild))
        if db.session.query(db.discord_channel).filter_by(discord_channel_id=ctx.channel.id).first() is None:
            db.session.add(db.discord_channel(ctx.channel))
        if db.session.query(db.discord_user).filter_by(discord_user_id=ctx.author.id).first() is None:
            db.session.add(db.discord_user(ctx.author))

        to_embed = nextcord.Embed(description="Opening kasino, hold on tight...")
        casino_msg = await ctx.send(embed=to_embed)
        
        casino = db.casino(question, option_1, option_2, casino_msg)
        db.session.add(casino)
        db.session.commit()

        return casino

    async def update_casino(self, casino_id):
        casino = db.session.query(db.casino).filter_by(id=casino_id).first()
        kasino_msg = await (await self.bot.fetch_channel(casino.discord_channel_id)).fetch_message(casino.discord_message_id)

        # FIGURE OUT AMOUNTS AND ODDS
        qry = db.session.query(func.sum(db.casino_bet.amount).label("total_amount"))
        aAmount = qry.filter_by(id=casino_id).filter_by(option='1').first().total_amount
        bAmount = qry.filter_by(id=casino_id).filter_by(option='2').first().total_amount
        aAmount = 0 if aAmount is None else aAmount
        bAmount = 0 if bAmount is None else bAmount
        if aAmount != 0:
            aOdds = float(aAmount + bAmount) / aAmount
        else:
            aOdds = 1.0
        if bAmount != 0:
            bOdds = float(aAmount + bAmount) / bAmount
        else:
            bOdds = 1.0

        # CREATE MESSAGE
        to_embed = nextcord.Embed(
            title=f'{"[LOCKED] " if casino.locked else ""}:game_die: {casino.question}',
            description=f'{"The kasino is locked! No more bets are taken in. Time to wait and see..." if casino.locked else f"The kasino has been opened! Place your bets using `-bet {casino.id} <amount> <1 or 2>`"}',
            color=nextcord.Colour.from_rgb(52, 79, 235)
        )
        to_embed.set_footer(text=f'On the table: {aAmount + bAmount} Karma | ID: {casino.id}')
        to_embed.set_thumbnail(url='https://cdn.betterttv.net/emote/602548a4d47a0b2db8d1a3b8/3x.gif')
        to_embed.add_field(name=f'**1:** {casino.option_1}',
                           value=f'**Odds:** 1:{round(aOdds, 2)}\n**Pool:** {aAmount} Karma')
        to_embed.add_field(name=f'**2:** {casino.option_2}',
                           value=f'**Odds:** 1:{round(bOdds, 2)}\n**Pool:** {bAmount} Karma')

        await kasino_msg.edit(embed=to_embed)

def setup(bot):
    bot.add_cog(Karma(bot))
