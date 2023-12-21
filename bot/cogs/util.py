import asyncio
import datetime
import logging
import math
import platform
import subprocess
from random import SystemRandom, shuffle

import discord
import psutil
from core import values
from core.bot import Substiify
from discord import MessageType, app_commands
from discord.ext import commands, tasks
from pytz import timezone

logger = logging.getLogger(__name__)


class Util(commands.Cog):

    COG_EMOJI = "📦"

    def __init__(self, bot: Substiify):
        self.bot = bot
        self.giveaway_task.start()

    @commands.command(name='teams', aliases=['team'])
    async def teams(self, ctx: commands.Context, *, players: str = None):
        """
        Create two teams from the current members of the voice channel or passed names for you to play custom games.
        """
        if ctx.author.voice:
            players_list = [member for member in ctx.author.voice.channel.members if not member.bot]
        elif players:
            players_list = players.split(',') if ',' in players else players.split(' ')
        else:
            return await ctx.send('You must be in a voice channel or provide a list of players.')

        if len(players_list) < 4:
            return await ctx.send('You must have at least 4 members to use this command.')

        shuffle(players_list)
        team_1 = players_list[:len(players_list) // 2]
        team_2 = players_list[len(players_list) // 2:]

        embed = discord.Embed(title='Teams', color=0x00FFFF)
        embed.add_field(name='Team 1', value="\n".join([f'{member} ' for member in team_1]))
        embed.add_field(name='Team 2', value="\n".join([f'{member} ' for member in team_2]))
        if ctx.author.voice and players:
            embed.set_footer(text='Did you know that if you are in a voice channel you can just type `<<teams`?')
        await ctx.send(embed=embed)

    @commands.hybrid_group(aliases=["give"])
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def giveaway(self, ctx: commands.Context):
        """
        Allows you to create giveaways on the server.
        To quickly create a giveaway, use the short command `<<give c [hosted_by]`. Hosted_by is optional.
        For more info, check the `giveaway create` help page.
        """
        await ctx.send_help(ctx.command)

    @giveaway.command(aliases=["c"], usage="create [hosted_by]")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        channel='In which channel should the Giveaway be hosted?',
        duration='For how long should the Giveaway be hosted? Type number followed by (m|h|d). Example: `10m`',
        prize='What is the prize of the Giveaway?',
        hosted_by='Who is hosting the Giveaway? If not specified, the author of the command will be the host.'
    )
    async def create(self, ctx: commands.Context, channel: discord.TextChannel, duration: str, prize: str, hosted_by: discord.Member = None):
        """
        Allows you to create a giveaway. Requires manage_channels permission.
        After calling this command, you will be asked to enter the prize, the time, and the channel.
        To set the host of the giveaway, specify the optional parameter `hosted_by`.

        Example:
        `<<giveaway create` or `<<giveaway create @user`
        Short:
        `<<give c` or `<<give c @user`
        """
        if hosted_by is None or hosted_by.bot:
            hosted_by = ctx.author

        channel = await self.bot.fetch_channel(channel.id)
        if not channel.permissions_for(ctx.me).send_messages:
            return await ctx.reply("I don't have permission to send messages in that channel", delete_after=60)

        time = self.convert(duration)
        # Check if Time is valid
        if time == -1:
            await ctx.send("The Time format was wrong")
            return
        elif time == -2:
            await ctx.send("The Time was not conventional number")
            return

        setup_complete = f"Setup finished. Giveaway for **'{prize}'** will be in {channel.mention}"
        await ctx.send(embed=discord.Embed(description=setup_complete))

        end = (datetime.datetime.now() + datetime.timedelta(seconds=time))
        end_string = end.strftime('%d.%m.%Y %H:%M')

        embed = self.create_giveaway_embed(hosted_by, prize)
        embed.description += f"\nReact with :tada: to enter!\nEnds <t:{int(end.timestamp())}:R>"
        embed.set_footer(text=f"Giveway ends on {end_string}")

        new_msg = await channel.send(embed=embed)
        stmt = '''INSERT INTO giveaway (discord_user_id, end_date, prize, discord_server_id, discord_channel_id, discord_message_id)
                  VALUES ($1, $2, $3, $4, $5, $6)'''
        await self.bot.db.execute(stmt, hosted_by.id, end, prize, ctx.guild.id, channel.id, new_msg.id)
        await new_msg.add_reaction("🎉")

    @giveaway.command(usage="reroll <message_id>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        message_id='The ID of the discord giveaway message you want to reroll.'
    )
    async def reroll(self, ctx: commands.Context, message_id: int):
        """
        Allows you to reroll a giveaway if something went wrong.
        """
        try:
            msg = await ctx.fetch_message(message_id)
        except Exception:
            await ctx.send("The message couldn't be found in this channel")
            return

        users = [user async for user in msg.reactions[0].users() if not user.bot]

        prize = await self.get_giveaway_prize(msg)
        giveaway_host = msg.embeds[0].fields[0].value
        embed = self.create_giveaway_embed(giveaway_host, prize)

        await self.pick_winner(msg.channel, users, prize, embed)
        await msg.edit(embed=embed)
        await ctx.message.delete()

    @giveaway.command(name='list',usage="list")
    async def giveaway_list(self, ctx: commands.Context):
        """
        Lists all active giveaways.
        """
        giveaways = await self.bot.db.fetch("SELECT * FROM giveaway WHERE discord_server_id = $1", ctx.guild.id)
        if len(giveaways) == 0:
            return await ctx.send("There are no active giveaways")

        embed = discord.Embed(title="Active Giveaways", description="")
        for giveaway in giveaways:
            embed.description += f"[{giveaway['prize']}](https://discord.com/channels/{giveaway['discord_server_id']}/{giveaway['discord_channel_id']}/{giveaway['discord_message_id']}) - Ends <t:{int(giveaway['end_date'].timestamp())}:R>\n"
        await ctx.send(embed=embed)

    @commands.command(name="giveawayInfo", hidden=True)
    @commands.is_owner()
    async def giveaway_info(self, ctx: commands.Context):
        """
        Shows information about he giveaway task.
        """
        if self.giveaway_task.time is None:
            times_string = "None"
        else:
            times_string = [f"{time}\n" for time in self.giveaway_task.time]
        embed = discord.Embed(title="Giveaway Task", description="")
        embed.add_field(name="Running", value=f"`{self.giveaway_task.is_running()}`", inline=False)
        embed.add_field(name="Current UTC time", value=f"`{datetime.datetime.now(datetime.timezone.utc)}`", inline=False)
        embed.add_field(name="Next iteration", value=f"`{self.giveaway_task.next_iteration}`", inline=False)
        embed.add_field(name="Last iteration", value=f"`{self.giveaway_task._last_iteration}`", inline=False)
        embed.add_field(name="Times", value=times_string, inline=False)
        await ctx.send(embed=embed)
        

    @giveaway.command(aliases=["cancel"], usage="stop <message_id>")
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    @app_commands.describe(
        message_id='The ID of the discord giveaway message you want to stop.'
    )
    async def stop(self, ctx: commands.Context, message_id: int):
        """
        Allows you to stop a giveaway. Takes the ID of the giveaway message as an argument.
        """
        # delete giveaway from db
        giveaway = await self.bot.db.execute('DELETE FROM giveaway WHERE discord_message_id = $1', message_id)
        if giveaway == 'DELETE 0':
            return await ctx.send("The message ID provided was wrong")
        msg = await ctx.fetch_message(message_id)
        new_embed = discord.Embed(title="Giveaway Cancelled", description="The giveaway has been cancelled!")
        await msg.edit(embed=new_embed)
        await ctx.send('Giveaway has been cancelled', delete_after=15)
        await ctx.message.delete()

    @tasks.loop(minutes=1)
    async def giveaway_task(self):
        giveaways = await self.bot.db.fetch('SELECT * FROM giveaway')
        for giveaway in giveaways:
            if datetime.datetime.now() < giveaway['end_date']:
                return
            channel = await self.bot.fetch_channel(giveaway['discord_channel_id'])
            try:
                message = await channel.fetch_message(giveaway['discord_message_id'])
            except discord.NotFound:
                await self.bot.db.execute('DELETE FROM giveaway WHERE id = $1', giveaway['id'])
                return await channel.send("Could not find the giveaway message! Deleting the giveaway.", delete_after=180)
            author_id = giveaway['discord_user_id']
            author = self.bot.get_user(author_id) or await self.bot.fetch_user(author_id)
            users = [user async for user in message.reactions[0].users() if not user.bot]
            prize = giveaway['prize']
            embed = self.create_giveaway_embed(author, prize)

            await self.pick_winner(users, channel, prize, embed)
            await message.edit(embed=embed)
            await self.bot.db.execute('DELETE FROM giveaway WHERE id = $1', giveaway['id'])

    async def pick_winner(self, users: list[discord.Member], channel: discord.TextChannel, prize: str, embed: discord.Embed):
        # Check if User list is not empty
        if len(users) <= 0:
            embed.remove_field(0)
            embed.set_footer(text="No one won the Giveaway")
            await channel.send('No one won the Giveaway')
        else:
            winner = SystemRandom.choice(users)
            embed.add_field(name=f"Congratulations on winning '{prize}'", value=winner.mention)
            await channel.send(f'Congratulations {winner.mention}! You won **{prize}**!')

    async def get_giveaway_prize(self, msg: discord.Message):
        return msg.embeds[0].description.split("Win **")[1].split("**!")[0]

    def convert(self, time):
        pos = ["m", "h", "d"]
        time_dict = {"m": 60, "h": 3600, "d": 24 * 3600}
        unit = time[-1]
        if unit not in pos:
            return -1
        try:
            time_val = int(time[:-1])
        except Exception:
            return -2
        return time_val * time_dict[unit]

    def create_giveaway_embed(self, author: discord.Member, prize):
        embed = discord.Embed(
            title=":tada: Giveaway :tada:",
            description=f"Win **{prize}**!",
            color=0x00FFFF
        )
        host = author.mention if isinstance(author, (discord.Member, discord.User)) else author
        embed.add_field(name="Hosted By:", value=host)
        return embed

    async def _cooldown_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandOnCooldown):
            embed = discord.Embed(
                title="Slow it down!",
                description=f"Try again in {error.retry_after:.2f}s.",
                color=discord.Colour.red(),
            )

            await ctx.send(embed=embed, delete_after=30)
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send('Missing the suggestion description', delete_after=30)
        await ctx.message.delete()

    @commands.cooldown(6, 5)
    @commands.hybrid_command(aliases=['av'])
    async def avatar(self, ctx: commands.Context, member: discord.Member | discord.User | None = None):
        """
        Enlarge and view your profile picture or another member
        """
        member = member or ctx.author
        current_avatar = member.display_avatar
        embed = discord.Embed(
            title=f"{str(member.display_name)}'s avatar",
            url=current_avatar.url,
            color=0x1E9FE3
        )
        embed.set_image(url=current_avatar.url)
        await ctx.send(embed=embed)

    @avatar.error
    async def avatar_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MemberNotFound):
            await ctx.send('Member not found', delete_after=30)

    @commands.group(aliases=['c'], invoke_without_command=True)
    @commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
    async def clear(self, ctx: commands.Context, amount: int = None):
        """
        Clears messages within the current channel.
        """
        if ctx.message.type == MessageType.reply:
            if message := ctx.message.reference.resolved:
                await message.delete()
                await ctx.message.delete()
            return
        if amount is None:
            return await ctx.send('Please specify the amount of messages to delete.', delete_after=15)

        if amount >= 100:
            return await ctx.send('Cannot delete more than 100 messages at a time!')
        await ctx.channel.purge(limit=amount + 1)

    @clear.command(aliases=['bot', 'b'])
    @commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
    async def clear_bot(self, ctx: commands.Context, amount: int):
        """Clears the bot's messages even in DMs"""
        bots_messages = [message async for message in ctx.channel.history(limit=amount + 1) if message.author == self.bot.user]

        if len(bots_messages) <= 100 and isinstance(ctx.channel, discord.TextChannel):
            await ctx.message.delete()
            await ctx.channel.delete_messages(bots_messages)

        elif isinstance(ctx.channel, discord.DMChannel):
            for message in bots_messages:
                await message.delete()
                await asyncio.sleep(0.75)

    @clear.error
    async def clear_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send('Please put an amount to clear.')

    @commands.command(aliases=['dink'])
    async def ping(self, ctx: commands.Context):
        """
        Shows the ping of the bot
        """
        title = 'Donk!' if 'dink' in ctx.message.content.lower() else 'Pong!'
        embed = discord.Embed(
            title=f'{title} 🏓',
            description=f'⏱️Ping: `{round(self.bot.latency*1000)}`ms',
            color=values.PRIMARY_COLOR
        )
        await ctx.message.delete()
        await ctx.send(embed=embed)

    @commands.command(name="specialThanks", hidden=True)
    async def special_thanks(self, ctx: commands.Context):
        peeople_who_helped = [
            "<@205704051856244736>", # @sprutz
            "<@299478604809764876>", # @thebadgod
            "<@291291715598286848>", # @joniiiiii
            "<@231151428167663616>", # @acurisu
            "<@153929916977643521>"  # @battlerush
        ]
        shuffle(peeople_who_helped)
        embed = discord.Embed(
            title="Special thanks for any help to those people",
            description=" ".join(peeople_who_helped),
            color=values.PRIMARY_COLOR,
        )

        await ctx.message.delete()
        await ctx.send(embed=embed, delete_after=120)

    @commands.command()
    async def info(self, ctx: commands.Context):
        """
        Shows different technical information about the bot
        """
        content = ''
        bot_uptime = time_up((discord.utils.utcnow() - self.bot.start_time).total_seconds())
        git_log_cmd = ['git', 'log', '-1', '--date=format:"%Y/%m/%d"', '--format=%ad']
        last_commit_date = subprocess.check_output(git_log_cmd).decode('utf-8').strip().strip('"')
        cpu_percent = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        ram_used = format_bytes((ram.total - ram.available))
        ram_percent = psutil.virtual_memory().percent
        bot_version = self.bot.version.get()
        proc = psutil.Process()

        with proc.oneshot():
            memory = proc.memory_full_info()
            content = f'**Instance uptime:** `{bot_uptime}`\n' \
                f'**Version:** `{bot_version}` | **Updated:** `{last_commit_date}`\n' \
                f'**Python:** `{platform.python_version()}` | **discord.py:** `{discord.__version__}`\n\n' \
                f'**CPU:** `{cpu_percent}%`\n' \
                f'**Process RAM:** `{format_bytes(memory.uss)}`\n' \
                f'**Total RAM:** `{ram_used} ({ram_percent}%)`\n\n' \
                f'**Made by:** <@{self.bot.owner_id}>'

        embed = discord.Embed(
            title=f'Info about {self.bot.user.display_name}',
            description=content,
            color=values.PRIMARY_COLOR,
            timestamp=datetime.datetime.now(timezone("Europe/Zurich"))
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
        await ctx.message.delete()


def time_up(seconds: int) -> str:
    if seconds <= 60:
        return "<1 minute"
    elif 3600 > seconds > 60:
        minutes = seconds // 60
        return f"{int(minutes)} minutes"
    hours = seconds // 3600  # Seconds divided by 3600 gives amount of hours
    minutes = (seconds % 3600) // 60  # The remaining seconds are looked at to see how many minutes they make up
    if hours >= 24:
        days = hours // 24
        hours = hours % 24
        return f"{int(days)} days, {int(hours)} hours, {int(minutes)} minutes"
    return f"{int(hours)} hours, {int(minutes)} minutes"


def format_bytes(size_in_bytes: int) -> str:
    units = ('B', 'KiB', 'MiB', 'GiB', 'TiB')
    power = int(math.log(max(abs(size_in_bytes), 1), 1024))
    return f"{size_in_bytes / (1024 ** power):.2f} {units[power]}"


async def setup(bot):
    await bot.add_cog(Util(bot))
