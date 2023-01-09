import discord
import asyncpg
import logging

from pathlib import Path

logger = logging.getLogger(__name__)

async def get_user_karma(pool: asyncpg.Pool, user: discord.Member):
    async with pool.acquire() as con:
        await con.fetch("SELECT 1")

def get_discord_server(server: discord.Guild):
    db_server = session.query(discord_server).filter_by(discord_server_id=server.id).first()
    if db_server is None:
        db_server = discord_server(server)
        session.add(db_server)
        session.commit()
    return db_server


def get_discord_channel(channel: discord.TextChannel):
    db_channel = session.query(discord_channel).filter_by(discord_channel_id=channel.id).first()
    if db_channel is None:
        db_channel = discord_channel(channel)
        session.add(db_channel)
        session.commit()
    return db_channel


# Creates database tables if the don't exist
async def create_database(pool: asyncpg.Pool):
    db_script = Path("./bot/db/CreateDatabase.sql").read_text('utf-8')
    await pool.execute(db_script)
