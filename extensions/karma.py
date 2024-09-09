import datetime
import logging
import os
import re

import discord
import matplotlib.pyplot as plt
from asyncpg import Record
from discord import app_commands
from discord.ext import commands

import core
import utils
from database import db_constants as dbc

logger = logging.getLogger(__name__)


UPSERT_KARMA_QUERY = """INSERT INTO karma (discord_user_id, discord_server_id, amount) VALUES ($1, $2, $3)
                        ON CONFLICT (discord_user_id, discord_server_id) DO UPDATE SET amount = karma.amount + $3"""
UPSERT_POST_VOTES_QUERY = """INSERT INTO post (discord_user_id, discord_server_id, discord_channel_id, discord_message_id, created_at, upvotes, downvotes)
                             VALUES ($1, $2, $3, $4, $5, $6, $7)
                             ON CONFLICT (discord_message_id) DO UPDATE SET upvotes = post.upvotes + $6, downvotes = post.downvotes + $7"""


class Karma(commands.Cog):
	COG_EMOJI = "☯️"

	def __init__(self, bot: core.Substiify, vote_channels: list[int]):
		self.bot = bot
		self.vote_channels = vote_channels

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message):
		if message.author.bot:
			return
		if message.type == discord.MessageType.thread_created:
			return
		if message.channel.id in self.vote_channels:
			try:
				upvote_emoji = self.bot.get_emoji(core.constants.UPVOTE_EMOTE_ID)
				downvote_emoji = self.bot.get_emoji(core.constants.DOWNVOTE_EMOTE_ID)

				await message.add_reaction(upvote_emoji)
				await message.add_reaction(downvote_emoji)
			except discord.NotFound:
				pass

	@commands.Cog.listener()
	async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
		await self.process_reaction(payload, add_reaction=True)

	@commands.Cog.listener()
	async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
		await self.process_reaction(payload, add_reaction=False)

	async def process_reaction(self, payload: discord.RawReactionActionEvent, add_reaction: bool) -> None:
		if payload.guild_id is None:
			return

		post = await self._get_post_from_db(payload.message_id)
		if post is None:
			user = await self.check_payload(payload)
			if user is None:
				return

			await self.bot.db.pool.execute(dbc.USER_INSERT_QUERY, user.id, user.display_name, user.display_avatar.url)
			user_id = user.id
		else:
			user_id = post["discord_user_id"]

		upvote_emotes = await self._get_karma_upvote_emotes(payload.guild_id)
		downvote_emotes = await self._get_karma_downvote_emotes(payload.guild_id)

		if payload.emoji.id not in [*upvote_emotes, *downvote_emotes]:
			return

		server = self.bot.get_guild(payload.guild_id)
		if server is None:
			logger.warning(f"Server {payload.guild_id} not found in cache for karma reaction. Fetching from API.")
			server = await self.bot.fetch_guild(payload.guild_id)
		channel = self.bot.get_channel(payload.channel_id)
		if channel is None:
			logger.warning(f"Channel {payload.channel_id} not found in cache for karma reaction. Fetching from API.")
			channel = await self.bot.fetch_channel(payload.channel_id)

		await self.bot.db._insert_server(server)
		await self.bot.db._insert_guild_channel(channel)

		is_upvote = payload.emoji.id in upvote_emotes

		karma_amount = 1  # Assume positive karma
		(upvote, downvote) = (1, 0)  # Assume upvote
		if not add_reaction:
			karma_amount *= -1
			upvote *= -1
		if not is_upvote:
			karma_amount *= -1
			(upvote, downvote) = (downvote, upvote)

		await self._upsert_karma(payload, user_id, karma_amount)
		await self._upsert_post_votes(payload, user_id, upvote, downvote)

	async def _get_post_from_db(self, message_id: int) -> Record:
		stmt = "SELECT * FROM post WHERE discord_message_id = $1"
		return await self.bot.db.pool.fetchrow(stmt, message_id)

	async def _upsert_karma(self, payload: discord.RawReactionActionEvent, user_id: int, amount: int):
		await self.bot.db.pool.execute(UPSERT_KARMA_QUERY, user_id, payload.guild_id, amount)

	async def _upsert_post_votes(
		self, payload: discord.RawReactionActionEvent, user_id: int, upvote: int, downvote: int
	):
		message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
		await self.bot.db.pool.execute(
			UPSERT_POST_VOTES_QUERY,
			user_id,
			payload.guild_id,
			payload.channel_id,
			payload.message_id,
			message.created_at.now(),
			upvote,
			downvote,
		)

	async def check_payload(self, payload: discord.RawReactionActionEvent) -> discord.Member | None:
		if payload.event_type == "REACTION_ADD" and payload.member.bot:
			return None
		try:
			message = await self.__get_message_from_payload(payload)
		except discord.errors.NotFound:
			return None
		if message.author.bot:
			return None
		reaction_user = payload.member or self.bot.get_user(payload.user_id)
		if not reaction_user:
			logger.warning(f"User {payload.user_id} not found in cache for karma reaction. Fetching from API.")
			reaction_user = await self.bot.fetch_user(payload.user_id)
		if reaction_user == message.author:
			return None
		return message.author

	async def __get_message_from_payload(self, payload: discord.RawReactionActionEvent) -> discord.Message | None:
		potential_message = [message for message in self.bot.cached_messages if message.id == payload.message_id]
		cached_message = potential_message[0] if potential_message else None
		if not cached_message:
			logger.debug(f"Message {payload.message_id} not found in cache for karma reaction. Fetching from API.")
			cached_message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
		return cached_message

	async def _get_user_karma(self, user_id: int, guild_id: int) -> int:
		stmt = "SELECT amount FROM karma WHERE discord_user_id = $1 AND discord_server_id = $2"
		return await self.bot.db.pool.fetchval(stmt, user_id, guild_id)

	async def _get_karma_emote_by_id(self, server_id: int, emote: discord.Emoji) -> Record:
		stmt = "SELECT * FROM karma_emote WHERE discord_server_id = $1 AND discord_emote_id = $2"
		return await self.bot.db.pool.fetchrow(stmt, server_id, emote.id)

	async def _get_karma_upvote_emotes(self, guild_id: int) -> list[int]:
		stmt_upvotes = "SELECT discord_emote_id FROM karma_emote WHERE discord_server_id = $1 AND increase_karma = True"
		emote_records = await self.bot.db.pool.fetch(stmt_upvotes, guild_id)
		server_upvote_emotes = [emote["discord_emote_id"] for emote in emote_records]
		server_upvote_emotes.append(int(core.constants.UPVOTE_EMOTE_ID))
		return server_upvote_emotes

	@commands.hybrid_group(invoke_without_command=True)
	async def votes(self, ctx: commands.Context):
		"""
		Shows if votes are enabled in the current channel
		"""
		if ctx.channel.id in self.vote_channels:
			embed = discord.Embed(color=discord.Color.green())
			embed.description = f"Votes are **ALREADY enabled** in {ctx.channel.mention}!"
		else:
			embed = discord.Embed(color=discord.Color.red())
			embed.description = f"Votes are **NOT enabled** in {ctx.channel.mention}!"
		await ctx.reply(embed=embed)

	@votes.command(name="list")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	async def list_votes(self, ctx: commands.Context):
		"""
		Lists all the votes channels that are enabled in the server
		"""
		stmt = "SELECT * FROM discord_channel WHERE discord_server_id = $1 AND upvote = True"
		upvote_channels = await self.bot.db.pool.fetch(stmt, ctx.guild.id)
		channels_string = "\n".join([f"{x['discord_channel_id']} ({x['channel_name']})" for x in upvote_channels])
		embed = discord.Embed(color=core.constants.PRIMARY_COLOR)
		if not channels_string:
			embed.description = "No votes channels found."
			return await ctx.send(embed=embed)
		embed.description = f"Votes are enabled in the following channels: {channels_string}"
		await ctx.send(embed=embed)

	@votes.command()
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(channel="The channel to enable votes in")
	async def enable(self, ctx: commands.Context, channel: discord.abc.GuildChannel = None):
		"""
		Enables votes in the current or specified channel. Requires Manage Channels permission.
		After enabling votes, the bot will add the upvote and downvote reactions to every message in the channel.
		This is good for something like a meme channel if you want to give upvotes and downvotes to the messages.

		If users click the reactions, user karma will be updated.
		"""
		channel = channel or ctx.channel
		if channel.id not in self.vote_channels:
			self.vote_channels.append(channel.id)
		stmt = "SELECT * FROM discord_channel WHERE discord_channel_id = $1 AND upvote = True"
		votes_enabled = await self.bot.db.pool.fetch(stmt, channel.id)
		logger.info(f"Votes enabled: {votes_enabled}")

		embed = discord.Embed(color=discord.Colour.green())
		if votes_enabled:
			embed.description = f"Votes are **already active** in {ctx.channel.mention}!"
			return await ctx.send(embed=embed)

		stmt = """INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id, upvote)
					VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_channel_id) DO UPDATE SET upvote = $5"""
		await self.bot.db.pool.execute(stmt, channel.id, channel.name, channel.guild.id, None, True)

		embed.description = f"Votes **enabled** in {channel.mention}!"
		await ctx.send(embed=embed)

	@votes.command()
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(channel="The channel to disable votes in")
	async def disable(self, ctx: commands.Context, channel: discord.TextChannel = None):
		"""
		Disables votes in the current channel. Requires Manage Channels permission.
		"""
		channel = channel or ctx.channel
		stmt = """INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id, upvote)
                  VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_channel_id) DO UPDATE SET upvote = $5"""
		await self.bot.db.pool.execute(stmt, channel.id, channel.name, channel.guild.id, None, False)

		if channel.id in self.vote_channels:
			self.vote_channels.remove(channel.id)

		embed = discord.Embed(
			description=f"Votes has been stopped in {channel.mention}!",
			color=discord.Colour.red(),
		)
		await ctx.send(embed=embed)

	@commands.group(
		aliases=["k"],
		usage="karma [user]",
		invoke_without_command=True,
	)
	@app_commands.describe(
		user="Which user do you want to see the karma of? If not specified, it will show your own karma."
	)
	async def karma(self, ctx: commands.Context, user: discord.User = None):
		"""
		Shows the karma of a user. If you dont specify a user, it will show your own.
		If you want to know what emote reactions are used for karma, use the subcommand `karma emotes`
		"""
		if user is None:
			user = ctx.author

		if user.bot:
			embed = discord.Embed(description="Bots don't have karma!", color=discord.Colour.red())
			return await ctx.reply(embed=embed)

		if user not in ctx.guild.members:
			embed = discord.Embed(description=f"{user} is not a member of this server.", color=discord.Colour.red())
			return await ctx.send(embed=embed)

		user_karma = await self._get_user_karma(user.id, ctx.guild.id)
		user_karma = 0 if user_karma is None else user_karma

		embed = discord.Embed(title=f"Karma - {ctx.guild.name}", description=f"{user.mention} has {user_karma} karma.")
		await ctx.send(embed=embed)

	@karma.error
	async def karma_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.BadArgument):
			embed = discord.Embed(description=error, color=discord.Colour.red())
			await ctx.send(embed=embed)

	@commands.cooldown(3, 10)
	@karma.command(name="donate", aliases=["wiretransfer", "wt"], usage="donate <user> <amount>")
	@app_commands.describe(
		user="Which user do you want to donate karma to?", amount="How much karma do you want to donate?"
	)
	async def karma_donate(self, ctx: commands.Context, *args):
		"""
		Donates karma to another user.
		"""
		if len(args) != 2:
			msg = f"Got {len(args)} arguments, expected 2."
			raise NotEnoughArguments(msg)

		user = None
		amount = None

		for arg in args:
			if user is None:
				user = self._find_guild_user(ctx.guild, arg)
				if user is not None:
					continue

			if amount is None and arg.isdigit():
				amount = int(arg)

			if user is not None and amount is not None:
				break

		logger.debug(f"Karma transfer params -> User: {user}, amount: {amount}")
		if user is None or amount is None:
			logger.error(
				f"Could not find a user or amount in the provided arguments. args: {args}; user: {user}; amount: {amount}"
			)
			return await ctx.reply("Could not find a user or amount in the provided arguments.")

		embed = discord.Embed(color=discord.Colour.red())
		if user.bot:
			embed.description = "You can't donate to bots!"
			return await ctx.send(embed=embed)

		if amount <= 0:
			embed.description = f"You cannot donate {amount} karma!"
			return await ctx.send(embed=embed)

		if user not in ctx.guild.members:
			embed.description = f"`{user}` is not a member of this server!"
			return await ctx.send(embed=embed)

		donator_karma = await self._get_user_karma(ctx.author.id, ctx.guild.id)
		if donator_karma is None:
			embed.description = "You don't have any karma!"
			return await ctx.send(embed=embed)

		if donator_karma < amount:
			embed.description = "You don't have enough karma!"
			return await ctx.send(embed=embed)

		await self.bot.db.pool.executemany(
			UPSERT_KARMA_QUERY, [(user.id, ctx.guild.id, amount), (ctx.author.id, ctx.guild.id, -amount)]
		)

		embed = discord.Embed(color=discord.Colour.green())
		embed.description = f"{ctx.author.mention} has donated {amount} karma to {user.mention}!"
		await ctx.send(embed=embed)

	def _find_guild_user(self, guild: discord.Guild, arg: str) -> discord.Member | None:
		members = guild.members
		match = commands.IDConverter._get_id_match(arg) or re.match(r"<@([0-9]{15,20})>$", arg)
		if match is None:
			# not a mention or an id
			username, _, discriminator = arg.rpartition("#")

			# If # isn't found then "discriminator" actually has the username
			if not username:
				discriminator, username = username, discriminator

			if discriminator == "0" or (len(discriminator) == 4 and discriminator.isdigit()):
				return discord.utils.find(lambda m: m.name == username and m.discriminator == discriminator, members)

			def pred(m: discord.Member) -> bool:
				return m.name == arg or m.global_name == arg

			return discord.utils.find(pred, members)
		else:
			user_id = int(match.group(1))
			return guild.get_member(user_id)

	@karma_donate.error
	async def karma_donate_error(self, ctx: commands.Context, error):
		embed = discord.Embed(color=discord.Colour.red())
		if isinstance(error, commands.CommandOnCooldown):
			embed.description = f"Please wait {error.retry_after:.2f} seconds before using this command again."
		elif isinstance(error, NotEnoughArguments):
			embed.description = "You didn't specify an `amount` or a `user` to donate to!"
		elif isinstance(error, commands.BadArgument):
			embed.description = f"Wrong command usage! Command usage is `{ctx.prefix}karma donate <user> <amount>`"
		else:
			embed.description = f"An unknown error occured. Please contact <@{self.bot.owner_id}> for help."
			logger.error(f"An unknown error occured in karma_donate: {error}")
		await ctx.send(embed=embed)
		error.is_handled = True

	@karma.group(name="emotes", aliases=["emote"], usage="emotes", invoke_without_command=True)
	async def karma_emotes(self, ctx: commands.Context):
		"""
		Shows the karma emotes of the server. Emotes in the `add` category increase karma,
		while emotes in the `remove` category decrease karma.
		If you want to add or remove an emote from the karma system,
		check the subcommand `karma emotes add` or `karma emotes remove`
		"""
		stmt = "SELECT * FROM karma_emote WHERE discord_server_id = $1 ORDER BY increase_karma DESC"
		karma_emotes = await self.bot.db.pool.fetch(stmt, ctx.guild.id)
		if not karma_emotes:
			return await ctx.send(embed=discord.Embed(title="No emotes found."))
		embed_string = ""
		last_action = ""
		for emote in karma_emotes:
			if emote["increase_karma"] != last_action:
				embed_string += f'\n`{"add" if emote["increase_karma"] is True else "remove"}:` '
				last_action = emote["increase_karma"]
			embed_string += f"{self.bot.get_emoji(emote['discord_emote_id'])} "
		embed = discord.Embed(title=f"Karma Emotes - {ctx.guild.name}", description=embed_string)
		await ctx.send(embed=embed)

	@karma_emotes.command(name="add", usage="add <emote> <action>")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(
		emote="Which emote do you want to add?",
		emote_action="What action should this emote do? (0 for add, 1 for remove karma)",
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
			embed = discord.Embed(title="Invalid action parameter.")
			return await ctx.send(embed=embed)

		existing_emote = await self._get_karma_emote_by_id(ctx.guild.id, emote)
		if existing_emote is not None:
			embed = discord.Embed(title="That emote is already added.")
			return await ctx.send(embed=embed)

		stmt_emote_count = "SELECT COUNT(*) FROM karma_emote WHERE discord_server_id = $1"
		max_emotes = await self.bot.db.pool.fetchval(stmt_emote_count, ctx.guild.id)
		if max_emotes >= 10:
			embed = discord.Embed(title="You can only have 10 emotes.")
			return await ctx.send(embed=embed)

		stmt_insert_emote = (
			"INSERT INTO karma_emote (discord_server_id, discord_emote_id, increase_karma) VALUES ($1, $2, $3)"
		)
		await self.bot.db.pool.execute(stmt_insert_emote, ctx.guild.id, emote.id, not bool(emote_action))

		embed = discord.Embed(title=f"Emote {emote} added to the list.")
		await ctx.send(embed=embed)
		if not ctx.interaction:
			await ctx.message.delete()

	@karma_emotes.command(name="remove", aliases=["delete"], usage="remove <emote>")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(emote="Which emote do you want to remove?")
	async def karma_emote_remove(self, ctx: commands.Context, emote: discord.Emoji):
		"""
		Remove an emote from the karma emotes for this server.
		"""
		existing_emote = await self._get_karma_emote_by_id(ctx.guild.id, emote)
		if existing_emote is None:
			embed = discord.Embed(title="That emote is not in the list.")
			return await ctx.send(embed=embed)

		stmt_delete_emote = "DELETE FROM karma_emote WHERE discord_server_id = $1 AND discord_emote_id = $2"
		await self.bot.db.pool.execute(stmt_delete_emote, ctx.guild.id, emote.id)

		embed = discord.Embed(title=f"Emote {emote} removed from the list.")
		await ctx.send(embed=embed)
		if not ctx.interaction:
			await ctx.message.delete()

	@commands.cooldown(1, 5, commands.BucketType.user)
	@karma.command(name="leaderboard", aliases=["lb", "leaderbord"], usage="leaderboard")
	async def karma_leaderboard(self, ctx: commands.Context, global_leaderboard: str = None):
		"""
		Shows users with the most karma on the server.
		"""
		async with ctx.typing():
			embed = discord.Embed(title="Karma Leaderboard")

			if global_leaderboard is None:
				stmt_karma_leaderboard = "SELECT discord_user_id, amount FROM karma WHERE discord_server_id = $1 ORDER BY amount DESC LIMIT 15"
				results = await self.bot.db.pool.fetch(stmt_karma_leaderboard, ctx.guild.id)

			elif global_leaderboard == "global":
				stmt_karma_leaderboard = "SELECT discord_user_id, amount FROM karma ORDER BY amount DESC LIMIT 15"
				results = await self.bot.db.pool.fetch(stmt_karma_leaderboard)

			embed.description = ""
			if not results:
				embed.description = "No users have karma."
				return await ctx.send(embed=embed)

			users_string = "".join([f"<@{entry['discord_user_id']}>\n" for entry in results])

			for index, entry in enumerate(results, start=1):
				user = self.bot.get_user(entry["discord_user_id"]) or await self.bot.fetch_user(
					entry["discord_user_id"]
				)
				embed.description += f"`{str(index).rjust(2)}.` | `{entry['amount']}` - {user.mention}\n"

			load_users_message = await ctx.send("Loading users...")
			await load_users_message.edit(content=users_string)
			await load_users_message.delete()
			await ctx.send(embed=embed)

	@commands.cooldown(1, 15, commands.BucketType.user)
	@karma.command(name="stats", usage="stats")
	async def karma_stats(self, ctx: commands.Context):
		"""
		Shows karma stats for the server.
		Some stats incluce total karma, karma amount in top percentile and more.
		"""
		async with ctx.typing():
			embed = discord.Embed(title="Karma Stats")

			karma_info = await self.bot.db.pool.fetchrow(
				"SELECT SUM(amount), COUNT(*) FROM karma WHERE discord_server_id = $1", ctx.guild.id
			)
			total_karma = karma_info["sum"]
			karma_users = karma_info["count"]

			if total_karma is None:
				embed.description = "No users have karma."
				return await ctx.send(embed=embed)

			avg_karma = total_karma / max(karma_users, 1)
			embed.add_field(
				name="Total Server Karma", value=f"`{total_karma:n} (of {karma_users} users)`", inline=False
			)
			embed.add_field(name="Average Karma per user", value=f"`{avg_karma:.2f}`", inline=False)

			# Top percentile calculation
			stmt_top_percentile = """
                SELECT amount
                FROM karma
                WHERE discord_server_id = $1
                ORDER BY amount DESC
                LIMIT (SELECT CEIL($2 * CAST(COUNT(*) AS float)) FROM karma)"""

			percentiles = [(0.1, "10"), (0.01, "1")]
			for percentile, label in percentiles:
				top_percentile = await self.bot.db.pool.fetch(stmt_top_percentile, ctx.guild.id, percentile)
				top_percentile = sum(entry["amount"] for entry in top_percentile)
				percantege = (top_percentile / total_karma) * 100
				embed.add_field(
					name=f"Top {label}% users karma",
					value=f"`{top_percentile:n} ({percantege:.2f}% of total)`",
					inline=False,
				)

			stmt_avg_upvote_ratio = """
                SELECT AVG(upvotes / downvotes) as average, COUNT(*) as post_count
                FROM post
                WHERE discord_server_id = $1
                    AND upvotes >= 1
                    AND downvotes >= 1"""

			avg_post_query = await self.bot.db.pool.fetchrow(stmt_avg_upvote_ratio, ctx.guild.id)
			avg_ratio = avg_post_query["average"] or 0
			post_count = avg_post_query["post_count"] or 0
			embed.add_field(
				name="Average upvote ratio per post", value=f"`{avg_ratio:.1f} ({post_count} posts)`", inline=False
			)

			await ctx.send(embed=embed)

	@commands.cooldown(1, 30, commands.BucketType.user)
	@karma.command(name="graph", usage="graph")
	async def karma_graph(self, ctx: commands.Context):
		"""
		Shows a graph of the amount of karma form every ten percent of users.
		"""
		async with ctx.typing():
			stmt_karma = """
                SELECT amount
                FROM karma
                WHERE discord_server_id = $1
                ORDER BY amount ASC
            """

			karma = await self.bot.db.pool.fetch(stmt_karma, ctx.guild.id)
			users_count = len(karma)
			if users_count == 0:
				embed = discord.Embed(title="Karma graph", description="No users have karma.")
				return await ctx.send(embed=embed)

			karma_percentiles = []
			for i in range(0, 101, 5):
				karma_percentile_list = karma[: int(users_count * (i / 100))]
				total_percentile_karma = sum(entry["amount"] for entry in karma_percentile_list)
				karma_percentiles.append((total_percentile_karma, i))

			timestamp = datetime.datetime.now().timestamp()
			filename = f"karma_graph_{timestamp}.png"

			filename = self._generate_graph(filename, karma_percentiles)
			await ctx.send(file=discord.File(filename, filename=filename))
			os.remove(filename)

	def _generate_graph(self, filename: str, data: list[tuple[int, int]]) -> str:
		x = [entry[1] for entry in data]
		y = [entry[0] for entry in data]

		plt.figure(figsize=(10, 6), facecolor="#141415")
		plt.bar(x, y, color="#6971f8", width=4)

		plt.title("Karma Graph", color="white", loc="left", fontsize=20)
		plt.xlabel("Percentile of users", color="white", fontsize=16)
		plt.ylabel("Total karma", color="white", fontsize=16)

		plt.gca().spines["bottom"].set_color("white")
		plt.gca().spines["left"].set_color("white")
		plt.gca().tick_params(axis="x", colors="white")
		plt.gca().tick_params(axis="y", colors="white")

		plt.gca().set_facecolor("#141415")
		plt.gca().spines["top"].set_visible(False)
		plt.gca().spines["right"].set_visible(False)
		plt.gca().spines["left"].set_visible(False)
		plt.gca().spines["bottom"].set_visible(False)

		plt.grid(axis="x", visible=False)
		plt.grid(axis="y", color="#4a4d60")

		def custom_formatter(x, pos):
			if x >= 1e6:  # Millions
				return f"{x*1e-6:.0f}M"
			elif x >= 1e3:  # Thousands
				return f"{x*1e-3:.0f}K"
			else:
				return f"{x:.0f}"

		plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(custom_formatter))
		plt.savefig(filename, facecolor="#141415")
		plt.close()
		return filename

	@commands.hybrid_group(name="post", aliases=["po"], invoke_without_command=True)
	async def post(self, ctx: commands.Context):
		await ctx.send_help(ctx.command)

	@commands.cooldown(2, 15, commands.BucketType.user)
	@post.command(name="leaderboard", aliases=["lb"], usage="lb [user]")
	async def post_leaderboard(self, ctx: commands.Context, user: discord.User = None):
		"""
		Posts the leaderboard of the most upvoted posts.
		"""
		async with ctx.typing():
			all_board = await self.fetch_and_create_leaderboard(ctx, user)
			month_board = await self.fetch_and_create_leaderboard(ctx, user, "30 days")
			week_board = await self.fetch_and_create_leaderboard(ctx, user, "7 days")

		embed = discord.Embed(title="Top Messages")
		embed.set_thumbnail(url=ctx.guild.icon)
		embed.add_field(name="Top 5 All Time", value=all_board, inline=False)
		embed.add_field(name="Top 5 This Month", value=month_board, inline=False)
		embed.add_field(name="Top 5 This Week", value=week_board, inline=False)
		await ctx.send(embed=embed)

	async def fetch_and_create_leaderboard(self, ctx: commands.Context, user: discord.User, interval: str = None):
		user_query = " AND discord_user_id = $2" if user else ""
		interval_query = f" AND created_at > NOW() - INTERVAL '{interval}'" if interval else ""
		stmt = (
			f"SELECT * FROM post WHERE discord_server_id = $1{user_query}{interval_query} ORDER BY upvotes DESC LIMIT 5"
		)
		params = (ctx.guild.id, user.id) if user else (ctx.guild.id,)
		posts = await self.bot.db.pool.fetch(stmt, *params)
		return await self._create_post_leaderboard(posts)

	@post.command(name="check", aliases=["c"], usage="check <post id>")
	@commands.is_owner()
	async def post_check(self, ctx: commands.Context, post_id: str):
		"""
		Checks if a post exists.
		"""
		try:
			post_id = int(post_id)
		except ValueError:
			embed = discord.Embed(title="Post ID must be a number.")
			return await ctx.reply(embed=embed, ephemeral=True)

		stmt_post = "SELECT * FROM post WHERE discord_message_id = $1"
		post = await self.bot.db.pool.fetchrow(stmt_post, post_id)
		if post is None:
			embed = discord.Embed(title="That post does not exist.")
			return await ctx.reply(embed=embed)

		server_upvote_emotes = await self._get_karma_upvote_emotes(ctx.guild.id)
		server_downvote_emotes = await self._get_karma_downvote_emotes(ctx.guild.id)

		channel = await self.bot.fetch_channel(post["discord_channel_id"])
		message = await channel.fetch_message(post["discord_message_id"])

		upvotes = 0
		downvotes = 0
		for reaction in message.reactions:
			if isinstance(reaction.emoji, (discord.Emoji, discord.PartialEmoji)):
				if reaction.emoji.id in server_upvote_emotes:
					upvotes += reaction.count - 1
				elif reaction.emoji.id in server_downvote_emotes:
					downvotes += reaction.count - 1

		old_upvotes = post["upvotes"]
		old_downvotes = post["downvotes"]
		karma_difference = (upvotes - old_upvotes) - (downvotes - old_downvotes)

		update_post_query = "UPDATE post SET upvotes = $1, downvotes = $2 WHERE discord_message_id = $3"
		await self.bot.db.pool.execute(UPSERT_KARMA_QUERY, message.author.id, message.guild.id, karma_difference)
		await self.bot.db.pool.execute(update_post_query, upvotes, downvotes, post_id)

		embed_string = f"""
            Old post upvotes: {old_upvotes}, Old post downvotes: {old_downvotes}\n
            Rechecked post upvotes: {upvotes}, Rechecked post downvotes: {downvotes}\n
            Karma difference: {karma_difference}
        """

		embed = discord.Embed(title=f"Post {post_id} check", description=embed_string)
		await ctx.send(embed=embed, delete_after=60)
		if not ctx.interaction:
			await ctx.message.delete()

	async def _create_post_leaderboard(self, posts: list[Record]):
		if not posts:
			return "No posts found."
		leaderboard = ""
		for index, post in enumerate(posts, start=1):
			jump_url = self._create_message_url(
				post["discord_server_id"], post["discord_channel_id"], post["discord_message_id"]
			)
			username = self.bot.get_user(post["discord_user_id"]) or await self.bot.fetch_user(post["discord_user_id"])
			leaderboard += f"**{index}.** [{username} ({post['upvotes']})]({jump_url})\n"
		return leaderboard

	def _create_message_url(self, server_id, channel_id, message_id):
		return f"https://discordapp.com/channels/{server_id}/{channel_id}/{message_id}"

	@commands.hybrid_group(name="kasino", aliases=["kas"], invoke_without_command=True)
	async def kasino(self, ctx: commands.Context):
		await ctx.send_help(ctx.command)

	@kasino.command(name="open", aliases=["o"], usage='open "<question>" "<option1>" "<option2>"')
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(
		question="The qestion users will bet on.",
		op_a="The first option users can bet on.",
		op_b="The second option users can bet on.",
	)
	async def kasino_open(self, ctx: commands.Context, question: str, op_a: str, op_b: str):
		"""Opens a karma kasino which allows people to bet on a question with two options.
		Check "karma" and "votes" commands for more info on karma.
		"""
		async with ctx.typing():
			to_embed = discord.Embed(description="Opening kasino, hold on tight...")
			kasino_msg = await ctx.send(embed=to_embed)
			stmt_kasino = """INSERT INTO kasino (discord_server_id, discord_channel_id, discord_message_id, question, option1, option2)
                         VALUES ($1, $2, $3, $4, $5, $6) RETURNING id"""
			kasino_id = await self.bot.db.pool.fetchval(
				stmt_kasino, ctx.guild.id, ctx.channel.id, kasino_msg.id, question, op_a, op_b
			)
			# TODO: Add kasino backup
		await _update_kasino_msg(ctx.bot, kasino_id)
		if not ctx.interaction:
			await ctx.message.delete()

	@kasino.command(name="close", usage="close <kasino_id> <winning_option>")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(
		kasino_id="The ID of the kasino you want to close. The ID should be visible in the kasino message.",
		winner="The winning option. 1 or 2. 3 to abort.",
	)
	async def kasino_close(self, ctx: commands.Context, kasino_id: int, winner: int):
		"""Closes a karma kasino and announces the winner. To cancel the kasino, use 3 as the winner."""
		kasino = await self.bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)

		if kasino is None:
			return await ctx.reply(f"Kasino with ID {kasino_id} is not open.")

		if kasino["discord_server_id"] != ctx.guild.id:
			return await ctx.reply(f"Kasino with ID {kasino_id} is not in this server.")

		if winner not in {1, 2, 3}:
			return await ctx.reply("Winner has to be 1, 2 or 3 (abort)")

		if ctx.interaction:
			await ctx.interaction.response.defer()

		if winner in {1, 2}:
			await self.win_kasino(kasino_id, winner)
		elif winner == 3:
			await self.abort_kasino(kasino_id)

		await self.send_conclusion(ctx, kasino_id, winner)
		await self.remove_kasino(kasino_id)
		if ctx.interaction:
			await ctx.interaction.followup.send("Kasino closed.", ephemeral=True)

	@kasino_close.error
	async def kasino_close_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.errors.MissingRequiredArgument):
			msg = f"You didn't provide a required argument!\nCorrect usage is `{ctx.prefix}kasino close <kasino_id> <winning_option>`"
			msg += "\nUse option `3` to close and abort the kasino (no winner)."
			embed = discord.Embed(description=msg, color=discord.Colour.red())
			await ctx.send(embed=embed)
		elif isinstance(error, commands.errors.BadArgument):
			await ctx.send(f"Bad argument: {error}")
		if not ctx.interaction:
			await ctx.message.delete()

	@kasino.command(name="list", aliases=["l"], usage="list")
	async def kasino_list(self, ctx: commands.Context):
		"""Lists all open kasinos on the server."""
		embed = discord.Embed(title="Open kasinos")
		stmt_kasinos = "SELECT * FROM kasino WHERE locked = False AND discord_server_id = $1 ORDER BY id ASC;"
		all_kasinos = await self.bot.db.pool.fetch(stmt_kasinos, ctx.guild.id)
		embed_kasinos = "".join(f'`{entry["id"]}` - {entry["question"]}\n' for entry in all_kasinos)
		embed.description = embed_kasinos or "No open kasinos found."
		await ctx.send(embed=embed, delete_after=300)
		if not ctx.interaction:
			await ctx.message.delete()

	@kasino.command(name="resend", usage="resend <kasino_id>")
	@commands.cooldown(1, 30)
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(kasino_id="The ID of the kasino you want to resend.")
	async def resend_kasino(self, ctx: commands.Context, kasino_id: int):
		"""Resends a kasino message if it got lost in the channel."""
		kasino = await self.bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
		if kasino is None:
			await ctx.send("Kasino not found.")
			return
		k_channel_id = kasino["discord_channel_id"]
		k_message_id = kasino["discord_message_id"]
		async with ctx.typing():
			k_channel = await self.bot.fetch_channel(k_channel_id)
			try:
				kasino_msg = await k_channel.fetch_message(k_message_id)
				await kasino_msg.delete()
			except discord.NotFound:
				pass
			new_kasino_msg = await ctx.send(embed=discord.Embed(description="Loading..."))
			stmt_update_kasino = "UPDATE kasino SET discord_channel_id = $1, discord_message_id = $2 WHERE id = $3;"
			await self.bot.db.pool.execute(stmt_update_kasino, ctx.channel.id, new_kasino_msg.id, kasino_id)
			await _update_kasino_msg(ctx.bot, kasino_id)

	async def _get_karma_downvote_emotes(self, guild_id: int) -> list[int]:
		stmt_downvotes = (
			"SELECT discord_emote_id FROM karma_emote WHERE discord_server_id = $1 AND increase_karma = False"
		)
		emote_records = await self.bot.db.pool.fetch(stmt_downvotes, guild_id)
		server_downvote_emotes = [emote["discord_emote_id"] for emote in emote_records]
		server_downvote_emotes.append(int(core.constants.DOWNVOTE_EMOTE_ID))
		return server_downvote_emotes

	async def send_conclusion(self, ctx: commands.Context, kasino_id: int, winner: int):
		kasino = await self.bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
		total_karma = await self.bot.db.pool.fetchval(
			"SELECT SUM(amount) FROM kasino_bet WHERE kasino_id = $1", kasino_id
		)
		to_embed = discord.Embed(color=discord.Colour.from_rgb(52, 79, 235))

		if winner in [1, 2]:
			winner_option = kasino["option1"] if winner == 1 else kasino["option2"]
			to_embed.title = f':tada: "{winner_option}" was correct! :tada:'
			to_embed.description = f"""Question: {kasino['question']}
                                       If you have chosen {winner}, you just won karma!
                                       Distributed to the winners: **{total_karma} Karma**'
                                    """
		elif winner == 3:
			kasino_question: str = kasino["question"]
			to_embed.title = f'🎲 "{kasino_question}" has been cancelled.'
			to_embed.description = f"Amount bet will be refunded to each user.\nReturned: {total_karma} Karma"

		to_embed.set_footer(text=f"as decided by {ctx.author}", icon_url=ctx.author.display_avatar)
		to_embed.set_thumbnail(url="https://cdn.betterttv.net/emote/602548a4d47a0b2db8d1a3b8/3x.gif")
		await ctx.send(embed=to_embed)
		logger.info(
			f"Kasino closed [ID: {kasino_id}, winner: {winner}, server: {ctx.guild.id}, total_karma: {total_karma}]."
		)
		return

	async def remove_kasino(self, kasino_id: int) -> None:
		kasino = await self.bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
		if kasino is None:
			return
		try:
			kasino_channel = await self.bot.fetch_channel(kasino["discord_channel_id"])
			kasino_msg = await kasino_channel.fetch_message(kasino["discord_message_id"])
			await kasino_msg.delete()
		except discord.errors.NotFound:
			pass
		await self.bot.db.pool.execute("DELETE FROM kasino WHERE id = $1", kasino_id)

	async def abort_kasino(self, kasino_id: int) -> None:
		stmt_kasino_and_bets = """SELECT * FROM kasino JOIN kasino_bet ON kasino.id = kasino_bet.kasino_id
                                  WHERE kasino.id = $1"""
		kasino_and_bets = await self.bot.db.pool.fetch(stmt_kasino_and_bets, kasino_id)
		stmt_update_user_karma = """UPDATE karma SET amount = amount + $1
                                    WHERE discord_user_id = $2 AND discord_server_id = $3"""
		for bet in kasino_and_bets:
			await self.bot.db.pool.execute(
				stmt_update_user_karma, bet["amount"], bet["discord_user_id"], bet["discord_server_id"]
			)
			user_karma = await self._get_user_karma(bet["discord_user_id"], bet["discord_server_id"])
			output = discord.Embed(
				title=f"**You have been refunded {bet['amount']} karma.**",
				color=discord.Colour.from_rgb(52, 79, 235),
				description=f"Question was: {bet['question']}\n' f'Remaining karma: {user_karma}",
			)
			user = self.bot.get_user(bet["discord_user_id"]) or await self.bot.fetch_user(bet["discord_user_id"])
			try:
				await user.send(embed=output)
			except discord.errors.Forbidden:
				logger.warning(f"Could not send kasino abort to {user.id}")

	async def win_kasino(self, kasino_id: int, winning_option: int):
		stmt_kasino_and_bets = """SELECT * FROM kasino JOIN kasino_bet ON kasino.id = kasino_bet.kasino_id
                                  WHERE kasino.id = $1"""
		kasino_and_bets = await self.bot.db.pool.fetch(stmt_kasino_and_bets, kasino_id)
		total_kasino_karma = sum(kb["amount"] for kb in kasino_and_bets)
		winners_bets = [kb for kb in kasino_and_bets if kb["option"] == winning_option]
		total_winner_karma = sum(kb["amount"] for kb in winners_bets)
		server_id = kasino_and_bets[0]["discord_server_id"]
		question = kasino_and_bets[0]["question"]

		if total_winner_karma is None:
			total_winner_karma = 0

		async def send_message(self: Karma, user_id: int, bet, win_amount: int) -> None:
			user_karma = await self._get_user_karma(user_id, server_id)
			title = f":chart_with_downwards_trend: **You have unfortunately lost {bet['amount']} karma...** :chart_with_downwards_trend:"
			color = discord.Colour.from_rgb(209, 25, 25)
			description = None
			if win_amount > 0:
				title = f":tada: **You have won {win_amount} karma!** :tada:"
				color = discord.Colour.from_rgb(66, 186, 50)
				description = f'Of which `{bet["amount"]}` you put down on the table'
			embed = discord.Embed(title=title, color=color, description=description)
			embed.add_field(name="Question was:", value=question, inline=False)
			embed.add_field(name="New karma balance:", value=user_karma, inline=False)
			user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
			try:
				await user.send(embed=embed)
			except discord.errors.Forbidden:
				logger.warning(f"Could not send kasino conclusion to {user_id}")

		for bet in winners_bets:
			win_ratio = bet["amount"] / total_winner_karma
			win_amount = round(win_ratio * total_kasino_karma)
			user_id = bet["discord_user_id"]

			stmt_update_user_karma = """UPDATE karma SET amount = amount + $1
                                        WHERE discord_user_id = $2 AND discord_server_id = $3"""
			await self.bot.db.pool.execute(stmt_update_user_karma, win_amount, user_id, server_id)
			await send_message(self, user_id, bet, win_amount)

		losers_bets = [kb for kb in kasino_and_bets if kb["option"] != winning_option]
		for bet in losers_bets:
			user_id = bet["discord_user_id"]
			await send_message(self, user_id, bet, 0)


