import logging
from datetime import datetime
from pathlib import Path

import asyncpg
import discord
from asyncpg import Record
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
            query = "INSERT INTO discord_server(discord_server_id, server_name) VALUES ($1, $2)"
            await con.executemany(query, servers)
        await ctx.send("Database populated", delete_after=30)

    ## INSERT

    async def insert_foundation_from_ctx(self, ctx):
        user = ctx.author
        server = ctx.guild
        channel = ctx.channel
        await self.insert_foundation(user, server, channel)

    async def insert_foundation(self, user: discord.Member, server: discord.Guild, channel: discord.abc.Messageable):
        async with self.pool.acquire() as con:
            avatar_url = user.avatar.url if user.avatar else None
            await con.execute(USER_INSERT_QUERY, user.id, user.name, user.discriminator, avatar_url)
            await con.execute(SERVER_INSERT_QUERY, server.id, server.name)
            if pchannel := channel.parent if hasattr(channel, 'parent') else None:
                await con.execute(CHANNEL_INSERT_QUERY, pchannel.id, pchannel.name, pchannel.guild.id, None)
            p_chan_id = pchannel.id if pchannel else None
            await con.execute(CHANNEL_INSERT_QUERY, channel.id, channel.name, channel.guild.id, p_chan_id)

    async def insert_to_cmd_history(self, ctx: Context) -> None:
        await self.insert_foundation_from_ctx(ctx)
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
            avatar_url = user.avatar.url if user.avatar else None
            await con.execute(USER_INSERT_QUERY, user.id, user.name, user.discriminator, avatar_url)

    async def insert_discord_server(self, server: discord.Guild):
        async with self.pool.acquire() as con:
            await con.execute(SERVER_INSERT_QUERY, server.id, server.name)

    async def insert_discord_channel(self, channel: discord.abc.Messageable):
        parent_channel = channel.parent.id if hasattr(channel, 'parent') else None
        async with self.pool.acquire() as con:
            await con.execute(CHANNEL_INSERT_QUERY, channel.id, channel.name, channel.guild.id, parent_channel)

    async def insert_kasino(self, ctx, question, option_1, option_2, kasino_msg) -> int:
        query = """INSERT INTO kasino
                   (question, option1, option2, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   RETURNING id
                """
        async with self.pool.acquire() as con:
            return await con.fetchval(query, question, option_1, option_2, ctx.guild.id, ctx.channel.id, kasino_msg.id)

    async def upsert_user_karma(self, user_id: int, guild_id: int, amount: int) -> None:
        query = """INSERT INTO karma (discord_user_id, discord_server_id, amount)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (discord_user_id, discord_server_id) DO UPDATE SET
                   amount = karma.amount + EXCLUDED.amount
                """
        async with self.pool.acquire() as con:
            await con.execute(query, user_id, guild_id, amount)

    async def insert_giveaway(self, creator: discord.Member, end_date: datetime, prize: str, msg: discord.Message):
        query = """INSERT INTO giveaway
                   (discord_user_id, end_date, prize, discord_server_id, discord_channel_id, discord_message_id)
                   VALUES ($1, $2, $3, $4, $5, $6)
                """
        async with self.pool.acquire() as con:
            await con.execute(query, creator.id, end_date, prize, msg.guild.id, msg.channel.id, msg.id)

    async def insert_karma_emote(self, server: discord.Guild, emote: discord.Emoji, action: bool) -> int:
        query = "INSERT INTO karma_emote (discord_emote_id, discord_server_id, increase_karma) VALUES ($1, $2, $3)"
        async with self.pool.acquire() as con:
            await con.execute(query, emote.id, server.id, action)

    async def insert_bet(self, kasino_id: int, user_id: int, amount: int, option: int) -> None:
        query = """INSERT INTO kasino_bet (kasino_id, discord_user_id, amount, option)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (kasino_id, discord_user_id) DO UPDATE SET
                   amount = kasino_bet.amount + EXCLUDED.amount
                """
        async with self.pool.acquire() as con:
            await con.execute(query, kasino_id, user_id, amount, option)

    ## UPDATE

    async def update_server_music_cleanup(self, ctx: Context, do_cleanup: bool):
        await self.insert_foundation_from_ctx(ctx)
        query = "UPDATE discord_server SET music_cleanup = $1 WHERE discord_server_id = $2"
        async with self.pool.acquire() as con:
            await con.execute(query, do_cleanup, ctx.guild.id)

    async def update_channel_votes(self, ctx: Context, do_vote: bool):
        await self.insert_foundation_from_ctx(ctx)
        query = "UPDATE discord_channel SET upvote = $1 WHERE discord_channel_id = $2"
        async with self.pool.acquire() as con:
            await con.execute(query, do_vote, ctx.channel.id)

    async def update_user_karma(self, user_id: int, serve_id: int, amount: int) -> None:
        query = "UPDATE karma SET amount = amount + $1 WHERE discord_user_id = $2 AND discord_server_id = $3"
        async with self.pool.acquire() as con:
            await con.execute(query, amount, user_id, serve_id)

    async def update_post_upvotes_and_downvotes(self, post_id: int, upvotes: int, downvotes: int) -> None:
        query = "UPDATE post SET upvotes = upvotes + $1, downvotes = downvotes + $2 WHERE discord_message_id = $3"
        async with self.pool.acquire() as con:
            await con.execute(query, upvotes, downvotes, post_id)

    async def update_kasino_lock_status(self, kasino_id: int, status: bool) -> None:
        query = "UPDATE kasino SET locked = $1 WHERE id = $2"
        async with self.pool.acquire() as con:
            await con.execute(query, status, kasino_id)

    async def update_kasino_message(self, kasino_id: int, channel_id: int, message_id: int) -> None:
        query = "UPDATE kasino SET discord_channel_id = $1, discord_message_id = $2 WHERE id = $3"
        async with self.pool.acquire() as con:
            await con.execute(query, channel_id, message_id, kasino_id)

    ## DELETE

    async def delete_giveaway(self, primary_key: int):
        async with self.pool.acquire() as con:
            await con.execute('DELETE FROM giveaway WHERE id = $1', primary_key)

    async def delete_giveaway_by_msg_id(self, msg_id: int):
        async with self.pool.acquire() as con:
            return await con.execute('DELETE FROM giveaway WHERE discord_message_id = $1', msg_id)

    async def delete_karma_emote(self, server: discord.Guild, emote: discord.Emoji):
        query = "DELETE FROM karma_emote WHERE discord_emote_id = $1 AND discord_server_id = $2"
        async with self.pool.acquire() as con:
            return await con.execute(query, emote.id, server.id)

    async def delete_kasino(self, kasino_id: int):
        async with self.pool.acquire() as con:
            await con.execute('DELETE FROM kasino WHERE id = $1', kasino_id)

    ## SELECT

    async def get_discord_user(self, user: discord.Member) -> Record:
        async with self.pool.acquire() as con:
            return await con.fetchrow("SELECT * FROM discord_user WHERE id = $1", user.id)

    async def get_discord_server_members(self, server_id: int) -> list[Record]:
        async with self.pool.acquire() as con:
            return await con.fetch("SELECT * FROM discord_user WHERE discord_server_id = $1", server_id)

    async def get_discord_server(self, server: discord.Guild) -> Record:
        query = "SELECT * FROM discord_server WHERE discord_server_id = $1"
        async with self.pool.acquire() as con:
            return await con.fetchrow(query, server.id)

    async def get_all_giveaways(self) -> list[Record]:
        async with self.pool.acquire() as con:
            return await con.fetch('SELECT * FROM giveaway')

    async def get_vote_channel(self, channel: discord.abc.Messageable) -> bool:
        query = "SELECT upvote FROM discord_channel WHERE discord_channel_id = $1"
        async with self.pool.acquire() as con:
            return await con.fetchval(query, channel.id)

    async def get_votes_channels(self, server: discord.Guild) -> list[Record]:
        query = "SELECT * FROM discord_channel WHERE discord_server_id = $1 AND upvote = True"
        async with self.pool.acquire() as con:
            return await con.fetch(query, server.id)

    async def get_all_votes_channels(self) -> list[Record]:
        async with self.pool.acquire() as con:
            return await con.fetch("SELECT * FROM discord_channel WHERE upvote = True")

    async def get_kasino(self, kasino_id) -> Record:
        query = "SELECT * FROM kasino WHERE id = $1"
        async with self.pool.acquire() as con:
            return await con.fetchrow(query, kasino_id)

    async def get_kasino_by_message_id(self, message_id) -> Record:
        query = "SELECT * FROM kasino WHERE discord_message_id = $1"
        async with self.pool.acquire() as con:
            return await con.fetchrow(query, message_id)

    async def get_kasino_by_server(self, server_id) -> list[Record]:
        query = "SELECT * FROM kasino WHERE discord_server_id = $1"
        async with self.pool.acquire() as con:
            return await con.fetch(query, server_id)

    async def get_kasino_and_bets(self, kasino_id) -> list[Record]:
        query = """SELECT k.question, kb.discord_user_id, kb.discord_server_id, kb.amount
                   FROM kasino k 
                   LEFT JOIN kasino_bet kb ON k.id = kb.kasino_id 
                   WHERE k.id = $1
                """
        async with self.pool.acquire() as con:
            return await con.fetch(query, kasino_id)

    async def get_kasino_bets_sum(self, kasino_id, option) -> float:
        query = "SELECT SUM(amount) FROM kasino_bet WHERE kasino_id = $1 AND option = $2"
        async with self.pool.acquire() as con:
            return await con.fetchval(query, kasino_id, option)

    async def get_total_kasino_karma(self, kasino_id) -> int:
        query = "SELECT SUM(amount) FROM kasino_bet WHERE kasino_id = $1"
        async with self.pool.acquire() as con:
            return await con.fetchval(query, kasino_id)

    async def get_kasino_user_bet(self, kasino_id, user_id) -> Record:
        query = "SELECT * FROM kasino_bet WHERE kasino_id = $1 AND discord_user_id = $2"
        async with self.pool.acquire() as con:
            return await con.fetchrow(query, kasino_id, user_id)

    async def get_user_karma(self, user_id: int, server_id: int) -> int:
        query = "SELECT amount FROM karma WHERE discord_server_id = $1 AND discord_user_id = $2"
        async with self.pool.acquire() as con:
            return await con.fetchval(query, server_id, user_id)

    async def get_karma_emotes(self, server_id: int) -> list[Record]:
        query = "SELECT * FROM karma_emote WHERE discord_server_id = $1 ORDER BY increase_karma"
        async with self.pool.acquire() as con:
            return await con.fetch(query, server_id)

    async def get_karma_emotes_count(self, server: discord.Guild) -> int:
        query = "SELECT COUNT(*) FROM karma_emote WHERE discord_server_id = $1"
        async with self.pool.acquire() as con:
            return await con.fetchval(query, server.id)

    async def get_karma_emote_by_id(self, server: discord.Guild, emote: discord.Emoji) -> Record:
        query = "SELECT * FROM karma_emote WHERE discord_server_id = $1 AND discord_emote_id = $2"
        async with self.pool.acquire() as con:
            return await con.fetchrow(query, server.id, emote.id)

    async def get_upvote_karma_emote_ids_by_server(self, server_id: int) -> list[int]:
        query = "SELECT discord_emote_id FROM karma_emote WHERE discord_server_id = $1 AND increase_karma = True"
        async with self.pool.acquire() as con:
            return await con.fetch(query, server_id)

    async def get_downvote_karma_emote_ids_by_server(self, server_id: int) -> list[int]:
        query = "SELECT discord_emote_id FROM karma_emote WHERE discord_server_id = $1 AND increase_karma = False"
        async with self.pool.acquire() as con:
            return await con.fetch(query, server_id)

    async def get_karma_leaderboard(self, server: discord.Guild, limit: int = 15) -> list[Record]:
        query = "SELECT * FROM karma WHERE discord_server_id = $1 ORDER BY amount DESC LIMIT $2"
        async with self.pool.acquire() as con:
            return await con.fetch(query, server.id, limit)

    async def get_karma_leaderboard_global(self) -> list[Record]:
        async with self.pool.acquire() as con:
            return await con.fetch("SELECT * FROM karma ORDER BY amount DESC")

    async def get_all_posts(self) -> list[Record]:
        async with self.pool.acquire() as con:
            return await con.fetch('SELECT * FROM post')

    async def get_post_by_message_id(self, message_id: int) -> Record:
        query = "SELECT * FROM post WHERE discord_message_id = $1"
        async with self.pool.acquire() as con:
            return await con.fetchrow(query, message_id)

    async def get_top_servers_posts(self, server: discord.Guild) -> list[Record]:
        query = "SELECT * FROM post WHERE discord_server_id = $1 ORDER BY upvotes DESC"
        async with self.pool.acquire() as con:
            return await con.fetch(query, server.id)

    async def get_top_servers_posts_monthly(self, server: discord.Guild) -> list[Record]:
        query = "SELECT * FROM post WHERE discord_server_id = $1 AND created_at > now() - interval '30 days' ORDER BY upvotes DESC"
        async with self.pool.acquire() as con:
            return await con.fetch(query, server.id)

    async def get_top_servers_posts_weekly(self, server: discord.Guild) -> list[Record]:
        query = "SELECT * FROM post WHERE discord_server_id = $1 AND created_at > now() - interval '7 days' ORDER BY upvotes DESC"
        async with self.pool.acquire() as con:
            return await con.fetch(query, server.id)

    async def get_command_usage_all(self, ctx: Context) -> list[Record]:
        await self.insert_foundation_from_ctx(ctx)
        query = """SELECT command_name, COUNT(*) FROM command_history
                   GROUP BY command_name
                   ORDER BY COUNT(*) DESC LIMIT 10
                """
        async with self.pool.acquire() as con:
            return await con.fetch(query)

    async def get_last_command_usage(self, ctx: Context, amount: int) -> list[Record]:
        await self.insert_foundation_from_ctx(ctx)
        query = """SELECT * FROM command_history JOIN discord_user
                   ON command_history.discord_user_id = discord_user.discord_user_id
                   WHERE command_history.discord_server_id = $1
                   ORDER BY command_history.date DESC LIMIT $2
                """
        async with self.pool.acquire() as con:
            return await con.fetch(query, ctx.guild.id, amount)

    async def get_command_usage_by_command(self, ctx: Context) -> list[Record]:
        await self.insert_foundation_from_ctx(ctx)
        query = """SELECT command_name, COUNT(*) FROM command_history
                   WHERE discord_server_id = $1
                   GROUP BY command_name
                   ORDER BY COUNT(*) DESC LIMIT 10
                """
        async with self.pool.acquire() as con:
            return await con.fetch(query, ctx.guild.id)

    async def get_command_usage_by_server(self, ctx: Context) -> list[Record]:
        await self.insert_foundation_from_ctx(ctx)
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
