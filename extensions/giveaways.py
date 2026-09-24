import asyncio
import datetime
import logging
import re
import secrets
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands, tasks

import core

logger = logging.getLogger(__name__)

DURATION_UNITS = {"m": 60, "h": 3600, "d": 24 * 3600}
MAX_DURATION = 365 * 24 * 3600


def parse_duration(duration: str) -> int | None:
	match = re.fullmatch(r"(\d+)([mhd])", duration.strip().lower())
	if match is None:
		return None
	seconds = int(match.group(1)) * DURATION_UNITS[match.group(2)]
	if not 0 < seconds <= MAX_DURATION:
		return None
	return seconds


class Giveaways(commands.Cog):
	# The whole delivery attempt is bounded below its persisted lease, including DB checkpoints.
	GIVEAWAY_DELIVERY_TIMEOUT = 60
	GIVEAWAY_LEASE_SECONDS = 120

	def __init__(self, bot: core.Substiify):
		self.bot = bot

	async def cog_load(self) -> None:
		self.giveaway_task.start()

	async def cog_unload(self) -> None:
		self.giveaway_task.cancel()

	@commands.hybrid_group(aliases=["give"])
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	async def giveaway(self, ctx: commands.Context):
		"""
		Create and manage giveaways.

		Prefer slash commands:
		/giveaway create channel: #channel duration: 10m|2h|1d prize: "..." [hosted_by]

		Prefix (alias):
		<<give c <#channel> <duration> <prize> [@host]
		"""
		await ctx.send_help(ctx.command)

	@giveaway.command(aliases=["c"], usage="create <channel> <duration> <prize> [hosted_by] [winners]")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(
		channel="In which channel should the Giveaway be hosted?",
		duration="For how long should the Giveaway be hosted? Type number followed by (m|h|d). Example: `10m`",
		prize="What is the prize of the Giveaway?",
		hosted_by="Who is hosting the Giveaway? If not specified, the author of the command will be the host.",
		winners="How many winners should be selected? Default: 1",
	)
	async def create(
		self,
		ctx: commands.Context,
		channel: discord.TextChannel,
		duration: str,
		prize: str,
		hosted_by: discord.Member = None,
		winners: int = 1,
	):
		"""
		Create a giveaway. Requires Manage Channels.

		Slash (recommended):
		/giveaway create channel: #channel duration: 10m|2h|1d prize: "..." [hosted_by] [winners]

		Prefix examples:
		<<giveaway create <#channel> <duration> <prize> [@host]
		<<give c <#channel> <duration> <prize> [@host]
		"""
		if ctx.guild is None or channel.guild.id != ctx.guild.id:
			return await self._safe_notify(ctx, content="Choose a giveaway channel in this server.")
		if hosted_by is None or hosted_by.bot:
			hosted_by = ctx.author

		channel = await self.bot.fetch_channel(channel.id)
		perms = channel.permissions_for(ctx.me)
		missing = []
		if not perms.send_messages:
			missing.append("Send Messages")
		if not perms.add_reactions:
			missing.append("Add Reactions")
		if not perms.read_message_history:
			missing.append("Read Message History")
		if missing:
			missing_list = ", ".join(missing)
			embed = discord.Embed(
				description=f"I need these permissions in {channel.mention}: {missing_list}", color=discord.Colour.red()
			)
			return await self._safe_notify(ctx, embed=embed)

		time = parse_duration(duration)
		if time is None:
			await self._safe_notify(
				ctx,
				embed=discord.Embed(
					description="Invalid duration. Use a positive number with m, h or d (max 365d), e.g. `2h`.",
					color=discord.Colour.red(),
				),
			)
			return

		end = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=time)
		end_string = end.strftime("%d.%m.%Y %H:%M")

		# Validate winners
		if winners < 1 or winners > 10:
			return await self._safe_notify(
				ctx,
				embed=discord.Embed(
					description="Please choose a winners count between 1 and 10.", color=discord.Colour.red()
				),
			)

		embed = self.create_giveaway_embed(hosted_by, prize, winners)
		base_desc = embed.description or ""
		embed.description = f"{base_desc}\nReact with :tada: to enter!\nEnds <t:{int(end.timestamp())}:R>"
		embed.set_footer(text=f"Giveaway ends on {end_string}")

		await self.bot.db.prepare_command_context(hosted_by, ctx.guild, channel)
		new_msg = await channel.send(embed=embed)
		stmt = """INSERT INTO giveaway
			(discord_user_id, end_date, prize, discord_server_id, discord_channel_id, discord_message_id, winners_count)
			VALUES ($1, $2, $3, $4, $5, $6, $7)"""
		try:
			await new_msg.add_reaction("🎉")
			await self.bot.db.pool.execute(
				stmt, hosted_by.id, end, prize, ctx.guild.id, channel.id, new_msg.id, winners
			)
		except Exception:
			try:
				await new_msg.delete()
			except discord.HTTPException, TimeoutError:
				logger.exception("Could not remove failed giveaway message %s", new_msg.id)
			raise
		setup_complete = f"Setup finished. Giveaway for **'{prize}'** will be in {channel.mention}"
		await self._safe_notify(ctx, embed=discord.Embed(description=setup_complete))

	@giveaway.command(usage="reroll <message_id>")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(message_id="The ID of the discord giveaway message you want to reroll.")
	async def reroll(self, ctx: commands.Context, message_id: int):
		"""Explicitly draw again, only after any previous result has finished delivery."""
		if ctx.guild is None:
			return await self._safe_notify(ctx, content="Giveaways belong to a server channel.")
		giveaway = await self.bot.db.pool.fetchrow(
			"""SELECT * FROM giveaway WHERE discord_message_id = $1
			AND discord_server_id = $2 AND discord_channel_id = $3""",
			message_id,
			ctx.guild.id,
			ctx.channel.id,
		)
		if giveaway is not None and (giveaway["cancelled_at"] or giveaway["unavailable_at"]):
			return await self._safe_notify(ctx, content="This giveaway was cancelled or its source is unavailable.")
		try:
			msg = await ctx.fetch_message(message_id)
			if msg.author.id != self.bot.user.id or msg.guild.id != ctx.guild.id or msg.channel.id != ctx.channel.id:
				return await self._safe_notify(ctx, content="This is not one of my giveaways in this channel.")
			users = await self._giveaway_entrants(msg)
		except discord.NotFound, discord.Forbidden, TimeoutError:
			return await self._safe_notify(ctx, content="The giveaway source could not be read; no reroll was made.")
		if giveaway is None:
			giveaway, result = await self._register_historical_giveaway(ctx, msg, users)
		else:
			result = await self._select_giveaway_result(
				giveaway["id"], users, self.get_giveaway_winners(msg), reroll_version=giveaway["result_version"]
			)
		if result is None:
			return await self._safe_notify(
				ctx, content="Only a completed giveaway can be rerolled. A pending result must finish first."
			)
		delivered = await self._deliver_giveaway_result(giveaway, result, msg.channel, msg)
		if delivered is False:
			return await self._safe_notify(
				ctx,
				content="Reroll saved, but delivery stopped because Discord denied access or the source disappeared.",
			)
		await self._safe_notify(ctx, content="The explicit reroll was saved. Any unfinished delivery will resume.")

	@giveaway.command(name="list", usage="list")
	async def giveaway_list(self, ctx: commands.Context):
		"""
		Lists all active giveaways.
		"""
		if ctx.guild is None:
			return await self._safe_notify(ctx, content="Giveaways belong to a server channel.")
		giveaways = await self.bot.db.pool.fetch(
			"""SELECT g.* FROM giveaway AS g
			LEFT JOIN giveaway_result AS r ON r.giveaway_id = g.id AND r.version = g.result_version
			WHERE g.discord_server_id = $1 AND g.cancelled_at IS NULL AND g.unavailable_at IS NULL
			AND (g.result_version = 0 OR r.completed_at IS NULL)""",
			ctx.guild.id,
		)
		if len(giveaways) == 0:
			return await ctx.send("There are no active giveaways")

		embed = discord.Embed(title="Active Giveaways", description="")
		for giveaway in giveaways:
			end_date = giveaway["end_date"]
			embed.description += f"[{giveaway['prize']}](https://discord.com/channels/{giveaway['discord_server_id']}/{giveaway['discord_channel_id']}/{giveaway['discord_message_id']}) - Ends <t:{int(end_date.timestamp())}:R>\n"
		await ctx.send(embed=embed)

	@commands.command(name="giveawayInfo", hidden=True)
	@commands.is_owner()
	async def giveaway_info(self, ctx: commands.Context):
		"""
		Shows information about he giveaway task.
		"""
		if self.giveaway_task.time is None:
			times_string = "None"
		else:
			times_string = [f"{time}\n" for time in self.giveaway_task.time]
		embed = discord.Embed(title="Giveaway Task", description="")
		embed.add_field(name="Running", value=f"`{self.giveaway_task.is_running()}`", inline=False)
		embed.add_field(
			name="Current UTC time", value=f"`{datetime.datetime.now(datetime.timezone.utc)}`", inline=False
		)
		embed.add_field(name="Next iteration", value=f"`{self.giveaway_task.next_iteration}`", inline=False)
		embed.add_field(name="Last iteration", value=f"`{self.giveaway_task._last_iteration}`", inline=False)
		embed.add_field(name="Times", value=times_string, inline=False)
		await ctx.send(embed=embed)

	@giveaway.command(aliases=["cancel"], usage="stop <message_id>")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(message_id="The ID of the discord giveaway message you want to stop.")
	async def stop(self, ctx: commands.Context, message_id: int):
		"""Cancel only an unselected giveaway belonging to this server and channel."""
		if ctx.guild is None:
			return await self._safe_notify(ctx, content="Giveaways belong to a server channel.")
		giveaway = await self.bot.db.pool.fetchrow(
			"""UPDATE giveaway SET cancelled_at = clock_timestamp()
			WHERE discord_message_id = $1 AND discord_server_id = $2 AND discord_channel_id = $3
			AND result_version = 0 AND cancelled_at IS NULL AND unavailable_at IS NULL RETURNING *""",
			message_id,
			ctx.guild.id,
			ctx.channel.id,
		)
		if giveaway is None:
			return await self._safe_notify(
				ctx,
				content="No cancellable giveaway exists here. Selected, delivering or completed results cannot be cancelled.",
			)
		try:
			msg = await ctx.fetch_message(message_id)
			if msg.author.id != self.bot.user.id:
				return await self._safe_notify(
					ctx, content="Giveaway cancelled in the database; the source is not mine to edit."
				)
			await msg.edit(
				embed=discord.Embed(title="Giveaway Cancelled", description="The giveaway has been cancelled!")
			)
		except discord.HTTPException, TimeoutError:
			logger.warning("Giveaway %s cancelled, but its source could not be updated.", giveaway["id"], exc_info=True)
			return await self._safe_notify(ctx, content="Giveaway cancelled. Its source message could not be updated.")
		await self._safe_notify(ctx, content="Giveaway has been cancelled.")

	@tasks.loop(seconds=30)
	async def giveaway_task(self) -> None:
		try:
			giveaways = await self.bot.db.pool.fetch(
				"""SELECT g.* FROM giveaway AS g
				LEFT JOIN giveaway_result AS r ON r.giveaway_id = g.id AND r.version = g.result_version
				WHERE g.end_date <= CURRENT_TIMESTAMP AND g.cancelled_at IS NULL AND g.unavailable_at IS NULL
				AND (g.result_version = 0 OR r.completed_at IS NULL)
				AND (r.delivery_until IS NULL OR r.delivery_until <= clock_timestamp())"""
			)
		except Exception:
			logger.exception("Failed to load active giveaways.")
			return

		for giveaway in giveaways:
			try:
				await self._process_giveaway(giveaway)
			except Exception:
				logger.exception("Failed to process giveaway %s.", giveaway["id"])

	async def _process_giveaway(self, giveaway) -> None:
		# A loop snapshot can predate cancellation, another worker, or an explicit reroll.
		giveaway = await self.bot.db.pool.fetchrow("SELECT * FROM giveaway WHERE id = $1", giveaway["id"])
		if giveaway is None or giveaway["cancelled_at"] or giveaway["unavailable_at"]:
			return
		if datetime.datetime.now(datetime.timezone.utc) < giveaway["end_date"]:
			return
		result = await self.bot.db.pool.fetchrow(
			"SELECT * FROM giveaway_result WHERE giveaway_id = $1 AND version = $2",
			giveaway["id"],
			giveaway["result_version"],
		)
		if result is not None and result["completed_at"] is not None:
			return
		try:
			channel = await self.bot.fetch_channel(giveaway["discord_channel_id"])
			if getattr(channel, "guild", None) is None or channel.guild.id != giveaway["discord_server_id"]:
				await self._mark_giveaway_unavailable(
					giveaway["id"], "Source channel does not belong to the recorded server."
				)
				return
			msg = await channel.fetch_message(giveaway["discord_message_id"])
			if msg.author.id != self.bot.user.id:
				await self._mark_giveaway_unavailable(giveaway["id"], "Source message is not owned by this bot.")
				return
			if result is None:
				users = await self._giveaway_entrants(msg)
		except (discord.NotFound, discord.Forbidden) as error:
			await self._mark_giveaway_unavailable(giveaway["id"], f"Source unavailable: {type(error).__name__}.")
			return
		if result is None:
			result = await self._select_giveaway_result(giveaway["id"], users, self.get_giveaway_winners(msg))
		if result is not None:
			await self._deliver_giveaway_result(giveaway, result, channel, msg)

	async def _giveaway_entrants(self, msg):
		reaction = discord.utils.find(lambda r: str(r.emoji) == "🎉", msg.reactions)
		return [] if reaction is None else [user.id async for user in reaction.users() if not user.bot]

	async def _select_giveaway_result(self, giveaway_id, entrant_ids, winners_count, *, reroll_version=None):
		# Discord snapshots are fetched before this transaction. Only the lock winner draws.
		async with self.bot.db.pool.acquire() as connection:
			async with connection.transaction():
				giveaway = await connection.fetchrow("SELECT * FROM giveaway WHERE id = $1 FOR UPDATE", giveaway_id)
				if giveaway is None or giveaway["cancelled_at"] or giveaway["unavailable_at"]:
					return None
				previous = await connection.fetchrow(
					"SELECT * FROM giveaway_result WHERE giveaway_id = $1 AND version = $2",
					giveaway_id,
					giveaway["result_version"],
				)
				if reroll_version is None:
					if previous is not None:
						return previous
					if giveaway["end_date"] > datetime.datetime.now(datetime.timezone.utc):
						return None
				elif (
					reroll_version != giveaway["result_version"] or previous is None or previous["completed_at"] is None
				):
					return None
				return await self._insert_giveaway_result(connection, giveaway, entrant_ids, winners_count)

	async def _insert_giveaway_result(self, connection, giveaway, entrant_ids, winners_count):
		unique = list(dict.fromkeys(entrant_ids))
		count = giveaway["winners_count"] or max(1, min(winners_count, 10))
		winner_ids = secrets.SystemRandom().sample(unique, min(count, len(unique))) if unique else []
		version = giveaway["result_version"] + 1
		result = await connection.fetchrow(
			"""INSERT INTO giveaway_result(giveaway_id, version, winners_count, winner_ids)
			VALUES ($1, $2, $3, $4) RETURNING *""",
			giveaway["id"],
			version,
			count,
			winner_ids,
		)
		await connection.execute(
			"UPDATE giveaway SET result_version = $2, winners_count = $3 WHERE id = $1",
			giveaway["id"],
			version,
			count,
		)
		return result

	async def _register_historical_giveaway(self, ctx, msg, entrant_ids):
		# Pre-migration completed giveaways were deleted from PostgreSQL. Adopt only
		# a bot-authored giveaway in this exact guild/channel, never an arbitrary embed.
		if not msg.embeds:
			return None, None
		embed = msg.embeds[0]
		host = next((str(field.value) for field in embed.fields if field.name == "Hosted By:"), "")
		host_match = re.fullmatch(r"<@!?([0-9]+)>", host)
		prize_match = re.fullmatch(r"Win \*\*(.+)\*\*!", embed.description or "", re.DOTALL)
		legacy_empty = not host and embed.footer.text == "No one won the giveaway (no one entered)"
		if embed.title != ":tada: Giveaway :tada:" or (host_match is None and not legacy_empty) or prize_match is None:
			return None, None
		host_id = int(host_match[1]) if host_match else None
		prize = prize_match[1]
		if (host_id is not None and not 0 < host_id < 2**63) or len(prize) > 255:
			return None, None
		async with self.bot.db.pool.acquire() as connection:
			async with connection.transaction():
				await self.bot.db.prepare_command_context(ctx.author, ctx.guild, ctx.channel, connection=connection)
				if host_id is not None:
					await connection.execute(
						"INSERT INTO discord_user(discord_user_id) VALUES ($1) ON CONFLICT DO NOTHING", host_id
					)
				giveaway = await connection.fetchrow(
					"""INSERT INTO giveaway(discord_user_id, end_date, prize, discord_server_id,
					discord_channel_id, discord_message_id, winners_count)
					VALUES ($1, CURRENT_TIMESTAMP, $2, $3, $4, $5, $6)
					ON CONFLICT (discord_message_id) DO NOTHING RETURNING *""",
					host_id,
					prize,
					ctx.guild.id,
					ctx.channel.id,
					msg.id,
					self.get_giveaway_winners(msg),
				)
				if giveaway is None:
					return None, None
				result = await self._insert_giveaway_result(
					connection, giveaway, entrant_ids, self.get_giveaway_winners(msg)
				)
				return giveaway, result

	async def _claim_giveaway_delivery(self, giveaway_id, version):
		token = uuid4()
		return await self.bot.db.pool.fetchrow(
			"""UPDATE giveaway_result AS r
			SET delivery_token = $3, delivery_until = clock_timestamp() + $4 * INTERVAL '1 second'
			FROM giveaway AS g WHERE r.giveaway_id = $1 AND r.version = $2
			AND g.id = r.giveaway_id AND g.result_version = r.version
			AND g.cancelled_at IS NULL AND g.unavailable_at IS NULL AND r.completed_at IS NULL
			AND (r.delivery_until IS NULL OR r.delivery_until <= clock_timestamp()) RETURNING r.*""",
			giveaway_id,
			version,
			token,
			self.GIVEAWAY_LEASE_SECONDS,
		)

	async def _deliver_giveaway_result(self, giveaway, result, channel, msg):
		result = await self._claim_giveaway_delivery(giveaway["id"], result["version"])
		if result is None:
			return
		token = result["delivery_token"]
		checkpointed = result["announcement_message_id"] is not None
		try:
			async with asyncio.timeout(self.GIVEAWAY_DELIVERY_TIMEOUT):
				embed, announcement = self._render_giveaway_result(giveaway, result)
				if not checkpointed:
					# Discord and PostgreSQL cannot commit atomically. If send is accepted
					# but its ack/checkpoint is lost, a retry can repeat this SAME result.
					sent = await channel.send(announcement)
					checkpointed = await self.bot.db.pool.fetchval(
						"""UPDATE giveaway_result SET announcement_message_id = $4
						WHERE giveaway_id = $1 AND version = $2 AND delivery_token = $3
						AND delivery_until > clock_timestamp() RETURNING TRUE""",
						giveaway["id"],
						result["version"],
						token,
						sent.id,
					)
					if not checkpointed:
						return
				if result["message_edited_at"] is None:
					await msg.edit(embed=embed)
				await self.bot.db.pool.execute(
					"""UPDATE giveaway_result SET message_edited_at = COALESCE(message_edited_at, clock_timestamp()),
					completed_at = clock_timestamp(), delivery_token = NULL, delivery_until = NULL
					WHERE giveaway_id = $1 AND version = $2 AND delivery_token = $3
					AND delivery_until > clock_timestamp()""",
					giveaway["id"],
					result["version"],
					token,
				)
		except (discord.NotFound, discord.Forbidden) as error:
			await self._mark_giveaway_unavailable(
				giveaway["id"], f"Result delivery unavailable: {type(error).__name__}.", token=token
			)
			return False
		finally:
			# Known announcements can retry only the idempotent edit immediately.
			# An ambiguous send/checkpoint retains its lease until expiry (also on crash).
			if checkpointed:
				await self.bot.db.pool.execute(
					"""UPDATE giveaway_result SET delivery_token = NULL, delivery_until = NULL
					WHERE giveaway_id = $1 AND version = $2 AND delivery_token = $3""",
					giveaway["id"],
					result["version"],
					token,
				)
		return True

	def _render_giveaway_result(self, giveaway, result):
		host = f"<@{giveaway['discord_user_id']}>" if giveaway["discord_user_id"] else "Unknown (historical giveaway)"
		embed = self.create_giveaway_embed(host, giveaway["prize"], result["winners_count"])
		if result["winner_ids"]:
			mentions = ", ".join(f"<@{user_id}>" for user_id in result["winner_ids"])
			embed.add_field(name=f"Congratulations on winning '{giveaway['prize']}'", value=mentions)
			announcement = f"Congratulations {mentions}! You won **{giveaway['prize']}**!"
			embed.set_footer(text="Giveaway ended")
		else:
			announcement = "No one won the giveaway (no one entered)"
			embed.set_footer(text=announcement)
		url = (
			f"https://discord.com/channels/{giveaway['discord_server_id']}/"
			f"{giveaway['discord_channel_id']}/{giveaway['discord_message_id']}"
		)
		if result["version"] > 1:
			announcement = f"Reroll: {announcement}"
		return embed, f"{announcement} — [Jump to giveaway]({url})"

	async def _mark_giveaway_unavailable(self, giveaway_id, reason, *, token=None):
		changed = await self.bot.db.pool.fetchval(
			"""UPDATE giveaway AS g SET unavailable_at = clock_timestamp(), unavailable_reason = $2
			WHERE g.id = $1 AND g.cancelled_at IS NULL AND g.unavailable_at IS NULL
			AND (g.result_version = 0 OR EXISTS (
				SELECT 1 FROM giveaway_result AS r WHERE r.giveaway_id = g.id AND r.version = g.result_version
				AND r.completed_at IS NULL AND (r.delivery_token = $3 OR r.delivery_until IS NULL
					OR r.delivery_until <= clock_timestamp())
			)) RETURNING TRUE""",
			giveaway_id,
			reason,
			token,
		)
		if changed:
			logger.warning(
				"Giveaway %s stopped: %s Any selected result and delivery checkpoints were retained.",
				giveaway_id,
				reason,
			)

	def get_giveaway_winners(self, msg: discord.Message):
		# Prefer a dedicated embed field "Winners:" if present
		try:
			for field in msg.embeds[0].fields:
				if str(field.name).strip().lower() == "winners:":
					value = int(str(field.value).strip())
					return max(1, min(value, 10))
		except Exception:
			pass
		return 1

	def create_giveaway_embed(self, author: discord.Member, prize, winners):
		embed = discord.Embed(
			title=":tada: Giveaway :tada:",
			description=f"Win **{prize}**!",
			color=core.constants.CYAN_COLOR,
		)
		host = author.mention if isinstance(author, (discord.Member, discord.User)) else author
		embed.add_field(name="Hosted By:", value=host)
		embed.add_field(name="Winners:", value=str(winners))
		return embed

	async def _safe_notify(
		self,
		ctx: commands.Context,
		*,
		content: str | None = None,
		embed: discord.Embed | None = None,
		delete_after: float | None = None,
	):
		# For slash invocations, prefer ephemeral interaction responses
		interaction = getattr(ctx, "interaction", None)
		if interaction is not None:
			try:
				await interaction.response.send_message(content=content, embed=embed, ephemeral=True)
				return
			except Exception:
				try:
					await interaction.followup.send(content=content, embed=embed, ephemeral=True)
					return
				except Exception:
					pass
		# For prefix, try sending in-channel, then DM fallback
		try:
			await ctx.send(content=content, embed=embed, delete_after=delete_after)
		except discord.Forbidden:
			try:
				await ctx.author.send(content=content, embed=embed)
			except Exception:
				pass


async def setup(bot: core.Substiify):
	await bot.add_cog(Giveaways(bot))