async def _update_kasino_msg(bot: core.Substiify, kasino_id: int) -> discord.Message:
	kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
	kasino_channel = await bot.fetch_channel(kasino["discord_channel_id"])
	kasino_msg = await kasino_channel.fetch_message(kasino["discord_message_id"])

	# FIGURE OUT AMOUNTS AND ODDS
	stmt_kasino_bets_sum = """SELECT SUM(amount) FROM kasino_bet WHERE kasino_id = $1 AND option = $2"""
	bets_a_amount: int = await bot.db.pool.fetchval(stmt_kasino_bets_sum, kasino_id, 1) or 0
	bets_b_amount: int = await bot.db.pool.fetchval(stmt_kasino_bets_sum, kasino_id, 2) or 0
	a_odds, b_odds = _calculate_odds(bets_a_amount, bets_b_amount)

	# CREATE MESSAGE
	description = "The kasino has been opened! Place your bets! :game_die:"
	if kasino["locked"]:
		description = "The kasino is locked! No more bets are taken in. Time to wait and see..."

	participants = await bot.db.pool.fetchval("SELECT COUNT(*) FROM kasino_bet WHERE kasino_id = $1", kasino_id)
	description += f"\n**Participants:** `{participants}`"

	title = f":game_die: {kasino['question']}"
	color = discord.Colour.from_rgb(52, 79, 235)
	if kasino["locked"]:
		title = f"[LOCKED] {title}"
		color = discord.Colour.from_rgb(209, 25, 25)

	embed = discord.Embed(title=title, description=description, color=color)
	embed.set_footer(text=f"On the table: {bets_a_amount + bets_b_amount} Karma | ID: {kasino_id}")
	embed.set_thumbnail(url="https://cdn.betterttv.net/emote/602548a4d47a0b2db8d1a3b8/3x.gif")
	embed.add_field(
		name=f'**1:** {kasino["option1"]}', value=f"**Odds:** 1:{round(a_odds, 3)}\n**Pool:** {bets_a_amount} Karma"
	)
	embed.add_field(
		name=f'**2:** {kasino["option2"]}', value=f"**Odds:** 1:{round(b_odds, 3)}\n**Pool:** {bets_b_amount} Karma"
	)

	await kasino_msg.edit(embed=embed, view=KasinoView(kasino))
	return kasino_msg


