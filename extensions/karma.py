import logging
import re

import discord
import asyncio
from asyncpg import Record
from discord import app_commands
from discord.ext import commands

import core
import utils

logger = logging.getLogger(__name__)


UPSERT_KARMA_QUERY = """INSERT INTO karma (discord_user_id, discord_server_id, amount) VALUES ($1, $2, $3)
                        ON CONFLICT (discord_user_id, discord_server_id) DO UPDATE SET amount = karma.amount + $3"""
UPSERT_POST_VOTES_QUERY = """INSERT INTO post (discord_user_id, discord_server_id, discord_channel_id, discord_message_id, created_at, upvotes, downvotes)
                             VALUES ($1, $2, $3, $4, $5, $6, $7)
                             ON CONFLICT (discord_message_id) DO UPDATE SET upvotes = post.upvotes + $6, downvotes = post.downvotes + $7"""


class KasinoStateError(ValueError):
	pass


def _check_kasino(kasino: Record | None, guild_id: int, *, allow_settled: bool = False) -> None:
	if kasino is None:
		raise KasinoStateError("Kasino not found. This message may be out of date.")
	if kasino["discord_server_id"] != guild_id:
		raise KasinoStateError("That kasino is not in this server.")
	if kasino["settled_at"] is not None and not allow_settled:
		raise KasinoStateError("This kasino has already closed. No more changes are allowed.")


async def _lock_karma_rows(conn, guild_id: int, user_ids) -> None:
	# Always lock in user-ID order, including inserts for participants with no balance row.
	for user_id in sorted(set(user_ids)):
		await conn.execute(
			"""INSERT INTO karma (discord_user_id, discord_server_id, amount) VALUES ($1, $2, 0)
			ON CONFLICT (discord_user_id, discord_server_id) DO NOTHING""",
			user_id,
			guild_id,
		)
		await conn.fetchval(
			"SELECT amount FROM karma WHERE discord_user_id = $1 AND discord_server_id = $2 FOR UPDATE",
			user_id,
			guild_id,
		)


def _calculate_payouts(bets: list[Record], winning_option: int) -> dict[int, int]:
	if winning_option == 3:
		return {bet["discord_user_id"]: bet["amount"] for bet in bets}
	payouts = {bet["discord_user_id"]: 0 for bet in bets}
	total_pool = sum(bet["amount"] for bet in bets)
	winners = [bet for bet in bets if bet["option"] == winning_option]
	winner_pool = sum(bet["amount"] for bet in winners)
	if not winner_pool:
		return payouts
	remainders = []
	for bet in winners:
		user_id = bet["discord_user_id"]
		payouts[user_id], remainder = divmod(bet["amount"] * total_pool, winner_pool)
		remainders.append((remainder, user_id))
	# Largest remainders win the indivisible units; equal remainders favor the lower user ID.
	remainders.sort(key=lambda entry: (-entry[0], entry[1]))
	for _, user_id in remainders[: total_pool - sum(payouts.values())]:
		payouts[user_id] += 1
	return payouts


