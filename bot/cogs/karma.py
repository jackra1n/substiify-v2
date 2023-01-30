import logging
import shutil
from datetime import datetime
from pathlib import Path

import discord
from asyncpg import Record
from core import values
from core.bot import Substiify
from discord.ext import commands

logger = logging.getLogger(__name__)


class Karma(commands.Cog):

    COG_EMOJI = "☯️"

    def __init__(self, bot: Substiify):
        self.bot = bot
        self.vote_channels = None
        bot.loop.create_task(self.load_vote_channels())

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
            embed = discord.Embed(color=0x23b40c)
            embed.description = f'Votes are **ALREADY enabled** in {ctx.channel.mention}!'
        else:
            embed = discord.Embed(color=0xf66045)
            embed.description = f'Votes are **NOT enabled** in {ctx.channel.mention}!'
        await ctx.send(embed=embed, delete_after=10)

    @votes.command(name='list')
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def list_votes(self, ctx):
        """
        Lists all the votes that are enabled in the server
        """
        upvote_channels = await self.bot.db.get_votes_channels(ctx.guild)
        channels_string = '\n'.join([f"{x['discord_channel_id']} ({x['channel_name']})" for x in upvote_channels])
        embed = discord.Embed(color=0x23b40c)
        embed.description = f'Votes are enabled in the following channels: {channels_string}'
        await ctx.send(embed=embed, delete_after=20)

    @votes.command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def start(self, ctx, channel: discord.TextChannel = None):
        """
        Enables votes in the current or specified channel. Requires Manage Channels permission.
        After enabling votes, the bot will add the upvote and downvote reactions to every message in the channel.
        This is good for something like a meme channel if you want to give upvotes and downvotes to the messages.

        If users click the reactions, user karma will be updated.
        """
        channel = channel or ctx.channel
        if channel.id not in self.vote_channels:
            self.vote_channels.append(channel.id)
        votes_enabled = await self.bot.db.get_vote_channel(channel)
        if not votes_enabled:
            await self.bot.db.update_channel_votes(channel, True)
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
    async def stop(self, ctx, channel: discord.TextChannel = None):
        """
        Disables votes in the current channel. Requires Manage Channels permission.
        """
        channel = channel or ctx.channel
        await self.bot.db.update_channel_votes(channel, False)
        if channel.id in self.vote_channels:
            self.vote_channels.remove(channel.id)
        await ctx.message.delete()
        await ctx.send(embed=discord.Embed(description=f'Votes has been stopped in {channel.mention}!', color=0xf66045))

    async def load_vote_channels(self) -> list:
        query = await self.bot.db.get_all_votes_channels()
        self.vote_channels = [x.discord_channel_id for x in query] if query is not None else []

    def get_upvote_emote(self):
        return self.bot.get_emoji(values.UPVOTE_EMOTE_ID)

    def get_downvote_emote(self):
        return self.bot.get_emoji(values.DOWNVOTE_EMOTE_ID)

    @commands.group(aliases=["k"], usage="karma [user]", invoke_without_command=True,)
    async def karma(self, ctx, user: discord.User = None):
        """
        Shows the karma of a user. If you dont specify a user, it will show your own.
        If you want to know what emote reactions are used for karma, use the subcommand `karma emotes`
        """
        if user is None:
            user = ctx.author
        if user.bot:
            return
        user_karma = await self.bot.db.get_user_karma(user.id, ctx.guild.id)
        user_karma = 0 if user_karma is None else user_karma
        embed = discord.Embed(title=f'Karma - {ctx.guild.name}', description=f'{user.mention} has {user_karma} karma.')
        await ctx.send(embed=embed, delete_after=120)
        await ctx.message.delete()

    @commands.cooldown(3, 10)
    @karma.command(name="donate", aliases=["wiretransfer", "wt"], usage="donate <amount> <user>")
    async def karma_donate(self, ctx, amount: int, user: discord.User):
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
        donator_karma = await self.bot.db.get_user_karma(ctx.author.id, ctx.guild.id)
        if donator_karma is None:
            embed.description = 'You don\'t have any karma!'
            return await ctx.send(embed=embed)
        if donator_karma < amount:
            embed.description = 'You don\'t have enough karma!'
            return await ctx.send(embed=embed)
        await self.bot.db.upsert_user_karma(user.id, ctx.guild.id, amount)
        await self.bot.db.upsert_user_karma(ctx.author.id, ctx.guild.id, -amount)
        embed = discord.Embed(color=0x23b40c)
        embed.description = f'{ctx.author.mention} has donated {amount} karma to {user.mention}!'
        await ctx.send(embed=embed)
        await ctx.message.delete()

    @karma_donate.error
    async def karma_donate_error(self, ctx, error):
        embed = discord.Embed(color=0xf66045)
        if isinstance(error, commands.MissingRequiredArgument):
            embed.description = 'You didn\'t specify a user to donate to!'
        elif isinstance(error, commands.BadArgument):
            embed.description=f'Wrong command usage! Command usage is `{ctx.prefix}karma donate <amount> <user>`'
        await ctx.send(embed=embed)
        await ctx.message.delete()

    @karma.group(name='emotes', aliases=['emote'], usage="emotes", invoke_without_command=True)
    async def karma_emotes(self, ctx):
        """
        Shows the karma emotes of the server. Emotes in the `add` category increase karma,
        while emotes in the `remove` category decrease karma.
        If you want to add or remove an emote from the karma system,
        check the subcommand `karma emotes add` or `karma emotes remove`
        """
        karma_emotes = await self.bot.db.get_karma_emotes(ctx.guild.id)
        if not karma_emotes:
            return await ctx.send(embed=discord.Embed(title='No emotes found.'), delete_after=60)
        embed_string = ''
        last_action = ''
        for emote in karma_emotes:
            if emote.action != last_action:
                embed_string += f'\n`{"add" if emote.action == 0 else "remove"}:` '
                last_action = emote.action
            embed_string += f"{self.bot.get_emoji(emote['discord_emote_id'])} "
        embed = discord.Embed(title=f'Karma Emotes - {ctx.guild.name}', description=embed_string)
        await ctx.send(embed=embed, delete_after=60)
        await ctx.message.delete()

    @karma_emotes.command(name='add', usage="add <emote> <action>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def karma_emote_add(self, ctx, emote: discord.Emoji, emote_action: int):
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
        existing_emote = await self.bot.db.get_karma_emote_by_id(ctx.guild, emote)
        if existing_emote is not None:
            embed = discord.Embed(title='That emote is already added.')
            return await ctx.send(embed=embed, delete_after=30)
        max_emotes = await self.bot.db.get_karma_emotes_count(ctx.guild.id)
        if max_emotes >= 10:
            embed = discord.Embed(title='You can only have 10 emotes.')
            return await ctx.send(embed=embed, delete_after=30)
        await self.bot.db.insert_karma_emote(ctx.guild, emote, emote_action)
        embed = discord.Embed(title=f'Emote {emote} added to the list.')
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @karma_emotes.command(name='remove', aliases=['delete'], usage="remove <emote>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def karma_emote_remove(self, ctx, emote: discord.Emoji):
        """
        Remove an emote from the karma emotes for this server.
        """
        existing_emote = await self.bot.db.get_karma_emote_by_id(ctx.guild, emote)
        if existing_emote is None:
            embed = discord.Embed(title='That emote is not in the list.')
            return await ctx.send(embed=embed, delete_after=20)
        await self.bot.db.delete_karma_emote(ctx.guild, emote)
        embed = discord.Embed(title=f'Emote {emote} removed from the list.')
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @karma.command(name='leaderboard', aliases=['lb', 'leaderbord'], usage="leaderboard")
    async def karma_leaderboard(self, ctx, global_leaderboard: str = None):
        """
        Shows users with the most karma on the server.
        """
        async with ctx.typing():
            embed = discord.Embed(title='Karma Leaderboard')
            if global_leaderboard is None:
                results = await self.bot.db.get_karma_leaderboard(ctx.guild)
            elif global_leaderboard == 'global':
                results = await self.bot.db.get_karma_leaderboard_global()
            if not results:
                embed.description = 'No users have any karma.'
            embed.description = ''
            for index, entry in enumerate(results, start=1):
                user = await self.bot.fetch_user(entry['discord_user_id'])
                embed.description += f"`{str(index).rjust(2)}.` | `{entry['amount']}` - {user.mention}\n"
            await ctx.send(embed=embed)
            await ctx.message.delete()

    @commands.command(aliases=['plb'], usage="postlb")
    async def postlb(self, ctx):
        """
        Posts the leaderboard of the most upvoted posts.
        """
        async with ctx.typing():
            posts = await self.bot.db.get_top_servers_posts(ctx.guild)
            monthly_posts = await self.bot.db.get_top_servers_posts_monthly(ctx.guild)
            weekly_posts = await self.bot.db.get_top_servers_posts_weekly(ctx.guild)

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
    async def check_post(self, ctx, post_id):
        """
        Checks if a post exists.
        """
        post: Record = await self.bot.db.get_post_by_message_id(post_id)
        if post is None:
            embed = discord.Embed(title='That post does not exist.')
            return await ctx.send(embed=embed, delete_after=30)

        server_upvote_emotes = await self.bot.db.get_upvote_karma_emote_ids_by_server(ctx.guild.id)
        server_downvote_emotes = await self.bot.db.get_downvote_karma_emote_ids_by_server(ctx.guild.id)

        server_upvote_emotes.append(values.UPVOTE_EMOTE_ID)
        server_downvote_emotes.append(values.DOWNVOTE_EMOTE_ID)

        channel = await self.bot.fetch_channel(post.discord_channel_id)
        message = await channel.fetch_message(post.discord_message_id)

        upvote_reactions = 0
        downvote_reactions = 0
        for reaction in message.reactions:
            if isinstance(reaction.emoji, (discord.Emoji, discord.PartialEmoji)):
                if reaction.emoji.id in server_upvote_emotes:
                    upvote_reactions += reaction.count - 1
                elif reaction.emoji.id in server_downvote_emotes:
                    downvote_reactions += reaction.count - 1

        old_upvotes = post['upvotes']
        old_downvotes = post['downvotes']

        karma_difference = (old_upvotes - upvote_reactions) + (old_downvotes - downvote_reactions)
        await self.bot.db.update_user_karma(post['discord_user_id'], post['discord_server_id'], karma_difference)
        await self.bot.db.update_post_upvotes_and_downvotes(post['discord_message_id'], upvote_reactions, downvote_reactions)

        embed_string = f"""Old post upvotes: {old_upvotes}, Old post downvotes: {old_downvotes}\n
                           Rechecked post upvotes: {upvote_reactions}, Rechecked post downvotes: {downvote_reactions}\n
                           Karma difference: {karma_difference}
                        """

        embed = discord.Embed(title=f'Post {post_id} check', description=embed_string)
        await ctx.send(embed=embed, delete_after=60)
        await ctx.message.delete()

    async def create_post_leaderboard(self, posts: list[Record]):
        leaderboard = ''
        for index, post in enumerate(posts, start=1):
            jump_url = self.create_message_url(post['discord_server_id'], post['discord_channel_id'], post['discord_message_id'])
            leaderboard += f"**{index}.** [{post['username']} ({post['upvotes']})]({jump_url})\n"
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
        async with ctx.typing():
            kasino_id = await self.add_kasino(ctx, question, op_a, op_b)
            # self.create_kasino_backup(kasino.id)
        await self.update_kasino_msg(ctx, kasino_id)

    @kasino.command(name='close', usage="close <kasino_id> <winning_option>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def kasino_close(self, ctx, kasino_id: int, winner: int):
        author_img = ctx.author.avatar

        kasino = await self.bot.db.get_kasino(kasino_id)

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

        await self.send_conclusion(ctx, kasino_id, winner, ctx.author, author_img)
        await self.remove_kasino(kasino_id)
        await ctx.message.delete()

    @kasino_close.error
    async def kasino_close_error(self, ctx, error):
        if isinstance(error, commands.errors.MissingRequiredArgument):
            msg = f'You didn\'t provide a required argument! Correct usage is `{ctx.prefix}kasino close <kasino_id> <winning_option>`'
            await ctx.send(msg, delete_after=20)
        elif isinstance(error, commands.errors.BadArgument):
            await ctx.send('Bad argument.', delete_after=20)
        await ctx.message.delete()

    @kasino.command(name='lock', usage="lock <kasino_id>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def kasino_lock(self, ctx, kasino_id: int):
        kasino = await self.bot.db.get_kasino(kasino_id)
        if kasino is None:
            return await ctx.author.send(f'Kasino with ID `{kasino_id}` does not exist.')
        if kasino['locked']:
            return await ctx.author.send(f'Kasino with ID `{kasino_id}` is already locked.')

        await self.bot.db.update_kasino_lock_status(kasino_id, True)
        await self.update_kasino_msg(ctx, kasino_id)
        await ctx.message.delete()

    @kasino.command(name='bet', usage="bet <kasino_id> <amount> <option>")
    async def kasino_bet(self, ctx, kasino_id: int, amount: str, option: int):
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

        kasino = await self.bot.db.get_kasino(kasino_id)

        if kasino is None:
            output_embed.title=f'Kasino with ID {kasino_id} is not open.'
            return await ctx.author.send(embed=output_embed, delete_after=30)

        if kasino['locked']:
            output_embed.title=f'kasino with ID {kasino_id} is locked.'
            return await ctx.author.send(embed=output_embed, delete_after=30)

        bettor_karma = await self.bot.db.get_user_karma(ctx.author.id, ctx.guild.id)
        if bettor_karma is None:
            return await ctx.send('You do not have any karma.')

        amount = bettor_karma if amount == "all" else amount

        if bettor_karma < amount:
            output_embed.title=f'You don\'t have that much karma. Your karma: {bettor_karma}'
            return await ctx.author.send(embed=output_embed, delete_after=30)

        total_bet = amount
        output = 'added'

        user_bet = await self.bot.db.get_kasino_user_bet(kasino_id, ctx.author.id)
        if user_bet is not None:
            if user_bet['option'] != option:
                output_embed.title = f'You can\'t change your choice on the bet with id {kasino_id}. No chickening out!'
                return await ctx.author.send(embed=output_embed)
            total_bet = user_bet['amount'] + amount
            output = 'increased'
        await self.bot.db.insert_bet(kasino_id, ctx.author.id, amount, option)
        await self.bot.db.update_user_karma(ctx.author.id, ctx.guild.id, -amount)

        output_embed.title = f'**Successfully {output} bet on option {option}, on kasino with ID {kasino_id} for {amount} karma! Total bet is now: {total_bet} Karma**'
        output_embed.color = discord.Colour.from_rgb(52, 79, 235)
        output_embed.description = f'Remaining karma: {bettor_karma - amount}'

        await self.update_kasino_msg(ctx, kasino_id)
        await ctx.author.send(embed=output_embed)
        await ctx.send(f"Bet added from {ctx.author}!", delete_after=30)
        await ctx.message.delete()

    @kasino.command(name='list', aliases=['l'], usage="list")
    async def kasino_list(self, ctx):
        embed = discord.Embed(title='Open kasinos')
        all_kasinos = await self.bot.db.get_kasino_by_server(ctx.guild.id)
        embed_kasinos = ''.join(f'`{entry["id"]}` - {entry["question"]}\n' for entry in all_kasinos)
        embed.description = embed_kasinos or "No open kasinos found."
        await ctx.send(embed=embed, delete_after=300)
        await ctx.message.delete()

    @kasino.command(name='resend', usage="resend <kasino_id>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def resend_kasino(self, ctx, kasino_id: int):
        kasino = await self.bot.db.get_kasino(kasino_id)
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
        await self.bot.db.update_kasino_message(kasino_id, ctx.channel.id, new_kasino_msg.id)
        await self.update_kasino_msg(ctx, kasino_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        user = await self.check_payload(payload)
        if user is None:
            return
        if payload.emoji.id in await self.get_query_karma_add(payload.guild_id):
            await self.bot.db.update_user_karma(user.id, payload.guild_id, 1)
            await self.bot.db.update_post_upvotes_and_downvotes(payload.message_id, 1, 0)
        elif payload.emoji.id in await self.get_query_karma_remove(payload.guild_id):
            await self.bot.db.update_user_karma(user.id, payload.guild_id, -1)
            await self.bot.db.update_post_upvotes_and_downvotes(payload.message_id, 0, 1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        user = await self.check_payload(payload)
        if user is None:
            return
        if payload.emoji.id in await self.get_query_karma_add(payload.guild_id):
            await self.bot.db.update_user_karma(user.id, payload.guild_id, -1)
            await self.bot.db.update_post_upvotes_and_downvotes(payload.message_id, 0, 1)
        elif payload.emoji.id in await self.get_query_karma_remove(payload.guild_id):
            await self.bot.db.update_user_karma(user.id, payload.guild_id, 1)
            await self.bot.db.update_post_upvotes_and_downvotes(payload.message_id, 1, 0)

    async def get_query_karma_add(self, guild_id):
        karma_emotes = await self.bot.db.get_upvote_karma_emote_ids_by_server(guild_id)
        karma_emotes.append(int(values.UPVOTE_EMOTE_ID))
        return karma_emotes

    async def get_query_karma_remove(self, guild_id):
        karma_emotes = await self.bot.db.get_downvote_karma_emote_ids_by_server(guild_id)
        karma_emotes.append(int(values.DOWNVOTE_EMOTE_ID))
        return karma_emotes

    async def check_payload(self, payload):
        if payload.event_type == 'REACTION_ADD' and payload.member.bot:
            return None
        try:
            message = await self.__get_message_from_payload(payload)
        except discord.errors.NotFound:
            return None
        if message.author.bot:
            return None
        reaction_user = payload.member or await self.bot.fetch_user(payload.user_id)
        if reaction_user == message.author:
            return None
        return message.author

    async def __get_message_from_payload(self, payload: discord.RawReactionActionEvent) -> discord.Message | None:
        potential_message = [message for message in self.bot.cached_messages if message.id == payload.message_id]
        cached_message = potential_message[0] if potential_message else None
        return cached_message or await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)

    async def send_conclusion(self, ctx, kasino_id, winner, author, author_img):
        kasino = await self.bot.db.get_kasino(kasino_id)
        total_karma = await self.bot.db.get_total_kasino_karma(kasino_id)
        to_embed = discord.Embed(color=discord.Colour.from_rgb(52, 79, 235))

        winner_option = kasino['option_1'] if winner == 1 else kasino['option_2']
        if winner in [1, 2]:
            to_embed.title = f':tada: "{winner_option}" was correct! :tada:'
            to_embed.description = f"""Question: {kasino.question}
                                       If you\'ve chosen {winner}, you\'ve just won karma!
                                       Distributed to the winners: **{total_karma} Karma**'
                                    """
        elif winner == 3:
            to_embed.title = f':game_die: "{kasino["question"]}" has been cancelled.',
            to_embed.description = f'Amount bet will be refunded to each user.\nReturned: {total_karma} Karma'

        to_embed.set_footer(
            text=f'as decided by {author}',
            icon_url=author_img
        )
        to_embed.set_thumbnail(url='https://cdn.betterttv.net/emote/602548a4d47a0b2db8d1a3b8/3x.gif')
        await ctx.send(embed=to_embed)
        return

    async def add_kasino(self, ctx, question, option_1, option_2):
        to_embed = discord.Embed(description="Opening kasino, hold on tight...")
        kasino_msg = await ctx.send(embed=to_embed)

        await self.bot.db.insert_foundation_from_ctx(ctx)
        return await self.bot.db.insert_kasino(ctx, question, option_1, option_2, kasino_msg)

    async def remove_kasino(self, kasino_id):
        kasino = await self.bot.db.get_kasino(kasino_id)
        if kasino is None:
            return
        try:
            kasino_channel = await self.bot.fetch_channel(kasino.discord_channel_id)
            kasino_msg = await kasino_channel.fetch_message(kasino.discord_message_id)
            await kasino_msg.delete()
        except discord.errors.NotFound:
            pass
        await self.bot.db.delete_kasino(kasino_id)

    def create_kasino_backup(self, kasino_id):
        today_string = datetime.now().strftime("%Y_%m_%d")
        now_time_string = datetime.now().strftime("%H%M")
        backup_folder = f"{values.DATA_PATH}/backups/{today_string}"
        Path(backup_folder).mkdir(parents=True, exist_ok=True)
        shutil.copy(values.DB_PATH, f"{backup_folder}/backup_{now_time_string}_{kasino_id}.sqlite")

    async def abort_kasino(self, kasino_id: int) -> None:
        kasino_and_bets = await self.bot.db.get_kasino_and_bets(kasino_id)
        for bet in kasino_and_bets:
            await self.bot.db.upsert_user_karma(bet['discord_user_id'], bet['discord_server_id'], bet['amount'])
            user_karma = await self.bot.db.get_user_karma(bet['discord_user_id'], bet['discord_server_id'])
            output = discord.Embed(
                title=f'**You\'ve been refunded {bet["amount"]} karma.**',
                color=discord.Colour.from_rgb(52, 79, 235),
                description=f'Question was: {bet["question"]}\n'
                            f'Remaining karma: {user_karma}'
            )
            await (await self.bot.fetch_user(bet['discord_user_id'])).send(embed=output)

    async def win_kasino(self, kasino_id: int, winning_option: int):
        kasino_and_bets = await self.bot.db.get_kasino_and_bets(kasino_id)
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

            await self.bot.db.upsert_user_karma(user_id, server_id, win_amount)
            user_karma = await self.bot.db.get_user_karma(user_id, server_id)
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
            user_karma = await self.bot.db.get_user_karma(user_id, server_id)
            icon = ':chart_with_downwards_trend:'
            output = discord.Embed(
                title = f'{icon} **You\'ve unfortunately lost {bet["amount"]} karma...** {icon}',
                color = discord.Colour.from_rgb(209, 25, 25),
                description = f'Question was: {question}\n'
                              f'New karma balance: {user_karma}'
            )
            await (await self.bot.fetch_user(user_id)).send(embed=output)

    async def update_kasino_msg(self, ctx, kasino_id: int) -> None:
        kasino = await self.bot.db.get_kasino(kasino_id)
        k_channel_id = kasino['discord_channel_id']
        k_message_id = kasino['discord_message_id']
        kasino_channel = await self.bot.fetch_channel(k_channel_id)
        kasino_msg = await kasino_channel.fetch_message(k_message_id)

        # FIGURE OUT AMOUNTS AND ODDS
        bets_a_amount = await self.bot.db.get_kasino_bets_sum(kasino_id, 1) or 0.0
        bets_b_amount = await self.bot.db.get_kasino_bets_sum(kasino_id, 2) or 0.0

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
    await bot.add_cog(Karma(bot))