def _calculate_odds(bets_a_amount: int, bets_b_amount: int) -> tuple[float, float]:
	total_bets: float = float(bets_a_amount + bets_b_amount)
	a_odds: float = total_bets / float(bets_a_amount) if bets_a_amount else 1.0
	b_odds: float = total_bets / float(bets_b_amount) if bets_b_amount else 1.0
	return a_odds, b_odds


class KasinoView(discord.ui.View):
	def __init__(self, kasino: Record):
		super().__init__(timeout=None)
		self.kasino = kasino
		if not kasino["locked"]:
			self.add_item(KasinoBetButton(1))
			self.add_item(KasinoBetButton(2))
		self.add_item(KasinoLockButton(kasino))


class KasinoBetButton(discord.ui.Button):
	def __init__(self, option: int):
		gamba_emoji = discord.PartialEmoji.from_str("karmabet:817354842699857920")
		self.option = option
		super().__init__(label=f"Bet: {option}", emoji=gamba_emoji, style=discord.ButtonStyle.blurple)

	async def callback(self, interaction: discord.Interaction):
		bot: core.Substiify = interaction.client
		if self.view.kasino["locked"]:
			return await interaction.response.send_message(
				"The kasino is locked! No more bets are taken in. Time to wait and see...", ephemeral=True
			)

		user_karma_query = "SELECT amount FROM karma WHERE discord_user_id = $1 AND discord_server_id = $2"
		bettor_karma = await bot.db.pool.fetchval(user_karma_query, interaction.user.id, interaction.guild.id)
		if bettor_karma is None:
			return await interaction.response.send_message("You don't have any karma!", ephemeral=True)

		stmt_bet = "SELECT * FROM kasino_bet WHERE kasino_id = $1 AND discord_user_id = $2;"
		user_bet = await bot.db.pool.fetchrow(stmt_bet, self.view.kasino["id"], interaction.user.id)
		if user_bet and user_bet["option"] != self.option:
			return await interaction.response.send_message(
				"You can't change your choice on the bet. No chickening out!", ephemeral=True
			)

		modal = KasinoBetModal(self.view.kasino, bettor_karma, user_bet, self.option)
		await interaction.response.send_modal(modal)


