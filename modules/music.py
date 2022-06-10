
import asyncio
import datetime
import json
import logging
import random
import re

import discord
import wavelink
from discord.ext import commands
from wavelink.ext import spotify
from wavelink import Player, Track

from utils import store, db

logger = logging.getLogger(__name__)

URL_REGEX = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"


class Music(commands.Cog):

    COG_EMOJI = "🎵"

    def __init__(self, bot):
        self.bot = bot
        with open(store.SETTINGS_PATH, "r") as settings:
            self.settings_json = json.load(settings)
        bot.loop.create_task(self.connect_nodes())

    async def connect_nodes(self):
        await self.bot.wait_until_ready()
        spotify_client_id = self.settings_json['spotify_client_id']
        spotify_client_secret = self.settings_json['spotify_client_secret']
        if not spotify_client_id or not spotify_client_secret:
            logger.warning("Spotify client id or secret not found. Spotify support disabled.")
            spotify_client = None
        else: 
            spotify_client = spotify.SpotifyClient(client_id=spotify_client_id, client_secret=spotify_client_secret)
        await wavelink.NodePool.create_node(bot=self.bot, host='0.0.0.0', port=2333, password='youshallnotpass', spotify_client=spotify_client)


    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):

        if member.id == self.bot.user.id and after.channel is None:
            player = wavelink.NodePool.get_node().get_player(before.channel.guild)
            if player is not None:
                await player.disconnect()

        if member.id != self.bot.user.id or before.channel is not None:
            return
        voice = after.channel.guild.voice_client
        time = 0

        while True:
            await asyncio.sleep(1)
            time = 0 if voice.is_playing() and not voice.is_paused() else time + 1
            if time < 300:
                continue
            player = wavelink.NodePool.get_node().get_player(after.channel.guild)
            if player is not None:
                await player.disconnect()
            else: 
                await voice.disconnect()
            if not voice.is_connected():
                break

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, node: wavelink.Node):
        """Event fired when a node has finished connecting."""
        logger.info(f'Node: <{node.identifier}> is ready!')

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, player: Player, track: Track, reason):
        """Event fired when a track ends."""
        if not player.queue.is_empty:
            partial = await player.queue.get_wait()
            track = await player.play(partial)
            track.requester = partial.requester
        else:
            await player.stop()


    async def cog_before_invoke(self, ctx):
        """ Command before-invoke handler. """
        guild_check = ctx.guild is not None

        if guild_check:
            await self.ensure_voice(ctx)

        return guild_check


    async def ensure_voice(self, ctx):
        """ This check ensures that the bot and command author are in the same voicechannel. """
        if ctx.command.name in ['players','cleanup']:
            return True

        player = ctx.voice_client

        if ctx.command.name in ['queue','now']:
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

    @commands.command(aliases=['p'], usage='play <url/query>')
    async def play(self, ctx, *, search: str):
        """ Plays or queues a song/playlist. Can be a YouTube URL, Soundcloud URL or a search query. 
        
        Examples:
        `<<play All girls are the same Juice WRLD` - searches for a song and queues it
        `<<play https://www.youtube.com/watch?v=dQw4w9WgXcQ` - plays a YouTube video
        """
        player: Player = ctx.voice_client
        if player is None:
            return await ctx.send("No player found.")
        search = search.strip('<>')

        embed = discord.Embed(color=discord.Color.blurple())
        if (decoded := spotify.decode_url(search)) is not None:
            if decoded["type"] is spotify.SpotifySearchType.unusable:
                return await ctx.reply("This Spotify URL is not usable.", ephemeral=True)
            embed = await self._queue_spotify(decoded, player, ctx.author)

        elif re.match(URL_REGEX, search) and 'list=' in search:
            playlist = await wavelink.NodePool.get_node().get_playlist(wavelink.YouTubePlaylist, search)
            if playlist is None:
                return await ctx.reply("No results found.", delete_after=30)
            embed = await self._queue_youtube_playlist(playlist, player, ctx.author)

        else:
            # Get the results for the search from Lavalink.
            try:
                track = await wavelink.YouTubeTrack.search(query=search, return_first=True)
            except IndexError as e:
                await ctx.message.delete()
                return await ctx.send("No results found.", delete_after=15)
            # Seems like should be this instead of try except
            if track is None:
                await ctx.message.delete()
                return await ctx.send("No results found.", delete_after=15)
            track.requester = ctx.author
            if not player.is_playing():
                await player.play(track)
            else:
                player.queue.put(track)
            embed.title = 'Track Enqueued'
            embed.description = f'[{track.title}]({track.uri})'

        server = db.get_discord_server(ctx.guild)
        delete_after = 60 if server.music_cleanup else None
        
        await ctx.message.delete()
        await ctx.send(embed=embed, delete_after=delete_after)

    @play.error
    async def play_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("Please provide a search query or URL.", delete_after=30)

    async def _queue_spotify(self, decoded, player, requester):
        embed = discord.Embed(color=discord.Color.blurple())
        if decoded["type"] in (spotify.SpotifySearchType.playlist, spotify.SpotifySearchType.album):
            tracks_count = 0
            async for partial in spotify.SpotifyTrack.iterator(query=decoded["id"], partial_tracks=True, type=decoded["type"]):
                partial.requester = requester
                player.queue.put(partial)
                tracks_count += 1
            embed.title = 'Playlist Enqueued'
            embed.description = f'{tracks_count} tracks'
            if not player.is_playing():
                track = await player.play(await player.queue.get_wait())
                embed.description += f'\nNow playing: [{track.title}]({track.uri})'
        else:
            track = await spotify.SpotifyTrack.search(query=decoded["id"], return_first=True)
            track.requester = requester
            if not player.is_playing():
                await player.play(track)
            else:
                player.queue.put(track)
            embed.title = 'Track Enqueued'
            embed.description = f'[{track.title}]({track.uri})'
        return embed

    async def _queue_youtube_playlist(self, playlist, player, requester):
        for track in playlist.tracks:
            track.requester = requester
            player.queue.put(track)
        if not player.is_playing():
            track = await player.play(await player.queue.get_wait())
            track.requester = requester
        embed = discord.Embed(color=discord.Color.blurple())
        embed.title = 'Playlist Enqueued'
        embed.description = f'{playlist.name} - {len(playlist.tracks)} tracks'
        return embed

    @commands.command(aliases=['disconnect', 'stop'])
    async def leave(self, ctx):
        """
        Disconnects the player from the voice channel and clears its queue.
        """
        player: Player = ctx.voice_client

        if not player.is_connected():
            return await ctx.send('Not connected.', delete_after=30)

        if ctx.author.voice.channel != player.channel:
            return await ctx.send('You\'re not in my voicechannel!', delete_after=30)

        await player.stop()
        player.queue.reset()
        await player.disconnect()

        await ctx.send('*⃣ | Disconnected.', delete_after=30)
        await ctx.message.delete()

    @leave.error
    async def leave_error(self, ctx, error):
        if isinstance(error, NoVoiceChannel):
            await ctx.send("No suitable voice channel was found.", delete_after = 30)
        
    @commands.command()
    async def skip(self, ctx):
        """
        Skips the current track. If there no more tracks in the queue, disconnects the player.
        """
        player: Player = ctx.voice_client

        if not player.is_playing():
            return await ctx.send('Not playing currently.', delete_after=15)

        await player.stop()
        await ctx.send('*⃣ | Skipped.', delete_after=15)
        await ctx.message.delete()

    @skip.error
    async def skip_error(self, ctx, error):
        if isinstance(error, NoVoiceChannel):
            await ctx.send("No suitable voice channel was found.", delete_after = 30)

    @commands.command(name="now" ,aliases=['np', 'current'])
    async def now_playing(self, ctx):
        """
        Shows info about the currently playing track.
        """
        player: Player = ctx.voice_client
        await ctx.message.delete()

        if not player.is_connected():
            return await ctx.send('Not connected.', delete_after=10)

        if not player.is_playing():
            return await ctx.send('Nothing playing.', delete_after=10)

        embed = self._create_current_song_embed(player)
        await ctx.send(embed=embed, delete_after=60)

    @now_playing.error
    async def now_playing_error(self, ctx, error):
        if isinstance(error, NoVoiceChannel):
            await ctx.send("No suitable voice channel was found.", delete_after = 30)
        if isinstance(error, NoPlayerFound):
            await ctx.send('No player found', delete_after=30)

    @commands.command()
    async def shuffle(self, ctx):
        """
        Randomly shuffles the queue.
        """
        player: Player = ctx.voice_client

        if len(player.queue) < 2:
            return await ctx.send('Not enough tracks to shuffle.', delete_after=15)

        random.shuffle(player.queue._queue)
        await ctx.send('*⃣ | Queue shuffled.', delete_after=15)
        await ctx.message.delete()


    @commands.group(aliases=['q'], invoke_without_command=True)
    async def queue(self, ctx):
        """
        Shows the queue in a paginated menu. Use the subcommand `clear` to clear the queue.
        """
        player: Player = ctx.voice_client
        await ctx.message.delete()
        if player.queue.count < 1:
            await ctx.send('Nothing queued.', delete_after=15)
            if player.track:
                embed = self._create_current_song_embed(player)
                await ctx.send(embed=embed)
            return

        current_page = 0
        queue_pages = self._create_queue_embed_list(ctx, player)
        queue_message = await ctx.send(embed=queue_pages[current_page], delete_after=150)

        await queue_message.add_reaction("⏮")
        await queue_message.add_reaction("❌")
        await queue_message.add_reaction("⏭")

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in {"⏮", "⏭", "❌"}
                and reaction.message.id == queue_message.id
            )

        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=150.0, check=check)
            except asyncio.TimeoutError:
                break
            if str(reaction.emoji) == "❌":
                await queue_message.delete()
                break
            elif str(reaction.emoji) == "⏮":
                if current_page != 0:
                    current_page -= 1
            elif str(reaction.emoji) == "⏭":
                if current_page != len(queue_pages) - 1:
                    current_page += 1
                
            await queue_message.remove_reaction(reaction.emoji, user)
            await queue_message.edit(embed=queue_pages[current_page])

    @queue.error
    async def queue_error(self, ctx, error):
        if isinstance(error, NoVoiceChannel):
            await ctx.send("No suitable voice channel was found.", delete_after = 30)
        if isinstance(error, NoPlayerFound):
            await ctx.send('No player found', delete_after=30)


    @queue.command(aliases=['clear'])
    async def queue_clear(self, ctx):
        """
        Clears the queue.
        """
        player: Player = ctx.voice_client
        player.queue.clear()
        await ctx.send('*⃣ | Queue cleared.', delete_after=30)
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
            return await ctx.send('No players found.', delete_after=15)

        # get server names by id
        server_names = [f'{player.guild.name} ({player.queue.count})' for player in players]

        embed = discord.Embed(color=discord.Color.blurple())
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

        if enable is None:
            embed = self.create_song_cleanup_embed(enable, server)
            return await ctx.send(embed=embed)

        server.music_cleanup = enable
        db.session.commit()
        
        embed = self.create_song_cleanup_embed(enable, server)
        await ctx.send(embed=embed)
        await ctx.message.delete()


    def create_song_cleanup_embed(self, enable, server):
        embed = discord.Embed(color=discord.Color.red())
        if enable or server.music_cleanup:
            embed = discord.Embed(color=discord.Color.green())
        if server.music_cleanup:
            status_string = '`enabled` <:greenTick:876177251832590348>'
        else:
            status_string = '`disabled` <:redCross:876177262813278288>'
        embed.title = 'Cleanup status'
        embed.description = f'Song messages auto-cleanup is {status_string}.'
        embed.set_footer(text = f'Use `{self.bot.command_prefix}cleanup <enable/disable>` to toggle.')
        return embed
    

    def _create_current_song_embed(self, player):
        embed = discord.Embed(color=discord.Color.blurple())
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


    def _create_queue_embed_list(self, ctx, player):
        songs_array = []
        for song in player.queue:
            try:
                songs_array.append(song)
            except IndexError:
                break

        pages = []
        for i in range(0, player.queue.count, 10):
            embed = discord.Embed(color=ctx.author.colour, timestamp=datetime.datetime.now(datetime.timezone.utc))

            embed.title = f"Queue ({player.queue.count})"
            embed.add_field(name='Now Playing', value=f'[{player.track.title}]({player.track.uri})')
            embed.set_footer(text=f"Queued by {ctx.author.display_name}", icon_url=ctx.author.avatar_url)

            upcoming = '\n'.join([f'`{index + 1}.` {track.title}' for index, track in enumerate(songs_array[i:i+10], start=i)])
            embed.add_field(name="Next up", value=upcoming, inline=False)
            pages.append(embed)
        return pages


class AlreadyConnectedToChannel(commands.CommandError):
    pass

class NoVoiceChannel(commands.CommandError):
    pass

class NoPermissions(commands.CommandError):
    pass

class NoPlayerFound(commands.CommandError):
    pass

class QueueIsEmpty(commands.CommandError):
    pass

class NoTracksFound(commands.CommandError):
    pass

class NoMoreTracks(commands.CommandError):
    pass


def setup(bot):
    bot.add_cog(Music(bot))
