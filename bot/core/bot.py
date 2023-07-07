import logging

import discord
import wavelink
from core import config
from core.version import Version
from discord.ext import commands
from utils import db
from wavelink.ext import spotify

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
    def __init__(self):
        intents = discord.Intents().all()
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.PREFIX),
            intents=intents,
            owner_id=276462585690193921
        )
        self.version = Version()

    async def setup_hook(self):
        if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
            logger.warning("Spotify client id or secret not found. Spotify support disabled.")
            spotify_client = None
        else:
            spotify_client = spotify.SpotifyClient(client_id=config.SPOTIFY_CLIENT_ID, client_secret=config.SPOTIFY_CLIENT_SECRET)
        node: wavelink.Node = wavelink.Node(uri=config.LAVALINK_NODE_URL, password=config.LAVALINK_PASSWORD)
        connected_nodes = await wavelink.NodePool.connect(client=self, nodes=[node], spotify=spotify_client)
        logger.info(f"Connected to {connected_nodes} nodes.")

        for extension in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(extension)
            except Exception as e:
                exc = f'{type(e).__name__}: {e}'
                logger.warning(f'Failed to load extension {extension}\n{exc}')

    async def on_ready(self):
        servers = len(self.guilds)
        activityName = f"{config.PREFIX}help | {servers} servers"
        activity = discord.Activity(type=discord.ActivityType.listening, name=activityName)
        await self.change_presence(activity=activity)

        logger.info(f'Logged on as {self.user} (ID: {self.user.id})')

    async def on_command_completion(self, ctx):
        logger.info(f'[{ctx.command.qualified_name}] executed for -> [{ctx.author}]')
        db.session.add(db.command_history(ctx))
        db.session.commit()

    async def on_command_error(self, ctx, error):
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
        try:
            await ctx.message.add_reaction('🆘')
        except discord.HTTPException:
            pass
