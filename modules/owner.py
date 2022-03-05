import json
import logging
import subprocess
from os import path, walk

import nextcord
from nextcord import Activity, ActivityType
from nextcord.ext import commands, tasks
from sqlalchemy import func

from utils import db, store

logger = logging.getLogger(__name__)

class Owner(commands.Cog):

    COG_EMOJI = "👑"

    def __init__(self, bot):
        self.bot = bot
        self.status_task.start()
        with open(store.SETTINGS_PATH, "r") as settings:
            self.settings = json.load(settings)
        self.prefix = self.settings["prefix"]
        self.message_server = None
        self.message_channel = None
        self.message_text = None


    async def set_default_status(self):
        servers = len(self.bot.guilds)
        activityName = f"{self.prefix}help | {servers} servers"
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
        embed = nextcord.Embed(description=f'Shutting down...', color=0xf66045)
        await ctx.send(embed=embed)
        await self.bot.close()

    @commands.is_owner()
    @commands.command()
    async def reload(self, ctx):
        """
        Fetches the lates git commit and reloads the bot.
        """
        await ctx.message.add_reaction('<:greenTick:876177251832590348>')
        subprocess.run(["/bin/git","pull","--no-edit"])
        try:
            for cog in self.get_modules():
                self.bot.reload_extension(f'modules.{cog}')
        except Exception as e:
            exc = f'{type(e).__name__}: {e}'
            await ctx.channel.send(f'Failed to reload extensions\n{exc}')
        await ctx.channel.send('Reloaded all cogs', delete_after=120)
        await ctx.message.delete()

    @commands.is_owner()
    @commands.group()
    async def status(self, ctx):
        """
        Shows the bot's status.
        """
        await ctx.send(f'{self.bot.presence.activity.name}')

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
        embed = nextcord.Embed(title="Current message settings", color=0xf66045)
        embed.add_field(name="Server", value=self.message_server)
        embed.add_field(name="Channel", value=self.message_channel)
        embed.add_field(name="Message", value=self.message_text)
        await ctx.send(embed=embed, delete_after=30)

    @commands.is_owner()
    @message.command(name="server")
    async def message_server(self, ctx, server_id: int = None):
        if server_id is None:
            return await ctx.send("Please provide a server id", delete_after=30)
        server = await self.bot.fetch_guild(server_id)
        if server is None:
            return await ctx.send("Server not found", delete_after=30)
        self.message_server = server
        await ctx.send(f"Server set to `{server}`", delete_after=30)

    @commands.is_owner()
    @message.command(name="channel")
    async def message_channel(self, ctx, channel_id: int = None):
        if channel_id is not None:
            self.message_channel = channel_id
            channel = await self.bot.fetch_channel(channel_id)
            if channel is None:
                return await ctx.send("Channel not found", delete_after=30)
            #check if has send_message permission
            if not channel.permissions_for(ctx.me).send_messages:
                return await ctx.send("I don't have permission to send messages in that channel")
            return await ctx.send(f"Channel set to {channel.mention}" , delete_after=30)


        if self.message_server is None:
            return await ctx.send("Please set a server first", delete_after=30)

        channels = await self.message_server.fetch_channels()
        text_channels = [channel for channel in channels if type(channel) == nextcord.channel.TextChannel]
        if len(channels) == 0:
            return await ctx.send("No text channels found in this server", delete_after=30)
        embed = nextcord.Embed(title="Channels", color=0xf66045)
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
            channel = await self.message_server.fetch_channel(text_channels[int(message.content)].id)
            if channel is not None:
                self.message_channel = channel
                await ctx.send(f"Channel set to `{channel.name}`", delete_after=30)
            else:
                await ctx.send("That channel doesn't exist", delete_after=30)

    @commands.is_owner()
    @message.command(name="text")
    async def message_text(self, ctx, *text: str):
        self.message_text = " ".join(text[:])
        await ctx.send(f"Text set to:\n {self.message_text}", delete_after=30)

    @commands.is_owner()
    @message.command(name="send")
    async def message_send(self, ctx):
        if self.message_server is None or self.message_channel is None or self.message_text is None:
            return await ctx.send("Please set all the settings first", delete_after=30)
        embed = nextcord.Embed(title="Message overview", color=nextcord.Colour.blurple())
        embed.description = "⚠️ Are you sure you want to send this message ⚠️"
        embed.add_field(name="Server", value=self.message_server)
        embed.add_field(name="Channel", value=self.message_channel)
        embed.add_field(name="Message", value=self.message_text)
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
            await self.message_channel.send(self.message_text)
            await ctx.send("Message sent", delete_after=30)
        else:
            await message.delete()
            await ctx.send("Message not sent", delete_after=30)


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
        embed = nextcord.Embed(
            title='Server Infos',
            color=nextcord.Colour.blurple()
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
        embed = nextcord.Embed(
            title='Server Ids',
            color=nextcord.Colour.blurple()
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
        channels = ''
        for channel in guild.channels:
            channels += f'{channel.name}\n'
        embed = nextcord.Embed(
            title='Channel Infos',
            color=nextcord.Colour.blurple()
        )
        embed.add_field(name='Name', value=channels, inline=True)
        await ctx.send(embed=embed, delete_after=120)


    @commands.is_owner()
    @commands.command(hidden=True)
    async def checkVCs(self, ctx, server_id : int):
        if server_id is None:
            return await ctx.send("Please provide a server ID to be checked", delete_after = 5)
        server = self.bot.get_guild(server_id)
        if server is None:
            return await ctx.send("Server not found", delete_after = 5)
        if len(server.voice_channels) == 0:
            return await ctx.send(f"No voice channels on {server.name}", delete_after = 20)
        for vc in server.voice_channels:
            if len(vc.members) > 0:
                members = []
                for member in vc.members:
                    member_string = f"{member.display_name}"
                    if member.voice.self_stream:
                        member_string += f" 🔴"
                    if member.voice.self_video:
                        member_string += f" 📷"
                    if member.voice.self_deaf:
                        member_string += f" 🎧🔇"
                    elif member.voice.self_mute:
                        member_string += f" 🎤🔇"
                    members.append(member_string)
                members_string = "\n".join(sorted(members))
                embed = nextcord.Embed(
                    title = vc.name,
                    description = members_string,
                    colour = nextcord.Colour.blurple()
                )
                await ctx.send(embed=embed)
        await ctx.message.delete()

    @commands.command()
    @commands.is_owner()
    async def version(self, ctx, version):
        """
        Sets the bot's version.
        """
        with open(store.SETTINGS_PATH, "r") as settings:
            settings_json = json.load(settings)
        settings_json['version'] = version
        with open(store.SETTINGS_PATH, "w") as settings:
            json.dump(settings_json, settings, indent=2)
        if ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            await ctx.message.delete()
        embed = nextcord.Embed(description=f'Version has been set to {version}')
        await ctx.send(embed=embed, delete_after=10)

    def get_modules(self):
        filenames = next(walk("modules"), (None, None, []))[2] 
        filenames.remove(path.basename(__file__))
        return [name.replace('.py','') for name in filenames]

    
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
        embed = create_command_usage_embed(commands_used_query, f"Top 10 total used commands")
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
        embed = nextcord.Embed(title=f"Top {amount} used commands on: **{ctx.guild.name}**", color=0xE3621E)
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
        embed = nextcord.Embed(title="Top servers used commands", color=0xE3621E)
        embed.add_field(name="Command", value=commands_used, inline=True)
        embed.add_field(name="Count", value=commands_count, inline=True)
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @commands.is_owner()
    @commands.command(name="invite", hidden=True)
    async def server_invite(self, ctx, guild_id: int):
        """
        Creates an invite for the bot
        """
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await ctx.send("Server not found", delete_after = 5)

        if not guild.me.guild_permissions.create_instant_invite:
            return await ctx.send("I don't have the permissions to create an invite on this server", delete_after = 5)

        channels = await guild.fetch_channels()
        text_channels = [channel for channel in channels if type(channel) == nextcord.channel.TextChannel and channel.permissions_for(guild.me).view_channel]
        if len(channels) == 0:
            return await ctx.send("No text channels found in this server", delete_after=30)
        channel_string = ""
        for index, channel in enumerate(text_channels):
            channel_string += f"{index}. {channel.name}\n"
        embed = nextcord.Embed(title="Channels", description=channel_string, color=0xf66045)
        await ctx.send(embed=embed, delete_after=120)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            message = await self.bot.wait_for('message', timeout=60, check=check)
        except TimeoutError:
            return await ctx.send("You didn't answer the questions in Time", delete_after=30)

        if message.content.isdigit():
            channel = await guild.fetch_channel(text_channels[int(message.content)].id)
            if channel is not None:
                try:
                    invite = await channel.create_invite(max_age=300, max_uses=1)
                except:
                    return await ctx.send("Failed to create invite", delete_after=30)
                await ctx.send(f'Invite for `{guild.name}/{channel.name}`:\n{invite.url}', delete_after=30)
            else:
                await ctx.send("That channel doesn't exist", delete_after=30)

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
    embed = nextcord.Embed(title=embed_title, color=0xE3621E)
    embed.add_field(name="Command", value=commands_used, inline=True)
    embed.add_field(name="Count", value=commands_count, inline=True)
    return embed

def setup(bot):
    bot.add_cog(Owner(bot))
