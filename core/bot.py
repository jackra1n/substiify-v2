import datetime
import logging

import aiohttp
import discord
import wavelink
from discord.app_commands import errors as slash_errors
from discord.ext import commands

import core
from database import Database

logger = logging.getLogger(__name__)


class Substiify(commands.Bot):
	def __init__(self, *, database: Database) -> None:
		self.db = database
		self.version = core.__version__
		self.start_time = datetime.datetime.now(datetime.timezone.utc)
		prefix = core.config.BOT_PREFIX
		if not prefix:
			raise RuntimeError("BOT_PREFIX must be configured before creating the bot")
		intents = discord.Intents().all()
		super().__init__(
			command_prefix=commands.when_mentioned_or(prefix),
			intents=intents,
			owner_id=276462585690193921,
			max_messages=3000,
		)
		self.before_invoke(self._prepare_command_context)

	async def _prepare_command_context(self, ctx: commands.Context) -> None:
		await self.db.prepare_command_context(ctx.author, ctx.guild, ctx.channel)

	async def setup_hook(self) -> None:
		await self.load_extension("core.events")
		await self.load_extension("extensions")

		url = core.config.LAVALINK_NODE_URL
		password = core.config.LAVALINK_PASSWORD

		if url and url.strip() and password and password.strip():
			node: wavelink.Node = wavelink.Node(uri=url, password=password)
			await wavelink.Pool.connect(client=self, nodes=[node])
		else:
			logger.warning("Lavalink is not configured. Skipping connection.")

	async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
		logging.info(f"Wavelink Node connected: {payload.node!r} | Resumed: {payload.resumed}")

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
		await self.db.pool.execute(
			query,
			command_name,
			parameters_string,
			ctx.author.id,
			server_id,
			ctx.channel.id,
			ctx.message.id,
		)
		try:
			await ctx.message.add_reaction("✅")
		except discord.errors.NotFound:
			pass
		except discord.errors.Forbidden:
			pass

	async def on_command_error(self, ctx: commands.Context, error) -> None:
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
		while isinstance(
			original, (commands.CommandInvokeError, commands.HybridCommandError, slash_errors.CommandInvokeError)
		):
			original = original.original
		service_error = original
		while service_error.__cause__ is not None:
			service_error = service_error.__cause__
		service_failure = isinstance(
			original, (wavelink.WavelinkException, aiohttp.ClientConnectionError, TimeoutError)
		) or isinstance(service_error, (wavelink.WavelinkException, aiohttp.ClientConnectionError, TimeoutError))
		reported_error = service_error if service_failure else original
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
		if isinstance(error, (commands.CheckFailure, commands.UserInputError)):
			await self._save_command_error(ctx, original)
		if isinstance(error, commands.CheckFailure):
			embed = discord.Embed(
				title="Insufficient permissions",
				description="You do not have permission to use this command.",
				color=discord.Color.red(),
			)
			await ctx.reply(embed=embed)
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
			return
		if isinstance(error, (commands.BadArgument, commands.UserInputError)):
			embed = discord.Embed(title="Invalid input", description=f"{error}", color=discord.Color.red())
			help_hint = f"Use '{core.config.BOT_PREFIX}help {ctx.command.qualified_name}' to learn more."
			embed.set_footer(text=help_hint)
			await ctx.reply(embed=embed)
			return

		is_music = ctx.command.cog is not None and ctx.command.cog.qualified_name == "Music"
		if service_failure and is_music:
			description = (
				"The music service couldn't complete that request. Please try again shortly or try another track."
			)
		elif service_failure:
			description = "A service is temporarily unavailable. Please try again shortly."
		else:
			description = "I couldn't complete that command. Please try again later."
		try:
			await ctx.send(
				embed=discord.Embed(
					title="Music Error" if is_music else "Command failed",
					description=description,
					color=discord.Color.red(),
				)
			)
		except (discord.HTTPException, aiohttp.ClientConnectionError, TimeoutError) as send_error:
			logger.warning("Could not send command failure response: %s: %s", type(send_error).__name__, send_error)

		await self._save_command_error(ctx, reported_error)

		try:
			await ctx.message.add_reaction("❌")
		except discord.HTTPException, aiohttp.ClientConnectionError, TimeoutError:
			pass

		ERRORS_CHANNEL_ID = 1219407043186659479
		if ctx.guild:
			error_msg = f"Error in {ctx.guild.name} ({ctx.guild.id}) by {ctx.author} -> {ctx.command.qualified_name}"
		else:
			error_msg = f"Error in DMs by {ctx.author} -> {ctx.command.qualified_name}"
		detail = f"{type(reported_error).__name__}: {reported_error}"
		embed = discord.Embed(title=error_msg[:256], description=detail[:4000], color=discord.Color.red())
		channel = self.get_channel(ERRORS_CHANNEL_ID)
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