class KasinoLockButton(discord.ui.Button):
	def __init__(self, kasino: Record):
		locked = kasino["locked"]
		self.lock_settings = {
			True: ("Unlock", "🔐", discord.ButtonStyle.red),
			False: ("Lock", "🔒", discord.ButtonStyle.grey),
		}
		label, emoji, style = self.lock_settings[locked]
		super().__init__(label=label, emoji=emoji, style=style)

	async def callback(self, interaction: discord.Interaction):
		bot: core.Substiify = interaction.client
		kasino_id = self.view.kasino["id"]
		if not interaction.user.guild_permissions.manage_channels and not await bot.is_owner(interaction.user):
			return await interaction.response.send_message(
				"You don't have permission to lock the kasino!", ephemeral=True
			)
		is_locked = await bot.db.pool.fetchval("SELECT locked FROM kasino WHERE id = $1", kasino_id)
		if is_locked:
			label_str = f"""Are you sure you want to unlock kasino ID: `{kasino_id}`?
					To make it fair, all people who bet will get a message so they can increase their bets!

					To confirm, press the button below."""
			embed = discord.Embed(
				title="Unlock kasino", description=label_str, color=discord.Colour.from_rgb(52, 79, 235)
			)
			await interaction.response.send_message(
				embed=embed, view=KasinoConfirmUnlockView(kasino_id), ephemeral=True
			)
		else:
			await bot.db.pool.execute("UPDATE kasino SET locked = True WHERE id = $1", kasino_id)
			await _update_kasino_msg(bot, kasino_id)
			self.label, self.emoji, self.style = self.lock_settings[True]
			await interaction.message.edit(view=self.view)
			await interaction.response.send_message("Kasino locked!", ephemeral=True)


