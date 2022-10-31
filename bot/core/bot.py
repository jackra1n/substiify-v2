import contextlib
import logging
import platform

import discord
from core import config
from core.version import Version
from discord.ext import commands
from utils import db
from utils.colors import colors, get_colored

logger = logging.getLogger('discord')


initial_extensions = (
    'free_games',
    'fun',
    'help',
    'karma',
    'music',
    'owner',
    'util'
)


def get_system_description():
    system_bits = (platform.machine(), platform.system(), platform.release())
    filtered_system_bits = (s.strip() for s in system_bits if s.strip())
    return " ".join(filtered_system_bits)


class Substiify(commands.Bot):
    def __init__(self):
        intents = discord.Intents().all()
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.PREFIX),
            intents=intents,
            owner_id=276462585690193921
        )
        self.version = Version()

    async def setup_hook(self):
        cogs_folder = 'cogs'
        for extension in initial_extensions:
            try:
                await self.load_extension(f"{cogs_folder}.{extension}")
            except Exception as e:
                exc = f'{type(e).__name__}: {e}'
                logger.warning(f'Failed to load extension {extension}\n{exc}')

    async def on_ready(self):
        servers = len(self.guilds)
        activityName = f"{config.PREFIX}help | {servers} servers"
        activity = discord.Activity(type=discord.ActivityType.listening, name=activityName)
        await self.change_presence(activity=activity)

        system_description = get_colored("Running on:", colors.green).ljust(30)
        python_version = get_colored("Python:", colors.blue).ljust(30)
        discord_version = get_colored("discord.py:", colors.yellow).ljust(30)

        print(f'{system_description} {get_system_description()}')
        print(f'{python_version} {platform.python_version()}')
        print(f'{discord_version} {discord.__version__}')

        logger.info(f'Logged on as {self.user} (ID: {self.user.id})')

    async def on_command_completion(self, ctx):
        logger.info(f'[{ctx.command.qualified_name}] executed for -> [{ctx.author}]')
        db.session.add(db.command_history(ctx))
        db.session.commit()

    async def on_command_error(self, ctx, error):
        if not ctx.command:
            logger.warning(f'Error without command occurred: [{ctx.author}] -> {error}')
        logger.error(f'[{ctx.command.qualified_name}] failed for [{ctx.author}] <-> [{error}]')
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send('You do not have permission to use this command.')
        with contextlib.suppress(Exception):
            await ctx.message.add_reaction('🆘')
