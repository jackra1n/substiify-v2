import logging
from pathlib import Path

import asyncpg
import discord
from asyncpg import Record
from discord.ext.commands import Context

logger = logging.getLogger(__name__)

USER_INSERT_QUERY = """INSERT INTO discord_user
                       (discord_user_id, username, avatar)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (discord_user_id) DO UPDATE
                       SET
                       username = EXCLUDED.username,
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

    def _transaction(call):
        def decorator(func):
            async def wrapper(self, query, *args, **kwargs):
                async with self.pool.acquire() as connection:
                    async with connection.transaction():
                        return await getattr(connection, call)(query, *args, **kwargs)
            return wrapper
        return decorator

    @_transaction("execute")
    async def execute(self, query, *args, **kwargs) -> str:
        pass

    @_transaction("executemany")
    async def executemany(self, query, *args, **kwargs) -> None:
        pass

    @_transaction("fetch")
    async def fetch(self, query, *args, **kwargs) -> list:
        pass

    @_transaction("fetchrow")
    async def fetchrow(self, query, *args, **kwargs) -> Record | None:
        pass

    @_transaction("fetchval")
    async def fetchval(self, query, *args, **kwargs):
        pass

    async def _insert_foundation(self, user: discord.Member, server: discord.Guild, channel: discord.abc.Messageable):
        avatar_url = user.display_avatar.url if user.display_avatar else None
        await self.execute(USER_INSERT_QUERY, user.id, user.name, avatar_url)
        await self.execute(SERVER_INSERT_QUERY, server.id, server.name)
        if pchannel := channel.parent if hasattr(channel, 'parent') else None:
            await self.execute(CHANNEL_INSERT_QUERY, pchannel.id, pchannel.name, pchannel.guild.id, None)
        p_chan_id = pchannel.id if pchannel else None
        await self.execute(CHANNEL_INSERT_QUERY, channel.id, channel.name, channel.guild.id, p_chan_id)

    # Creates database tables if they don't exist
    async def create_database(self):
        db_script = Path("./bot/db/CreateDatabase.sql").read_text('utf-8')
        await self.execute(db_script)