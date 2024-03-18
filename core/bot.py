import datetime
import logging

import discord
import wavelink
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
            command_prefix=commands.when_mentioned_or(core.config.PREFIX),
            intents=intents,
            owner_id=276462585690193921,
            max_messages=3000
        )

    async def setup_hook(self) -> None:
        await self.load_extension('core.events')
        await self.load_extension('extensions')

        node: wavelink.Node = wavelink.Node(uri=core.config.LAVALINK_NODE_URL, password=core.config.LAVALINK_PASSWORD)
        await wavelink.Pool.connect(client=self, nodes=[node])


    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logging.info(f"Wavelink Node connected: {payload.node!r} | Resumed: {payload.resumed}")

    async def on_ready(self: commands.Bot) -> None:
        servers = len(self.guilds)
        activity_name = f"{core.config.PREFIX}help | {servers} servers"
        activity = discord.Activity(type=discord.ActivityType.listening, name=activity_name)
        await self.change_presence(activity=activity)
        logger.info(f'Logged on as {self.user} (ID: {self.user.id})')

    async def on_command_completion(self, ctx: commands.Context) -> None:
        logger.info(f'[{ctx.command.qualified_name}] executed for -> [{ctx.author}]')

        server_id = ctx.guild.id if ctx.guild else None
        parameters = ctx.kwargs.values() if ctx.kwargs else ctx.args[2:]
        parameters_string = ', '.join([str(parameter) for parameter in parameters])

        query = """INSERT INTO command_history
                   (command_name, parameters, discord_user_id, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES ($1, $2, $3, $4, $5, $6)"""
        await self.db.execute(query, ctx.command.qualified_name, parameters_string, ctx.author.id, server_id, ctx.channel.id, ctx.message.id)
        try:
            await ctx.message.add_reaction('✅')
        except discord.errors.NotFound:
            pass

    async def on_command_error(self, ctx: commands.Context, error) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if not ctx.command:
            logger.warning(f'Error without command occurred: [{ctx.author}] -> {error}')
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.message.add_reaction('⏳')
            await ctx.send(f'This command is on cooldown. Try again in {error.retry_after:.2f} seconds.', ephemeral=True)
            return
        logger.error(f'[{ctx.command.qualified_name}] failed for [{ctx.author}] <-> [{error}]')
        if isinstance(error, commands.CheckFailure):
            await ctx.send('You do not have permission to use this command.')
        if hasattr(error, 'is_handled'):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send('A required argument is missing.')

        ERRORS_CHANNEL_ID = 1219407043186659479
        embed = discord.Embed(title='Error', description=f'```{error}```', color=discord.Color.red())
        await self.bot.get_channel(ERRORS_CHANNEL_ID).send(embed=embed)

        try:
            await ctx.message.add_reaction('❌')
        except discord.errors.NotFound:
            pass

    async def close(self) -> None:
        await self.db.pool.close()
        await super().close()
