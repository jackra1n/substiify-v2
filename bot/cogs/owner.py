import logging
import random
from typing import Literal, Optional

import discord
from core import config, values
from core.bot import Substiify
from core.version import VersionType
from discord import Activity, ActivityType
from discord.ext import commands, tasks
from discord.ext.commands import Greedy

logger = logging.getLogger(__name__)


# temporary imports for migration
import sqlite3
from datetime import datetime
from pathlib import Path



class Owner(commands.Cog):

    COG_EMOJI = "👑"

    def __init__(self, bot: Substiify):
        self.bot = bot
        self.status_task.start()
        self.message_server = None
        self.message_channel = None
        self.message_text = None
        self.message_embed = False
        self.embed_title = None

    async def set_default_status(self):
        if self.bot.is_ready():
            servers = len(self.bot.guilds)
            activity_name = f"{config.PREFIX}help | {servers} servers"
            activity = Activity(type=ActivityType.listening, name=activity_name)
            await self.bot.change_presence(activity=activity)

    @tasks.loop(minutes=30)
    async def status_task(self):
        await self.set_default_status()

    @commands.is_owner()
    @commands.command(hidden=True)
    async def shutdown(self, ctx: commands.Context):
        """
        Shuts down the bot. Made this in case something goes wrong.
        """
        embed = discord.Embed(description='Shutting down...', color=0xf66045)
        await ctx.send(embed=embed)
        await self.bot.close()

    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def sync(self, ctx: commands.Context, guilds: Greedy[discord.Object], spec: Optional[Literal["~", "*", "^"]] = None) -> None:
        if not guilds:
            if spec == "~":
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "*":
                ctx.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await ctx.bot.tree.sync(guild=ctx.guild)
            elif spec == "^":
                ctx.bot.tree.clear_commands(guild=ctx.guild)
                await ctx.bot.tree.sync(guild=ctx.guild)
                synced = []
            else:
                synced = await ctx.bot.tree.sync()

            await ctx.send(f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}")
            return

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")

    @commands.is_owner()
    @commands.group()
    async def status(self, ctx: commands.Context):
        """
        Shows the bot's status.
        """
        pass

    @commands.is_owner()
    @status.command()
    async def count(self, ctx: commands.Context, count: int):
        """
        Sets the bot's status to a fake number of servers the bot is in.
        """
        self.status_task.stop()
        activity_name = f"{ctx.prefix}help | {count} servers"
        activity = Activity(type=ActivityType.listening, name=activity_name)
        await self.bot.change_presence(activity=activity)

    @commands.is_owner()
    @status.command()
    async def set(self, ctx: commands.Context, *text: str):
        """
        Sets the bot's status to the given text.
        """
        self.status_task.stop()
        status = " ".join(text[:])
        activity = Activity(type=ActivityType.listening, name=status)
        await self.bot.change_presence(activity=activity)

    @commands.is_owner()
    @status.command()
    async def reset(self, ctx: commands.Context):
        """
        Resets the bot's status to the default.
        """
        await self.set_default_status()
        self.status_task.restart()

    @commands.is_owner()
    @commands.group()
    async def server(self, ctx: commands.Context):
        """
        Shows server information.
        """
        pass

    @commands.is_owner()
    @server.command(name='list')
    async def server_list(self, ctx: commands.Context):
        """
        Lists all the servers the bot is in.
        """
        servers = ''
        user_count = ''
        owner = ''
        for guild in self.bot.guilds:
            servers += f'{guild.name}\n'
            user_count += f'{guild.member_count}\n'
            owner += f'{guild.owner.mention} {guild.owner}\n'
        embed = discord.Embed(
            title='Server Infos',
            color=discord.Colour.blurple()
        )
        embed.add_field(name='Name', value=servers, inline=True)
        embed.add_field(name='Cnt', value=user_count, inline=True)
        embed.add_field(name='Owner', value=owner, inline=True)
        await ctx.send(embed=embed, delete_after=120)

    @commands.is_owner()
    @server.command(name='ids')
    async def server_ids(self, ctx: commands.Context):
        servers = ''
        server_ids = ''
        for guild in self.bot.guilds:
            servers += f'{guild}\n'
            server_ids += f'{guild.id}\n'
        embed = discord.Embed(
            title='Server Ids',
            color=discord.Colour.blurple()
        )
        embed.add_field(name='Name', value=servers, inline=True)
        embed.add_field(name='Id', value=server_ids, inline=True)
        await ctx.send(embed=embed, delete_after=120)

    @commands.is_owner()
    @server.command(name='channels', hidden=True)
    async def channel_list(self, ctx: commands.Context, guild_id: int):
        """
        Lists all the channels in a server.
        """
        guild = self.bot.get_guild(guild_id)
        channels = ''.join(f'{channel.name}\n' for channel in guild.channels)
        embed = discord.Embed(
            title='Channel Infos',
            color=discord.Colour.blurple()
        )
        embed.add_field(name='Name', value=channels, inline=True)
        await ctx.send(embed=embed, delete_after=120)

    @commands.is_owner()
    @commands.group(invoke_without_command=True)
    async def version(self, ctx: commands.Context):
        """
        Sets the bot's version.
        """
        embed = discord.Embed(color=discord.Colour.green())
        embed.add_field(name='Current version', value=self.bot.version.get())
        await ctx.send(embed=embed, delete_after=30)

    @commands.is_owner()
    @version.command(name='set')
    async def set_version(self, ctx: commands.Context, version_type: VersionType, value: int):
        """
        Sets the minor version.
        """
        self.bot.version.set(version_type, value)
        embed = discord.Embed(description=f'{version_type.value} version has been set to {value}')
        await ctx.send(embed=embed, delete_after=15)

    @set_version.error
    async def set_version_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Please specify the version type and value", delete_after=30)
        if isinstance(error, commands.BadArgument):
            await ctx.send("Version type not recognized. Version types are 'major' or 'minor'", delete_after=30)

    @commands.group(name="usage", invoke_without_command=True)
    async def usage(self, ctx: commands.Context):
        """
        Shows a lits of most used command on the current server
        """
        stmt_usage = 'SELECT command, COUNT(*) AS cnt FROM command_history WHERE guild_id = $1 GROUP BY command ORDER BY cnt DESC LIMIT 10'
        commands_used = await self.bot.db.fetch(stmt_usage, ctx.guild.id)
        embed = create_command_usage_embed(commands_used)
        embed.title = f"Top 10 used commands on: **{ctx.guild.name}**"
        await ctx.send(embed=embed, delete_after=180)
        await ctx.message.delete()

    @usage.command(name="all")
    async def usage_all(self, ctx: commands.Context):
        """
        Shows a list of most used commands on all servers
        """
        stmt_usage = 'SELECT command, COUNT(*) AS cnt FROM command_history GROUP BY command ORDER BY cnt DESC LIMIT 10'
        commands_used = await self.bot.db.fetch(stmt_usage)
        embed = create_command_usage_embed(commands_used)
        embed.title = "Top 10 total used commands"
        await ctx.send(embed=embed, delete_after=180)
        await ctx.message.delete()

    @usage.command(name="last")
    @commands.is_owner()
    async def usage_last(self, ctx: commands.Context, amount: int = 10):
        """
        Shows a list of last used commands on the current server
        """
        amount = min(amount, 20)
        stmt_last = '''SELECT * FROM command_history JOIN discord_user
                       ON command_history.discord_user_id = discord_user.discord_user_id
                       WHERE guild_id = $1 ORDER BY date DESC LIMIT $2'''
        commands_used = await self.bot.db.fetch(stmt_last, ctx.guild.id, amount)
        longest_user = self.get_longest_property_length(commands_used, 'username')
        longest_cmd = self.get_longest_property_length(commands_used, 'command_name')
        commands_used_string = ""
        for command in commands_used:
            discord_tmstmp = f"<t:{int(command['date'].timestamp())}:R>"
            cmd = command['command_name'].center(longest_cmd)
            user = command['username'].center(longest_user)
            commands_used_string += f"`{user}` used `{cmd}` {discord_tmstmp}\n"
        embed = discord.Embed(title=f"Last {amount} used commands on: **{ctx.guild.name}**", color=values.PRIMARY_COLOR)
        embed.description = commands_used_string
        await ctx.send(embed=embed, delete_after=60)
        await ctx.message.delete()

    def get_longest_property_length(self, record_list: list, prprty: str) -> len:
        longest_record = max(record_list, key=lambda x: len(x[prprty]))
        return len(longest_record[prprty])

    @usage.command(name="servers")
    @commands.is_owner()
    async def usage_servers(self, ctx: commands.Context):
        """
        Shows a list of servers with most used commands
        """
        stmt = '''SELECT COUNT(*) AS count, server_name FROM command_history JOIN discord_server
                  ON command_history.discord_server_id = discord_server.discord_server_id
                  GROUP BY server_name ORDER BY count DESC LIMIT 10'''
        commands_used_query = await self.bot.db.fetch(stmt)
        commands_used = ""
        commands_count = ""
        for row in commands_used_query:
            commands_used += f"`{row['server_name']}`\n"
            commands_count += f"{row['count']}\n"
        embed = discord.Embed(title="Top servers used commands", color=values.PRIMARY_COLOR)
        embed.add_field(name="Command", value=commands_used, inline=True)
        embed.add_field(name="Count", value=commands_count, inline=True)
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @commands.is_owner()
    @commands.group(name="db", invoke_without_command=True, hidden=True)
    async def db_command(self, ctx: commands.Context):
        """
        Database commands
        """
        pass

    @commands.is_owner()
    @db_command.command(name="populate")
    async def db_populate(self, ctx: commands.Context):
        """
        Populates the database with the default values
        """
        print("starting to populate database...")
        servers = self.bot.guilds

        for server in servers:
            stmt = '''INSERT INTO discord_server (discord_server_id, server_name) VALUES ($1, $2)
                      ON CONFLICT (discord_server_id) DO UPDATE SET server_name = $2'''
            await self.bot.db.execute(stmt, server.id, server.name)

            for channel in server.channels:
                if hasattr(channel, 'parent_id'):
                    print(f"inserting parent channel: {channel.parent}...")
                    stmt = '''INSERT INTO discord_channel (discord_channel_id, discord_server_id, channel_name) VALUES ($1, $2, $3)
                              ON CONFLICT (discord_channel_id) DO UPDATE SET channel_name = $3'''
                    await self.bot.db.execute(stmt, channel.parent.id, server.id, channel.parent.name)

                print(f"inserting channel: {channel}...")
                stmt = '''INSERT INTO discord_channel (discord_channel_id, discord_server_id, channel_name) VALUES ($1, $2, $3)
                          ON CONFLICT (discord_channel_id) DO UPDATE SET channel_name = $3'''
                await self.bot.db.execute(stmt, channel.id, server.id, channel.name)

        for post in await self.bot.db.fetch("SELECT * FROM post"):
            user_id = post['discord_user_id']
            discord_user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            print(f"fetched user: {discord_user}...")
            stmt = '''INSERT INTO discord_user (discord_user_id, username, avatar) VALUES ($1, $2, $3)
                      ON CONFLICT (discord_user_id) DO UPDATE SET username = $2, avatar = $3'''
            await self.bot.db.execute(stmt, discord_user.id, discord_user.name, discord_user.display_avatar.url)
    
        for command in await self.bot.db.fetch("SELECT discord_user_id FROM command_history GROUP BY discord_user_id"):
            user_id = command['discord_user_id']
            discord_user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            stmt = '''INSERT INTO discord_user (discord_user_id, username, avatar) VALUES ($1, $2, $3)
                      ON CONFLICT (discord_user_id) DO UPDATE SET username = $2, avatar = $3'''
            await self.bot.db.execute(stmt, discord_user.id, discord_user.name, discord_user.display_avatar.url)

        await ctx.send("Database populated", delete_after=30)

    @commands.is_owner()
    @db_command.command(name="generateTestData")
    async def db_generate_test_data(self, ctx: commands.Context):
        """
        Generates test data for the database
        """
        # fetch all users from the server
        async for user in ctx.guild.fetch_members(limit=None):
            print(f"inserting user: {user}...")
            stmt_insert_user = '''INSERT INTO discord_user (discord_user_id, username, avatar) VALUES ($1, $2, $3)
                                  ON CONFLICT (discord_user_id) DO UPDATE SET username = $2, avatar = $3'''
            await self.bot.db.execute(stmt_insert_user, user.id, user.name, user.display_avatar.url)
            stmt_insert_user_karma = '''INSERT INTO karma (discord_user_id, discord_server_id, amount) VALUES ($1, $2, $3)
                                        ON CONFLICT (discord_user_id, discord_server_id) DO UPDATE SET amount = $3'''
            random_karma = random.randint(500, 3000)
            await self.bot.db.execute(stmt_insert_user_karma, user.id, ctx.guild.id, random_karma)

    @commands.is_owner()
    @db_command.command(name="migrate")
    async def db_migrate(self, ctx: commands.Context):
        """
        Migrates the sqlite database to postgresql
        """
        await migrate_db(self.bot, ctx)
        await ctx.send("Database migrated")

