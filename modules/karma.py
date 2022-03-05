import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import nextcord
import numpy as np
from nextcord.ext import commands
from sqlalchemy.sql import func

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
        vote_channel = db.get_discord_channel(channel)
        if not vote_channel.upvote:
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
            channel_array = np.append(channel_array, entry.discord_channel_id)
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

    @karma.command(name="donate", aliases=["wiretransfer", "wt"], usage="donate <amount> <user>")
    async def karma_donate(self, ctx, amount: int, user: nextcord.User):
        """
        Donates karma to another user.
        """
        if user.bot:
            return await ctx.send(embed=nextcord.Embed(description=f'You can\'t donate to bots!', color=0xf66045))
        if amount <= 0:
            return await ctx.send(embed=nextcord.Embed(description=f'You cannot donate {amount} karma!', color=0xf66045))
        donator_karma = db.session.query(db.karma).filter_by(discord_user_id=ctx.author.id).filter_by(discord_server_id=ctx.guild.id).first()
        if donator_karma is None:
            return await ctx.send(embed=nextcord.Embed(description=f'You don\'t have any karma!', color=0xf66045))
        if donator_karma.amount < amount:
            return await ctx.send(embed=nextcord.Embed(description=f'You don\'t have enough karma!', color=0xf66045))
        # check if user is a member of the server
        if user not in ctx.guild.members:
            return await ctx.send(embed=nextcord.Embed(description=f'`{user}` is not a member of this server!', color=0xf66045))
        user_karma = db.session.query(db.karma).filter_by(discord_user_id=user.id).filter_by(discord_server_id=ctx.guild.id).first()
        if user_karma is None:
            user_karma = db.karma(user.id, ctx.guild.id, amount)
            db.session.add(user_karma)
        else:
            user_karma.amount += amount
        donator_karma.amount -= amount
        db.session.commit()
        embed = nextcord.Embed(description=f'{ctx.author.mention} has donated {amount} karma to {user.mention}!', color=0x23b40c)
        await ctx.send(embed=embed)
        await ctx.message.delete()

    @karma_donate.error
    async def karma_donate_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=nextcord.Embed(description=f'You didn\'t specify a user to donate to!', color=0xf66045))
            await ctx.message.delete()
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=nextcord.Embed(description=f'Wrong command usage! Command usage is `{self.bot.command_prefix}karma donate <amount> <user>`', color=0xf66045))
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


    @commands.command(aliases=['plb'], usage="postlb")
    async def postlb(self, ctx):
        """
        Posts the leaderboard of the most upvoted posts.
        """
        query = db.session.query(db.post, db.discord_user).join(db.post, db.post.discord_user_id == db.discord_user.discord_user_id).filter_by(discord_server_id=ctx.guild.id)

        async with ctx.typing():
            posts = query.order_by(db.post.upvotes.desc()).limit(5)
            monthly_posts = query.filter(db.post.created_at > datetime.now() - timedelta(days=30)).order_by(db.post.upvotes.desc()).limit(5)
            weekly_posts = query.filter(db.post.created_at > datetime.now() - timedelta(days=7)).order_by(db.post.upvotes.desc()).limit(5)

            all_board = await self.create_post_leaderboard(posts)
            month_board = await self.create_post_leaderboard(monthly_posts)
            week_board = await self.create_post_leaderboard(weekly_posts)
        
        embed = nextcord.Embed(title='Top Messages')
        embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.add_field(name='Top 5 All Time', value=all_board, inline=False)
        embed.add_field(name='Top 5 This Month', value=month_board, inline=False)
        embed.add_field(name='Top 5 This Week', value=week_board, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name='checkpost', aliases=['cp'], usage="checkpost <post id>")
    @commands.is_owner()
    async def check_post(self, ctx, post_id):
        """
        Checks if a post exists.
        """
        post = db.session.query(db.post).filter_by(discord_server_id=ctx.guild.id).filter_by(discord_message_id=post_id).first()
        if post is None:
            embed = nextcord.Embed(title='That post does not exist.')
            return await ctx.send(embed=embed, delete_after=30)
        query = db.session.query(db.karma_emote).filter_by(discord_server_id=ctx.guild.id)

        server_upvote_emotes = query.filter_by(action=0).all()
        server_downvote_emotes = query.filter_by(action=1).all()

        server_upvote_emotes_ids = [emote.discord_emote_id for emote in server_upvote_emotes]
        server_downvote_emotes_ids = [emote.discord_emote_id for emote in server_downvote_emotes]

        channel = await self.bot.fetch_channel(post.discord_channel_id)
        message = await channel.fetch_message(post.discord_message_id)

        upvote_reactions = 0
        downvote_reactions = 0
        for reaction in message.reactions:
            if isinstance(reaction.emoji, nextcord.Emoji) or isinstance(reaction.emoji, nextcord.PartialEmoji):
                if reaction.emoji.id in server_upvote_emotes_ids:
                    upvote_reactions += reaction.count-1
                if reaction.emoji.id in server_downvote_emotes_ids:
                    downvote_reactions += reaction.count-1            

        old_upvotes = post.upvotes
        old_downvotes = post.downvotes

        karma_difference = (old_upvotes - upvote_reactions) + (old_downvotes - downvote_reactions)
        user_karma = db.session.query(db.karma).filter_by(discord_server_id=ctx.guild.id).filter_by(discord_user_id=post.discord_user_id).first()
        user_karma.amount += karma_difference

        post.upvotes = upvote_reactions
        post.downvotes = downvote_reactions
        db.session.commit()

        embed_string = f'Old post upvotes: {old_upvotes}, Old post downvotes: {old_downvotes}\nRechecked post upvotes: {upvote_reactions}, Rechecked post downvotes: {downvote_reactions}\nKarma difference: {karma_difference}'

        embed = nextcord.Embed(title=f'Post {post_id} check', description=embed_string)
        await ctx.send(embed=embed, delete_after=60)
        await ctx.message.delete()



    async def create_post_leaderboard(self, posts_query):
        leaderboard = ''
        for index, post in enumerate(posts_query, start=1):
            jump_url = self.create_message_url(post[0].discord_server_id, post[0].discord_channel_id, post[0].discord_message_id)
            leaderboard += f'**{index}.** [{post[1].username} ({post[0].upvotes})]({jump_url})\n'
        return leaderboard if len(leaderboard) > 0 else 'No posts found.'

    def create_message_url(self, server_id, channel_id, message_id):
        return f'https://discordapp.com/channels/{server_id}/{channel_id}/{message_id}'


    @commands.group(name='kasino', aliases=['kas'], invoke_without_command=True)
    async def kasino(self, ctx):
        await ctx.send_help(ctx.command)

    @kasino.command(name='open', aliases=['o'], usage="open \"<question>\" \"<option1>\" \"<option2>\"")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def kasino_open(self, ctx, question, op_a, op_b):
        await ctx.message.delete()
        kasino = await self.add_kasino(ctx, question, op_a, op_b)
        self.create_kasino_backup(kasino.id)
        await self.update_kasino(kasino.id)

    @kasino.command(name='close', usage="close <kasino_id> <winning_option>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def kasino_close(self, ctx, kasino_id: int, winner: int):
        author_img = ctx.author.avatar.url

        
        if not self.is_kasino_open(kasino_id):
            return await ctx.author.send(f'Kasino with ID {kasino_id} is not open.')

        kasino = db.session.query(db.kasino).filter_by(id=kasino_id).first()
        if kasino.discord_server_id != ctx.guild.id:
            return await ctx.send(f'Kasino with ID {kasino_id} is not in this server.', delete_after=120)

        if winner == 3:
            await self.abort_kasino(kasino_id)
        elif winner in [1, 2]:
            await self.win_kasino(kasino_id, winner)
        else:
            return await ctx.author.send(f'Winner has to be 1, 2 or 3 (abort)')

        await self.send_conclusion(ctx, kasino_id, winner, ctx.author, author_img)
        await self.remove_kasino(kasino_id)
        await ctx.message.delete()

    @kasino_close.error
    async def kasino_close_error(self, ctx, error):
        if isinstance(error, commands.errors.MissingRequiredArgument):
            await ctx.send(f'You didn\'t provide a required argument! Correct usage is `{self.bot.command_prefix}kasino close <kasino_id> <winning_option>`', delete_after=20)
            await ctx.message.delete()
        elif isinstance(error, commands.errors.BadArgument):
            await ctx.send(f'Bad argument.', delete_after=20)
            await ctx.message.delete()


    @kasino.command(name='lock', usage="lock <kasino_id>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def kasino_lock(self, ctx, kasino_id: int):
        if not self.is_kasino_open(kasino_id):
            return await ctx.author.send(f'Kasino with ID `{kasino_id}` does not exist.')
        if db.session.query(db.kasino).filter_by(id=kasino_id).first().locked:
            return await ctx.author.send(f'Kasino with ID `{kasino_id}` is already locked.')

        self.lock_kasino(kasino_id)
        await self.update_kasino(kasino_id)
        await ctx.message.delete()

    @kasino.command(name='bet', usage="bet <kasino_id> <amount> <option>")
    async def kasino_bet(self, ctx, kasino_id: int, amount, option: int):
        await ctx.message.delete()
        if not self.is_kasino_open(kasino_id):
            return await ctx.send(f'Kasino with ID `{kasino_id}` is not open.')
        better_karma = db.session.query(db.karma).filter_by(discord_user_id=ctx.author.id).filter_by(discord_server_id=ctx.guild.id).first()
        if better_karma is None:
            return await ctx.send(f'You do not have any karma.')

        if option not in [1, 2]:
            output = nextcord.Embed(
                title=f'Wrong usage. Correct usage is `{self.bot.command_prefix}kasino bet <kasino_id> <amount> <1 or 2>`',
                color=nextcord.Colour.from_rgb(209, 25, 25)
            )
            return await ctx.author.send(embed=output)
        if amount == "all":
            amount = self.get_user_karma(ctx.author.id, ctx.guild.id)
        else:
            amount = int(amount)
        if better_karma.amount < amount:
            output = nextcord.Embed(
                title=f'You don\'t have that much karma. Your karma: {better_karma.amount}',
                color=nextcord.Colour.from_rgb(209, 25, 25)
            )
            return await ctx.author.send(embed=output)

        if not self.is_kasino_open(kasino_id):
            output = nextcord.Embed(
                title=f'Kasino with ID {kasino_id} is not open.',
                color=nextcord.Colour.from_rgb(209, 25, 25)
            )
            return await ctx.author.send(embed=output)

        if self.is_kasino_locked(kasino_id):
            output = nextcord.Embed(
                title=f'kasino with ID {kasino_id} is locked.',
                color=nextcord.Colour.from_rgb(209, 25, 25)
            )
            return await ctx.author.send(embed=output)
            
        if amount < 1:
            output = nextcord.Embed(
                title='You tried to bet < 1 karma! Silly you!',
                color=nextcord.Colour.from_rgb(209, 25, 25)
            )
            return await ctx.author.send(embed=output)

        if self.has_user_bet(kasino_id, ctx.author.id):
            if not self.is_same_bet_option(kasino_id, ctx.author.id, option):
                output = nextcord.Embed(
                    title=f'You can\'t change your choice on the bet with id {kasino_id}. No chickening out!',
                    color=nextcord.Colour.from_rgb(209, 25, 25)
                )
                return await ctx.author.send(embed=output)
            total_bet = self.increase_bet(kasino_id, ctx.author.id, ctx.guild.id, amount)
            output = nextcord.Embed(
                title=f'**Successfully increased bet on option {option}, on kasino with ID {kasino_id} for {amount} karma! Total bet is now: {total_bet} Karma**',
                color=nextcord.Colour.from_rgb(52, 79, 235),
                description=f'Remaining karma: {better_karma.amount}'
            )
            await ctx.author.send(embed=output)
        else:
            self.add_bet(kasino_id, ctx.author.id, ctx.guild.id, amount, option)
            user_karma = self.get_user_karma(ctx.author.id, ctx.guild.id)
            output = nextcord.Embed(
                title=f'**Successfully added bet on option {option}, on kasino with ID {kasino_id} for {amount} karma! Total bet is now: {amount} Karma**',
                color=nextcord.Colour.from_rgb(52, 79, 235),
                description=f'Your remaining karma: {user_karma}'
            )
            await ctx.author.send(embed=output)
        await self.update_kasino(kasino_id)


    @kasino.command(name='list', aliases=['l'], usage="list")
    async def kasino_list(self, ctx):
        embed = nextcord.Embed(title='Open kasinos')
        embed_kasinos = ''
        for entry in db.session.query(db.kasino).filter_by(discord_server_id=ctx.guild.id).filter_by(locked=False).all():
            embed_kasinos += f'{entry.id} - {entry.question}\n'
        embed_kasinos = "No open kasinos found." if embed_kasinos == '' else embed_kasinos
        embed.description = embed_kasinos
        await ctx.send(embed=embed, delete_after=300)
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
        query = db.session.query(db.karma_emote.discord_emote_id).filter_by(discord_server_id=guild_id).filter_by(action=0).all()
        emote_list = [ x[0] for x in query ] if query is not None else []
        emote_list.append(int(store.UPVOTE_EMOTE_ID))
        return emote_list

    def get_query_karma_remove(self, guild_id):
        query = db.session.query(db.karma_emote.discord_emote_id).filter_by(discord_server_id=guild_id).filter_by(action=1).all()
        emote_list = [ x[0] for x in query ] if query is not None else []
        emote_list.append(int(store.DOWNVOTE_EMOTE_ID))
        return emote_list

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

    def get_user_karma(self, user_id: int, server_id: int):
        karma = db.session.query(db.karma).filter_by(discord_user_id=user_id).filter_by(discord_server_id=server_id).first()
        if karma is None:
            db.session.add(db.karma(discord_user_id=user_id, discord_server_id=server_id, amount=0))
            db.session.commit()
            return 0
        return karma.amount

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

    async def send_conclusion(self, ctx, kasino_id, winner, author, author_img):
        kasino = db.session.query(db.kasino).filter_by(id=kasino_id).first()
        qry = db.session.query(func.sum(db.kasino_bet.amount).label("total_amount"))
        total_karma = qry.filter_by(kasino_id=kasino_id).first().total_amount
        to_embed = None

        if winner == 1:
            to_embed = nextcord.Embed(
                title=f':tada: "{kasino.option_1}" was correct! :tada:',
                description=f'Question: {kasino.question}\nIf you\'ve chosen 1, you\'ve just won karma!\nDistributed to the winners: **{total_karma} Karma**',
                color=nextcord.Colour.from_rgb(52, 79, 235)
            )
        elif winner == 2:
            to_embed = nextcord.Embed(
                title=f':tada: "{kasino.option_2}" was correct! :tada:',
                description=f'Question: {kasino.question}\nIf you\'ve chosen 2, you\'ve just won karma!\nDistributed to the winners: **{total_karma} Karma**',
                color=nextcord.Colour.from_rgb(52, 79, 235)
            )
        elif winner == 3:
            to_embed = nextcord.Embed(
                title=f':game_die: "{kasino.question}" has been cancelled.',
                description=f'Amount bet will be refunded to each user.\nReturned: {total_karma} Karma',
                color=nextcord.Colour.from_rgb(52, 79, 235)
            )

        to_embed.set_footer(
            text=f'as decided by {author}',
            icon_url=author_img
        )
        to_embed.set_thumbnail(url='https://cdn.betterttv.net/emote/602548a4d47a0b2db8d1a3b8/3x.gif')
        await ctx.send(embed=to_embed)
        return

    async def add_kasino(self, ctx, question, option_1, option_2):
        if db.session.query(db.discord_server).filter_by(discord_server_id=ctx.guild.id).first() is None:
            db.session.add(db.discord_server(ctx.guild))
        if db.session.query(db.discord_channel).filter_by(discord_channel_id=ctx.channel.id).first() is None:
            db.session.add(db.discord_channel(ctx.channel))
        if db.session.query(db.discord_user).filter_by(discord_user_id=ctx.author.id).first() is None:
            db.session.add(db.discord_user(ctx.author))

        to_embed = nextcord.Embed(description="Opening kasino, hold on tight...")
        kasino_msg = await ctx.send(embed=to_embed)
        
        kasino = db.kasino(question, option_1, option_2, kasino_msg)
        db.session.add(kasino)
        db.session.commit()
        return kasino

    async def remove_kasino(self, kasino_id):
        kasino = db.session.query(db.kasino).filter_by(id=kasino_id).first()
        if kasino is None:
            return
        try:
            kasino_msg = await (await self.bot.fetch_channel(kasino.discord_channel_id)).fetch_message(kasino.discord_message_id)
            await kasino_msg.delete()
        except nextcord.errors.NotFound:
            pass
        db.session.delete(kasino)
        bets = db.session.query(db.kasino_bet).filter_by(kasino_id=kasino_id).all()
        for bet in bets:
            db.session.delete(bet)
        db.session.commit()

    def create_kasino_backup(self, kasino_id):
        today_string = datetime.now().strftime("%Y_%m_%d")
        now_time_string = datetime.now().strftime("%H%M")
        backup_folder = f"{store.DATA_PATH}/backups/{today_string}"
        Path(backup_folder).mkdir(parents=True, exist_ok=True)
        shutil.copy(store.DB_PATH, f"{backup_folder}/backup_{now_time_string}_{kasino_id}.sqlite")

    def has_user_bet(self, kasino_id: int, user_id: int):
        return db.session.query(db.kasino_bet).filter_by(discord_user_id=user_id).filter_by(kasino_id=kasino_id).first() is not None

    def is_same_bet_option(self, kasino_id: int, user_id: int, option: int):
        return db.session.query(db.kasino_bet).filter_by(discord_user_id=user_id).filter_by(kasino_id=kasino_id).first().option == option

    def add_bet(self, kasino_id: int, user_id: int, server_id: int, amount: int, option: int):
        db.session.add(db.kasino_bet(kasino_id, user_id, amount, option))
        user_karma = db.session.query(db.karma).filter_by(discord_user_id=user_id).filter_by(discord_server_id=server_id).first()
        user_karma.amount -= amount
        db.session.commit()

    def increase_bet(self, kasino_id: int, user_id: int, server_id: int, increase_amount: int):
        existing_bet = db.session.query(db.kasino_bet).filter_by(discord_user_id=user_id).filter_by(kasino_id=kasino_id).first()
        existing_bet.amount += increase_amount
        user_karma = db.session.query(db.karma).filter_by(discord_user_id=user_id).filter_by(discord_server_id=server_id).first()
        user_karma.amount -= increase_amount
        db.session.commit()
        return existing_bet.amount

    def is_kasino_open(self, kasino_id: int):
        return db.session.query(db.kasino).filter_by(id=kasino_id).first() is not None

    def is_kasino_locked(self, kasino_id: int):
        return db.session.query(db.kasino).filter_by(id=kasino_id).first().locked

    def lock_kasino(self, kasino_id: int):
        db.session.query(db.kasino).filter_by(id=kasino_id).update({'locked': True})
        db.session.commit()
    
    async def abort_kasino(self, kasino_id: int):
        bets = db.session.query(db.kasino_bet, db.kasino).join(db.kasino, db.kasino.id == db.kasino_bet.kasino_id).filter_by(id=kasino_id).all()
        kasino = db.session.query(db.kasino).filter_by(id=kasino_id).first()
        for bet in bets:
            self.change_user_karma(bet[0].discord_user_id, bet[1].discord_server_id, bet[0].amount)
            user_karma = self.get_user_karma(bet[0].discord_user_id, bet[1].discord_server_id)
            output = nextcord.Embed(
                title=f'**You\'ve been refunded {bet[0].amount} karma.**',
                color=nextcord.Colour.from_rgb(52, 79, 235),
                description=f'Question was: {kasino.question}\n'
                            f'Remaining karma: {user_karma}'
            )
            await (await self.bot.fetch_user(bet[0].discord_user_id)).send(embed=output)
        return True

    async def win_kasino(self, kasino_id: int, winning_option: int):
        qry = db.session.query(db.kasino_bet, db.kasino).join(db.kasino, db.kasino.id == db.kasino_bet.kasino_id)
        winners = qry.filter(db.kasino_bet.kasino_id==kasino_id).filter(db.kasino_bet.option==winning_option).all()
        losers = qry.filter(db.kasino_bet.kasino_id==kasino_id).filter(db.kasino_bet.option != winning_option).all()
        qry = db.session.query(func.sum(db.kasino_bet.amount).label("total_amount"))
        total_winner_karma = qry.filter_by(kasino_id=kasino_id).filter_by(option=winning_option).first().total_amount
        total_kasino_karma = qry.filter_by(kasino_id=kasino_id).first().total_amount
        question = db.session.query(db.kasino).filter_by(id=kasino_id).first().question

        if total_winner_karma is None:
            total_winner_karma = 0

        for bet in winners:
            win_ratio = bet[0].amount / total_winner_karma
            win_amount = round(win_ratio * total_kasino_karma)

            self.change_user_karma(bet[0].discord_user_id, bet[1].discord_server_id, win_amount)
            user_karma = self.get_user_karma(bet[0].discord_user_id, bet[1].discord_server_id)
            output = nextcord.Embed(
                title=f':tada: **You\'ve won {win_amount} karma!** :tada:',
                color=nextcord.Colour.from_rgb(66, 186, 50),
                description=f'(Of which {bet[0].amount} you put down on the table)\n'
                            f'Question was: {question}\n'
                            f'New karma balance: {user_karma}'
            )
            await (await self.bot.fetch_user(bet[0].discord_user_id)).send(embed=output)
        for bet in losers:
            user_karma = self.get_user_karma(bet[0].discord_user_id, bet[1].discord_server_id)
            output = nextcord.Embed(
                title=f':chart_with_downwards_trend: **You\'ve unfortunately lost {bet[0].amount} karma...** :chart_with_downwards_trend:',
                color=nextcord.Colour.from_rgb(209, 25, 25),
                description=f'Question was: {question}\n'
                            f'New karma balance: {user_karma}'
            )
            await (await self.bot.fetch_user(bet[0].discord_user_id)).send(embed=output)
        db.session.commit()

    async def update_kasino(self, kasino_id: int):
        kasino = db.session.query(db.kasino).filter_by(id=kasino_id).first()
        kasino_msg = await (await self.bot.fetch_channel(kasino.discord_channel_id)).fetch_message(kasino.discord_message_id)

        # FIGURE OUT AMOUNTS AND ODDS
        qry = db.session.query(func.sum(db.kasino_bet.amount).label("total_amount"))
        aAmount = qry.filter_by(kasino_id=kasino_id).filter_by(option='1').first().total_amount
        bAmount = qry.filter_by(kasino_id=kasino_id).filter_by(option='2').first().total_amount
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
        description = f"The kasino has been opened! Place your bets using `{self.bot.command_prefix}kasino bet {kasino.id} <amount> <1 or 2>`"
        if kasino.locked: 
            description = f'The kasino is locked! No more bets are taken in. Time to wait and see...'
        to_embed = nextcord.Embed(
            title=f'{"[LOCKED] " if kasino.locked else ""}:game_die: {kasino.question}',
            description=description,
            color=nextcord.Colour.from_rgb(52, 79, 235)
        )
        to_embed.set_footer(text=f'On the table: {aAmount + bAmount} Karma | ID: {kasino.id}')
        to_embed.set_thumbnail(url='https://cdn.betterttv.net/emote/602548a4d47a0b2db8d1a3b8/3x.gif')
        to_embed.add_field(name=f'**1:** {kasino.option_1}',
                           value=f'**Odds:** 1:{round(aOdds, 2)}\n**Pool:** {aAmount} Karma')
        to_embed.add_field(name=f'**2:** {kasino.option_2}',
                           value=f'**Odds:** 1:{round(bOdds, 2)}\n**Pool:** {bAmount} Karma')

        await kasino_msg.edit(embed=to_embed)

def setup(bot):
    bot.add_cog(Karma(bot))
