import logging
from pathlib import Path

import asyncpg
import discord
from discord.ext.commands import Context

logger = logging.getLogger(__name__)

class Database:

    def __init__(self, bot: discord.Client, pool: asyncpg.Pool) -> None:
        self.bot = bot
        self.pool = pool

    async def _populate(self, ctx: Context) -> None:
        servers = self.bot.guilds
        async with self.pool.acquire() as con:
            query = 'INSERT INTO discord_server(discord_server_id, server_name) VALUES ($1, $2)'
            servers = [(server.id, server.name) for server in servers]
        await ctx.send("Database populated", delete_after=30)

    async def insert_to_cmd_history(self, ctx: Context) -> None:
        if await self.get_discord_user(ctx.author) is None:
            await self.insert_discord_user(ctx.author)
        cmd_name = ctx.command.root_parent.qualified_name if ctx.command.root_parent else ctx.command.qualified_name
        server_id = ctx.guild.id if ctx.guild else None
        query = """INSERT INTO command_history
                   (command_name, discord_user_id, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES
                   ($1, $2, $3, $4, $5)
                """
        async with self.pool.acquire() as con:
            await con.execute(query, cmd_name, ctx.author.id, server_id, ctx.channel.id, ctx.message.id)

    async def get_discord_user(self, user: discord.Member):
        async with self.pool.acquire() as con:
            user = await con.fetchrow("SELECT * FROM discord_user WHERE id = $1", user.id)

    async def insert_discord_user(self, user: discord.Member):
        query = "INSERT INTO discord_user (discord_user_id, username, discriminator, avatar) VALUES ($1, $2, $3, $4)"
        async with self.pool.acquire() as con:
            await con.execute(query, user.id, user.name, user.discriminator, user.avatar.url)

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
