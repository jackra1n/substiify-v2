import datetime
import logging

import discord
import time
from core import config
from core.version import Version
from discord.ext import commands
from utils.db import Database

logger = logging.getLogger(__name__)

INITIAL_EXTENSIONS = [
    'cogs.free_games',
    'cogs.fun',
    'cogs.help',
    'cogs.karma',
    'cogs.music',
    'cogs.owner',
    'cogs.util',
    'jishaku'
]

try:
    import jishaku
except ModuleNotFoundError:
    INITIAL_EXTENSIONS.remove('jishaku')
else:
    del jishaku


class Substiify(commands.Bot):

    db: Database
    start_time: datetime.datetime

    def __init__(self) -> None:
        intents = discord.Intents().all()
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.PREFIX),
            intents=intents,
            owner_id=276462585690193921
        )
        self.version = Version()

    async def setup_hook(self) -> None:
        self.start_time: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)
        for extension in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(extension)
            except Exception as e:
                exc = f'{type(e).__name__}: {e}'
                logger.warning(f'Failed to load extension {extension}\n{exc}')

    async def on_ready(self) -> None:
        servers = len(self.guilds)
        activityName = f"{config.PREFIX}help | {servers} servers"
        activity = discord.Activity(type=discord.ActivityType.listening, name=activityName)
        await self.change_presence(activity=activity)

        logger.info(f'Logged on as {self.user} (ID: {self.user.id})')

    async def on_command_completion(self, ctx) -> None:
        logger.info(f'[{ctx.command.qualified_name}] executed for -> [{ctx.author}]')
        await self.db.insert_to_cmd_history(ctx)

    async def on_command_error(self, ctx, error) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if not ctx.command:
            logger.warning(f'Error without command occurred: [{ctx.author}] -> {error}')
            return
        logger.error(f'[{ctx.command.qualified_name}] failed for [{ctx.author}] <-> [{error}]')
        if isinstance(error, commands.CheckFailure):
            await ctx.send('You do not have permission to use this command.')
        if hasattr(error, 'is_handled') and error.is_handled:
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send('A required argument is missing.')
        try:
            await ctx.message.add_reaction('🆘')
        except discord.HTTPException:
            pass

    async def close(self) -> None:
        await self.pool.close()
        await super().close()
