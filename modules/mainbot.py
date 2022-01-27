import json
import logging
import platform

import nextcord
from nextcord.ext import commands

from utils import db, store
from utils.colors import colors, get_colored

logger = logging.getLogger(__name__)
ignored_modules = ['music.py']

class MainBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        with open(store.SETTINGS_PATH, "r") as settings:
            self.settings = json.load(settings)
        self.prefix = self.settings["prefix"]
        self.startup_extensions = [
            "duel",
            "freeGames",
            "fun",
            "giveaway",
            "karma",
            "music",
            "owner",
            "submit",
            "util",
            "votes"
        ]

    @commands.Cog.listener()
    async def on_ready(self):
        await self.load_extensions()

        connected_as = get_colored("Connected as:", colors.cyan).ljust(30)
        python_version = get_colored("Python:", colors.blue).ljust(30)
        nextcord_version = get_colored("nextcord:", colors.yellow).ljust(30)
        system_description = get_colored("Running on:", colors.green).ljust(30)

        print('\n', '='*40, sep='')
        print(f'{connected_as} {self.bot.user}')
        print(f'{python_version} {platform.python_version()}')
        print(f'{nextcord_version} {nextcord.__version__}')
        print(f'{system_description} {self.get_system_description()}')
        print('='*40, '\n')

        logger.info(f'{self.bot.user} is ready!')

    def get_system_description(self):
        system_bits = (platform.machine(), platform.system(), platform.release())
        filtered_system_bits = (s.strip() for s in system_bits if s.strip())
        return " ".join(filtered_system_bits)

    async def load_extensions(self):
        for extension in self.startup_extensions:
            try:
                self.bot.load_extension('modules.'+extension)
            except Exception as e:
                exc = f'{type(e).__name__}: {e}'
                logger.warning(f'Failed to load extension {extension}\n{exc}')

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        logger.info(f'[{ctx.command.qualified_name}] executed for -> [{ctx.author}]')
        command = ctx.command.root_parent if ctx.command.root_parent else ctx.command
        db.session.add(db.command_history(command, ctx.message))
        db.session.commit()

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if 'is not found' in str(error):
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send(f'You do not have permission to use this command.')
        await ctx.message.add_reaction('🆘')
        logger.error(f'[{ctx.command.qualified_name}] failed for [{ctx.author}] <-> [{error}]')

def setup(bot):
    bot.add_cog(MainBot(bot))
