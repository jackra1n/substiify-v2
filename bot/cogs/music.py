
import asyncio
import datetime
import logging
import random
import re

import discord
import wavelink
from core import config
from core.bot import Substiify
from discord import Interaction
from discord.ext import commands
from wavelink import Player
from wavelink.ext import spotify

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
            player: Player = before.channel.guild.voice_client
            if player is not None:
                await player.disconnect()

    def is_bot_last_vc_member(self, channel: discord.VoiceChannel):
        return channel and self.bot.user in channel.members and len(self.get_vc_users(channel)) == 0

    def get_vc_users(self, channel: discord.VoiceChannel):
        return [member for member in channel.members if not member.bot]

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEventPayload):
        """Event fired when a track ends."""
        player = payload.player
        if player.queue.loop:
            track = await player.play(payload.track)
        elif not player.queue.is_empty:
            partial = await player.queue.get_wait()
            track = await player.play(partial)
            track.requester = partial.requester
        else:
            await player.stop()

        def check(payload: wavelink.TrackEventPayload):
            return payload.player.guild == player.guild
        try:
            await self.bot.wait_for("wavelink_track_start", check=check, timeout=300)
        except asyncio.TimeoutError:
            await player.disconnect()

    async def cog_before_invoke(self, ctx):
        """ Command before-invoke handler. """
        guild_check = ctx.guild is not None

        if guild_check:
            await self.ensure_voice(ctx)

        return guild_check

    async def ensure_voice(self, ctx):
        """ This check ensures that the bot and command author are in the same voicechannel. """
        if wavelink.NodePool.nodes is None:
            raise NoNodeAccessible()

        if ctx.command.name in ['players', 'cleanup']:
            return True

        player = ctx.voice_client
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

            await ctx.author.voice.channel.connect(cls=Player)
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
        player: Player = ctx.voice_client
        tracks = []

        search = search.strip('<>')
        if not ctx.interaction:
            await ctx.message.delete()

        if "open.spotify" in search:
            tracks = await spotify.SpotifyTrack.search(search)
            if not tracks:
                return await ctx.reply("This Spotify URL is not usable.", ephemeral=True)

        elif "soundcloud.com" in search and not "sets/" in search:
            tracks = await player.current_node.get_tracks(query=search, cls=wavelink.SoundCloudTrack)

        else: 
            tracks = await wavelink.Playable.search(search)

        if not tracks:
            raise NoTracksFound()
    
        stmt_cleanup = "SELECT music_cleanup FROM discord_server WHERE discord_server_id = $1"
        music_cleanup = await self.bot.db.fetchval(stmt_cleanup, ctx.guild.id)
        delete_after = 60 if music_cleanup else None

        embed = await self._queue_songs(tracks, player, ctx.author)
        await ctx.send(embed=embed, delete_after=delete_after)

    async def _queue_songs(self, songs, player: wavelink.Player, requester):
        embed = discord.Embed(color=EMBED_COLOR)
        if isinstance(songs, wavelink.Playlist):
            for track in songs.tracks:
                track.requester = requester
                player.queue.put(track)
            embed.title = 'Playlist Queued'
            embed.description = f'{songs.name} - {len(songs.tracks)} tracks'
        else:
            for song in songs:
                song.requester = requester
                player.queue.put(song)
            embed.title = 'Song Queued' if len(songs) == 1 else f'Songs Queued ({len(songs)})'
        if not player.is_playing():
            track = await player.play(await player.queue.get_wait())
            track.requester = requester
            embed.description = f'[{track.title}]({track.uri})'
        return embed

    @commands.hybrid_command(name="loop")
    async def _loop(self, ctx):
        """ Loops the current song. """
        player: Player = ctx.voice_client
        looping = not player.queue.loop
        player.queue.loop = looping
        embed_color = discord.Color.green() if looping else discord.Color.red()
        embed = discord.Embed(color=embed_color)
        embed.title = '🔁 Looping' if looping else '⏭️ Not looping'
        embed.description = 'Now looping the current song.' if looping else 'Stopped looping.'
        await ctx.send(embed=embed)

    @commands.hybrid_command(aliases=['disconnect', 'stop'])
    async def leave(self, ctx):
        """
        Disconnects the player from the voice channel and clears its queue.
        """
        player: Player = ctx.voice_client

        if player is None:
            raise NoPlayerFound()

        if ctx.author.voice.channel != player.channel:
            raise DifferentVoiceChannel()

        await player.stop()
        player.queue.reset()
        await player.disconnect()

        embed = discord.Embed(title='*⃣ | Disconnected', color=EMBED_COLOR)
        await ctx.send(embed=embed, delete_after=30)
        if not ctx.interaction:
            await ctx.message.delete()

    @commands.hybrid_command()
    async def skip(self, ctx):
        """
        Skips the current track. If there no more tracks in the queue, disconnects the player.
        """
        player: Player = ctx.voice_client

        if not player.is_playing():
            return await ctx.send('Not playing anything currently.', delete_after=15)

        old_song = f'Skipped: [{player.current.title}]({player.current.uri})'
        await player.stop()
        if player.queue.is_empty:
            old_song += '\n*⃣ | Queue is empty.'
        embed = discord.Embed(color=EMBED_COLOR, title='⏭ | Skipped.', description=old_song)
        embed.set_footer(text=f'Requested by {ctx.author}', icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed, delete_after=30)
        if not ctx.interaction:
            await ctx.message.delete()

    @commands.hybrid_command(name="now", aliases=['np', 'current'])
    async def now_playing(self, ctx):
        """
        Shows info about the currently playing track.
        """
        player: Player = ctx.voice_client
        if not ctx.interaction:
            await ctx.message.delete()

        if player is None:
            raise NoPlayerFound()

        if not player.is_playing():
            return await ctx.send('Nothing playing.', delete_after=10)

        embed = self._create_current_song_embed(player)
        await ctx.send(embed=embed, delete_after=60)

    @commands.hybrid_command()
    async def shuffle(self, ctx):
        """
        Randomly shuffles the queue.
        """
        player: Player = ctx.voice_client

        if len(player.queue) < 2:
            return await ctx.reply('Not enough tracks to shuffle.', delete_after=15)

        random.shuffle(player.queue._queue)
        embed = discord.Embed(color=EMBED_COLOR, title='🔀 | Queue shuffled.')
        await ctx.send(embed=embed, delete_after=15)
        if not ctx.interaction:
            await ctx.message.delete()

    @commands.hybrid_group()
    async def queue(self, ctx):
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(color=EMBED_COLOR)
            embed.description = "You probably want to call `<<queue show`.\nUsing a slash command for that might be even handier."

    @queue.command(name='show')
    async def _show(self, ctx):
        """
        Shows the queue in a paginated menu.
        """
        player: Player = ctx.voice_client
        if not ctx.interaction:
            await ctx.message.delete()
        if player.queue.count < 1:
            embed = self._create_current_song_embed(player) if player.current else None
            await ctx.send('Nothing queued.', embed=embed, delete_after=120)
            return

        queue_pages = self._create_queue_embed_list(ctx)
        view = PaginatorView(ctx.author, queue_pages) if len(queue_pages) > 1 else None
        await ctx.send(embed=queue_pages[0], delete_after=120, view=view)

    @queue.command(name='move')
    async def queue_move(self, ctx, from_index: int, to_index: int):
        """
        Moves a track from one position in the queue to another.
        """
        player: Player = ctx.voice_client
        if not ctx.interaction:
            await ctx.message.delete()
        if not player.queue:
            embed = discord.Embed(color=EMBED_COLOR, title='*⃣ | Queue is empty.')
            return await ctx.send(embed=embed, delete_after=15)

        song_to_move = player.queue._queue[from_index - 1]
        player.queue.put_at_index(to_index - 1, song_to_move)
        del player.queue._queue[from_index]
        embed = discord.Embed(color=EMBED_COLOR, title='*⃣ | Queue moved.')
        embed.description = f'Moved `{song_to_move.title}` from position {from_index} to {to_index}.'
        await ctx.send(embed=embed, delete_after=15)

    @queue.command(name='clear')
    async def queue_clear(self, ctx):
        """
        Clears the queue.
        """
        player: Player = ctx.voice_client
        player.queue.clear()
        embed = discord.Embed(color=EMBED_COLOR, title='*⃣ | Queue cleared.')
        await ctx.send(embed=embed, delete_after=30)
        if not ctx.interaction:
            await ctx.message.delete()

    @commands.is_owner()
    @commands.command(hidden=True)
    async def players(self, ctx):
        """
        Shows all active players. Mostly used to check before deploying a new version.
        """
        players = wavelink.NodePool.get_node().players
        if not ctx.interaction:
            await ctx.message.delete()
        if not players:
            embed = discord.Embed(color=EMBED_COLOR, title='*⃣ | No active players found.')
            return await ctx.send(embed=embed, delete_after=15)

        # get server names by id
        server_names = [f'{player.guild.name}, queued: `{player.queue.count}`, {"`playing`" if player.is_playing() else "`not playing`"}' for _, player in players.items()]

        embed = discord.Embed(color=EMBED_COLOR)
        embed.title = 'Active players'
        embed.description = '\n'.join(f'{server_name}' for server_name in server_names)
        await ctx.send(embed=embed, delete_after=60)

    @commands.hybrid_command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def cleanup(self, ctx, enable: bool = None):
        """
        Enables/disables the auto-cleanup of the music queue messages that appear after queueing a new song.
        """
        if enable is not None:
            await self.bot.db.update_server_music_cleanup(ctx.guild.id, enable)

        embed = self.create_song_cleanup_embed(ctx, enable)
        await ctx.send(embed=embed)
        if not ctx.interaction:
            await ctx.message.delete()

    def create_song_cleanup_embed(self, ctx, enable):
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
        embed.title = 'Now Playing'
        if player.queue.loop:
            embed.title += ' | (Looping)'
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

    def _create_queue_embed_list(self, ctx):
        player: Player = ctx.voice_client
        songs_array = []
        for song in player.queue:
            try:
                songs_array.append(song)
            except IndexError:
                break

        pages = []
        for i in range(0, player.queue.count, 10):
            embed = discord.Embed(color=EMBED_COLOR, timestamp=datetime.datetime.now(datetime.timezone.utc))

            embed.title = f"Queue ({player.queue.count})"
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
        super().__init__('No tracks were found.')


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
