import logging
from pathlib import Path

import asyncpg
import discord

logger = logging.getLogger(__name__)

class Database:

    def __init__(self, bot: discord.Client, pool: asyncpg.Pool) -> None:
        self.bot = bot
        self.pool = pool

    async def insert_to_cmd_history(self, ctx: discord.ext.commands.Context) -> None:
        cmd_name = ctx.command.root_parent.qualified_name if ctx.command.root_parent else ctx.command.qualified_name
        server_id = ctx.guild.id if ctx.guild else None
        async with self.pool.acquire() as con:
            await con.execute(f'INSERT INTO command_history VALUES (\'{cmd_name}\', {ctx.author.id}, {ctx.message.id}, {server_id}, {ctx.channel.id})')

    async def get_all_giveaways(self) -> list:
        async with self.pool.acquire() as con:
            return await con.fetch('SELECT * FROM giveaway')

    async def get_discord_server(self, server: discord.Guild):
        async with self.pool.acquire() as con:
            stmt = f'SELECT * FROM giveaway WHERE discord_server_id = {server.id}'
            if server := await con.fetchrow(stmt):
                return server
            await con.execute(f'INSERT INTO discord_server VALUES ({server.id}, {server.name})')
        return server

    # Creates database tables if the don't exist
    async def create_database(self):
        db_script = Path("./bot/db/CreateDatabase.sql").read_text('utf-8')
        await self.pool.execute(db_script)
