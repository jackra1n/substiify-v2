
import asyncio
import datetime
import logging
import random

import discord
import wavelink
from core import config
from core.bot import Substiify
from discord import Interaction
from discord.ext import commands

logger = logging.getLogger(__name__)

EMBED_COLOR = 0x292B3E


class Music(commands.Cog):

    COG_EMOJI = "🎵"

    def __init__(self, bot: Substiify):
        self.bot = bot

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply('Please provide a search query or URL.')
        if isinstance(error, MusicError):
            error.is_handled = True
            await ctx.send(error)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before: discord.VoiceState, after):
        if self.is_bot_last_vc_member(before.channel):
            player: wavelink.Player = before.channel.guild.voice_client
            if player is not None:
                await player.disconnect()

    def is_bot_last_vc_member(self, channel: discord.VoiceChannel):
        return channel and self.bot.user in channel.members and len(self.get_vc_users(channel)) == 0

    def get_vc_users(self, channel: discord.VoiceChannel):
        return [member for member in channel.members if not member.bot]

    async def cog_before_invoke(self, ctx: commands.Context):
        """ Command before-invoke handler. """
        guild_check = ctx.guild is not None

        if guild_check:
            await self.ensure_voice(ctx)

        return guild_check

    async def ensure_voice(self, ctx: commands.Context):
        """ This check ensures that the bot and command author are in the same voicechannel. """
        if wavelink.Pool.nodes is None:
            raise NoNodeAccessible()

        if ctx.command.name in ['players', 'cleanup']:
            return True

        player: wavelink.Player = ctx.voice_client
        if ctx.command.name in ['show', 'now']:
            if player is None:
                raise NoPlayerFound()
            return True

        if not ctx.author.voice or not ctx.author.voice.channel:
            raise NoVoiceChannel()

        should_connect = ctx.command.name in ['play']
        if not player:
            if not should_connect:
                raise NoPlayerFound()

            permissions = ctx.author.voice.channel.permissions_for(ctx.me)
            if not permissions.connect or not permissions.speak:
                raise NoPermissions()

            await ctx.author.voice.channel.connect(cls=wavelink.Player)
            return True

        if player.channel != ctx.author.voice.channel:
            raise DifferentVoiceChannel()

    @commands.hybrid_command(aliases=['p'], usage='play <url/query>')
    async def play(self, ctx: commands.Context, *, search: str):
        """ Plays or queues a song/playlist. Can be a YouTube, Spotify, Soundcloud link or a search query.

        Examples:
        `<<play All girls are the same Juice WRLD` - searches for a song and queues it
        `<<play https://www.youtube.com/watch?v=dQw4w9WgXcQ` - plays a YouTube video
        """
        player: wavelink.Player = ctx.voice_client
        player.autoplay = wavelink.AutoPlayMode.partial

        search = search.strip('<>')
        if not ctx.interaction:
            await ctx.message.delete()

        tracks: wavelink.Search = await wavelink.Playable.search(search)
        if not tracks:
            raise NoTracksFound()
    
        stmt_cleanup = "SELECT music_cleanup FROM discord_server WHERE discord_server_id = $1"
        music_cleanup = await self.bot.db.fetchval(stmt_cleanup, ctx.guild.id)
        delete_after = 60 if music_cleanup else None

        queued_songs_count = await player.queue.put_wait(tracks)
        embed = discord.Embed(color=EMBED_COLOR)
        embed.title = f'Songs Queued ({queued_songs_count})'

        if not player.playing:
            track = await player.play(player.queue.get())
            embed.description = f'[{track.title}]({track.uri})'
        await ctx.send(embed=embed, delete_after=delete_after)

    @commands.hybrid_command(name="loop")
    async def _loop(self, ctx: commands.Context):
        """ Loops the current song. """
        player: wavelink.Player = ctx.voice_client
        if player.queue.mode != wavelink.QueueMode.loop:
            player.queue.mode = wavelink.QueueMode.loop
        else:
            player.queue.mode = wavelink.QueueMode.normal
        looping = player.queue.mode == wavelink.QueueMode.loop

        embed_color = discord.Color.green() if looping else discord.Color.red()
        embed = discord.Embed(color=embed_color)
        embed.title = '🔁 Looping' if looping else '⏭️ Not looping'
        embed.description = 'Now looping the current song.' if looping else 'Stopped looping.'
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['disconnect', 'stop'])
    async def leave(self, ctx: commands.Context):
        """
        Disconnects the player from the voice channel and clears its queue.
        """
        player: wavelink.Player = ctx.voice_client

        if player is None:
            raise NoPlayerFound()

        if ctx.author.voice.channel != player.channel:
            raise DifferentVoiceChannel()

        await player.stop()
        player.queue.clear()
        await player.disconnect()

        embed = discord.Embed(title='*⃣ | Disconnected', color=EMBED_COLOR)
        await ctx.send(embed=embed, delete_after=30)

    @commands.hybrid_command()
    async def skip(self, ctx: commands.Context):
        """
        Skips the current track. If there no more tracks in the queue, disconnects the player.
        """
        player: wavelink.Player = ctx.voice_client

        if not player.playing:
            return await ctx.send('Not playing anything currently.', delete_after=15)

        old_song = f'Skipped: [{player.current.title}]({player.current.uri})'
        await player.stop()
        if len(player.queue) < 1:
            old_song += '\n*⃣ | Queue is empty.'
        embed = discord.Embed(color=EMBED_COLOR, title='⏭ | Skipped.', description=old_song)
        embed.set_footer(text=f'Requested by {ctx.author}', icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed, delete_after=30)

    @commands.hybrid_command(name="now", aliases=['np', 'current'])
    async def now_playing(self, ctx: commands.Context):
        """
        Shows info about the currently playing track.
        """
        player: wavelink.Player = ctx.voice_client
        if not ctx.interaction:
            await ctx.message.delete()

        if player is None:
            raise NoPlayerFound()

        if not player.playing:
            return await ctx.send('Nothing playing.', delete_after=10)

        embed = self._create_current_song_embed(player)
        await ctx.send(embed=embed, delete_after=60)

    @commands.hybrid_command()
    async def shuffle(self, ctx: commands.Context):
        """
        Randomly shuffles the queue.
        """
        player: wavelink.Player = ctx.voice_client

        if len(player.queue) < 2:
            return await ctx.reply('Not enough tracks to shuffle.', delete_after=15)

        random.shuffle(player.queue._queue)
        embed = discord.Embed(color=EMBED_COLOR, title='🔀 | Queue shuffled.')
        await ctx.send(embed=embed, delete_after=15)
        if not ctx.interaction:
            await ctx.message.delete()

    @commands.hybrid_group()
    async def queue(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(color=EMBED_COLOR)
            embed.description = "You probably want to call `<<queue show`.\nUsing a slash command for that might be even handier."

    @queue.command(name='show')
    async def _show(self, ctx: commands.Context):
        """
        Shows the queue in a paginated menu.
        """
        player: wavelink.Player = ctx.voice_client
        if not ctx.interaction:
            await ctx.message.delete()
        if len(player.queue) < 1:
            embed = self._create_current_song_embed(player) if player.current else None
            await ctx.send('Nothing queued.', embed=embed, delete_after=120)
            return

        queue_pages = self._create_queue_embed_list(ctx)
        view = PaginatorView(ctx.author, queue_pages) if len(queue_pages) > 1 else None
        await ctx.send(embed=queue_pages[0], delete_after=120, view=view)

    @commands.is_owner()
    @commands.command(hidden=True)
    async def players(self, ctx: commands.Context):
        """
        Shows all active players. Mostly used to check before deploying a new version.
        """
        players = wavelink.Pool.get_node().players
        if not ctx.interaction:
            await ctx.message.delete()
        if not players:
            embed = discord.Embed(color=EMBED_COLOR, title='*⃣ | No active players found.')
            return await ctx.send(embed=embed, delete_after=15)

        # get server names by id
        server_names = [f'{player.guild.name}, queued: `{len(player.queue)}`, {"`playing`" if player.playing else "`not playing`"}' for _, player in players.items()]

        embed = discord.Embed(color=EMBED_COLOR)
        embed.title = 'Active players'
        embed.description = '\n'.join(f'{server_name}' for server_name in server_names)
        await ctx.send(embed=embed, delete_after=60)

    @commands.hybrid_command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def cleanup(self, ctx: commands.Context, enable: bool = None):
        """
        Enables/disables the auto-cleanup of the music queue messages that appear after queueing a new song.
        """
        if enable is not None:
            stmt_cleanup = 'UPDATE discord_server SET music_cleanup = $1 WHERE discord_server.discord_server_id = $2'
            await self.bot.db.execute(stmt_cleanup, enable, ctx.guild.id)

        embed = self.create_song_cleanup_embed(ctx, enable)
        await ctx.send(embed=embed)

    def create_song_cleanup_embed(self, ctx: commands.Context, enable: bool):
        embed = discord.Embed(color=discord.Color.red())
        status_string = '`disabled` <:redCross:876177262813278288>'
        if enable:
            embed = discord.Embed(color=discord.Color.green())
            status_string = '`enabled` <:greenTick:876177251832590348>'
        embed.title = 'Cleanup status'
        embed.description = f'Song messages auto-cleanup is {status_string}.'
        embed.set_footer(text=f'Use `{ctx.prefix}cleanup <enable/disable>` to toggle.')
        return embed

    def _create_current_song_embed(self, player: wavelink.Player):
        embed = discord.Embed(color=EMBED_COLOR)
        embed.title = f'Now Playing (Looping: {player.queue.mode.name})'
        embed.description = f'[{player.current.title}]({player.current.uri})'
        if not player.current.is_stream:
            song_timestamp = player.position if player.position > 0 else player.last_position
            position = str(datetime.timedelta(milliseconds=song_timestamp)).split(".")[0]
            song_length = str(datetime.timedelta(milliseconds=player.current.duration)).split(".")[0]
            embed.add_field(name='Duration', value=f"{position}/{song_length}")
        else:
            embed.add_field(name='Duration', value='LIVE 🔴')
        requester = 'Unknown'
        if hasattr(player.current, 'requester'):
            requester = player.current.requester.mention
        embed.add_field(name='Queued By', value=requester)
        return embed

    def _create_queue_embed_list(self, ctx: commands.Context):
        player: wavelink.Player = ctx.voice_client
        songs_array = []
        for song in player.queue:
            try:
                songs_array.append(song)
            except IndexError:
                break

        pages = []
        for i in range(0, len(player.queue), 10):
            embed = discord.Embed(color=EMBED_COLOR, timestamp=datetime.datetime.now(datetime.timezone.utc))

            embed.title = f"Queue ({len(player.queue)})"
            embed.add_field(name='Now Playing', value=f'[{player.current.title}]({player.current.uri})')
            embed.set_footer(text=f"Queued by {ctx.author}", icon_url=ctx.author.display_avatar.url)

            upcoming = '\n'.join([f'`{index + 1}.` {track.title}' for index, track in enumerate(songs_array[i:i + 10], start=i)])
            embed.add_field(name="Next up", value=upcoming, inline=False)
            pages.append(embed)
        return pages


class PaginatorView(discord.ui.View):
    def __init__(self, author: discord.Member | discord.User, pages: list[discord.Embed]):
        super().__init__()
        self.current_page = 0
        self.author = author
        self.pages = pages

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(f':warn: {interaction.user.mention} **You aren\'t the author of this embed**')
            return False
        return True

    @discord.ui.button(emoji='⏮', style=discord.ButtonStyle.primary)
    async def previous(self, interaction: Interaction, button: discord.ui.Button):
        self.current_page = max(self.current_page - 1, 0)
        button.disabled = self.current_page == 0
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
        button.disabled = False

    @discord.ui.button(emoji='❌', style=discord.ButtonStyle.grey)
    async def close(self, interaction: Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.message.delete()

    @discord.ui.button(emoji='⏭', style=discord.ButtonStyle.primary)
    async def next(self, interaction: Interaction, button: discord.ui.Button):
        self.current_page = min(self.current_page + 1, len(self.pages) - 1)
        button.disabled = self.current_page == len(self.pages) - 1
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
        button.disabled = False


class MusicError(commands.CommandError):
    pass


class NoVoiceChannel(MusicError):
    def __init__(self):
        super().__init__('You are not in a voice channel.')


class NoPermissions(MusicError):
    def __init__(self):
        super().__init__('I do not have the permissions to join your voice channel.')


class NoPlayerFound(MusicError):
    def __init__(self):
        super().__init__('No active player found.')


class NoTracksFound(MusicError):
    def __init__(self):
        super().__init__('Could not find any tracks with that query. Please try again.')


class DifferentVoiceChannel(MusicError):
    def __init__(self):
        super().__init__('You are not in the same voice channel as the bot.')


class NoNodeAccessible(MusicError):
    def __init__(self):
        super().__init__('No playing agent is available at the moment. Please try again later or contact support.')


async def setup(bot):
    if all([config.LAVALINK_NODE_URL, config.LAVALINK_PASSWORD]):
        await bot.add_cog(Music(bot))
    else:
        logger.warning("Lavalink is not configured. Skipping Music cog.")
