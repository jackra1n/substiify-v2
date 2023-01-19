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

    async def insert_foundation(self, ctx):
        await self.insert_discord_user(ctx.author)
        await self.insert_discord_server(ctx.guild)
        if hasattr(ctx.channel, 'parent'):
            await self.insert_discord_channel(ctx.channel.parent)
        await self.insert_discord_channel(ctx.channel)

    async def insert_to_cmd_history(self, ctx: Context) -> None:
        await self.insert_foundation(ctx)
        cmd_name = ctx.command.root_parent.qualified_name if ctx.command.root_parent else ctx.command.qualified_name
        server_id = ctx.guild.id if ctx.guild else None
        query = """INSERT INTO command_history
                   (command_name, discord_user_id, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES ($1, $2, $3, $4, $5)
                """
        async with self.pool.acquire() as con:
            await con.execute(query, cmd_name, ctx.author.id, server_id, ctx.channel.id, ctx.message.id)

    async def insert_discord_user(self, user: discord.Member):
        query = """INSERT INTO discord_user
                   (discord_user_id, username, discriminator, avatar)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (discord_user_id) DO UPDATE
                   SET
                   username = EXCLUDED.username,
                   discriminator = EXCLUDED.discriminator,
                   avatar = EXCLUDED.avatar
                """
        async with self.pool.acquire() as con:
            await con.execute(query, user.id, user.name, user.discriminator, user.avatar.url)

    async def insert_discord_server(self, server: discord.Guild):
        query = """INSERT INTO discord_server
                   (discord_server_id, server_name)
                   VALUES ($1, $2)
                   ON CONFLICT (discord_server_id) DO UPDATE
                   SET
                   server_name = EXCLUDED.server_name
                """
        async with self.pool.acquire() as con:
            await con.execute(query, server.id, server.name)

    async def insert_discord_channel(self, channel: discord.abc.Messageable):
        query = """INSERT INTO discord_channel
                   (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (discord_channel_id) DO UPDATE
                   SET
                   channel_name = EXCLUDED.channel_name,
                   parent_discord_channel_id = EXCLUDED.parent_discord_channel_id
                """
        parent_channel = channel.parent.id if hasattr(channel, 'parent') else None
        async with self.pool.acquire() as con:
            await con.execute(query, channel.id, channel.name, channel.guild.id, parent_channel)

    async def insert_kasino(self, question, option_1, option_2, kasino_msg):
        query = """INSERT INTO kasino
                   (question, option1, option2, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES ($1, $2, $3, $4, $5, $6)
                """
        async with self.pool.acquire() as con:
            await con.execute(query, question, option_1, option_2, kasino_msg.id)

    async def get_discord_user(self, user: discord.Member):
        async with self.pool.acquire() as con:
            user = await con.fetchrow("SELECT * FROM discord_user WHERE id = $1", user.id)

    async def get_discord_server(self, server: discord.Guild):
        query = 'SELECT * FROM discord_server WHERE discord_server_id = $1'
        async with self.pool.acquire() as con:
            return await con.fetchrow(query, server.id)

    async def get_all_giveaways(self) -> list:
        async with self.pool.acquire() as con:
            return await con.fetch('SELECT * FROM giveaway')

    async def get_kasino(self, kasino_id):
        query = "SELECT * FROM kasino WHERE kasino_id = $1"
        async with self.pool.acquire() as con:
            return await con.fetchrow(query, kasino_id)

    async def get_total_kasino_karma(self, kasino_id) -> int:
        query = "SELECT SUM(amount) FROM kasino_bet WHERE kasino_id = $1"
        async with self.pool.acquire() as con:
            return await con.fetchval(query, kasino_id)

    # Creates database tables if the don't exist
    async def create_database(self):
        db_script = Path("./bot/db/CreateDatabase.sql").read_text('utf-8')
        await self.pool.execute(db_script)