class Karma(commands.Cog):
	COG_EMOJI = "☯️"

	def __init__(self, bot: core.Substiify, vote_channels: list[int]):
		self.bot = bot
		self.vote_channels = vote_channels
		self.kasino_message_locks: dict[int, asyncio.Lock] = {}

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
				await _lock_karma_rows(conn, guild.id, users)
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

	def _create_kasino_message_url(self, kasino: Record) -> str:
		return self._create_message_url(
			kasino["discord_server_id"], kasino["discord_channel_id"], kasino["discord_message_id"]
		)

	def _create_message_url(self, server_id, channel_id, message_id) -> str:
		return f"https://discordapp.com/channels/{server_id}/{channel_id}/{message_id}"

	@commands.hybrid_group(name="kasino", aliases=["kas"], invoke_without_command=True)
	async def kasino(self, ctx: commands.Context):
		"""Karma kasino which allows people to bet on a question with two options.
		If you want to open a kasino, use the subcommand `kasino open`.
		"""
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
		await ctx.defer()
		await self.bot.db.prepare_command_context(ctx.author, ctx.guild, ctx.channel)
		kasino_msg = await _notify(ctx.send(embed=discord.Embed(description="Opening kasino...")))
		if kasino_msg is None:
			return
		kasino_id = await self.bot.db.pool.fetchval(
			"""INSERT INTO kasino (discord_server_id, discord_channel_id, discord_message_id, question, option1, option2)
			VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
			ctx.guild.id,
			ctx.channel.id,
			kasino_msg.id,
			question,
			op_a,
			op_b,
		)
		await _update_kasino_msg(ctx.bot, kasino_id)

	@kasino.command(name="close", usage="close <kasino_id> <winning_option>")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(
		kasino_id="The ID of the kasino you want to close. The ID should be visible in the kasino message.",
		winner="The winning option. 1 or 2. 3 to abort.",
	)
	async def kasino_close(self, ctx: commands.Context, kasino_id: int, winner: int):
		"""Closes a karma kasino and announces the winner. To cancel the kasino, use 3 as the winner."""
		await ctx.defer()
		try:
			kasino, bets, newly_settled = await self.settle_kasino(kasino_id, ctx.guild.id, winner)
		except KasinoStateError as error:
			return await _notify(ctx.reply(str(error), ephemeral=True))
		await _update_kasino_msg(self.bot, kasino_id)
		await _notify(
			ctx.send(
				content=None if newly_settled else "Already closed; showing the saved result. No karma was paid again.",
				embed=_kasino_conclusion(kasino, bets),
			)
		)
		if newly_settled:
			await self._notify_kasino_results(kasino, bets)

	@kasino_close.error
	async def kasino_close_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.errors.MissingRequiredArgument):
			msg = f"You didn't provide a required argument!\nCorrect usage is `{ctx.prefix}kasino close <kasino_id> <winning_option>`"
			msg += "\nUse option `3` to close and abort the kasino (no winner)."
			embed = discord.Embed(description=msg, color=discord.Colour.red())
			await ctx.send(embed=embed)
		elif isinstance(error, commands.errors.BadArgument):
			await ctx.send(f"Bad argument: {error}")
		else:
			# Unrecognized errors keep their invocation for the central handler's diagnostics.
			return
		error.is_handled = True
		if not ctx.interaction:
			await ctx.message.delete()

	@kasino.command(name="list", aliases=["l"], usage="list")
	async def kasino_list(self, ctx: commands.Context):
		"""Lists all open kasinos on the server."""
		await ctx.defer()
		embed = discord.Embed(title="Open kasinos")
		stmt_kasinos = """SELECT * FROM kasino
			WHERE settled_at IS NULL AND discord_server_id = $1 ORDER BY id ASC"""
		all_kasinos: list[Record] = await self.bot.db.pool.fetch(stmt_kasinos, ctx.guild.id)
		embed_kasinos = "".join(
			f"`{entry['id']}` - [{entry['question']}]({self._create_kasino_message_url(entry)}){' (locked)' if entry['locked'] else ''}\n"
			for entry in all_kasinos
		)
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
		await ctx.defer()
		try:
			kasino = await self.bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
			_check_kasino(kasino, ctx.guild.id, allow_settled=True)
		except KasinoStateError as error:
			return await _notify(ctx.send(str(error), ephemeral=True))
		await self.bot.db.prepare_command_context(ctx.author, ctx.guild, ctx.channel)
		message = await _update_kasino_msg(self.bot, kasino_id, resend_channel=ctx.channel)
		if message is None:
			await _notify(
				ctx.send(
					"Could not resend the kasino message. The saved state is unchanged; try again.", ephemeral=True
				)
			)
		elif ctx.interaction:
			await _notify(ctx.send(f"Kasino message sent: {message.jump_url}", ephemeral=True))

	async def _get_karma_downvote_emotes(self, guild_id: int) -> list[int]:
		stmt_downvotes = (
			"SELECT discord_emote_id FROM karma_emote WHERE discord_server_id = $1 AND increase_karma = False"
		)
		emote_records = await self.bot.db.pool.fetch(stmt_downvotes, guild_id)
		server_downvote_emotes = [emote["discord_emote_id"] for emote in emote_records]
		server_downvote_emotes.append(int(core.constants.DOWNVOTE_EMOTE_ID))
		return server_downvote_emotes

	async def place_kasino_bet(
		self, kasino_id: int, guild_id: int, user_id: int, option: int, amount: int
	) -> tuple[int, int, bool]:
		if option not in {1, 2} or not 0 < amount <= 2**63 - 1:
			raise KasinoStateError("Choose option 1 or 2 and a positive karma amount within the supported range.")
		async with self.bot.db.pool.acquire(timeout=5) as conn:
			async with conn.transaction():
				kasino = await conn.fetchrow("SELECT * FROM kasino WHERE id = $1 FOR UPDATE", kasino_id)
				_check_kasino(kasino, guild_id)
				if kasino["locked"]:
					raise KasinoStateError("This kasino is locked. No more bets are accepted.")
				bet = await conn.fetchrow(
					"SELECT * FROM kasino_bet WHERE kasino_id = $1 AND discord_user_id = $2",
					kasino_id,
					user_id,
				)
				if bet is not None and bet["option"] != option:
					raise KasinoStateError("Your existing bet is on the other option. You cannot change sides.")
				remaining = await conn.fetchval(
					"""UPDATE karma SET amount = amount - $1
					WHERE discord_user_id = $2 AND discord_server_id = $3 AND amount >= $1
					RETURNING amount""",
					amount,
					user_id,
					guild_id,
				)
				if remaining is None:
					raise KasinoStateError("You don't have enough karma!")
				total_bet = await conn.fetchval(
					"""INSERT INTO kasino_bet (kasino_id, discord_user_id, amount, option)
					VALUES ($1, $2, $3, $4)
					ON CONFLICT (kasino_id, discord_user_id)
					DO UPDATE SET amount = kasino_bet.amount + EXCLUDED.amount
					RETURNING amount""",
					kasino_id,
					user_id,
					amount,
					option,
				)
		return total_bet, remaining, bet is not None

	async def settle_kasino(
		self, kasino_id: int, guild_id: int, winning_option: int
	) -> tuple[Record, list[Record], bool]:
		"""Persist the entire settlement before any Discord work; retries only read its result."""
		if winning_option not in {1, 2, 3}:
			raise KasinoStateError("Winner has to be 1, 2 or 3 (abort).")
		async with self.bot.db.pool.acquire(timeout=5) as conn:
			async with conn.transaction():
				kasino = await conn.fetchrow("SELECT * FROM kasino WHERE id = $1 FOR UPDATE", kasino_id)
				_check_kasino(kasino, guild_id, allow_settled=True)
				bets = await conn.fetch(
					"SELECT * FROM kasino_bet WHERE kasino_id = $1 ORDER BY discord_user_id", kasino_id
				)
				if kasino["settled_at"] is not None:
					if kasino["winning_option"] != winning_option:
						raise KasinoStateError(
							f"This kasino already closed with result {kasino['winning_option']}; its result cannot change."
						)
					return kasino, bets, False
				payouts = _calculate_payouts(bets, winning_option)
				await _lock_karma_rows(conn, guild_id, payouts)
				for user_id, payout in payouts.items():
					await conn.execute(
						"UPDATE kasino_bet SET payout = $1 WHERE kasino_id = $2 AND discord_user_id = $3",
						payout,
						kasino_id,
						user_id,
					)
					if payout:
						await conn.execute(
							"UPDATE karma SET amount = amount + $1 WHERE discord_user_id = $2 AND discord_server_id = $3",
							payout,
							user_id,
							guild_id,
						)
				kasino = await conn.fetchrow(
					"""UPDATE kasino SET settled_at = NOW(), winning_option = $2, locked = True
					WHERE id = $1 RETURNING *""",
					kasino_id,
					winning_option,
				)
				bets = await conn.fetch(
					"SELECT * FROM kasino_bet WHERE kasino_id = $1 ORDER BY discord_user_id", kasino_id
				)
		return kasino, bets, True

	async def set_kasino_locked(self, kasino_id: int, guild_id: int, locked: bool) -> tuple[Record, bool]:
		async with self.bot.db.pool.acquire(timeout=5) as conn:
			async with conn.transaction():
				kasino = await conn.fetchrow("SELECT * FROM kasino WHERE id = $1 FOR UPDATE", kasino_id)
				_check_kasino(kasino, guild_id)
				if kasino["locked"] == locked:
					return kasino, False
				kasino = await conn.fetchrow(
					"UPDATE kasino SET locked = $2 WHERE id = $1 RETURNING *", kasino_id, locked
				)
		return kasino, True

	async def _notify_kasino_results(self, kasino: Record, bets: list[Record]) -> None:
		# A fixed number of workers bounds Discord requests without allocating a task per bettor.
		pending = iter(bets)

		async def send_results():
			for bet in pending:
				payout = bet["payout"]
				if kasino["winning_option"] == 3:
					title = f"You have been refunded {payout} karma."
				elif payout:
					title = f"You have won {payout} karma!"
				else:
					title = f"You lost your {bet['amount']} karma bet."
				embed = discord.Embed(
					title=title,
					description=f"Question: {kasino['question']}\nOriginal bet: {bet['amount']} karma.",
					color=core.constants.PRIMARY_COLOR,
				)
				await _send_kasino_dm(self.bot, bet["discord_user_id"], embed)

		try:
			async with asyncio.timeout(60):
				await asyncio.gather(*(send_results() for _ in range(min(5, len(bets)))))
		except TimeoutError:
			logger.warning("Timed out notifying kasino %s participants; settlement is saved.", kasino["id"])


async def _notify(operation):
	"""Discord delivery is best effort and must never undo a committed database operation."""
	try:
		async with asyncio.timeout(10):
			return await operation
	except discord.HTTPException, TimeoutError, OSError:
		logger.warning("Could not deliver kasino notification.", exc_info=True)
		return None


async def _send_kasino_dm(bot: core.Substiify, user_id: int, embed: discord.Embed) -> None:
	async def send():
		user = bot.get_user(user_id) or await bot.fetch_user(user_id)
		await user.send(embed=embed)

	await _notify(send())


def _kasino_conclusion(kasino: Record, bets: list[Record]) -> discord.Embed:
	total_pool = sum(bet["amount"] for bet in bets)
	paid = sum(bet["payout"] for bet in bets)
	winner = kasino["winning_option"]
	if winner == 3:
		title = f"Cancelled: {kasino['question']}"
		description = f"All bets have been refunded.\nReturned: **{paid} karma**."
	else:
		title = f"Result: {kasino[f'option{winner}']}"
		description = f"Question: {kasino['question']}\n"
		if any(bet["option"] == winner for bet in bets):
			description += f"Distributed to the winners: **{paid} karma**."
		else:
			description += f"No one bet on the winning option. **{total_pool} karma** was forfeited; no payouts."
	embed = discord.Embed(title=title, description=description, color=core.constants.PRIMARY_COLOR)
	embed.set_footer(text=f"Closed kasino | ID: {kasino['id']}")
	return embed


async def _update_kasino_msg(bot: core.Substiify, kasino_id: int, *, resend_channel=None) -> discord.Message | None:
	# Serialize renders on this bot so a slow pre-settlement edit cannot overwrite the conclusion.
	cog: Karma = bot.get_cog("Karma")
	lock = cog.kasino_message_locks.setdefault(kasino_id, asyncio.Lock())
	async with lock:
		try:
			async with asyncio.timeout(30):
				kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
				if kasino is None:
					return None
				old_message = None
				if resend_channel is not None:
					_check_kasino(kasino, resend_channel.guild.id, allow_settled=True)
					kasino_msg = await _notify(
						resend_channel.send(embed=discord.Embed(description="Loading kasino..."))
					)
					if kasino_msg is None:
						return None
					moved = await bot.db.pool.fetchval(
						"""UPDATE kasino SET discord_channel_id = $1, discord_message_id = $2
						WHERE id = $3 AND discord_channel_id = $4 AND discord_message_id = $5
						RETURNING id""",
						resend_channel.id,
						kasino_msg.id,
						kasino_id,
						kasino["discord_channel_id"],
						kasino["discord_message_id"],
					)
					if moved is None:
						await _notify(kasino_msg.delete())
						return None
					old_message = (kasino["discord_channel_id"], kasino["discord_message_id"])
				else:
					channel = bot.get_channel(kasino["discord_channel_id"]) or await bot.fetch_channel(
						kasino["discord_channel_id"]
					)
					kasino_msg = await channel.fetch_message(kasino["discord_message_id"])

				# Fetch one coherent current snapshot after channel/message network calls.
				async with bot.db.pool.acquire(timeout=5) as conn:
					async with conn.transaction(isolation="repeatable_read", readonly=True):
						kasino = await conn.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
						bets = await conn.fetch("SELECT * FROM kasino_bet WHERE kasino_id = $1", kasino_id)
				if kasino is None:
					return None
				if kasino["discord_message_id"] != kasino_msg.id:
					await _notify(kasino_msg.edit(view=None))
					return None
				if kasino["settled_at"] is not None:
					embed = _kasino_conclusion(kasino, bets)
					view = None
				else:
					bets_a_amount = sum(bet["amount"] for bet in bets if bet["option"] == 1)
					bets_b_amount = sum(bet["amount"] for bet in bets if bet["option"] == 2)
					a_odds, b_odds = _calculate_odds(bets_a_amount, bets_b_amount)
					description = "Place your bets!"
					if kasino["locked"]:
						description = "The kasino is locked. No more bets are accepted."
					description += f"\n**Participants:** `{len(bets)}`"
					embed = discord.Embed(
						title=f"{'[LOCKED] ' if kasino['locked'] else ''}{kasino['question']}",
						description=description,
						color=core.constants.PRIMARY_COLOR,
					)
					embed.set_footer(text=f"On the table: {bets_a_amount + bets_b_amount} Karma | ID: {kasino_id}")
					for option, odds, amount in ((1, a_odds, bets_a_amount), (2, b_odds, bets_b_amount)):
						embed.add_field(
							name=f"**{option}:** {kasino[f'option{option}']}",
							value=f"**Odds:** 1:{odds:.3f}\n**Pool:** {amount} Karma",
						)
					view = KasinoView(kasino)
				await kasino_msg.edit(embed=embed, view=view)
				if old_message is not None:

					async def delete_old_message():
						channel_id, message_id = old_message
						channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
						message = await channel.fetch_message(message_id)
						await message.delete()

					await _notify(delete_old_message())
				return kasino_msg
		except Exception:
			logger.warning("Could not refresh kasino %s; its database state is saved.", kasino_id, exc_info=True)
			return None


def _calculate_odds(bets_a_amount: int, bets_b_amount: int) -> tuple[float, float]:
	total_bets: float = float(bets_a_amount + bets_b_amount)
	a_odds: float = total_bets / float(bets_a_amount) if bets_a_amount else 1.0
	b_odds: float = total_bets / float(bets_b_amount) if bets_b_amount else 1.0
	return a_odds, b_odds


class KasinoView(discord.ui.View):
	def __init__(self, kasino: Record):
		super().__init__(timeout=None)
		self.kasino = kasino
		if kasino["settled_at"] is not None:
			return
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
		try:
			# Modals cannot follow a defer, so bound this lookup below Discord's response deadline.
			async with asyncio.timeout(2):
				kasino = await bot.db.pool.fetchrow(
					"""SELECT k.*, balance.amount AS bettor_karma, bet.option AS bet_option
					FROM kasino k
					LEFT JOIN karma balance ON balance.discord_user_id = $2
						AND balance.discord_server_id = k.discord_server_id
					LEFT JOIN kasino_bet bet ON bet.kasino_id = k.id AND bet.discord_user_id = $2
					WHERE k.id = $1""",
					self.view.kasino["id"],
					interaction.user.id,
				)
			_check_kasino(kasino, interaction.guild_id)
			if kasino["locked"]:
				raise KasinoStateError("This kasino is locked. No more bets are accepted.")
			if not kasino["bettor_karma"] or kasino["bettor_karma"] < 1:
				raise KasinoStateError("You don't have enough karma!")
			if kasino["bet_option"] is not None and kasino["bet_option"] != self.option:
				raise KasinoStateError("Your existing bet is on the other option. You cannot change sides.")
		except TimeoutError:
			return await _notify(
				interaction.response.send_message("The database is busy. Please try again.", ephemeral=True)
			)
		except KasinoStateError as error:
			return await _notify(interaction.response.send_message(str(error), ephemeral=True))
		await _notify(interaction.response.send_modal(KasinoBetModal(kasino, kasino["bettor_karma"], self.option)))


class KasinoLockButton(discord.ui.Button):
	def __init__(self, kasino: Record):
		locked = kasino["locked"]
		self.expected_locked = locked
		self.lock_settings = {
			True: ("Unlock", "🔐", discord.ButtonStyle.red),
			False: ("Lock", "🔒", discord.ButtonStyle.grey),
		}
		label, emoji, style = self.lock_settings[locked]
		super().__init__(label=label, emoji=emoji, style=style)

	async def callback(self, interaction: discord.Interaction):
		await interaction.response.defer(ephemeral=True)
		bot: core.Substiify = interaction.client
		cog: Karma = bot.get_cog("Karma")
		kasino_id = self.view.kasino["id"]
		if not interaction.user.guild_permissions.manage_channels and not await bot.is_owner(interaction.user):
			return await _notify(
				interaction.followup.send("You don't have permission to lock this kasino.", ephemeral=True)
			)
		try:
			kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
			_check_kasino(kasino, interaction.guild_id)
			if kasino["locked"] != self.expected_locked:
				await _notify(
					interaction.followup.send(
						"The kasino lock state changed. Please use the refreshed buttons.",
						ephemeral=True,
					)
				)
				await _update_kasino_msg(bot, kasino_id)
				return
			if kasino["locked"]:
				embed = discord.Embed(
					title="Unlock kasino",
					description=f"Unlock kasino {kasino_id}? Participants will be notified that they can increase their bets.",
					color=core.constants.PRIMARY_COLOR,
				)
				return await _notify(
					interaction.followup.send(
						embed=embed,
						view=KasinoConfirmUnlockView(kasino_id),
						ephemeral=True,
					)
				)
			_, changed = await cog.set_kasino_locked(kasino_id, interaction.guild_id, True)
		except KasinoStateError as error:
			return await _notify(interaction.followup.send(str(error), ephemeral=True))
		await _notify(
			interaction.followup.send(
				"Kasino locked!" if changed else "Kasino is already locked.",
				ephemeral=True,
			)
		)
		await _update_kasino_msg(bot, kasino_id)


class KasinoBetModal(discord.ui.Modal):
	def __init__(self, kasino: Record, bettor_karma: int, option: int):
		title = utils.ux.strip_emotes(kasino["question"])
		if len(title) > 45:
			title = title[:42] + "..."
		super().__init__(title=title)
		self.option = option
		self.kasino_id = kasino["id"]
		option_str = kasino[f"option{option}"]
		label_str = f"Bet for option: {option_str}"
		if len(label_str) > 45:
			label_str = label_str[:42] + "..."
		self.bet_amount_input = discord.ui.TextInput(
			label=label_str, style=discord.TextStyle.short, placeholder=f"Your karma: {bettor_karma}", required=True
		)
		self.add_item(self.bet_amount_input)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		await interaction.response.defer(ephemeral=True)
		bot: core.Substiify = interaction.client
		cog: Karma = bot.get_cog("Karma")
		try:
			amount = int(self.bet_amount_input.value)
		except ValueError:
			return await _notify(interaction.followup.send("Invalid amount.", ephemeral=True))
		try:
			await bot.db.prepare_command_context(interaction.user, interaction.guild, interaction.channel)
			total_bet, remaining_karma, increased = await cog.place_kasino_bet(
				self.kasino_id,
				interaction.guild_id,
				interaction.user.id,
				self.option,
				amount,
			)
		except KasinoStateError as error:
			return await _notify(interaction.followup.send(str(error), ephemeral=True))
		output = "Increased" if increased else "Added"
		output_embed = discord.Embed(
			title=f"{output} bet on option {self.option} for {amount} karma.",
			description=f"Kasino ID: {self.kasino_id}\nTotal bet: {total_bet} karma\nRemaining karma: {remaining_karma}",
			color=core.constants.PRIMARY_COLOR,
		)
		await _notify(interaction.followup.send(embed=output_embed, ephemeral=True))
		await _update_kasino_msg(bot, self.kasino_id)


class KasinoConfirmUnlockView(discord.ui.View):
	def __init__(self, kasino_id: int):
		super().__init__(timeout=None)
		self.kasino_id = kasino_id

	@discord.ui.button(label="Unlock", style=discord.ButtonStyle.blurple)
	async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
		await interaction.response.defer(ephemeral=True)
		bot: core.Substiify = interaction.client
		cog: Karma = bot.get_cog("Karma")
		if not interaction.user.guild_permissions.manage_channels and not await bot.is_owner(interaction.user):
			return await _notify(
				interaction.followup.send("You don't have permission to unlock this kasino.", ephemeral=True)
			)
		try:
			kasino, changed = await cog.set_kasino_locked(self.kasino_id, interaction.guild_id, False)
		except KasinoStateError as error:
			return await _notify(interaction.followup.send(str(error), ephemeral=True))
		if not changed:
			return await _notify(interaction.followup.send("Kasino is already unlocked.", ephemeral=True))
		await _notify(
			interaction.followup.send(
				"Kasino unlocked. Participant notifications will be attempted.",
				ephemeral=True,
			)
		)
		await _update_kasino_msg(bot, self.kasino_id)
		kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", self.kasino_id)
		if kasino is None or kasino["settled_at"] is not None or kasino["locked"]:
			return
		kasino_members = iter(
			await bot.db.pool.fetch("SELECT discord_user_id FROM kasino_bet WHERE kasino_id = $1", self.kasino_id)
		)
		embed = discord.Embed(
			title=f"Kasino {self.kasino_id} unlocked!",
			description=f"{kasino['question']}\n[Jump to kasino]({cog._create_kasino_message_url(kasino)})",
			color=core.constants.PRIMARY_COLOR,
		)

		async def send_notifications():
			for member in kasino_members:
				await _send_kasino_dm(bot, member["discord_user_id"], embed)

		try:
			async with asyncio.timeout(60):
				await asyncio.gather(*(send_notifications() for _ in range(5)))
		except TimeoutError:
			logger.warning("Timed out notifying participants of kasino %s unlock.", self.kasino_id)


class NotEnoughArguments(commands.UserInputError):
	pass


async def setup(bot: core.Substiify):
	query = await bot.db.pool.fetch("SELECT * FROM discord_channel WHERE upvote = True")
	upvote_channels = [channel["discord_channel_id"] for channel in query] or []
	await bot.add_cog(Karma(bot, upvote_channels))
