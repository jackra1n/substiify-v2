import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

import discord
import plotly.graph_objects as go
from asyncpg import Record
from core import values
from core.bot import Substiify
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


UPDATE_KARMA_QUERY = '''INSERT INTO karma (discord_user_id, discord_server_id, amount) VALUES ($1, $2, $3)
                        ON CONFLICT (discord_user_id, discord_server_id) DO UPDATE SET amount = karma.amount + $3'''
UPDATE_POST_VOTES_QUERY = '''INSERT INTO post (discord_user_id, discord_server_id, discord_channel_id, discord_message_id, created_at, upvotes, downvotes)
                             VALUES ($1, $2, $3, $4, $5, $6, $7)
                             ON CONFLICT (discord_message_id) DO UPDATE SET upvotes = post.upvotes + $6, downvotes = post.downvotes + $7'''


class Karma(commands.Cog):

    COG_EMOJI = "☯️"

    def __init__(self, bot: Substiify, vote_channels: list[int]):
        self.bot = bot
        self.vote_channels = vote_channels

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.channel.id in self.vote_channels and not message.author.bot:
            try:
                await message.add_reaction(self.get_upvote_emote())
                await message.add_reaction(self.get_downvote_emote())
            except discord.NotFound:
                pass

    @commands.hybrid_group(invoke_without_command=True)
    async def votes(self, ctx: commands.Context):
        """
        Shows if votes are enabled in the current channel
        """
        if ctx.channel.id in self.vote_channels:
            embed = discord.Embed(color=0x23b40c)
            embed.description = f'Votes are **ALREADY enabled** in {ctx.channel.mention}!'
        else:
            embed = discord.Embed(color=0xf66045)
            embed.description = f'Votes are **NOT enabled** in {ctx.channel.mention}!'
        await ctx.reply(embed=embed, delete_after=30)

    @votes.command(name='list')
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def list_votes(self, ctx: commands.Context):
        """
        Lists all the votes channels that are enabled in the server
        """
        stmt = 'SELECT * FROM discord_channel WHERE discord_server_id = $1 AND upvote = True'
        upvote_channels = await self.bot.db.fetch(stmt, ctx.guild.id)
        channels_string = '\n'.join([f"{x['discord_channel_id']} ({x['channel_name']})" for x in upvote_channels])
        embed = discord.Embed(color=0x23b40c)
        if not channels_string:
            embed.description = 'No votes channels found.'
            return await ctx.send(embed=embed, delete_after=20)
        embed.description = f'Votes are enabled in the following channels: {channels_string}'
        await ctx.send(embed=embed, delete_after=20)

    @votes.command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        channel="The channel to enable votes in"
    )
    async def start(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """
        Enables votes in the current or specified channel. Requires Manage Channels permission.
        After enabling votes, the bot will add the upvote and downvote reactions to every message in the channel.
        This is good for something like a meme channel if you want to give upvotes and downvotes to the messages.

        If users click the reactions, user karma will be updated.
        """
        channel = channel or ctx.channel
        if channel.id not in self.vote_channels:
            self.vote_channels.append(channel.id)
        stmt = 'SELECT * FROM discord_channel WHERE discord_channel_id = $1 AND upvote = True'
        votes_enabled = await self.bot.db.fetch(stmt, channel.id)
        logger.info(f'Votes enabled: {votes_enabled}')
        if not votes_enabled:
            stmt = '''INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id, upvote)
                      VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_channel_id) DO UPDATE SET upvote = $5'''
            await self.bot.db.execute(stmt, channel.id, channel.name, channel.guild.id, channel.category_id, True)
        else:
            embed = discord.Embed(
                description=f'Votes are **already active** in {ctx.channel.mention}!',
                color=0x23b40c
            )
            await ctx.send(embed=embed, delete_after=20)
            return
        embed = discord.Embed(
            description=f'Votes **enabled** in {channel.mention}!',
            color=0x23b40c
        )
        await ctx.send(embed=embed)
        await ctx.message.delete()

    @votes.command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        channel="The channel to disable votes in"
    )
    async def stop(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """
        Disables votes in the current channel. Requires Manage Channels permission.
        """
        channel = channel or ctx.channel
        stmt = '''INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id, upvote)
                  VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_channel_id) DO UPDATE SET upvote = $5'''
        await self.bot.db.execute(stmt, channel.id, channel.name, channel.guild.id, channel.category_id, False)

        if channel.id in self.vote_channels:
            self.vote_channels.remove(channel.id)

        await ctx.message.delete()
        await ctx.send(embed=discord.Embed(description=f'Votes has been stopped in {channel.mention}!', color=0xf66045))

    def get_upvote_emote(self):
        return self.bot.get_emoji(values.UPVOTE_EMOTE_ID)

    def get_downvote_emote(self):
        return self.bot.get_emoji(values.DOWNVOTE_EMOTE_ID)

    @commands.group(aliases=["k"], usage="karma [user]", invoke_without_command=True,)
    @app_commands.describe(
        user='Which user do you want to see the karma of? If not specified, it will show your own karma.'
    )
    async def karma(self, ctx: commands.Context, user: discord.User = None):
        """
        Shows the karma of a user. If you dont specify a user, it will show your own.
        If you want to know what emote reactions are used for karma, use the subcommand `karma emotes`
        """
        if user is None:
            user = ctx.author

        if user.bot:
            return await ctx.reply(embed=discord.Embed(description="Bots don't have karma!", color=0xf66045))

        user_karma = await self._get_user_karma(user.id, ctx.guild.id)
        user_karma = 0 if user_karma is None else user_karma

        embed = discord.Embed(title=f'Karma - {ctx.guild.name}', description=f'{user.mention} has {user_karma} karma.')
        await ctx.send(embed=embed)

    @karma.error
    async def karma_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.BadArgument):
            embed = discord.Embed(color=0xf66045, description=error)
            await ctx.send(embed=embed)

    @commands.cooldown(3, 10)
    @karma.command(name="donate", aliases=["wiretransfer", "wt"], usage="donate <user> <amount>")
    @app_commands.describe(
        user='Which user do you want to donate karma to?',
        amount='How much karma do you want to donate?'
    )
    async def karma_donate(self, ctx: commands.Context, user: discord.User, amount: int):
        """
        Donates karma to another user.
        """
        embed = discord.Embed(color=0xf66045)
        if user.bot:
            embed.description = 'You can\'t donate to bots!'
            return await ctx.send(embed=embed)
        
        if amount <= 0:
            embed.description = f'You cannot donate {amount} karma!'
            return await ctx.send(embed=embed)
        
        if user not in ctx.guild.members:
            embed.description = f'`{user}` is not a member of this server!'
            return await ctx.send(embed=embed)
        
        donator_karma = await self._get_user_karma(ctx.author.id, ctx.guild.id)
        if donator_karma is None:
            embed.description = 'You don\'t have any karma!'
            return await ctx.send(embed=embed)
        
        if donator_karma < amount:
            embed.description = 'You don\'t have enough karma!'
            return await ctx.send(embed=embed)
        
        stmt_karma = '''
            INSERT INTO karma (discord_user_id, discord_server_id, amount) VALUES ($1, $2, $3)
            ON CONFLICT (discord_user_id, discord_server_id) DO UPDATE SET amount = karma.amount + $3'''
        await self.bot.db.executemany(stmt_karma, [(user.id, ctx.guild.id, amount), (ctx.author.id, ctx.guild.id, -amount)])

        embed = discord.Embed(color=0x23b40c)
        embed.description = f'{ctx.author.mention} has donated {amount} karma to {user.mention}!'
        await ctx.send(embed=embed)

    @karma_donate.error
    async def karma_donate_error(self, ctx: commands.Context, error):
        embed = discord.Embed(color=0xf66045)
        if isinstance(error, commands.MissingRequiredArgument):
            embed.description = 'You didn\'t specify a user to donate to!'
        elif isinstance(error, commands.BadArgument):
            embed.description=f'Wrong command usage! Command usage is `{ctx.prefix}karma donate <user> <amount>`'
        await ctx.send(embed=embed)

    @karma.group(name='emotes', aliases=['emote'], usage="emotes", invoke_without_command=True)
    async def karma_emotes(self, ctx: commands.Context):
        """
        Shows the karma emotes of the server. Emotes in the `add` category increase karma,
        while emotes in the `remove` category decrease karma.
        If you want to add or remove an emote from the karma system,
        check the subcommand `karma emotes add` or `karma emotes remove`
        """
        stmt = "SELECT * FROM karma_emote WHERE discord_server_id = $1 ORDER BY increase_karma DESC"
        karma_emotes = await self.bot.db.fetch(stmt, ctx.guild.id)
        if not karma_emotes:
            return await ctx.send(embed=discord.Embed(title='No emotes found.'), delete_after=60)
        embed_string = ''
        last_action = ''
        for emote in karma_emotes:
            if emote['increase_karma'] != last_action:
                embed_string += f'\n`{"add" if emote["increase_karma"] is True else "remove"}:` '
                last_action = emote['increase_karma']
            embed_string += f"{self.bot.get_emoji(emote['discord_emote_id'])} "
        embed = discord.Embed(title=f'Karma Emotes - {ctx.guild.name}', description=embed_string)
        await ctx.send(embed=embed, delete_after=60)

    @karma_emotes.command(name='add', usage="add <emote> <action>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        emote='Which emote do you want to add?',
        emote_action='What action should this emote do? (0 for add, 1 for remove karma)'
    )
    async def karma_emote_add(self, ctx: commands.Context, emote: discord.Emoji, emote_action: int):
        """
        Add an emote to the karma emotes for this server. Takes an emoji and an action (0 for add, 1 for remove karma)
        The votes from this bots Votes module automatically add karma to the user. No need to add those emotes to the emote list.

        Example:
        `<<karma emotes add :upvote: 0` - adds the upvote emote to list as karma increasing emote
        `<<karma emotes add :downvote: 1` - adds the downvote emote to list as karma decreasing emote
        """
        if emote_action not in [0, 1]:
            embed = discord.Embed(title='Invalid action parameter.')
            return await ctx.send(embed=embed, delete_after=30)
        
        existing_emote = await self._get_karma_emote_by_id(ctx.guild.id, emote)
        if existing_emote is not None:
            embed = discord.Embed(title='That emote is already added.')
            return await ctx.send(embed=embed, delete_after=30)
        
        stmt_emote_count = "SELECT COUNT(*) FROM karma_emote WHERE discord_server_id = $1"
        max_emotes = await self.bot.db.fetchval(stmt_emote_count, ctx.guild.id)
        if max_emotes >= 10:
            embed = discord.Embed(title='You can only have 10 emotes.')
            return await ctx.send(embed=embed, delete_after=30)
        
        stmt_insert_emote = "INSERT INTO karma_emote (discord_server_id, discord_emote_id, increase_karma) VALUES ($1, $2, $3)"
        await self.bot.db.execute(stmt_insert_emote, ctx.guild.id, emote.id, not bool(emote_action))

        embed = discord.Embed(title=f'Emote {emote} added to the list.')
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @karma_emotes.command(name='remove', aliases=['delete'], usage="remove <emote>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        emote='Which emote do you want to remove?'
    )
    async def karma_emote_remove(self, ctx: commands.Context, emote: discord.Emoji):
        """
        Remove an emote from the karma emotes for this server.
        """
        existing_emote = await self._get_karma_emote_by_id(ctx.guild.id, emote)
        if existing_emote is None:
            embed = discord.Embed(title='That emote is not in the list.')
            return await ctx.send(embed=embed, delete_after=20)
        
        stmt_delete_emote = "DELETE FROM karma_emote WHERE discord_server_id = $1 AND discord_emote_id = $2"
        await self.bot.db.execute(stmt_delete_emote, ctx.guild.id, emote.id)

        embed = discord.Embed(title=f'Emote {emote} removed from the list.')
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @commands.cooldown(1, 5, commands.BucketType.user)
    @karma.command(name='leaderboard', aliases=['lb', 'leaderbord'], usage="leaderboard")
    async def karma_leaderboard(self, ctx: commands.Context, global_leaderboard: str = None):
        """
        Shows users with the most karma on the server.
        """
        async with ctx.typing():
            embed = discord.Embed(title='Karma Leaderboard')

            if global_leaderboard is None:
                stmt_karma_leaderboard = "SELECT discord_user_id, amount FROM karma WHERE discord_server_id = $1 ORDER BY amount DESC LIMIT 15"
                results = await self.bot.db.fetch(stmt_karma_leaderboard, ctx.guild.id)

            elif global_leaderboard == 'global':
                stmt_karma_leaderboard = "SELECT discord_user_id, amount FROM karma ORDER BY amount DESC LIMIT 15"
                results = await self.bot.db.fetch(stmt_karma_leaderboard)

            embed.description = ''
            if not results:
                embed.description = 'No users have karma.'
                return await ctx.send(embed=embed)

            users_string = ''.join([f"<@{entry['discord_user_id']}>\n" for entry in results])
            load_users_message = await ctx.send('Loading users...')
            await load_users_message.edit(content=users_string)
            await load_users_message.delete()

            for index, entry in enumerate(results, start=1):
                user = self.bot.get_user(entry['discord_user_id']) or await self.bot.fetch_user(entry['discord_user_id'])
                embed.description += f"`{str(index).rjust(2)}.` | `{entry['amount']}` - {user.mention}\n"

            await ctx.send(embed=embed)

    @commands.cooldown(1, 15, commands.BucketType.user)
    @karma.command(name='stats', usage="stats")
    async def karma_stats(self, ctx: commands.Context):
        """
        Shows karma stats for the server.
        Some stats incluce total karma, karma amount in top percentile and more.
        """
        async with ctx.typing():
            embed = discord.Embed(title='Karma Stats')

            karma_info = await self.bot.db.fetchrow("SELECT SUM(amount), COUNT(*) FROM karma WHERE discord_server_id = $1", ctx.guild.id)
            total_karma = karma_info['sum']
            karma_users = karma_info['count']

            if total_karma is None:
                embed.description = 'No users have karma.'
                return await ctx.send(embed=embed)

            avg_karma = total_karma / max(karma_users, 1)
            embed.add_field(name='Total Server Karma', value=f'`{total_karma:n} (of {karma_users} users)`', inline=False)
            embed.add_field(name='Average Karma per user', value=f'`{avg_karma:.2f}`', inline=False)

            # Top percentile calculation
            stmt_top_percentile = '''
                SELECT amount
                FROM karma
                WHERE discord_server_id = $1
                ORDER BY amount DESC
                LIMIT (SELECT CEIL($2 * CAST(COUNT(*) AS float)) FROM karma)'''

            percentiles = [(0.1, '10'), (0.01, '1')]
            for percentile, label in percentiles:
                top_percentile = await self.bot.db.fetch(stmt_top_percentile, ctx.guild.id, percentile)
                top_percentile = sum(entry['amount'] for entry in top_percentile)
                percantege = (top_percentile / total_karma) * 100
                embed.add_field(name=f'Top {label}% users karma', value=f'`{top_percentile:n} ({percantege:.2f}% of total)`', inline=False)

            stmt_avg_upvote_ratio = '''
                SELECT AVG(upvotes / downvotes) as average, COUNT(*) as post_count
                FROM post
                WHERE discord_server_id = $1 
                    AND upvotes >= 1
                    AND downvotes >= 1'''

            avg_post_query = await self.bot.db.fetchrow(stmt_avg_upvote_ratio, ctx.guild.id)
            avg_ratio = avg_post_query['average'] or 0
            post_count = avg_post_query['post_count'] or 0
            embed.add_field(name='Average upvote ratio per post', value=f'`{avg_ratio:.1f} ({post_count} posts)`', inline=False)

            await ctx.send(embed=embed)

    @commands.cooldown(1, 30, commands.BucketType.user)
    @karma.command(name='graph', usage="graph")
    async def karma_stats_graph(self, ctx: commands.Context):
        """
        Shows a graph of the amount of karma form every ten percent of users.
        """
        async with ctx.typing():
            stmt_karma = '''
                SELECT amount
                FROM karma
                WHERE discord_server_id = $1
                ORDER BY amount ASC
            '''

            karma = await self.bot.db.fetch(stmt_karma, ctx.guild.id)
            users_count = len(karma)
            if users_count == 0:
                embed = discord.Embed(title='Karma graph', description='No users have karma.')
                return await ctx.send(embed=embed)

            karma_percentiles = []
            for i in range(0, 101, 5):
                karma_percentile_list = karma[:int(users_count * (i / 100))]
                total_percentile_karma = sum(entry['amount'] for entry in karma_percentile_list)
                karma_percentiles.append((total_percentile_karma, i))

            x = [entry[1] for entry in karma_percentiles]
            y = [entry[0] for entry in karma_percentiles]

            timestamp = datetime.now().timestamp()
            filename = f'karma_graph_{timestamp}.png'

            fig = go.Figure(data=go.Bar(x=x, y=y))
            fig.update_layout(title='Karma Graph', xaxis_title='Percentile of users', yaxis_title='Total karma')
            fig.update_layout(template='plotly_dark')
            fig.write_image(filename)

            await ctx.send(file=discord.File(filename))
            os.remove(filename)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.hybrid_command(aliases=['plb'], usage="postlb")
    async def postlb(self, ctx: commands.Context):
        """
        Posts the leaderboard of the most upvoted posts.
        """
        stmt_top_server = "SELECT * FROM post WHERE discord_server_id = $1 ORDER BY upvotes DESC LIMIT 5"
        stmt_top_monthly = "SELECT * FROM post WHERE discord_server_id = $1 AND created_at > NOW() - INTERVAL '30 days' ORDER BY upvotes DESC LIMIT 5"
        stmt_top_weekly = "SELECT * FROM post WHERE discord_server_id = $1 AND created_at > NOW() - INTERVAL '7 days' ORDER BY upvotes DESC LIMIT 5"
        async with ctx.typing():
            posts = await self.bot.db.fetch(stmt_top_server, ctx.guild.id)
            monthly_posts = await self.bot.db.fetch(stmt_top_monthly, ctx.guild.id)
            weekly_posts = await self.bot.db.fetch(stmt_top_weekly, ctx.guild.id)

            all_board = await self.create_post_leaderboard(posts)
            month_board = await self.create_post_leaderboard(monthly_posts)
            week_board = await self.create_post_leaderboard(weekly_posts)

        embed = discord.Embed(title='Top Messages')
        embed.set_thumbnail(url=ctx.guild.icon)
        embed.add_field(name='Top 5 All Time', value=all_board, inline=False)
        embed.add_field(name='Top 5 This Month', value=month_board, inline=False)
        embed.add_field(name='Top 5 This Week', value=week_board, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name='checkpost', aliases=['cp'], usage="checkpost <post id>")
    @commands.is_owner()
    async def check_post(self, ctx: commands.Context, post_id: int):
        """
        Checks if a post exists.
        """
        stmt_post = "SELECT * FROM post WHERE id = $1"
        post = await self.bot.db.fetchrow(stmt_post, post_id)
        if post is None:
            embed = discord.Embed(title='That post does not exist.')
            return await ctx.send(embed=embed, delete_after=30)

        server_upvote_emotes = await self._get_karma_upvote_emotes(ctx.guild)
        server_downvote_emotes = await self._get_karma_downvote_emotes(ctx.guild)

        channel = await self.bot.fetch_channel(post['discord_channel_id'])
        message = await channel.fetch_message(post['discord_message_id'])

        upvotes = 0
        downvotes = 0
        for reaction in message.reactions:
            if isinstance(reaction.emoji, (discord.Emoji, discord.PartialEmoji)):
                if reaction.emoji.id in server_upvote_emotes:
                    upvotes += reaction.count - 1
                elif reaction.emoji.id in server_downvote_emotes:
                    downvotes += reaction.count - 1

        old_upvotes = post['upvotes']
        old_downvotes = post['downvotes']
        karma_difference = (old_upvotes - upvotes) + (old_downvotes - downvotes)

        await self.bot.db.execute(UPDATE_KARMA_QUERY, message.author.id, message.guild.id, karma_difference)
        await self.bot.db.execute(
            UPDATE_POST_VOTES_QUERY,
            message.author.id,
            message.guild.id,
            channel.id,
            message.id,
            message.created_at,
            upvotes,
            downvotes
        )

        embed_string = f"""
            Old post upvotes: {old_upvotes}, Old post downvotes: {old_downvotes}\n
            Rechecked post upvotes: {upvotes}, Rechecked post downvotes: {downvotes}\n
            Karma difference: {karma_difference}
        """

        embed = discord.Embed(title=f'Post {post_id} check', description=embed_string)
        await ctx.send(embed=embed, delete_after=60)
        await ctx.message.delete()

    async def create_post_leaderboard(self, posts: list[Record]):
        if not posts:
            return 'No posts found.'
        leaderboard = ''
        for index, post in enumerate(posts, start=1):
            jump_url = self.create_message_url(post['discord_server_id'], post['discord_channel_id'], post['discord_message_id'])
            username = self.bot.get_user(post['discord_user_id']) or await self.bot.fetch_user(post['discord_user_id'])
            leaderboard += f"**{index}.** [{username} ({post['upvotes']})]({jump_url})\n"
        return leaderboard

    def create_message_url(self, server_id, channel_id, message_id):
        return f'https://discordapp.com/channels/{server_id}/{channel_id}/{message_id}'

    @commands.hybrid_group(name='kasino', aliases=['kas'], invoke_without_command=True)
    async def kasino(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @kasino.command(name='open', aliases=['o'], usage="open \"<question>\" \"<option1>\" \"<option2>\"")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        question="The qestion users will bet on.",
        op_a="The first option users can bet on.",
        op_b="The second option users can bet on."
    )
    async def kasino_open(self, ctx: commands.Context, question: str, op_a: str, op_b: str):
        """ Opens a karma kasino which allows people to bet on a question with two options.
            Check "karma" and "votes" commands for more info on karma.
        """
        if not ctx.interaction:
            await ctx.message.delete()
        async with ctx.typing():
            kasino_id = await self.add_kasino(ctx, question, op_a, op_b)
            # TODO: Add kasino backup
            # self.create_kasino_backup(kasino.id)
        await self.update_kasino_msg(ctx, kasino_id)

    @kasino.command(name='close', usage="close <kasino_id> <winning_option>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        kasino_id="The ID of the kasino you want to close. The ID should be visible in the kasino message.",
        winner="The winning option. 1 or 2. 3 to abort."
    )
    async def kasino_close(self, ctx: commands.Context, kasino_id: int, winner: int):
        """ Closes a karma kasino and announces the winner. To cancel the kasino, use 3 as the winner."""
        kasino = await self.bot.db.fetchrow('SELECT * FROM kasino WHERE id = $1', kasino_id)

        if kasino is None:
            return await ctx.author.send(f'Kasino with ID {kasino_id} is not open.')

        if kasino['discord_server_id'] != ctx.guild.id:
            return await ctx.send(f'Kasino with ID {kasino_id} is not in this server.', delete_after=120)

        if winner in {1, 2}:
            await self.win_kasino(kasino_id, winner)
        elif winner == 3:
            await self.abort_kasino(kasino_id)
        else:
            return await ctx.author.send('Winner has to be 1, 2 or 3 (abort)')

        author_avatar = ctx.author.display_avatar.url
        await self.send_conclusion(ctx, kasino_id, winner, ctx.author, author_avatar)
        await self.remove_kasino(kasino_id)
        await ctx.message.delete()

    @kasino_close.error
    async def kasino_close_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.errors.MissingRequiredArgument):
            msg = f'You didn\'t provide a required argument! Correct usage is `{ctx.prefix}kasino close <kasino_id> <winning_option>`'
            await ctx.send(msg, delete_after=20)
        elif isinstance(error, commands.errors.BadArgument):
            await ctx.send('Bad argument.', delete_after=20)
        await ctx.message.delete()

    @kasino.command(name='lock', usage="lock <kasino_id>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        kasino_id="The ID of the kasino you want to lock. The ID should be visible in the kasino message."
    )
    async def kasino_lock(self, ctx: commands.Context, kasino_id: int):
        """ Locks a karma kasino. Users can no longer bet on this kasino."""
        kasino = await self.bot.db.fetchrow('SELECT locked FROM kasino WHERE id = $1', kasino_id)
        if kasino is None:
            return await ctx.author.send(f'Kasino with ID `{kasino_id}` does not exist.')
        if kasino['locked']:
            return await ctx.author.send(f'Kasino with ID `{kasino_id}` is already locked.')

        await self.bot.db.execute('UPDATE kasino SET locked = True WHERE id = $1', kasino_id)
        await self.update_kasino_msg(ctx, kasino_id)
        await ctx.message.delete()

    @kasino.command(name='bet', usage="bet <kasino_id> <amount> <option>")
    @app_commands.describe(
        kasino_id="The ID of the kasino you want to bet on. The ID should be visible in the kasino message.",
        amount="The amount of karma you want to bet. `all` to bet all your karma.",
        option="The option you want to bet on. 1 or 2."
    )
    async def kasino_bet(self, ctx: commands.Context, kasino_id: int, amount: str, option: int):
        """ Bets karma on a kasino. You can only bet on a kasino that is not locked."""
        output_embed = discord.Embed(color=discord.Colour.from_rgb(209, 25, 25))

        if option not in [1, 2]:
            output_embed.title=f'Wrong usage. Correct usage is `{ctx.prefix}kasino bet <kasino_id> <amount> <1 or 2>`'
            return await ctx.author.send(embed=output_embed, delete_after=30)
        
        if amount != 'all':
            try:
                amount = int(amount)
            except ValueError:
                output_embed.title=f'Wrong usage. Correct usage is `{ctx.prefix}kasino bet <kasino_id> <amount> <1 or 2>`'
                return await ctx.author.send(embed=output_embed, delete_after=30)
            if amount < 1:
                output_embed.title='You tried to bet < 1 karma! Silly you!'
                return await ctx.author.send(embed=output_embed, delete_after=30)

        kasino = await self.bot.db.fetchrow('SELECT * FROM kasino WHERE id = $1', kasino_id)

        if kasino is None:
            output_embed.title=f'Kasino with ID {kasino_id} is not open.'
            return await ctx.author.send(embed=output_embed, delete_after=30)

        if kasino['locked']:
            output_embed.title=f'kasino with ID {kasino_id} is locked.'
            return await ctx.author.send(embed=output_embed, delete_after=30)

        bettor_karma = await self._get_user_karma(ctx.author.id, ctx.guild.id)
        if bettor_karma is None:
            return await ctx.send('You do not have any karma.')

        amount = bettor_karma if amount == "all" else amount

        if bettor_karma < amount:
            output_embed.title=f'You don\'t have that much karma. Your karma: {bettor_karma}'
            return await ctx.author.send(embed=output_embed, delete_after=30)

        total_bet = amount
        output = 'added'

        stmt_bet = 'SELECT * FROM kasino_bet WHERE kasino_id = $1 AND discord_user_id = $2;'
        user_bet = await self.bot.db.fetchrow(stmt_bet, kasino_id, ctx.author.id)
        if user_bet is not None:
            if user_bet['option'] != option:
                output_embed.title = f'You can\'t change your choice on the bet with id {kasino_id}. No chickening out!'
                return await ctx.author.send(embed=output_embed)
            total_bet = user_bet['amount'] + amount
            output = 'increased'
        stmt_bet = '''INSERT INTO kasino_bet (kasino_id, discord_user_id, amount, option) VALUES ($1, $2, $3, $4)
                      ON CONFLICT (kasino_id, discord_user_id) DO UPDATE SET amount = kasino_bet.amount + $3;
                      UPDATE user_karma SET karma = user_karma.karma - $3 WHERE discord_user_id = $2 AND discord_server_id = $5;'''
        await self.bot.db.execute(stmt_bet, kasino_id, ctx.author.id, amount, option, ctx.guild.id)

        output_embed.title = f'**Successfully {output} bet on option {option}, on kasino with ID {kasino_id} for {amount} karma! Total bet is now: {total_bet} Karma**'
        output_embed.color = discord.Colour.from_rgb(52, 79, 235)
        output_embed.description = f'Remaining karma: {bettor_karma - amount}'

        await self.update_kasino_msg(ctx, kasino_id)
        await ctx.author.send(embed=output_embed)
        await ctx.send(f"Bet added from {ctx.author}!", delete_after=30)
        await ctx.message.delete()

    @kasino.command(name='list', aliases=['l'], usage="list")
    async def kasino_list(self, ctx: commands.Context):
        """ Lists all open kasinos on the server. """
        embed = discord.Embed(title='Open kasinos')
        stmt_kasinos = 'SELECT * FROM kasino WHERE locked = False AND discord_server_id = $1 ORDER BY id ASC;'
        all_kasinos = await self.bot.db.fetch(stmt_kasinos, ctx.guild.id)
        embed_kasinos = ''.join(f'`{entry["id"]}` - {entry["question"]}\n' for entry in all_kasinos)
        embed.description = embed_kasinos or "No open kasinos found."
        await ctx.send(embed=embed, delete_after=300)
        await ctx.message.delete()

    @kasino.command(name='resend', usage="resend <kasino_id>")
    @commands.cooldown(1, 30)
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        kasino_id="The ID of the kasino you want to resend."
    )
    async def resend_kasino(self, ctx: commands.Context, kasino_id: int):
        """ Resends a kasino message if it got lost in the channel. """
        kasino = await self.bot.db.fetchrow('SELECT * FROM kasino WHERE id = $1', kasino_id)
        if kasino is None:
            await ctx.send('Kasino not found.')
            return
        k_channel_id = kasino['discord_channel_id']
        k_message_id = kasino['discord_message_id']
        k_channel = await self.bot.fetch_channel(k_channel_id)
        try:
            kasino_msg = await k_channel.fetch_message(k_message_id)
            await kasino_msg.delete()
        except discord.NotFound:
            pass
        new_kasino_msg = await ctx.send(embed=discord.Embed(description='Loading...'))
        stmt_update_kasino = 'UPDATE kasino SET discord_channel_id = $1, discord_message_id = $2 WHERE id = $3;'
        await self.bot.db.execute(stmt_update_kasino, ctx.channel.id, new_kasino_msg.id, kasino_id)
        await self.update_kasino_msg(ctx, kasino_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self.process_reaction(payload, add_reaction=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self.process_reaction(payload, add_reaction=False)

    async def process_reaction(self, payload: discord.RawReactionActionEvent, add_reaction: bool) -> None:
        user = await self.check_payload(payload)
        if user is None:
            return

        upvote_emotes = await self._get_karma_upvote_emotes(payload.guild_id)
        downvote_emotes = await self._get_karma_downvote_emotes(payload.guild_id)

        if payload.emoji.id in upvote_emotes:
            karma_modifier = 1 if add_reaction else -1
            post_votes_modifier = 1 if add_reaction else 0
        elif payload.emoji.id in downvote_emotes:
            karma_modifier = -1 if add_reaction else 1
            post_votes_modifier = -1 if add_reaction else 0
        else:
            return
        
        server = self.bot.get_guild(payload.guild_id) or await self.bot.fetch_guild(payload.guild_id)
        channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)

        await self._insert_user(user)
        await self._insert_server(server)
        await self._insert_channel(channel)

        await self._update_karma(payload, user, karma_modifier)
        await self._update_post_votes(payload, user, post_votes_modifier, 0)
            
    async def _insert_user(self, user: discord.Member):
        stmt = '''
            INSERT INTO discord_user (discord_user_id, username, avatar) VALUES ($1, $2, $3)
            ON CONFLICT (discord_user_id) DO UPDATE SET username = $2, avatar = $3;'''
        await self.bot.db.execute(stmt, user.id, user.display_name, user.display_avatar.url)

    async def _insert_server(self, server: discord.Guild):
        stmt = '''
            INSERT INTO discord_server (discord_server_id, server_name) VALUES ($1, $2)
            ON CONFLICT (discord_server_id) DO UPDATE SET server_name = $2;'''
        await self.bot.db.execute(stmt, server.id, server.name)

    async def _insert_channel(self, channel: discord.abc.GuildChannel):
        if isinstance(channel, discord.Thread) and channel.parent:
            await self._insert_channel(channel.parent)
        stmt = '''
            INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id)
            VALUES ($1, $2, $3, $4) ON CONFLICT (discord_channel_id) DO UPDATE SET channel_name = $2;'''
        parent_channel_id = channel.parent_id if isinstance(channel, discord.Thread) else None
        await self.bot.db.execute(stmt, channel.id, channel.name, channel.guild.id, parent_channel_id)

    async def _update_karma(self, payload: discord.RawReactionActionEvent, user: discord.Member, amount: int):
        await self.bot.db.execute(
            UPDATE_KARMA_QUERY,
            user.id,
            payload.guild_id,
            amount
        )

    async def _update_post_votes(self, payload: discord.RawReactionActionEvent, user: discord.Member, upvote: int, downvote: int):
        message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
        await self.bot.db.execute(
            UPDATE_POST_VOTES_QUERY,
            user.id,
            payload.guild_id,
            payload.channel_id,
            payload.message_id,
            message.created_at.utcnow(),
            upvote,
            downvote
        )

    async def _get_user_karma(self, user_id: int, guild_id: int) -> int:
        stmt = "SELECT amount FROM karma WHERE discord_user_id = $1 AND discord_server_id = $2"
        return await self.bot.db.fetchval(stmt, user_id, guild_id)
    
    async def _get_karma_emote_by_id(self, server_id: int, emote: discord.Emoji) -> Record:
        stmt = "SELECT * FROM karma_emote WHERE discord_server_id = $1 AND discord_emote_id = $2"
        return await self.bot.db.fetchrow(stmt, server_id, emote.id)
    
    async def _get_karma_upvote_emotes(self, guild_id: int) -> list[int]:
        stmt_upvotes = "SELECT discord_emote_id FROM karma_emote WHERE discord_server_id = $1 AND increase_karma = True"
        emote_records = await self.bot.db.fetch(stmt_upvotes, guild_id)
        server_upvote_emotes = [emote['discord_emote_id'] for emote in emote_records]
        server_upvote_emotes.append(int(values.UPVOTE_EMOTE_ID))
        return server_upvote_emotes

    async def _get_karma_downvote_emotes(self, guild_id: int) -> list[int]:
        stmt_downvotes = "SELECT discord_emote_id FROM karma_emote WHERE discord_server_id = $1 AND increase_karma = False"
        emote_records = await self.bot.db.fetch(stmt_downvotes, guild_id)
        server_downvote_emotes = [emote['discord_emote_id'] for emote in emote_records]
        server_downvote_emotes.append(int(values.DOWNVOTE_EMOTE_ID))
        return server_downvote_emotes

    async def check_payload(self, payload: discord.RawReactionActionEvent) -> discord.Member | None:
        if payload.event_type == 'REACTION_ADD' and payload.member.bot:
            return None
        try:
            message = await self.__get_message_from_payload(payload)
        except discord.errors.NotFound:
            return None
        if message.author.bot:
            return None
        reaction_user = payload.member or self.bot.get_user(payload.user_id) or await self.bot.fetch_user(payload.user_id)
        if reaction_user == message.author:
            return None
        return message.author

    async def __get_message_from_payload(self, payload: discord.RawReactionActionEvent) -> discord.Message | None:
        potential_message = [message for message in self.bot.cached_messages if message.id == payload.message_id]
        cached_message = potential_message[0] if potential_message else None
        return cached_message or await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)

    async def send_conclusion(self, ctx: commands.Context, kasino_id: int, winner: int, author, author_avatar: str):
        kasino = await self.bot.db.fetchrow('SELECT * FROM kasino WHERE id = $1', kasino_id)
        total_karma = await self.bot.db.fetchval('SELECT SUM(amount) FROM kasino_bet WHERE kasino_id = $1', kasino_id)
        to_embed = discord.Embed(color=discord.Colour.from_rgb(52, 79, 235))

        winner_option = kasino['option1'] if winner == 1 else kasino['option2']
        if winner in [1, 2]:
            to_embed.title = f':tada: "{winner_option}" was correct! :tada:'
            to_embed.description = f"""Question: {kasino['question']}
                                       If you\'ve chosen {winner}, you\'ve just won karma!
                                       Distributed to the winners: **{total_karma} Karma**'
                                    """
        elif winner == 3:
            to_embed.title = f':game_die: "{kasino["question"]}" has been cancelled.',
            to_embed.description = f'Amount bet will be refunded to each user.\nReturned: {total_karma} Karma'

        to_embed.set_footer(
            text=f'as decided by {author}',
            icon_url=author_avatar
        )
        to_embed.set_thumbnail(url='https://cdn.betterttv.net/emote/602548a4d47a0b2db8d1a3b8/3x.gif')
        await ctx.send(embed=to_embed)
        return

    async def add_kasino(self, ctx: commands.Context, question: str, option1: str, option2: str) -> int:
        to_embed = discord.Embed(description="Opening kasino, hold on tight...")
        kasino_msg = await ctx.send(embed=to_embed)

        stmt_kasino = '''INSERT INTO kasino (discord_server_id, discord_channel_id, discord_message_id, question, option1, option2)
                         VALUES ($1, $2, $3, $4, $5, $6) RETURNING id'''
        return await self.bot.db.fetchval(stmt_kasino, ctx.guild.id, ctx.channel.id, kasino_msg.id, question, option1, option2)

    async def remove_kasino(self, kasino_id: int) -> None:
        kasino = await self.bot.db.fetchrow('SELECT * FROM kasino WHERE id = $1', kasino_id)
        if kasino is None:
            return
        try:
            kasino_channel = await self.bot.fetch_channel(kasino.discord_channel_id)
            kasino_msg = await kasino_channel.fetch_message(kasino.discord_message_id)
            await kasino_msg.delete()
        except discord.errors.NotFound:
            pass
        await self.bot.db.execute('DELETE FROM kasino WHERE id = $1', kasino_id)

    def create_kasino_backup(self, kasino_id: int):
        today_string = datetime.now().strftime("%Y_%m_%d")
        now_time_string = datetime.now().strftime("%H%M")
        backup_folder = f"{values.DATA_PATH}/backups/{today_string}"
        Path(backup_folder).mkdir(parents=True, exist_ok=True)
        shutil.copy(values.DB_PATH, f"{backup_folder}/backup_{now_time_string}_{kasino_id}.sqlite")

    async def abort_kasino(self, kasino_id: int) -> None:
        stmt_kasino_and_bets = '''SELECT * FROM kasino JOIN kasino_bet ON kasino.id = kasino_bet.kasino_id
                                  WHERE kasino.id = $1'''
        kasino_and_bets = await self.bot.db.fetch(stmt_kasino_and_bets, kasino_id)
        stmt_update_user_karma = '''UPDATE user_karma SET karma = karma + $1
                                    WHERE discord_user_id = $2 AND discord_server_id = $3'''
        for bet in kasino_and_bets:
            await self.bot.db.execute(stmt_update_user_karma, bet['amount'], bet['discord_user_id'], bet['discord_server_id'])
            user_karma = await self._get_user_karma(bet['discord_user_id'], bet['discord_server_id'])
            output = discord.Embed(
                title=f'**You\'ve been refunded {bet["amount"]} karma.**',
                color=discord.Colour.from_rgb(52, 79, 235),
                description=f'Question was: {bet["question"]}\n'
                            f'Remaining karma: {user_karma}'
            )
            user = self.bot.get_user(bet['discord_user_id']) or await self.bot.fetch_user(bet['discord_user_id'])
            await user.send(embed=output)

    async def win_kasino(self, kasino_id: int, winning_option: int):
        stmt_kasino_and_bets = '''SELECT * FROM kasino JOIN kasino_bet ON kasino.id = kasino_bet.kasino_id
                                  WHERE kasino.id = $1'''
        kasino_and_bets = await self.bot.db.fetch(stmt_kasino_and_bets, kasino_id)
        total_kasino_karma = sum(kb['amount'] for kb in kasino_and_bets)
        winners_bets = [kb for kb in kasino_and_bets if kb['option'] == winning_option]
        total_winner_karma = sum(kb['amount'] for kb in winners_bets)
        server_id = kasino_and_bets[0]['discord_server_id']
        question = kasino_and_bets[0]['question']

        if total_winner_karma is None:
            total_winner_karma = 0

        for bet in winners_bets:
            win_ratio = bet['amount'] / total_winner_karma
            win_amount = round(win_ratio * total_kasino_karma)
            user_id = bet['discord_user_id']

            stmt_update_user_karma = '''UPDATE user_karma SET karma = karma + $1
                                        WHERE discord_user_id = $2 AND discord_server_id = $3'''
            await self.bot.db.execute(stmt_update_user_karma, win_amount, user_id, server_id)
            user_karma = await self._get_user_karma(user_id, server_id)
            output = discord.Embed(
                title=f':tada: **You\'ve won {win_amount} karma!** :tada:',
                color=discord.Colour.from_rgb(66, 186, 50),
                description=f'(Of which {bet["amount"]} you put down on the table)\n'
                            f'Question was: {question}\n'
                            f'New karma balance: {user_karma}'
            )
            await (await self.bot.fetch_user(user_id)).send(embed=output)

        losers_bets = [kb for kb in kasino_and_bets if kb['option'] != winning_option]
        for bet in losers_bets:
            user_id = bet['discord_user_id']
            user_karma = await self._get_user_karma(user_id, server_id)
            icon = ':chart_with_downwards_trend:'
            output = discord.Embed(
                title = f'{icon} **You\'ve unfortunately lost {bet["amount"]} karma...** {icon}',
                color = discord.Colour.from_rgb(209, 25, 25),
                description = f'Question was: {question}\n'
                              f'New karma balance: {user_karma}'
            )
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(embed=output)

    async def update_kasino_msg(self, ctx: commands.Context, kasino_id: int) -> None:
        kasino = await self.bot.db.fetchrow('SELECT * FROM kasino WHERE id = $1', kasino_id)
        k_channel_id = kasino['discord_channel_id']
        k_message_id = kasino['discord_message_id']
        kasino_channel = await self.bot.fetch_channel(k_channel_id)
        kasino_msg = await kasino_channel.fetch_message(k_message_id)

        # FIGURE OUT AMOUNTS AND ODDS
        stmt_kasino_bets_sum = '''SELECT SUM(amount) FROM kasino_bet WHERE kasino_id = $1 AND option = $2'''
        bets_a_amount = await self.bot.db.fetchval(stmt_kasino_bets_sum, kasino_id, 1) or 0.0
        bets_b_amount = await self.bot.db.fetchval(stmt_kasino_bets_sum, kasino_id, 2) or 0.0

        bets_a_amount = float(bets_a_amount)
        bets_b_amount = float(bets_b_amount)

        total_bets = bets_a_amount + bets_b_amount
        a_odds = total_bets / bets_a_amount if bets_a_amount else 1.0
        b_odds = total_bets / bets_b_amount if bets_b_amount else 1.0

        # CREATE MESSAGE
        description = f"The kasino has been opened!\nPlace your bets using `{ctx.prefix}kasino bet {kasino_id} <amount> <1 or 2>`"
        if kasino['locked'] :
            description = 'The kasino is locked! No more bets are taken in. Time to wait and see...'
        to_embed = discord.Embed(
            title=f'{"[LOCKED] " if kasino["locked"] else ""}:game_die: {kasino["question"]}',
            description=description,
            color=discord.Colour.from_rgb(52, 79, 235)
        )
        to_embed.set_footer(text=f'On the table: {bets_a_amount + bets_b_amount} Karma | ID: {kasino_id}')
        to_embed.set_thumbnail(url='https://cdn.betterttv.net/emote/602548a4d47a0b2db8d1a3b8/3x.gif')
        to_embed.add_field(name=f'**1:** {kasino["option1"]}',
                           value=f'**Odds:** 1:{round(a_odds, 2)}\n**Pool:** {bets_a_amount} Karma')
        to_embed.add_field(name=f'**2:** {kasino["option2"]}',
                           value=f'**Odds:** 1:{round(b_odds, 2)}\n**Pool:** {bets_b_amount} Karma')

        await kasino_msg.edit(embed=to_embed)


async def setup(bot):
    query = await bot.db.fetch('SELECT * FROM discord_channel WHERE upvote = True')
    upvote_channels = [channel['discord_channel_id'] for channel in query] or []
    await bot.add_cog(Karma(bot, upvote_channels))
