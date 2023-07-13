import logging
from datetime import datetime
from pathlib import Path

import asyncpg
import discord
from asyncpg import Record
from asyncpg.connection import Connection
from discord.ext.commands import Context

logger = logging.getLogger(__name__)

USER_INSERT_QUERY = """INSERT INTO discord_user
                       (discord_user_id, username, discriminator, avatar)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (discord_user_id) DO UPDATE
                       SET
                       username = EXCLUDED.username,
                       discriminator = EXCLUDED.discriminator,
                       avatar = EXCLUDED.avatar
                    """

SERVER_INSERT_QUERY = """INSERT INTO discord_server
                         (discord_server_id, server_name)
                         VALUES ($1, $2)
                         ON CONFLICT (discord_server_id) DO UPDATE
                         SET
                         server_name = EXCLUDED.server_name
                      """

CHANNEL_INSERT_QUERY = """INSERT INTO discord_channel
                          (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id)
                          VALUES ($1, $2, $3, $4)
                          ON CONFLICT (discord_channel_id) DO UPDATE
                          SET
                          channel_name = EXCLUDED.channel_name,
                          parent_discord_channel_id = EXCLUDED.parent_discord_channel_id
                       """


class Database:
    def __init__(self, bot: discord.Client, pool: asyncpg.Pool) -> None:
        self.bot = bot
        self.pool = pool

    def _transaction(func):
        async def wrapper(self, *args, **kwargs):
            async with self.pool.acquire() as con:
                async with con.transaction():
                    return await func(self, con, *args, **kwargs)
        return wrapper

    @_transaction
    async def execute(self, con: Connection, query: str, *args, **kwargs) -> None:
        await con.execute(query, *args, **kwargs)

    @_transaction
    async def executemany(self, con: Connection, query: str, *args, **kwargs) -> None:
        await con.executemany(query, *args, **kwargs)

    @_transaction
    async def fetch(self, con: Connection, query: str, *args, **kwargs) -> Record:
        return await con.fetch(query, *args, **kwargs)
    
    @_transaction
    async def fetchrow(self, con: Connection, query: str, *args, **kwargs) -> Record:
        return await con.fetchrow(query, *args, **kwargs)
    
    @_transaction
    async def fetchval(self, con: Connection, query: str, *args, **kwargs) -> Record:
        return await con.fetchval(query, *args, **kwargs)
    
    @_transaction
    async def _populate(self, con: Connection, ctx: Context) -> None:
        servers = [(server.id, server.name) for server in self.bot.guilds]
        query = "INSERT INTO discord_server(discord_server_id, server_name) VALUES ($1, $2)"
        await con.executemany(query, servers)
        await ctx.send("Database populated", delete_after=30)

    @_transaction
    async def insert_foundation_from_ctx(self, con: Connection, ctx: Context):
        user = ctx.author
        server = ctx.guild
        channel = ctx.channel
        await self.insert_foundation(con, user, server, channel)

    @_transaction
    async def insert_foundation(self, con: Connection, user: discord.Member, server: discord.Guild, channel: discord.abc.Messageable):
        avatar_url = user.avatar.url if user.avatar else None
        await con.execute(USER_INSERT_QUERY, user.id, user.name, user.discriminator, avatar_url)
        await con.execute(SERVER_INSERT_QUERY, server.id, server.name)
        if pchannel := channel.parent if hasattr(channel, 'parent') else None:
            await con.execute(CHANNEL_INSERT_QUERY, pchannel.id, pchannel.name, pchannel.guild.id, None)
        p_chan_id = pchannel.id if pchannel else None
        await con.execute(CHANNEL_INSERT_QUERY, channel.id, channel.name, channel.guild.id, p_chan_id)

    @_transaction
    async def insert_to_cmd_history(self, con: Connection, ctx: Context) -> None:
        await self.insert_foundation_from_ctx(con, ctx)
        cmd_name = ctx.command.root_parent.qualified_name if ctx.command.root_parent else ctx.command.qualified_name
        server_id = ctx.guild.id if ctx.guild else None
        query = """INSERT INTO command_history
                   (command_name, discord_user_id, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES ($1, $2, $3, $4, $5)
                """
        await con.execute(query, cmd_name, ctx.author.id, server_id, ctx.channel.id, ctx.message.id)

    # Creates database tables if they don't exist
    async def create_database(self):
        db_script = Path("./bot/db/CreateDatabase.sql").read_text('utf-8')
        async with self.pool.acquire() as con:
            await con.execute(db_script)