async def migrate_db(bot: Substiify, ctx: commands.Context) -> None:
    try:
        sqlite_conn = sqlite3.connect('main.sqlite')
        sqlite_cursor = sqlite_conn.cursor()
    except Exception as e:
        print(f"Error connecting to sqlite database: {e}")
        return
    
    # create tables in postgresql using the sql script
    print("Creating tables in postgresql...")
    db_script = Path("./bot/db/CreateDatabase.sql").read_text('utf-8')
    await bot.db.execute(db_script)              

    # migrate discord_server
    print("Migrating discord_server...")
    sqlite_cursor.execute("SELECT * FROM discord_server")
    server_rows = sqlite_cursor.fetchall()
    for row in server_rows:
        await bot.db.execute(
            "INSERT INTO discord_server (discord_server_id, server_name, music_cleanup) VALUES ($1, $2, $3) ON CONFLICT (discord_server_id) DO NOTHING",
            int(row[0]), str(row[1]), bool(row[2])
        )
    # migrate discord_channel
    print("Migrating discord_channel...")
    sqlite_cursor.execute("SELECT * FROM discord_channel")
    channel_rows = sqlite_cursor.fetchall()
    for row in channel_rows:
        # check if the server of the channel exists in the postgresql db
        server_exists = await check_if_server_exists_and_insert(bot, row[2])
        if not server_exists:
            continue
        # if parent_discord_channel_id exists, find it in the db and insert it first
        if row[3] is not None:
            sqlite_cursor.execute("SELECT * FROM discord_channel WHERE discord_channel_id = ?", (row[3],))
            parent_row = sqlite_cursor.fetchone()
            if parent_row is None:
                # try getting the parent channel from discord
                try:
                    discord_channel = bot.get_channel(row[3]) or await bot.fetch_channel(row[3])
                except Exception as e:
                    print(f"Error fetching parent channel: {e}")
                    continue
                if discord_channel is None:
                    print(f"Parent channel with id {row[3]} does not exist in the database. Skipping...")
                    continue
                await bot.db.execute(
                    "INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id, upvote) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_channel_id) DO NOTHING",
                    discord_channel.id, discord_channel.name, discord_channel.guild.id, discord_channel.category_id, False
                )
            else:
                await bot.db.execute(
                    "INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id, upvote) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_channel_id) DO NOTHING",
                    parent_row[0], parent_row[1], parent_row[2], parent_row[3], bool(parent_row[4])
                )
        await bot.db.execute(
            "INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id, parent_discord_channel_id, upvote) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_channel_id) DO NOTHING",
            row[0], row[1], row[2], row[3], bool(row[4])
        )
            

    # migrate discord_user
    print("Migrating discord_user...")
    sqlite_cursor.execute("SELECT * FROM discord_user")
    user_rows = sqlite_cursor.fetchall()
    for row in user_rows:
        await bot.db.execute(
            "INSERT INTO discord_user (discord_user_id, username, avatar, is_bot) VALUES ($1, $2, $3, $4) ON CONFLICT (discord_user_id) DO NOTHING",
            int(row[0]), str(row[1]), str(row[2]), bool(row[3])
        )

    # migrate command_history
    print("Migrating command_history...")
    sqlite_cursor.execute("SELECT * FROM command_history")
    command_rows = sqlite_cursor.fetchall()
    for row in command_rows:
        # check if the server, channel or user exists in the postgresql db and try to fetch them from discord if they don't
        server_exists = await check_if_server_exists_and_insert(bot, row[4])
        if not server_exists:
            continue
        channel_exists = await check_if_channel_exists_and_insert(bot, row[5])
        if not channel_exists:
            continue
        user_exists = await check_if_user_exists_and_insert(bot, row[3])
        if not user_exists:
            continue
        await bot.db.execute(
            "INSERT INTO command_history (id, command_name, parameters, discord_user_id, discord_server_id, discord_channel_id, discord_message_id, date) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT (id) DO NOTHING",
            row[0], row[1], None, row[3], row[4], row[5], row[6], datetime.strptime(row[2], '%Y-%m-%d %H:%M:%S.%f')
        )
    # restart the sequence
    print("Restarting sequence...")
    await bot.db.execute("ALTER SEQUENCE command_history_id_seq RESTART WITH $1", len(command_rows) + 1)

    # migrate karma
    print("Migrating karma...")
    sqlite_cursor.execute("SELECT * FROM karma")
    karma_rows = sqlite_cursor.fetchall()
    for row in karma_rows:
        # check if the server or user exists in the postgresql db
        server_exists = await check_if_server_exists_and_insert(bot, row[2])
        if not server_exists:
            continue
        user_exists = await check_if_user_exists_and_insert(bot, row[1])
        if not user_exists:
            continue
        await bot.db.execute(
            "INSERT INTO karma (id, discord_user_id, discord_server_id, amount) VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING",
            row[0], row[1], row[2], row[3]
        )
    # restart the sequence
    print("Restarting sequence...")
    await bot.db.execute("ALTER SEQUENCE karma_id_seq RESTART WITH $1", len(karma_rows) + 1)

    # migrate post
    print("Migrating post...")
    sqlite_cursor.execute("SELECT * FROM post")
    post_rows = sqlite_cursor.fetchall()
    for row in post_rows:
        # check if the server, channel or user exists in the postgresql db and try to fetch them from discord if they don't
        server_exists = await check_if_server_exists_and_insert(bot, row[2])
        if not server_exists:
            continue
        channel_exists = await check_if_channel_exists_and_insert(bot, row[3])
        if not channel_exists:
            continue
        user_exists = await check_if_user_exists_and_insert(bot, row[1])
        if not user_exists:
            continue
        await bot.db.execute(
            "INSERT INTO post (discord_message_id, discord_user_id, discord_server_id, discord_channel_id, created_at, upvotes, downvotes) VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (discord_message_id) DO NOTHING",
            int(row[0]), int(row[1]), int(row[2]), int(row[3]), datetime.strptime(row[4], '%Y-%m-%d %H:%M:%S.%f'), int(row[5]), int(row[6])
        )

    # migrate karma_emote
    print("Migrating karma_emote...")
    sqlite_cursor.execute("SELECT * FROM karma_emote")
    karma_emote_rows = sqlite_cursor.fetchall()
    for row in karma_emote_rows:
        # check if the server or user exists in the postgresql db
        server_exists = await check_if_server_exists_and_insert(bot, row[2])
        if not server_exists:
            continue
        await bot.db.execute(
            "INSERT INTO karma_emote (id, discord_emote_id, discord_server_id, increase_karma) VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING",
            int(row[0]), int(row[1]), int(row[2]), bool(row[3])
        )
    # restart the sequence
    print("Restarting sequence...")
    await bot.db.execute("ALTER SEQUENCE karma_emote_id_seq RESTART WITH $1", len(karma_emote_rows) + 1)

    # close connections
    sqlite_cursor.close()
    sqlite_conn.close()
    print("Done!")

