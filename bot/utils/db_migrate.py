import sqlite3
import asyncpg
import asyncio

from asyncpg.connection import Connection
from pathlib import Path

from datetime import datetime


async def migrate_db() -> None:
    # get sqlite databse path
    db_path = input("Enter the path to the sqlite database: ")
    # connect to sqlite database
    try:
        sqlite_conn = sqlite3.connect(db_path)
        sqlite_cursor = sqlite_conn.cursor()
    except Exception as e:
        print(f"Error connecting to sqlite database: {e}")
        return
    # connect to postgresql database
    postgres_dsn = input("Enter the postgresql dsn (leave blank for default): ")
    if postgres_dsn == "":
        postgres_dsn = "postgresql://postgres:example@localhost:5432/substiify"
    try:
        postgres_conn: Connection = await asyncpg.connect(postgres_dsn)
    except Exception as e:
        print(f"Error connecting to postgresql: {e}")
        return
    
    # create tables in postgresql using the sql script
    print("Creating tables in postgresql...")
    db_script = Path("../db/CreateDatabase.sql").read_text('utf-8')
    await postgres_conn.execute(db_script)              

    # migrate discord_server
    print("Migrating discord_server...")
    sqlite_cursor.execute("SELECT * FROM discord_server")
    server_rows = sqlite_cursor.fetchall()
    for row in server_rows:
        await postgres_conn.execute(
            "INSERT INTO discord_server (discord_server_id, server_name, music_cleanup) VALUES ($1, $2, $3) ON CONFLICT (discord_server_id) DO NOTHING",
            int(row[0]), str(row[1]), bool(row[2])
        )
    # migrate discord_channel
    print("Migrating discord_channel...")
    sqlite_cursor.execute("SELECT * FROM discord_channel")
    channel_rows = sqlite_cursor.fetchall()
    for row in channel_rows:
        # if parent_discord_channel_id exists, find it in the db and insert it first
        if row[3] is not None:
            sqlite_cursor.execute("SELECT * FROM discord_channel WHERE discord_channel_id = ?", (row[3],))
            parent_row = sqlite_cursor.fetchone()
            await postgres_conn.execute(
                "INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id, upvote) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_channel_id) DO NOTHING",
                int(parent_row[0]), str(parent_row[1]), int(parent_row[2]), int(parent_row[3]), int(parent_row[4])
            )
        await postgres_conn.execute(
            "INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id, upvote) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_channel_id) DO NOTHING",
            int(row[0]), str(row[1]), int(row[2]), int(row[3]), int(row[4])
        )

    # migrate discord_user
    print("Migrating discord_user...")
    sqlite_cursor.execute("SELECT * FROM discord_user")
    user_rows = sqlite_cursor.fetchall()
    for row in user_rows:
        await postgres_conn.execute(
            "INSERT INTO discord_user (discord_user_id, username, avatar, is_bot) VALUES ($1, $2, $3, $4) ON CONFLICT (discord_user_id) DO NOTHING",
            int(row[0]), str(row[1]), str(row[2]), bool(row[3])
        )

    # migrate command_history
    print("Migrating command_history...")
    sqlite_cursor.execute("SELECT * FROM command_history")
    command_rows = sqlite_cursor.fetchall()
    for row in command_rows:
        await postgres_conn.execute(
            "INSERT INTO command_history (id, command_name, parameters, discord_user_id, discord_server_id, discord_channel_id, discord_message_id, timestamp) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT (id) DO NOTHING",
            int(row[0]), str(row[1]), str(row[2]), int(row[3]), int(row[4]), int(row[5]), int(row[6]), int(row[7])
        )

    # migrate karma
    print("Migrating karma...")
    sqlite_cursor.execute("SELECT * FROM karma")
    karma_rows = sqlite_cursor.fetchall()
    for row in karma_rows:
        await postgres_conn.execute(
            "INSERT INTO karma (id, discord_user_id, discord_server_id, amount) VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING",
            int(row[0]), int(row[1]), int(row[2]), int(row[3])
        )

    # migrate post
    print("Migrating post...")
    sqlite_cursor.execute("SELECT * FROM post")
    post_rows = sqlite_cursor.fetchall()
    for row in post_rows:
        await postgres_conn.execute(
            "INSERT INTO post (discord_message_id, discord_user_id, discord_server_id, discord_channel_id, created_at, upvotes, downvotes) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (discord_message_id) DO NOTHING",
            int(row[0]), int(row[1]), int(row[2]), int(row[3]), datetime.fromtimestamp(int(row[4])), int(row[5]), int(row[6])
        )

    # migrate karma_emote
    print("Migrating karma_emote...")
    sqlite_cursor.execute("SELECT * FROM karma_emote")
    karma_emote_rows = sqlite_cursor.fetchall()
    for row in karma_emote_rows:
        await postgres_conn.execute(
            "INSERT INTO karma_emote (id, discord_emote_id, discord_server_id, increase_karma) VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING",
            int(row[0]), int(row[1]), int(row[2]), bool(row[3])
        )

    # close connections
    sqlite_cursor.close()
    sqlite_conn.close()
    await postgres_conn.close()
    print("Done!")

if __name__ == "__main__":
    asyncio.run(migrate_db())
