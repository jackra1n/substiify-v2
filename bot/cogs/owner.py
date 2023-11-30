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
        stmt_usage = 'SELECT command_name, COUNT(*) AS cnt FROM command_history WHERE discord_server_id = $1 GROUP BY command_name ORDER BY cnt DESC LIMIT 10'
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
        stmt_usage = 'SELECT command_name, COUNT(*) AS cnt FROM command_history GROUP BY command_name ORDER BY cnt DESC LIMIT 10'
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
                       WHERE discord_server_id = $1 ORDER BY date DESC LIMIT $2'''
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
        commands_count += f"{result['cnt']}\n"
    embed = discord.Embed(color=values.PRIMARY_COLOR)
    embed.add_field(name="Command", value=commands_used, inline=True)
    embed.add_field(name="Count", value=commands_count, inline=True)
    return embed


async def setup(bot):
    await bot.add_cog(Owner(bot))