class KasinoBetModal(discord.ui.Modal):
	def __init__(self, kasino: Record, bettor_karma: int, user_bet: Record, option: int):
		title = utils.ux.strip_emotes(kasino["question"])
		if len(title) > 45:
			title = title[:42] + "..."
		super().__init__(title=title)
		self.option = option
		self.kasino = kasino
		self.bettor_karma = bettor_karma
		self.user_bet = user_bet
		option_str = kasino[f"option{option}"]
		label_str = f"Bet for option: {option_str}"
		if len(label_str) > 45:
			label_str = label_str[:42] + "..."
		self.bet_amount_input = discord.ui.TextInput(
			label=label_str, style=discord.TextStyle.short, placeholder=f"Your karma: {bettor_karma}", required=True
		)
		self.add_item(self.bet_amount_input)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		bot: core.Substiify = interaction.client
		kasino_id: int = self.kasino["id"]
		amount: int = self.bet_amount_input.value
		bettor_karma: int = await bot.db.pool.fetchval(
			"SELECT amount FROM karma WHERE discord_user_id = $1 AND discord_server_id = $2",
			interaction.user.id,
			interaction.guild.id,
		)
		user_bet: Record = self.user_bet

		try:
			amount = int(amount)
		except ValueError:
			return await interaction.response.send_message("Invalid amount", ephemeral=True)
		if amount < 1:
			return await interaction.response.send_message("You tried to bet < 1 karma! Silly you!", ephemeral=True)

		if self.kasino["locked"]:
			return await interaction.response.send_message(
				"The kasino is locked! No more bets are taken in. Time to wait and see...", ephemeral=True
			)

		if bettor_karma < amount:
			return await interaction.response.send_message("You don't have enough karma!", ephemeral=True)

		total_bet = amount
		output = "added"

		if user_bet is not None:
			total_bet = user_bet["amount"] + amount
			output = "increased"

		stmt_bet = """INSERT INTO kasino_bet (kasino_id, discord_user_id, amount, option) VALUES ($1, $2, $3, $4)
                      ON CONFLICT (kasino_id, discord_user_id) DO UPDATE SET amount = kasino_bet.amount + $3;"""
		stmt_update_user_karma = (
			"UPDATE karma SET amount = karma.amount - $1 WHERE discord_user_id = $2 AND discord_server_id = $3;"
		)
		async with bot.db.pool.acquire() as conn:
			async with conn.transaction():
				await conn.execute(stmt_bet, kasino_id, interaction.user.id, amount, self.option)
				await conn.execute(stmt_update_user_karma, amount, interaction.user.id, interaction.guild.id)

		output_embed = discord.Embed(color=discord.Colour.from_rgb(209, 25, 25))
		output_embed.title = f"**Successfully {output} bet on option {self.option}, on kasino with ID {kasino_id} for {amount} karma! Total bet is now: {total_bet} Karma**"
		output_embed.color = discord.Colour.from_rgb(52, 79, 235)
		output_embed.description = f"Remaining karma: {bettor_karma - amount}"

		await interaction.response.send_message(embed=output_embed, ephemeral=True)
		logger.info(f"Bet[user: {interaction.user}, amount: {amount}, option: {self.option}, kasino: {kasino_id}]")
		await _update_kasino_msg(bot, kasino_id)


