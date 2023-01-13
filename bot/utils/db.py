import logging
from pathlib import Path

import asyncpg
import discord

logger = logging.getLogger(__name__)

class Database:

    def __init__(self, bot: discord.Client, pool: asyncpg.Pool) -> None:
        self.bot = bot
        self.pool = pool
        self.create_database()

    async def get_all_giveaways(self) -> list:
        async with self.pool.acquire() as con:
            await con.fetch("SELECT * FROM giveaway")

    def get_discord_server(self, server: discord.Guild):
        db_server = session.query(discord_server).filter_by(discord_server_id=server.id).first()
        if db_server is None:
            db_server = discord_server(server)
            session.add(db_server)
            session.commit()
        return db_server

    def get_discord_channel(self, channel: discord.TextChannel):
        db_channel = session.query(discord_channel).filter_by(discord_channel_id=channel.id).first()
        if db_channel is None:
            db_channel = discord_channel(channel)
            session.add(db_channel)
            session.commit()
        return db_channel

    # Creates database tables if the don't exist
    async def create_database(self):
        db_script = Path("./bot/db/CreateDatabase.sql").read_text('utf-8')
        await self.pool.execute(db_script)
