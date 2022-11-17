
import asyncio
import datetime
import logging
import random
import re

import discord
import wavelink
from core import config
from discord import Interaction
from discord.ext import commands
from utils import db
from wavelink import Player, Track
from wavelink.ext import spotify

logger = logging.getLogger(__name__)

URL = re.compile(r'https?://(?:www\.)?.+')
EMBED_COLOR = 0x292B3E


class Music(commands.Cog):

    COG_EMOJI = "🎵"

    def __init__(self, bot):
        self.bot = bot
        bot.loop.create_task(self.connect_nodes())

    async def connect_nodes(self):
        await self.bot.wait_until_ready()
        if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
            logger.warning("Spotify client id or secret not found. Spotify support disabled.")
            spotify_client = None
        else:
            spotify_client = spotify.SpotifyClient(client_id=config.SPOTIFY_CLIENT_ID, client_secret=config.SPOTIFY_CLIENT_SECRET)
        await wavelink.NodePool.create_node(bot=self.bot, host=config.LAVALINK_HOST, port=config.LAVALINK_PORT, password=config.LAVALINK_PASSWORD, spotify_client=spotify_client)

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply('Please provide a search query or URL.')
        if isinstance(error, MusicError):
            error.is_handled = True
            await ctx.send(error)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member == self.bot.user and after.channel is None:
            player = wavelink.NodePool.get_node().get_player(before.channel.guild)
            if player is not None:
                await player.disconnect()

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, node: wavelink.Node):
        """Event fired when a node has finished connecting."""
        logger.info(f'Node: <{node.identifier}> is ready!')

    @commands.Cog.listener()
    async def on_wavelink_websocket_closed(self, player: wavelink.Player, reason: str, code: int):
        """Event fired when a node's websocket connection is closed."""
        logger.warn(f'Websocket closed! Guild: {player.guild} | Reason: {reason} | Code: {code}')

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, player: Player, track: Track, reason):
        """Event fired when a track ends."""
        if hasattr(player, 'loop') and player.loop:
            return await player.play(track)
        if not player.queue.is_empty:
            partial = await player.queue.get_wait()
            track = await player.play(partial)
            track.requester = partial.requester
        else:
            await player.stop()

        def check(p: wavelink.Player):
            return p.guild == player.guild
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

        if ctx.command.name in ['queue', 'now']:
            if player is None:
                raise NoPlayerFound()
            return True

        should_connect = ctx.command.name in ['play']

        if not ctx.author.voice or not ctx.author.voice.channel:
            raise NoVoiceChannel()

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

    @commands.command(aliases=['p'], usage='play <url/query>')
    async def play(self, ctx, *, search: str):
        """ Plays or queues a song/playlist. Can be a YouTube URL, Soundcloud URL or a search query.

        Examples:
        `<<play All girls are the same Juice WRLD` - searches for a song and queues it
        `<<play https://www.youtube.com/watch?v=dQw4w9WgXcQ` - plays a YouTube video
        """
        player: Player = ctx.voice_client

        embed = None
        tracks = None

        search = search.strip('<>')
        await ctx.message.delete()

        # Check spotify
        if (decoded := spotify.decode_url(search)) is not None:
            if decoded["type"] is spotify.SpotifySearchType.unusable:
                return await ctx.reply("This Spotify URL is not usable.", ephemeral=True)
            embed = await self._queue_spotify(decoded, player, ctx.author)

        elif URL.match(search):
            if 'list=' in search:
                tracks = await player.node.get_playlist(wavelink.YouTubePlaylist, search)
            else:
                tracks = await player.node.get_tracks(wavelink.Track, search)

        else:
            try:
                track = await wavelink.YouTubeTrack.search(query=search, return_first=True)
                tracks = [track]
            except IndexError as e:
                raise NoTracksFound() from e

        if embed is None and tracks is None:
            raise NoTracksFound()
        if embed is None:
            embed = await self._queue_songs(tracks, player, ctx.author)

        server = db.get_discord_server(ctx.guild)
        delete_after = 60 if server.music_cleanup else None
        await ctx.send(embed=embed, delete_after=delete_after)

    async def _queue_spotify(self, decoded, player, requester):
        msg = ''
        embed = discord.Embed(color=EMBED_COLOR)
        if decoded["type"] in (spotify.SpotifySearchType.playlist, spotify.SpotifySearchType.album):
            tracks_count = 0
            async for track in spotify.SpotifyTrack.iterator(query=decoded["id"], partial_tracks=True, type=decoded["type"]):
                track.requester = requester
                player.queue.put(track)
                tracks_count += 1
            embed.title = 'Playlist Queued'
            msg = f'{tracks_count} tracks'
        else:
            track = await spotify.SpotifyTrack.search(query=decoded["id"], return_first=True)
            track.requester = requester
            player.queue.put(track)
            embed.title = 'Song Queued'
            msg = f'Now playing: [{track.title}]({track.uri})'

        if not player.is_playing():
            track = await player.play(await player.queue.get_wait())
            track.requester = requester
        embed.description = msg
        return embed

    async def _queue_songs(self, songs, player, requester):
        embed = discord.Embed(color=EMBED_COLOR)
        if isinstance(songs, wavelink.YouTubePlaylist):
            for track in songs.tracks:
                track.requester = requester
                player.queue.put(track)
            embed.title = 'Playlist Queued'
            embed.description = f'{songs.name} - {len(songs.tracks)} tracks'
        else:
            song = songs[0]
            song.requester = requester
            player.queue.put(song)
            embed.title = 'Song Queued'
            embed.description = f'[{song.title}]({song.uri})'
        if not player.is_playing():
            track = await player.play(await player.queue.get_wait())
            track.requester = requester
        return embed

    @commands.hybrid_command(name="loop")
    async def _loop(self, ctx):
        """ Loops the current song. """
        player: Player = ctx.voice_client
        if hasattr(player, 'loop'):
            player.loop = not player.loop
        else:
            player.loop = True
        embed_color = discord.Color.green() if player.loop else discord.Color.red()
        embed = discord.Embed(color=embed_color)
        embed.title = '🔁 Looping' if player.loop else '⏭️ Not looping'
        embed.description = 'Now looping the current song.' if player.loop else 'Stopped looping.'
        await ctx.send(embed=embed)

    @commands.command(aliases=['disconnect', 'stop'])
    async def leave(self, ctx):
        """
        Disconnects the player from the voice channel and clears its queue.
        """
        player: Player = ctx.voice_client

        if not player.is_connected():
            raise NoPlayerFound()

        if ctx.author.voice.channel != player.channel:
            raise DifferentVoiceChannel()

        await player.stop()
        player.queue.reset()
        await player.disconnect()

        embed = discord.Embed(title='*⃣ | Disconnected', color=EMBED_COLOR)
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @commands.command()
    async def skip(self, ctx):
        """
        Skips the current track. If there no more tracks in the queue, disconnects the player.
        """
        player: Player = ctx.voice_client

        if not player.is_playing():
            return await ctx.send('Not playing anything currently.', delete_after=15)

        old_song = f'Skipped: [{player.track.title}]({player.track.uri})'
        await player.stop()
        if player.queue.is_empty:
            old_song += '\n*⃣ | Queue is empty.'
        embed = discord.Embed(color=EMBED_COLOR, title='⏭ | Skipped.', description=old_song)
        embed.set_footer(text=f'Requested by {ctx.author}', icon_url=ctx.author.avatar)
        await ctx.send(embed=embed, delete_after=30)
        await ctx.message.delete()

    @commands.command(name="now", aliases=['np', 'current'])
    async def now_playing(self, ctx):
        """
        Shows info about the currently playing track.
        """
        player: Player = ctx.voice_client
        await ctx.message.delete()

        if not player.is_connected():
            raise NoPlayerFound()

        if not player.is_playing():
            return await ctx.send('Nothing playing.', delete_after=10)

        embed = self._create_current_song_embed(player)
        await ctx.send(embed=embed, delete_after=60)

    @commands.command()
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
        await ctx.message.delete()

    @commands.group(aliases=['q'], invoke_without_command=True)
    async def queue(self, ctx):
        """
        Shows the queue in a paginated menu. Use the subcommand `clear` to clear the queue.
        """
        player: Player = ctx.voice_client
        await ctx.message.delete()
        if player.queue.count < 1:
            if player.track:
                embed = self._create_current_song_embed(player)
            await ctx.send('Nothing queued.', embed=embed, delete_after=15)
            return

        queue_pages = self._create_queue_embed_list(ctx)
        view = PaginatorView(ctx.author, queue_pages) if len(queue_pages) > 1 else None
        await ctx.send(embed=queue_pages[0], delete_after=180, view=view)

    @queue.command(name='move')
    async def queue_move(self, ctx, from_index: int, to_index: int):
        """
        Moves a track from one position in the queue to another.
        """
        player: Player = ctx.voice_client
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
        await ctx.message.delete()

    @commands.is_owner()
    @commands.command(hidden=True)
    async def players(self, ctx):
        """
        Shows all active players. Mostly used to check before deploying a new version.
        """
        players = wavelink.NodePool.get_node().players
        if not players:
            await ctx.message.delete()
            embed = discord.Embed(color=EMBED_COLOR, title='*⃣ | No active players found.')
            return await ctx.send(embed=embed, delete_after=15)

        # get server names by id
        server_names = [f'{player.guild.name} ({player.queue.count})' for player in players]

        embed = discord.Embed(color=EMBED_COLOR)
        embed.title = 'Active players'
        embed.description = '\n'.join(f'{server_name}' for server_name in server_names)
        await ctx.send(embed=embed, delete_after=60)
        await ctx.message.delete()

    @commands.command()
    @commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
    async def cleanup(self, ctx, enable: bool = None):
        """
        Enables/disables the auto-cleanup of the music queue messages that appear after queueing a new song.
        """
        server = db.get_discord_server(ctx.guild)

        if enable is not None:
            server.music_cleanup = enable
            db.session.commit()

        embed = self.create_song_cleanup_embed(ctx, enable, server)
        await ctx.send(embed=embed)
        await ctx.message.delete()

    def create_song_cleanup_embed(self, ctx, enable, server):
        embed = discord.Embed(color=discord.Color.red())
        if enable or server.music_cleanup:
            embed = discord.Embed(color=discord.Color.green())
        if server.music_cleanup:
            status_string = '`enabled` <:greenTick:876177251832590348>'
        else:
            status_string = '`disabled` <:redCross:876177262813278288>'
        embed.title = 'Cleanup status'
        embed.description = f'Song messages auto-cleanup is {status_string}.'
        embed.set_footer(text=f'Use `{ctx.prefix}cleanup <enable/disable>` to toggle.')
        return embed

    def _create_current_song_embed(self, player):
        embed = discord.Embed(color=EMBED_COLOR)
        embed.title = 'Now Playing'
        embed.description = f'[{player.track.title}]({player.track.uri})'
        if not player.track.is_stream():
            timestamp = str(datetime.timedelta(seconds=player.track.duration)).split(".")[0]
            position = str(datetime.timedelta(seconds=player.position)).split(".")[0]
            embed.add_field(name='Duration', value=f"{position}/{timestamp}")
        else:
            embed.add_field(name='Duration', value='LIVE 🔴')
        embed.add_field(name='Queued By', value=f"{player.track.requester.mention}")
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
            embed.add_field(name='Now Playing', value=f'[{player.track.title}]({player.track.uri})')
            embed.set_footer(text=f"Queued by {ctx.author}", icon_url=ctx.author.avatar)

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
            interaction.response.send_message(f':warn: {interaction.user.mention} **You aren\'t the author of this embed**')
            return False
        return True

    @discord.ui.button(emoji='⏮', style=discord.ButtonStyle.primary)
    async def previous(self, interaction: Interaction, button: discord.ui.Button):
        self.current_page = max(self.current_page - 1, 0)
        button.disabled = (self.current_page == 0)
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
        button.disabled = False

    @discord.ui.button(emoji='❌', style=discord.ButtonStyle.grey)
    async def close(self, interaction: Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.message.delete()

    @discord.ui.button(emoji='⏭', style=discord.ButtonStyle.primary)
    async def next(self, interaction: Interaction, button: discord.ui.Button):
        self.current_page = min(self.current_page + 1, len(self.pages) - 1)
        button.disabled = (self.current_page == len(self.pages) - 1)
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
    if all([config.LAVALINK_HOST, config.LAVALINK_PORT, config.LAVALINK_PASSWORD]):
        await bot.add_cog(Music(bot))
    else:
        logger.warning("Lavalink is not configured. Skipping Music cog.")
