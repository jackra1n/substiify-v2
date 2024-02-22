import logging

import discord
from core.bot import Substiify
from discord.ext import commands

EVENTS_CHANNEL_ID = 1131685580300877916

logger = logging.getLogger(__name__)


class Events(commands.Cog):

    def __init__(self, bot: Substiify):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        await self.bot.db._insert_foundation(ctx.author, ctx.guild, ctx.channel)

    #
    # GUILD EVENTS
    #

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self.bot.get_channel(EVENTS_CHANNEL_ID).send(f'Joined guild `{guild.name}` ({guild.id})')
        await self._insert_server(guild)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        stmt = 'UPDATE discord_server SET server_name = $1 WHERE discord_server_id = $2'
        await self.bot.db.execute(stmt, after.name, after.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        await self.bot.get_channel(EVENTS_CHANNEL_ID).send(f'Left guild `{guild.name}` ({guild.id})')

    #
    # CHANNEL EVENTS
    #

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        await self._insert_server(channel.guild)
        await self._insert_channel(channel)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        stmt = 'UPDATE discord_channel SET channel_name = $1 WHERE discord_channel_id = $2'
        await self.bot.db.execute(stmt, after.name, after.id)

    async def _insert_server(self, guild: discord.Guild):
        stmt = '''
            INSERT INTO discord_server (discord_server_id, server_name) VALUES ($1, $2)
            ON CONFLICT (discord_server_id) DO UPDATE SET server_name = $2
        '''
        await self.bot.db.execute(stmt, guild.id, guild.name)

    async def _insert_channel(self, channel: discord.abc.GuildChannel):
        stmt = '''
            INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id) VALUES ($1, $2, $3)
            ON CONFLICT (discord_channel_id) DO UPDATE SET channel_name = $2
        '''
        await self.bot.db.execute(stmt, channel.id, channel.name, channel.guild.id)


async def setup(bot: Substiify):
    await bot.add_cog(Events(bot))