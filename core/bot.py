import asyncio
import datetime
import logging

import aiohttp
import asyncpg
import discord
from discord.app_commands import errors as slash_errors
from discord.ext import commands

import core
from database import Database

logger = logging.getLogger(__name__)

_TRANSIENT_DATABASE_ERRORS = (
	TimeoutError,
	OSError,
	asyncpg.PostgresConnectionError,
	asyncpg.CannotConnectNowError,
	asyncpg.TooManyConnectionsError,
	asyncpg.QueryCanceledError,
	asyncpg.LockNotAvailableError,
)


class _CommandPreparationError(commands.CommandInvokeError):
	"""Database preparation failed before the command callback could run."""


class Substiify(commands.Bot):
	_PREPARATION_TIMEOUT = 1.5

	def __init__(self, *, database: Database) -> None:
		self.db = database
		self.version = core.__version__
		self.start_time = datetime.datetime.now(datetime.timezone.utc)
		prefix = core.config.BOT_PREFIX
		if not prefix:
			raise RuntimeError("BOT_PREFIX must be configured before creating the bot")
		owner_id = core.config.BOT_OWNER_ID
		if owner_id is None:
			raise RuntimeError("BOT_OWNER_ID must be configured before creating the bot")
		intents = discord.Intents().all()
		super().__init__(
			command_prefix=commands.when_mentioned_or(prefix),
			intents=intents,
			owner_id=owner_id,
			max_messages=3000,
		)
		self.before_invoke(self._prepare_command_context)

	async def _prepare_command_context(self, ctx: commands.Context) -> None:
		# An initial response fixes visibility and can only be used once. Leave it
		# to the callback (including modal responses), but bound unacknowledged
		# interactions' database work so failures can still be answered in time.
		timeout = None
		if ctx.interaction is not None and not ctx.interaction.response.is_done():
			# Checks/converters may already have consumed part of Discord's three
			# seconds. Reserve a second for sending the failure response.
			elapsed = (discord.utils.utcnow() - ctx.interaction.created_at).total_seconds()
			timeout = max(0.0, min(self._PREPARATION_TIMEOUT, 2.0 - elapsed))
		try:
			async with asyncio.timeout(timeout):
				await self.db.prepare_command_context(ctx.author, ctx.guild, ctx.channel)
		except commands.CommandError:
			raise
		except Exception as error:
			raise _CommandPreparationError(error) from error

	async def setup_hook(self) -> None:
		await self.load_extension("core.events")
		await self.load_extension("extensions")

	async def on_ready(self) -> None:
		user = self.user
		if user is None:
			logger.error("Ready event received without a bot user.")
			return
		servers = len(self.guilds)
		activity_name = f"{core.config.BOT_PREFIX}help | {servers} servers"
		activity = discord.Activity(type=discord.ActivityType.listening, name=activity_name)
		await self.change_presence(activity=activity)
		colored_name = f"\x1b[96m{user}\x1b[0m"
		logger.info(f"Logged on as {colored_name} (ID: {user.id})")

	async def on_command_completion(self, ctx: commands.Context) -> None:
		command = ctx.command
		if command is None:
			logger.warning("Command completion received without a command.")
			return
		command_name = command.qualified_name
		parameters = ctx.kwargs.values() if ctx.kwargs else ctx.args[2:]
		parameters_string = ", ".join([str(parameter) if parameter is not None else "" for parameter in parameters])
		if parameters_string == "":
			parameters_string = None

		if parameters_string is None:
			logger.info(f"[{command_name}] executed for -> [{ctx.author}]")
		else:
			log_parameters = parameters_string[:60] + "…" if len(parameters_string) > 60 else parameters_string
			logger.info(f"[{command_name}] executed for -> [{ctx.author}] with params: {log_parameters}")

		server_id = ctx.guild.id if ctx.guild else None
		query = """INSERT INTO command_history
                   (command_name, parameters, discord_user_id, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES ($1, $2, $3, $4, $5, $6)"""
		try:
			await self.db.pool.execute(
				query,
				command_name,
				parameters_string,
				ctx.author.id,
				server_id,
				ctx.channel.id,
				ctx.message.id,
			)
		except Exception:
			logger.exception(f"Failed to persist command history for {command_name}")
		try:
			await ctx.message.add_reaction("✅")
		except discord.errors.NotFound:
			pass
		except discord.errors.Forbidden:
			pass

	async def on_command_error(
		self,
		ctx: commands.Context,
		error,
		*,
		service_errors: tuple[type[Exception], ...] = (),
		error_title: str = "Command failed",
		service_message: str = "A service is temporarily unavailable. Please try again shortly.",
	) -> None:
		"""Report failures centrally, with optional backend types and messages supplied by a cog."""
		if getattr(error, "is_handled", False):
			return
		if isinstance(error, (commands.CommandNotFound, slash_errors.CommandNotFound)):
			logger.warning(f"Command not found: [{ctx.author}] -> {ctx.message.content}")
			return
		if not ctx.command:
			logger.warning(f"Error without command occurred: [{ctx.author}] -> {error}")
			return
		if isinstance(error, (commands.CommandOnCooldown, slash_errors.CommandOnCooldown)):
			await ctx.message.add_reaction("⏳")
			embed = discord.Embed(
				title="Slow it down!",
				description=f"Try again in {error.retry_after:.2f}s.",
				color=discord.Color.orange(),
			)
			await ctx.reply(embed=embed)
			return
		original = error
		preparation_failed = False
		while isinstance(
			original, (commands.CommandInvokeError, commands.HybridCommandError, slash_errors.CommandInvokeError)
		):
			preparation_failed |= isinstance(original, _CommandPreparationError)
			original = original.original
		service_errors = (*service_errors, aiohttp.ClientConnectionError, *_TRANSIENT_DATABASE_ERRORS)
		reported_error = original
		service_failure = False
		cause = original
		while cause is not None:
			if isinstance(cause, service_errors):
				reported_error = cause
				service_failure = True
			cause = cause.__cause__
		if service_failure:
			logger.warning(
				"[%s] failed for [%s]: %s: %s",
				ctx.command.qualified_name,
				ctx.author,
				type(reported_error).__name__,
				str(reported_error).partition("\n")[0],
			)
		else:
			logger.error("[%s] failed for [%s]", ctx.command.qualified_name, ctx.author, exc_info=original)
		if isinstance(error, commands.CheckFailure):
			embed = discord.Embed(
				title="Insufficient permissions",
				description="You do not have permission to use this command.",
				color=discord.Color.red(),
			)
			await ctx.reply(embed=embed)
			await self._save_command_error(ctx, original)
			return
		if isinstance(error, commands.MissingRequiredArgument):
			param_obj = getattr(error, "param", None)
			param_name = getattr(param_obj, "displayed_name", None) or getattr(param_obj, "name", None)
			description = f"`{param_name}` is a required argument." if param_name else str(error)
			embed = discord.Embed(
				title="Missing required argument",
				description=description,
				color=discord.Color.red(),
			)
			help_hint = f"Use '{core.config.BOT_PREFIX}help {ctx.command.qualified_name}' to learn more."
			embed.set_footer(text=help_hint)
			await ctx.reply(embed=embed)
			await self._save_command_error(ctx, original)
			return
		if isinstance(error, (commands.BadArgument, commands.UserInputError)):
			embed = discord.Embed(title="Invalid input", description=f"{error}", color=discord.Color.red())
			help_hint = f"Use '{core.config.BOT_PREFIX}help {ctx.command.qualified_name}' to learn more."
			embed.set_footer(text=help_hint)
			await ctx.reply(embed=embed)
			await self._save_command_error(ctx, original)
			return

		if service_failure:
			description = service_message
		else:
			description = "I couldn't complete that command. Please try again later."
		try:
			await ctx.send(
				embed=discord.Embed(
					title=error_title,
					description=description,
					color=discord.Color.red(),
				)
			)
		except (discord.HTTPException, aiohttp.ClientConnectionError, TimeoutError) as send_error:
			logger.warning("Could not send command failure response: %s: %s", type(send_error).__name__, send_error)

		# Retrying preparation solely to log its known outage only delays reporting.
		# Unexpected SQL/schema failures still retain their normal incident trail.
		if not (preparation_failed and service_failure):
			await self._save_command_error(ctx, reported_error)

		try:
			await ctx.message.add_reaction("❌")
		except discord.HTTPException, aiohttp.ClientConnectionError, TimeoutError:
			pass

		if core.config.ERRORS_CHANNEL_ID is None:
			return
		if ctx.guild:
			error_msg = f"Error in {ctx.guild.name} ({ctx.guild.id}) by {ctx.author} -> {ctx.command.qualified_name}"
		else:
			error_msg = f"Error in DMs by {ctx.author} -> {ctx.command.qualified_name}"
		detail = f"{type(reported_error).__name__}: {reported_error}"
		embed = discord.Embed(title=error_msg[:256], description=detail[:4000], color=discord.Color.red())
		channel = self.get_channel(core.config.ERRORS_CHANNEL_ID)
		if isinstance(channel, discord.abc.Messageable):
			try:
				await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
			except (discord.HTTPException, aiohttp.ClientConnectionError, TimeoutError) as send_error:
				logger.warning("Could not send command error report: %s: %s", type(send_error).__name__, send_error)

	async def _save_command_error(self, ctx: commands.Context, error: Exception) -> None:
		command = ctx.command
		if command is None:
			logger.error("Cannot persist a command error without a command.")
			return
		command_name = command.qualified_name
		try:
			await self.db.prepare_command_context(ctx.author, ctx.guild, ctx.channel)
			await self.db.pool.execute(
				"""INSERT INTO command_error
				   (command_name, error_type, error_message, raw_message, discord_user_id,
				    discord_server_id, discord_channel_id, discord_message_id, is_dm)
				   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
				command_name,
				type(error).__name__,
				str(error),
				ctx.message.content,
				ctx.author.id,
				ctx.guild.id if ctx.guild else None,
				ctx.channel.id,
				ctx.message.id,
				ctx.guild is None,
			)
		except Exception:
			logger.exception("Failed to persist command error for %s", command_name)
