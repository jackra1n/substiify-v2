import json
import logging
import platform
import subprocess
from datetime import datetime
from random import shuffle

import nextcord
import psutil
from nextcord import MessageType
from nextcord.ext import commands
from pytz import timezone
from sqlalchemy import func

from utils import db, store

logger = logging.getLogger(__name__)

class Util(commands.Cog):

    COG_EMOJI = "📦"

    def __init__(self, bot):
        self.bot = bot
        with open(store.SETTINGS_PATH, "r") as settings:
            self.settings = json.load(settings)

    @commands.cooldown(6, 5)
    @commands.command(aliases=['avatar'])
    async def av(self, ctx, member: nextcord.Member = None):
        """
        Enlarge and view your profile picture or another member
        """
        await ctx.message.delete()
        member = ctx.author if member is None else member
        embed = nextcord.Embed(
            title=f"{str(member.display_name)}'s avatar",
            url=member.avatar.url,
            colour=nextcord.Colour.light_grey()
        )
        embed.set_image(url=member.avatar.url)
        await ctx.channel.send(embed=embed)

    @commands.group(aliases=['c'], invoke_without_command = True)
    @commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
    async def clear(self, ctx, amount: int):
        """
        Clears messages within the current channel.
        """
        if ctx.message.type == MessageType.reply:
            message = ctx.message.reference.resolved
            if message:
                await message.delete()
                await ctx.message.delete()
            return
        if amount >= 100:
            return await ctx.channel.send('Cannot delete more than 100 messages at a time!')
        await ctx.channel.purge(limit=amount + 1)

    @clear.command(aliases=['bot', 'b'])
    @commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
    async def clear_bot(self, ctx, amount: int):
        """Clears the bot's messages"""
        if amount >= 100:
            return await ctx.channel.send('Cannot delete more than 100 messages at a time!')
        def check(message):
            return message.author == self.bot.user
        await ctx.channel.purge(limit=amount + 1, check=check, bulk=False)

    @clear.error
    async def clear_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.channel.send('Please put an amount to clear.')

    @commands.command(aliases=['dink'])
    async def ping(self, ctx):
        """
        Shows the ping of the bot
        """
        title = 'Pong!'
        if 'dink' in ctx.message.content.lower():
            title = 'Donk!'
        embed = nextcord.Embed(title=f'{title} 🏓', description=f'⏱️Ping:`{round(self.bot.latency*1000)}` ms')
        await ctx.message.delete()
        await ctx.send(embed=embed)

    @commands.command(hidden=True)
    async def specialThanks(self, ctx):
        peeople_who_helped = ["<@205704051856244736>", "<@812414532563501077>", "<@299478604809764876>", "<@291291715598286848>", "<@224618877626089483>", "<@231151428167663616>"]
        shuffle(peeople_who_helped)
        embed = nextcord.Embed(
            title="Special thanks for any help to those people",
            description = f" ".join(peeople_who_helped)
        )
        await ctx.message.delete()
        await ctx.channel.send(embed=embed, delete_after=120)

    @commands.command()
    async def info(self, ctx):
        """
        Shows different technical information about the bot
        """
        bot_time = time_up((datetime.now() - store.SCRIPT_START).total_seconds()) #uptime of the bot
        last_commit_date = subprocess.check_output(['git', 'log', '-1', '--date=format:"%Y/%m/%d"', '--format=%ad']).decode('utf-8').strip().strip('"')
        cpu_percent = psutil.cpu_percent()
        ram = psutil.virtual_memory()
        ram_used = format_bytes((ram.total - ram.available))
        ram_percent = psutil.virtual_memory().percent
        with open(store.SETTINGS_PATH, "r") as settings:
            self.settings = json.load(settings)

        content = f'**Instance uptime:** `{bot_time}`\n' \
            f'**Version:** `{self.settings["version"]}` | **Updated:** `{last_commit_date}`\n' \
            f'**Python:** `{platform.python_version()}` | **{nextcord.__name__}:** `{nextcord.__version__}`\n\n' \
            f'**CPU:** `{cpu_percent}%` | **RAM:** `{ram_used} ({ram_percent}%)`\n\n' \
            f'**Made by:** <@{self.bot.owner_id}>' 

        embed = nextcord.Embed(
            title=f'Info about {self.bot.user.display_name}',
            description=content, colour=nextcord.Colour(0xc44c27),
            timestamp=datetime.now(timezone("Europe/Zurich"))
        )
        embed.set_thumbnail(url=self.bot.user.avatar.url)
        embed.set_footer(text=f"Requested by by {ctx.author.display_name}")
        await ctx.channel.send(embed=embed)
        await ctx.message.delete()

    @commands.group(name="usage", invoke_without_command=True)
    async def usage(self, ctx):
        """
        Shows most used commands on the server
        """
        commands_used_query = db.session.query(db.command_history.command, func.count('*')).filter_by(server_id=ctx.guild.id).group_by(db.command_history.command).order_by(func.count('*').desc()).all()
        embed = create_command_usage_embed(commands_used_query, f"Top used commands on: **{ctx.guild.name}**")
        await ctx.send(embed=embed, delete_after=180)

    @usage.command(name="all")
    async def usage_all(self, ctx): 
        """
        Shows most used commands on all servers
        """
        commands_used_query = db.session.query(db.command_history.command, func.count('*')).group_by(db.command_history.command).order_by(func.count('*').desc()).all()
        embed = create_command_usage_embed(commands_used_query, f"Top total used commands")
        await ctx.send(embed=embed, delete_after=180)

    @usage.command(name="servers")
    async def usage_servers(self, ctx):
        """
        Shows servers where the bot is used the most
        """
        commands_used_query = db.session.query(db.command_history.command, func.count('*')).group_by(db.command_history.server_id).order_by(func.count('*').desc()).all()
        embed = create_command_usage_embed(commands_used_query, f"Top servers used commands")
        await ctx.send(embed=embed, delete_after=180)

def setup(bot):
    bot.add_cog(Util(bot))

def create_command_usage_embed(commands_used_query, embed_title):
    commands_used = ""
    commands_count = ""
    for row in commands_used_query:
        commands_used += f"{row[0]}\n"
        commands_count += f"{row[1]}\n"
    embed = nextcord.Embed(title=embed_title, color=0x00ff00)
    embed.add_field(name="Command", value=commands_used, inline=True)
    embed.add_field(name="Count", value=commands_count, inline=True)
    return embed

def time_up(t):
    if t <= 60:
        return f"<1 minute"
    elif 3600 > t > 60:
        minutes = t // 60
        return f"{int(minutes)} minutes"
    elif t >= 3600:
        hours = t // 3600  # Seconds divided by 3600 gives amount of hours
        minutes = (t % 3600) // 60  # The remaining seconds are looked at to see how many minutes they make up
        if hours >= 24:
            days = hours // 24
            hours = hours % 24
            return f"{int(days)} days, {int(hours)} hours, {int(minutes)} minutes"
        return f"{int(hours)} hours, {int(minutes)} minutes"

def format_bytes(size: int) -> str:
    # 2**10 = 1024
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power:
        size /= power
        n += 1
    return f'{round(size, 2)}{power_labels[n]}'
