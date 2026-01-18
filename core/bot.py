import datetime
import logging

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
		intents = discord.Intents().all()
		super().__init__(
			command_prefix=commands.when_mentioned_or(core.config.BOT_PREFIX),
			intents=intents,
			owner_id=276462585690193921,
			max_messages=3000,
		)

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

	async def on_ready(self: commands.Bot) -> None:
		servers = len(self.guilds)
		activity_name = f"{core.config.BOT_PREFIX}help | {servers} servers"
		activity = discord.Activity(type=discord.ActivityType.listening, name=activity_name)
		await self.change_presence(activity=activity)
		colored_name = f"\x1b[96m{self.user}\x1b[0m"
		logger.info(f"Logged on as {colored_name} (ID: {self.user.id})")

	async def on_command_completion(self, ctx: commands.Context) -> None:
		logger.info(f"[{ctx.command.qualified_name}] executed for -> [{ctx.author}]")

		parameters = ctx.kwargs.values() if ctx.kwargs else ctx.args[2:]
		parameters_string = ", ".join([str(parameter) if parameter is not None else "" for parameter in parameters])
		if parameters_string == "":
			parameters_string = None

		server_id = ctx.guild.id if ctx.guild else None
		query = """INSERT INTO command_history
                   (command_name, parameters, discord_user_id, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES ($1, $2, $3, $4, $5, $6)"""
		await self.db.pool.execute(
			query,
			ctx.command.qualified_name,
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
		if hasattr(error, "is_handled"):
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
		logger.error(f"[{ctx.command.qualified_name}] failed for [{ctx.author}] <-> [{error}]")
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

		try:
			await ctx.message.add_reaction("❌")
		except discord.errors.NotFound:
			pass
		except discord.errors.Forbidden:
			pass

		ERRORS_CHANNEL_ID = 1219407043186659479
		if ctx.guild:
			error_msg = f"Error in {ctx.guild.name} ({ctx.guild.id}) by {ctx.author} -> {ctx.command.qualified_name}"
		else:
			error_msg = f"Error in DMs by {ctx.author} -> {ctx.command.qualified_name}"
		embed = discord.Embed(title=error_msg, description=f"```{error}```", color=discord.Color.red())
		channel = self.get_channel(ERRORS_CHANNEL_ID)
		if channel is not None:
			try:
				await channel.send(embed=embed)
			except discord.Forbidden:
				pass
			except discord.HTTPException:
				pass

	async def close(self) -> None:
		await self.db.pool.close()
		await super().close()