async def check_if_server_exists_and_insert(bot: Substiify, server_id: int) -> bool:
    server = await bot.db.fetchrow("SELECT * FROM discord_server WHERE discord_server_id = $1", server_id)
    if server is None:
        try:
            bot_server = bot.get_guild(server_id) or await bot.fetch_guild(server_id)
        except Exception as e:
            print(f"Error fetching server: {e}")
            return False
        if bot_server is None:
            print(f"Server with id {server_id} does not exist in the database. Skipping...")
            return False
        bot.db.execute(
            "INSERT INTO discord_server (discord_server_id, server_name) VALUES ($1, $2) ON CONFLICT (discord_server_id) DO NOTHING",
            server_id, bot_server.name
        )
    return True

async def check_if_channel_exists_and_insert(bot: Substiify, channel_id: int) -> bool:
    channel = await bot.db.fetchrow("SELECT * FROM discord_channel WHERE discord_channel_id = $1", channel_id)
    if channel is None:
        try:
            bot_channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        except Exception as e:
            print(f"Error fetching channel: {e}")
            return False
        if bot_channel is None:
            print(f"Channel with id {channel_id} does not exist in the database. Skipping...")
            return False
        bot.db.execute(
            "INSERT INTO discord_channel (discord_channel_id, channel_name, discord_server_id) VALUES ($1, $2, $3) ON CONFLICT (discord_channel_id) DO NOTHING",
            channel_id, bot_channel.name, bot_channel.guild.id
        )
    return True

async def check_if_user_exists_and_insert(bot: Substiify, user_id: int) -> bool:
    user = await bot.db.fetchrow("SELECT * FROM discord_user WHERE discord_user_id = $1", user_id)
    if user is None:
        try:
            bot_user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        except Exception as e:
            print(f"Error fetching user: {e}")
            return False
        if bot_user is None:
            print(f"User with id {user_id} does not exist in the database. Skipping...")
            return False
        bot.db.execute(
            "INSERT INTO discord_user (discord_user_id, username, avatar) VALUES ($1, $2, $3) ON CONFLICT (discord_user_id) DO NOTHING",
            user_id, bot_user.name, bot_user.display_avatar.url
        )
    return True


def create_command_usage_embed(results):
    commands_used = ""
    commands_count = ""
    for result in results:
        commands_used += f"`{result['command_name']}`\n"
        commands_count += f"{result['count']}\n"
    embed = discord.Embed(color=values.PRIMARY_COLOR)
    embed.add_field(name="Command", value=commands_used, inline=True)
    embed.add_field(name="Count", value=commands_count, inline=True)
    return embed


async def setup(bot):
    await bot.add_cog(Owner(bot))