class KasinoConfirmUnlockView(discord.ui.View):
	def __init__(self, kasino_id: int):
		super().__init__(timeout=None)
		self.kasino_id = kasino_id

	@discord.ui.button(label="Unlock", style=discord.ButtonStyle.blurple)
	async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
		bot: core.Substiify = interaction.client
		if not interaction.user.guild_permissions.manage_channels and not await bot.is_owner(interaction.user):
			return await interaction.response.send_message(
				"You don't have permission to unlock the kasino!", ephemeral=True
			)
		kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", self.kasino_id)
		is_locked = kasino["locked"]
		if not is_locked:
			return await interaction.response.send_message("Kasino is already unlocked!", ephemeral=True)
		await bot.db.pool.execute("UPDATE kasino SET locked = False WHERE id = $1", self.kasino_id)
		kasino_msg = await _update_kasino_msg(bot, self.kasino_id)
		kasino_members = await bot.db.pool.fetch(
			"SELECT discord_user_id FROM kasino_bet WHERE kasino_id = $1", self.kasino_id
		)
		embed = discord.Embed(
			title=f"🎲 Kasino `[ID: {self.kasino_id}]` unlocked!",
			description=f"{kasino['question']}\n[Jump to kasino]({kasino_msg.jump_url})",
			color=discord.Colour.from_rgb(52, 79, 235),
		)
		embed.set_footer(text=f"Unlocked by {interaction.user}", icon_url=interaction.user.display_avatar)

		for member in kasino_members:
			user = bot.get_user(member["discord_user_id"]) or await bot.fetch_user(member["discord_user_id"])
			await user.send(embed=embed)
		await interaction.response.send_message("Kasino unlocked! All kasino members messaged.", ephemeral=True)


class NotEnoughArguments(commands.UserInputError):
	pass


async def setup(bot: core.Substiify):
	query = await bot.db.pool.fetch("SELECT * FROM discord_channel WHERE upvote = True")
	upvote_channels = [channel["discord_channel_id"] for channel in query] or []
	await bot.add_cog(Karma(bot, upvote_channels))
