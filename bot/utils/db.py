import logging
from pathlib import Path
from datetime import datetime

import asyncpg
import discord
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

    async def _populate(self, ctx: Context) -> None:
        servers = [(server.id, server.name) for server in self.bot.guilds]
        async with self.pool.acquire() as con:
            query = 'INSERT INTO discord_server(discord_server_id, server_name) VALUES ($1, $2)'
            await con.executemany(query, servers)
        await ctx.send("Database populated", delete_after=30)

    async def insert_foundation(self, ctx):
        user = ctx.author
        server = ctx.guild
        channel = ctx.channel
        async with self.pool.acquire() as con:
            await con.execute(USER_INSERT_QUERY, user.id, user.name, user.discriminator, user.avatar.url)
            await con.execute(SERVER_INSERT_QUERY, server.id, server.name)
            if pchannel := channel.parent if hasattr(channel, 'parent') else None:
                await con.execute(CHANNEL_INSERT_QUERY, pchannel.id, pchannel.name, pchannel.guild.id, None)
            p_chan_id = pchannel.id if pchannel else None
            await con.execute(CHANNEL_INSERT_QUERY, channel.id, channel.name, channel.guild.id, p_chan_id)

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
        async with self.pool.acquire() as con:
            await con.execute(USER_INSERT_QUERY, user.id, user.name, user.discriminator, user.avatar.url)

    async def insert_discord_server(self, server: discord.Guild):
        async with self.pool.acquire() as con:
            await con.execute(SERVER_INSERT_QUERY, server.id, server.name)

    async def insert_discord_channel(self, channel: discord.abc.Messageable):
        parent_channel = channel.parent.id if hasattr(channel, 'parent') else None
        async with self.pool.acquire() as con:
            await con.execute(CHANNEL_INSERT_QUERY, channel.id, channel.name, channel.guild.id, parent_channel)

    async def insert_kasino(self, question, option_1, option_2, kasino_msg):
        query = """INSERT INTO kasino
                   (question, option1, option2, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES ($1, $2, $3, $4, $5, $6)
                """
        async with self.pool.acquire() as con:
            await con.execute(query, question, option_1, option_2, kasino_msg.id)

    async def insert_giveaway(self, creator: discord.Member, end_date: datetime, prize: str, msg: discord.Message):
        query = """INSERT INTO giveaway
            (discord_user_id, end_date, prize, discord_server_id, discord_channel_id, discord_message_id)
            VALUES ($1, $2, $3, $4, $5, $6)
        """
        async with self.pool.acquire() as con:
            await con.execute(query, creator.id, end_date, prize, msg.guild.id, msg.channel.id, msg.id)

    async def update_server_music_cleanup(self, ctx: Context, do_cleanup: bool):
        await self.insert_foundation(ctx)
        query = 'UPDATE discord_server SET music_cleanup = $1 WHERE discord_server_id = $2'
        async with self.pool.acquire() as con:
            await con.execute(query, do_cleanup, ctx.guild.id)

    async def delete_giveaway(self, primary_key: int):
        async with self.pool.acquire() as con:
            await con.execute(f'DELETE FROM giveaway WHERE id = {primary_key}')

    async def delete_giveaway_by_msg_id(self, msg_id: int):
        async with self.pool.acquire() as con:
            return await con.execute(f'DELETE FROM giveaway WHERE discord_message_id = {msg_id}')

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

    async def get_all_posts(self) -> list:
        async with self.pool.acquire() as con:
            return await con.fetch('SELECT * FROM post')

    async def get_command_usage_all(self, ctx: Context) -> list:
        await self.insert_foundation(ctx)
        query = """SELECT command_name, COUNT(*) FROM command_history
                 GROUP BY command_name
                 ORDER BY COUNT(*) DESC LIMIT 10
                """
        async with self.pool.acquire() as con:
            return await con.fetch(query)

    async def get_last_command_usage(self, ctx: Context, amount: int) -> list:
        await self.insert_foundation(ctx)
        query = """SELECT * FROM command_history JOIN discord_user
                   ON command_history.discord_user_id = discord_user.discord_user_id
                   WHERE command_history.discord_server_id = $1
                   ORDER BY command_history.date DESC LIMIT $2
                """
        async with self.pool.acquire() as con:
            return await con.fetch(query, ctx.guild.id, amount)

    async def get_command_usage_by_command(self, ctx: Context) -> list:
        await self.insert_foundation(ctx)
        query = """SELECT command_name, COUNT(*) FROM command_history
                   WHERE discord_server_id = $1
                   GROUP BY command_name
                   ORDER BY COUNT(*) DESC LIMIT 10
                """
        async with self.pool.acquire() as con:
            return await con.fetch(query, ctx.guild.id)

    async def get_command_usage_by_server(self, ctx: Context) -> list:
        await self.insert_foundation(ctx)
        query = """SELECT server_name, COUNT(*) FROM command_history JOIN discord_server
                   ON command_history.discord_server_id = discord_server.discord_server_id
                   GROUP BY server_name
                   ORDER BY COUNT(*) DESC LIMIT 10
                """
        async with self.pool.acquire() as con:
            return await con.fetch(query)

    # Creates database tables if the don't exist
    async def create_database(self):
        db_script = Path("./bot/db/CreateDatabase.sql").read_text('utf-8')
        await self.pool.execute(db_script)
