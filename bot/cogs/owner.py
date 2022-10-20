import json
import logging
import subprocess
from os import path, walk

import discord
from core import config
from core.version import VersionType
from discord import Activity, ActivityType
from discord.ext import commands, tasks
from sqlalchemy import func
from utils import db

logger = logging.getLogger('discord')


class Owner(commands.Cog):

    COG_EMOJI = "👑"

    def __init__(self, bot):
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
            activityName = f"{config.PREFIX}help | {servers} servers"
            activity = Activity(type=ActivityType.listening, name=activityName)
            await self.bot.change_presence(activity=activity)

    @tasks.loop(minutes=30)
    async def status_task(self):
        await self.set_default_status()

    @commands.is_owner()
    @commands.command(hidden=True)
    async def shutdown(self, ctx):
        """
        Shuts down the bot. Made this in case something goes wrong.
        """
        embed = discord.Embed(description='Shutting down...', color=0xf66045)
        await ctx.send(embed=embed)
        await self.bot.close()

    @commands.is_owner()
    @commands.command()
    async def reload(self, ctx):
        """
        Fetches the lates git commit and reloads the bot.
        """
        await ctx.message.add_reaction('<:greenTick:876177251832590348>')
        subprocess.run(["/bin/git", "pull", "--no-edit"])
        try:
            for cog in self.get_modules():
                self.bot.reload_extension(f'modules.{cog}')
        except Exception as e:
            exc = f'{type(e).__name__}: {e}'
            await ctx.send(f'Failed to reload extensions\n{exc}')
        await ctx.send('Reloaded all cogs', delete_after=120)
        await ctx.message.delete()

    @commands.is_owner()
    @commands.group()
    async def status(self, ctx):
        """
        Shows the bot's status.
        """
        pass

    @commands.is_owner()
    @status.command()
    async def count(self, ctx, count):
        """
        Sets the bot's status to a fake number of servers the bot is in.
        """
        self.status_task.stop()
        activityName = f"{self.prefix}help | {count} servers"
        activity = Activity(type=ActivityType.listening, name=activityName)
        await self.bot.change_presence(activity=activity)

    @commands.is_owner()
    @status.command()
    async def set(self, ctx, *text: str):
        """
        Sets the bot's status to the given text.
        """
        self.status_task.stop()
        status = " ".join(text[:])
        activity = Activity(type=ActivityType.listening, name=status)
        await self.bot.change_presence(activity=activity)

    @commands.is_owner()
    @status.command()
    async def reset(self, ctx):
        """
        Resets the bot's status to the default.
        """
        await self.set_default_status()
        self.status_task.restart()

    @commands.is_owner()
    @commands.group(invoke_without_command=True, hidden=True)
    async def message(self, ctx):
        embed = discord.Embed(title="Current message settings", color=0xf66045)
        embed.add_field(name="Server", value=self.message_server)
        embed.add_field(name="Channel", value=self.message_channel)
        embed.add_field(name="Message", value=self.message_text)
        embed.add_field(name="Embed", value=self.message_embed)
        embed.add_field(name="Embed title", value=self.embed_title)
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @commands.is_owner()
    @message.group(invoke_without_command=True, name="embed")
    async def _embed(self, ctx, option: bool):
        self.message_embed = option
        await ctx.send(f'Embed messages set to {option}', delete_after=30)
        await ctx.message.delete()

    @commands.is_owner()
    @_embed.command(name="title")
    async def title(self, ctx, title: str):
        self.embed_title = title
        await ctx.send(f'Embed title set to `{title}`', delete_after=30)
        await ctx.message.delete()

    @commands.is_owner()
    @message.command(name="server")
    async def message_server(self, ctx, server_id: int = None):
        await ctx.message.delete()
        if server_id is None:
            return await ctx.send("Please provide a server id", delete_after=30)
        server = await self.bot.fetch_guild(server_id)
        if server is None:
            return await ctx.send("Server not found", delete_after=30)
        self.message_server = server
        await ctx.send(f"Server set to `{server}`", delete_after=30)

    @commands.is_owner()
    @message.command(name="channel")
    async def message_channel(self, ctx, channel_id: int = None, ignore_permissions: bool = False):
        if channel_id is not None:
            channel = await self.bot.fetch_channel(channel_id)
            if channel is None:
                return await ctx.send("Channel not found", delete_after=30)
            # check if has send_message permission
            if not channel.permissions_for(ctx.me).send_messages and not ignore_permissions:
                return await ctx.send("I don't have permission to send messages in that channel")
            self.message_channel = channel
            await ctx.message.delete()
            return await ctx.send(f"Channel set to {channel.mention}", delete_after=30)

        if self.message_server is None:
            return await ctx.send("Please set a server first", delete_after=30)

        channels = await self.message_server.fetch_channels()
        text_channels = [channel for channel in channels if type(channel) == discord.channel.TextChannel]
        if len(channels) == 0:
            return await ctx.send("No text channels found in this server", delete_after=30)
        embed = discord.Embed(title="Channels", color=0xf66045)
        channel_string = ""
        for index, channel in enumerate(text_channels):
            channel_string += f"{index}. {channel.name}\n"
        embed.add_field(name="Channels", value=channel_string)
        await ctx.send(embed=embed, delete_after=120)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            message = await self.bot.wait_for('message', timeout=60, check=check)
        except TimeoutError:
            return await ctx.send("You didn't answer the questions in Time", delete_after=30)
        if message.content.isdigit():
            channel = await self.bot.fetch_channel(text_channels[int(message.content)].id)
            if channel is not None:
                self.message_channel = channel
                await ctx.send(f"Channel set to `{channel.name}`", delete_after=30)
            else:
                await ctx.send("That channel doesn't exist", delete_after=30)
        await ctx.message.delete()

    @commands.is_owner()
    @message.command(name="text")
    async def message_text(self, ctx, *text: str):
        self.message_text = " ".join(text[:])
        await ctx.send(f"Text set to:\n`{self.message_text}`", delete_after=30)
        await ctx.message.delete()

    @commands.is_owner()
    @message.command(name="send")
    async def message_send(self, ctx):
        if self.message_server is None or self.message_channel is None or self.message_text is None:
            return await ctx.send("Please set all the settings first", delete_after=30)
        embed = discord.Embed(title="Message overview", color=discord.Colour.blurple())
        embed.description = "⚠️ Are you sure you want to send this message ⚠️"
        embed.add_field(name="Server", value=self.message_server)
        embed.add_field(name="Channel", value=self.message_channel)
        embed.add_field(name="Message", value=self.message_text)
        embed.add_field(name="Embed", value=self.message_embed)
        embed.add_field(name="Embed title", value=self.embed_title)
        message = await ctx.send(embed=embed)
        await message.add_reaction("✅")
        await message.add_reaction("❌")

        def check(reaction, user):
            return user == ctx.author and reaction.message.id == message.id and reaction.emoji in ("✅", "❌")

        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=30, check=check)
        except TimeoutError:
            await ctx.send("You didn't answer the questions in Time", delete_after=30)
            return

        if reaction.emoji == "✅":
            await message.delete()
            if self.message_embed:
                embed = discord.Embed(title=self.embed_title, color=discord.Colour.blurple())
                if self.message_text is None:
                    return await ctx.send("Please set the text first", delete_after=30)
                embed.description = self.message_text
                await self.message_channel.send(embed=embed)
            else:
                await self.message_channel.send(self.message_text)
            await ctx.send("Message sent", delete_after=30)
        else:
            await message.delete()
            await ctx.send("Message not sent", delete_after=30)
        await ctx.message.delete()

    @commands.is_owner()
    @commands.group()
    async def server(self, ctx):
        """
        Shows server information.
        """
        pass

    @commands.is_owner()
    @server.command(name='list')
    async def server_list(self, ctx):
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
    async def server_ids(self, ctx):
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
    async def channel_list(self, ctx, guild_id: int):
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
    async def version(self, ctx):
        """
        Sets the bot's version.
        """
        embed = discord.Embed(color=discord.Colour.green())
        embed.add_field(name='Current version', value=self.bot.version.get())
        await ctx.send(embed=embed, delete_after=30)

    @commands.is_owner()
    @version.command(name='set')
    async def set_version(self, ctx, version_type: VersionType, value: int):
        """
        Sets the minor version.
        """
        self.bot.version.set(version_type, value)
        embed = discord.Embed(description=f'{version_type.value} version has been set to {value}')
        await ctx.send(embed=embed, delete_after=15)

    @set_version.error
    async def set_version_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Please specify the version type and value", delete_after=30)
        if isinstance(error, commands.BadArgument):
            await ctx.send("Version type not recognized. Version types are 'major' or 'minor'", delete_after=30)

    def get_modules(self):
        filenames = next(walk("modules"), (None, None, []))[2]
        filenames.remove(path.basename(__file__))
        return [name.replace('.py', '') for name in filenames]

    @commands.group(name="usage", invoke_without_command=True)
    async def usage(self, ctx):
        """
        Shows a lits of most used command on the current server
        """
        commands_used_query = db.session.query(db.command_history.command, func.count('*')).filter_by(discord_server_id=ctx.guild.id).group_by(db.command_history.command).order_by(func.count('*').desc()).limit(10).all()
        embed = create_command_usage_embed(commands_used_query, f"Top 10 used commands on: **{ctx.guild.name}**")
        await ctx.send(embed=embed, delete_after=180)
        await ctx.message.delete()

    @usage.command(name="all")
    async def usage_all(self, ctx):
        """
        Shows a list of most used commands on all servers
        """
        commands_used_query = db.session.query(db.command_history.command, func.count('*')).group_by(db.command_history.command).order_by(func.count('*').desc()).limit(10).all()
        embed = create_command_usage_embed(commands_used_query, "Top 10 total used commands")
        await ctx.send(embed=embed, delete_after=180)
        await ctx.message.delete()

    @usage.command(name="last")
    @commands.is_owner()
    async def usage_last(self, ctx, amount: int = 10):
        """
        Shows a list of most used commands on the last server
        """
        commands_used_query = db.session.query(db.command_history, db.discord_user).join(db.command_history, db.command_history.discord_user_id == db.discord_user.discord_user_id).filter_by(discord_server_id=ctx.guild.id).order_by(db.command_history.date.desc()).limit(amount).all()
        commands_used = ""
        for command in commands_used_query:
            formated_date = command[0].date.strftime("%d/%m/%Y %H:%M:%S")
            commands_used += f"`{command[0].command}` used by `{command[1].username}` at {formated_date}\n"
        embed = discord.Embed(title=f"Top {amount} used commands on: **{ctx.guild.name}**", color=0xE3621E)
        embed.description = commands_used
        await ctx.send(embed=embed, delete_after=60)
        await ctx.message.delete()

    @usage.command(name="servers")
    @commands.is_owner()
    async def usage_servers(self, ctx):
        """
        Shows a list of servers with most used commands
        """
        commands_used_query = db.session.query(db.command_history.discord_server_id, db.discord_server, func.count('*')).join(db.command_history, db.command_history.discord_server_id == db.discord_server.discord_server_id).group_by(db.command_history.discord_server_id).order_by(func.count('*').desc()).all()
        commands_used = ""
        commands_count = ""
        for row in commands_used_query:
            commands_used += f"`{row[1].server_name}`\n"
            commands_count += f"{row[2]}\n"
        embed = discord.Embed(title="Top servers used commands", color=0xE3621E)
        embed.add_field(name="Command", value=commands_used, inline=True)
        embed.add_field(name="Count", value=commands_count, inline=True)
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @commands.is_owner()
    @commands.group(name="db", invoke_without_command=True, hidden=True)
    async def db_command(self, ctx):
        """
        Database commands
        """
        pass

    @commands.is_owner()
    @db_command.command(name="create")
    async def db_create(self, ctx):
        """
        Creates the database
        """
        db.Base.metadata.create_all(db.engine)
        await ctx.send("Database created", delete_after=30)

    @commands.is_owner()
    @db_command.command(name="execute")
    async def db_execute(self, ctx, *, query):
        """
        Executes a statement on the database
        """
        try:
            result = db.execute_sql(query)
            await ctx.send(result, delete_after=30)
        except Exception as e:
            await ctx.send(e, delete_after=30)

    @commands.is_owner()
    @db_command.command(name="convert")
    async def db_convert(self, ctx, version):
        """
        Converts the database to the specified version
        """
        try:
            db.convert_db(version)
            await ctx.send("Database converted", delete_after=30)
        except Exception as e:
            await ctx.send(e, delete_after=30)

    @commands.is_owner()
    @db_command.command(name="populate")
    async def db_populate(self, ctx):
        """
        Populates the database with the default values
        """
        servers = self.bot.guilds
        for server in servers:
            if db.session.query(db.discord_server).filter_by(discord_server_id=server.id).first() is None:
                db.session.add(db.discord_server(server))
            for channel in server.channels:
                if db.session.query(db.discord_channel).filter_by(discord_channel_id=channel.id).first() is None:
                    db.session.add(db.discord_channel(channel))
        db.session.commit()
        for post in db.session.query(db.post).group_by(db.post.discord_user_id).all():
            if db.session.query(db.discord_user).filter_by(discord_user_id=post.discord_user_id).first() is None:
                discord_user = await self.bot.fetch_user(post.discord_user_id)
                print(f"fetched user: {discord_user}...")
                db.session.add(db.discord_user(discord_user))
        db.session.commit()
        for command in db.session.query(db.command_history).group_by(db.command_history.discord_user_id).all():
            if db.session.query(db.discord_user).filter_by(discord_user_id=command.discord_user_id).first() is None:
                discord_user = await self.bot.fetch_user(command.discord_user_id)
                db.session.add(db.discord_user(discord_user))
        db.session.commit()
        await ctx.send("Database populated", delete_after=30)


def create_command_usage_embed(commands_used_query, embed_title):
    commands_used = ""
    commands_count = ""
    for row in commands_used_query:
        commands_used += f"`{row[0]}`\n"
        commands_count += f"{row[1]}\n"
    embed = discord.Embed(title=embed_title, color=0xE3621E)
    embed.add_field(name="Command", value=commands_used, inline=True)
    embed.add_field(name="Count", value=commands_count, inline=True)
    return embed


async def setup(bot):
    await bot.add_cog(Owner(bot))
