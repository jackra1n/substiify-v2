import logging
import re
from datetime import timedelta

import discord
import asyncio
from asyncpg import Record
from discord import app_commands
from discord.ext import commands

import core
from database.karma import lock_karma_rows

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

		if payload.emoji.id is None:
			return

		upvote_emotes = await self._get_karma_upvote_emotes(payload.guild_id)
		downvote_emotes = await self._get_karma_downvote_emotes(payload.guild_id)

		if payload.emoji.id not in [*upvote_emotes, *downvote_emotes]:
			return

		post = await self._get_post_from_db(payload.message_id)
		message = None
		if post is None:
			result = await self.check_payload(payload)
			if result is None:
				return
			user, message = result

			await self.bot.db.upsert_user(user)
			user_id = user.id
		else:
			user_id = post["discord_user_id"]

		try:
			server = self.bot.get_guild(payload.guild_id)
			if server is None:
				logger.warning(f"Server {payload.guild_id} not found in cache for karma reaction. Fetching from API.")
				server = await self.bot.fetch_guild(payload.guild_id)
			channel = self.bot.get_channel(payload.channel_id)
			if channel is None:
				logger.warning(
					f"Channel {payload.channel_id} not found in cache for karma reaction. Fetching from API."
				)
				channel = await self.bot.fetch_channel(payload.channel_id)
		except Exception as e:
			logger.warning(f"Failed to fetch guild/channel for karma reaction: {e}")
			return

		await self.bot.db.upsert_server(server)
		await self.bot.db.upsert_channel(channel)

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
		await self._upsert_post_votes(payload, user_id, upvote, downvote, message=message)

	async def _get_post_from_db(self, message_id: int) -> Record:
		stmt = "SELECT * FROM post WHERE discord_message_id = $1"
		return await self.bot.db.pool.fetchrow(stmt, message_id)

	async def _upsert_karma(self, payload: discord.RawReactionActionEvent, user_id: int, amount: int):
		await self.bot.db.pool.execute(UPSERT_KARMA_QUERY, user_id, payload.guild_id, amount)

	async def _upsert_post_votes(
		self,
		payload: discord.RawReactionActionEvent,
		user_id: int,
		upvote: int,
		downvote: int,
		message: discord.Message | None = None,
	):
		if message is None:
			await self.bot.db.pool.execute(
				"UPDATE post SET upvotes = post.upvotes + $1, downvotes = post.downvotes + $2 WHERE discord_message_id = $3",
				upvote,
				downvote,
				payload.message_id,
			)
			return

		await self.bot.db.pool.execute(
			UPSERT_POST_VOTES_QUERY,
			user_id,
			payload.guild_id,
			payload.channel_id,
			payload.message_id,
			message.created_at,
			upvote,
			downvote,
		)

	async def check_payload(
		self, payload: discord.RawReactionActionEvent
	) -> tuple[discord.Member, discord.Message] | None:
		if payload.event_type == "REACTION_ADD" and payload.member.bot:
			return None
		message = await self.__get_message_from_payload(payload)
		if message is None:
			return None
		if message.author.bot:
			return None
		reaction_user = payload.member or self.bot.get_user(payload.user_id)
		if not reaction_user:
			logger.warning(f"User {payload.user_id} not found in cache for karma reaction. Fetching from API.")
			try:
				reaction_user = await self.bot.fetch_user(payload.user_id)
			except Exception as e:
				logger.warning(f"Failed to fetch user {payload.user_id} for karma reaction: {e}")
				return None
		if reaction_user == message.author:
			return None
		return message.author, message

	async def __get_message_from_payload(self, payload: discord.RawReactionActionEvent) -> discord.Message | None:
		cached_message = discord.utils.get(self.bot.cached_messages, id=payload.message_id)
		if cached_message is not None:
			return cached_message
		channel = self.bot.get_channel(payload.channel_id)
		if channel is None:
			logger.debug(f"Channel {payload.channel_id} not in cache, cannot fetch message for karma reaction.")
			return None
		try:
			logger.debug(f"Message {payload.message_id} not found in cache for karma reaction. Fetching from API.")
			return await channel.fetch_message(payload.message_id)
		except discord.errors.NotFound:
			logger.debug(f"Message {payload.message_id} not found (deleted).")
			return None
		except Exception as e:
			logger.warning(f"Failed to fetch message {payload.message_id} for karma reaction: {e}")
			return None

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
		stmt = "SELECT * FROM discord_channel WHERE discord_channel_id = $1 AND upvote = True"
		votes_enabled = await self.bot.db.pool.fetch(stmt, channel.id)
		logger.info(f"Votes enabled: {votes_enabled}")

		# The cache only follows confirmed database state; a failed enable must leave it unchanged.
		embed = discord.Embed(color=discord.Colour.green())
		if votes_enabled:
			if channel.id not in self.vote_channels:
				self.vote_channels.append(channel.id)
			embed.description = f"Votes are **already active** in {ctx.channel.mention}!"
			return await ctx.send(embed=embed)

		await self.bot.db.upsert_channel(channel)
		await self.bot.db.pool.execute(
			"UPDATE discord_channel SET upvote = True WHERE discord_channel_id = $1", channel.id
		)
		if channel.id not in self.vote_channels:
			self.vote_channels.append(channel.id)

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
		await self.bot.db.upsert_channel(channel)
		await self.bot.db.pool.execute(
			"UPDATE discord_channel SET upvote = False WHERE discord_channel_id = $1", channel.id
		)

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
			await ctx.reply(embed=embed)
			error.is_handled = True

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

		if not 0 < amount <= 2**63 - 1:
			embed.description = f"You cannot donate {amount} karma!"
			return await ctx.send(embed=embed)

		if user not in ctx.guild.members:
			embed.description = f"`{user}` is not a member of this server!"
			return await ctx.send(embed=embed)

		if not await self.donate_karma(ctx.author, user, ctx.guild, amount):
			embed.description = "You don't have enough karma!"
			return await ctx.send(embed=embed)

		embed = discord.Embed(color=discord.Colour.green())
		embed.description = f"{ctx.author.mention} has donated {amount} karma to {user.mention}!"
		await ctx.send(embed=embed)

	async def donate_karma(
		self, donor: discord.User, recipient: discord.User, guild: discord.Guild, amount: int
	) -> bool:
		"""Transfer karma atomically; a failed conditional debit never credits the recipient."""
		if not 0 < amount <= 2**63 - 1:
			raise ValueError("Donation amount must be a positive BIGINT.")
		users = {user.id: user for user in (donor, recipient)}
		async with self.bot.db.pool.acquire(timeout=5) as conn:
			async with conn.transaction():
				# Metadata upserts lock users too; reciprocal transfers use the same order throughout.
				for user_id in sorted(users):
					await self.bot.db.upsert_user(users[user_id], connection=conn)
				await self.bot.db.upsert_server(guild, connection=conn)
				await lock_karma_rows(conn, guild.id, users)
				remaining = await conn.fetchval(
					"""UPDATE karma SET amount = amount - $1
					WHERE discord_user_id = $2 AND discord_server_id = $3 AND amount >= $1
					RETURNING amount""",
					amount,
					donor.id,
					guild.id,
				)
				if remaining is None:
					return False
				await conn.execute(
					"UPDATE karma SET amount = amount + $1 WHERE discord_user_id = $2 AND discord_server_id = $3",
					amount,
					recipient.id,
					guild.id,
				)
		return True

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
			# Unexpected errors must reach the central handler for diagnostics, persistence and admin reports.
			return
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
				embed_string += f"\n`{'add' if emote['increase_karma'] is True else 'remove'}:` "
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

			if not results:
				embed.description = "No users have karma."
				return await ctx.send(embed=embed)

			async def get_user(user_id: int) -> discord.User:
				return self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)

			users = await asyncio.gather(*[get_user(entry["discord_user_id"]) for entry in results])

			lines = [
				f"`{str(i).rjust(2)}.` | `{entry['amount']}` - {user.mention}"
				for i, (entry, user) in enumerate(zip(results, users), start=1)
			]

			embed.description = "\n".join(lines)
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
			month_board = await self.fetch_and_create_leaderboard(ctx, user, timedelta(days=30))
			week_board = await self.fetch_and_create_leaderboard(ctx, user, timedelta(days=7))

		embed = discord.Embed(title="Top Messages")
		embed.set_thumbnail(url=ctx.guild.icon)
		embed.add_field(name="Top 5 All Time", value=all_board, inline=False)
		embed.add_field(name="Top 5 This Month", value=month_board, inline=False)
		embed.add_field(name="Top 5 This Week", value=week_board, inline=False)
		await ctx.send(embed=embed)

	async def fetch_and_create_leaderboard(
		self, ctx: commands.Context, user: discord.User | None, interval: timedelta | None = None
	):
		stmt = "SELECT * FROM post WHERE discord_server_id = $1"
		params: list = [ctx.guild.id]
		if user:
			params.append(user.id)
			stmt += f" AND discord_user_id = ${len(params)}"
		if interval:
			params.append(interval)
			stmt += f" AND created_at > NOW() - ${len(params)}::interval"
		stmt += " ORDER BY upvotes DESC LIMIT 5"
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

	async def _create_post_leaderboard(self, posts: list[Record]) -> str:
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

	def _create_message_url(self, server_id, channel_id, message_id) -> str:
		return f"https://discordapp.com/channels/{server_id}/{channel_id}/{message_id}"

	async def _get_karma_downvote_emotes(self, guild_id: int) -> list[int]:
		stmt_downvotes = (
			"SELECT discord_emote_id FROM karma_emote WHERE discord_server_id = $1 AND increase_karma = False"
		)
		emote_records = await self.bot.db.pool.fetch(stmt_downvotes, guild_id)
		server_downvote_emotes = [emote["discord_emote_id"] for emote in emote_records]
		server_downvote_emotes.append(int(core.constants.DOWNVOTE_EMOTE_ID))
		return server_downvote_emotes


class NotEnoughArguments(commands.UserInputError):
	pass


async def setup(bot: core.Substiify):
	query = await bot.db.pool.fetch("SELECT * FROM discord_channel WHERE upvote = True")
	upvote_channels = [channel["discord_channel_id"] for channel in query] or []
	await bot.add_cog(Karma(bot, upvote_channels))
