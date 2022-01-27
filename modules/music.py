
import datetime
import random
import re

import lavalink
import nextcord
from nextcord.ext import commands

from helper.LavalinkVoiceClient import LavalinkVoiceClient

url_rx = re.compile(r'https?://(?:www\.)?.+')


class Music(commands.Cog):

    COG_EMOJI = "🎵"

    def __init__(self, bot):
        self.bot = bot
        # This ensures the client isn't overwritten during cog reloads.
        if not hasattr(bot, 'lavalink'):
            bot.lavalink = lavalink.Client(bot.user.id)
            bot.lavalink.add_node('127.0.0.1', 2333, 'youshallnotpass', 'eu', 'default-node')

        lavalink.add_event_hook(self.track_hook)

    def cog_unload(self):
        """ Cog unload handler. This removes any event hooks that were registered. """
        self.bot.lavalink._event_hooks.clear()

    async def cog_before_invoke(self, ctx):
        """ Command before-invoke handler. """
        guild_check = ctx.guild is not None

        if guild_check:
            await self.ensure_voice(ctx)

        return guild_check

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            await ctx.send(error.original)

    async def ensure_voice(self, ctx):
        """ This check ensures that the bot and command author are in the same voicechannel. """
        if ctx.command.name in ('players','queue','now_playing',):
            return True
        player = self.bot.lavalink.player_manager.create(ctx.guild.id, endpoint=str(ctx.guild.region))
        should_connect = ctx.command.name in ('play',)

        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandInvokeError('Join a voicechannel first.')

        if not player.is_connected:
            if not should_connect:
                raise commands.CommandInvokeError('Not connected.')

            permissions = ctx.author.voice.channel.permissions_for(ctx.me)

            if not permissions.connect or not permissions.speak:
                raise commands.CommandInvokeError('I need the `CONNECT` and `SPEAK` permissions.')

            player.store('channel', ctx.channel.id)
            await ctx.author.voice.channel.connect(cls=LavalinkVoiceClient)
        else:
            if int(player.channel_id) != ctx.author.voice.channel.id:
                raise commands.CommandInvokeError('You need to be in my voicechannel.')

    async def track_hook(self, event):
        if isinstance(event, lavalink.events.QueueEndEvent):
            guild_id = int(event.player.guild_id)
            guild = self.bot.get_guild(guild_id)
            await guild.voice_client.disconnect(force=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        if before.channel is None:
            return
        if self.bot.user not in before.channel.members:
            return
        users = [user for user in before.channel.members if not user.bot]
        if len(users) == 0:
            player = self.bot.lavalink.player_manager.get(member.guild.id)
            player.queue.clear()
            await player.stop()
            guild = self.bot.get_guild(member.guild.id)
            await guild.voice_client.disconnect(force=True)

    @commands.command(aliases=['p'])
    async def play(self, ctx, *, query: str):
        """ Plays or queues a song/playlist. Can be a YouTube URL, Soundcloud URL or a search query. 
        
        Examples:
        `<<play All girls are the same Juice WRLD` - searches for a song and queues it
        `<<play https://www.youtube.com/watch?v=dQw4w9WgXcQ` - plays a YouTube video
        """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        query = query.strip('<>')

        if not url_rx.match(query):
            query = f'ytsearch:{query}'

        # Get the results for the query from Lavalink.
        results = await player.node.get_tracks(query)

        if not results or not results['tracks']:
            return await ctx.send('Nothing found!', delete_after=30)

        embed = nextcord.Embed(color=nextcord.Color.blurple())

        if results['loadType'] == 'PLAYLIST_LOADED':
            tracks = results['tracks']

            for track in tracks:
                # Add all of the tracks from the playlist to the queue.
                player.add(requester=ctx.author.id, track=track)

            embed.title = 'Playlist Enqueued!'
            embed.description = f'{results["playlistInfo"]["name"]} - {len(tracks)} tracks'
        else:
            track = results['tracks'][0]
            embed.title = 'Track Enqueued'
            embed.description = f'[{track["info"]["title"]}]({track["info"]["uri"]})'

            track = lavalink.models.AudioTrack(track, ctx.author.id, recommended=True)
            player.add(requester=ctx.author.id, track=track)

        await ctx.send(embed=embed)

        if not player.is_playing:
            await player.play()
        await ctx.message.delete()

    @commands.command(aliases=['disconnect', 'stop'])
    async def leave(self, ctx):
        """
        Disconnects the player from the voice channel and clears its queue.
        """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not player.is_connected:
            return await ctx.send('Not connected.', delete_after=30)

        if not ctx.author.voice or (player.is_connected and ctx.author.voice.channel.id != int(player.channel_id)):
            return await ctx.send('You\'re not in my voicechannel!', delete_after=30)

        player.queue.clear()
        await player.stop()
        await ctx.voice_client.disconnect(force=True)
        await ctx.send('*⃣ | Disconnected.', delete_after=30)
        await ctx.message.delete()

    @commands.command()
    async def skip(self, ctx):
        """
        Skips the current track. If there no more tracks in the queue, disconnects the player.
        """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not player.is_playing:
            return await ctx.send('Not playing currently.', delete_after=15)

        await player.skip()
        await ctx.send('*⃣ | Skipped.', delete_after=15)
        await ctx.message.delete()

    @commands.command(name="now" ,aliases=['np', 'current'])
    async def now_playing(self, ctx):
        """
        Shows info about the currently playing track.
        """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not player.is_connected:
            return await ctx.send('Not connected.', delete_after=10)

        if not player.current:
            return await ctx.send('Nothing playing.', delete_after=10)

        embed = self._create_current_song_embed(player)
        await ctx.send(embed=embed)
        await ctx.message.delete()

    @commands.command()
    async def shuffle(self, ctx):
        """
        Randomly shuffles the queue.
        """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if len(player.queue) < 2:
            return await ctx.send('Not enough tracks to shuffle.', delete_after=15)

        random.shuffle(player.queue)
        await ctx.send('*⃣ | Queue shuffled.', delete_after=15)
        await ctx.message.delete()

    @commands.group(aliases=['q'], invoke_without_command=True)
    async def queue(self, ctx):
        """
        Shows the queue in a paginated menu.
        """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if len(player.queue) == 0:
            return await ctx.send('Nothing queued.', delete_after=15)
        elif len(player.queue) == 1:
            embed = self._create_current_song_embed(player)
            return await ctx.send(embed=embed)
        await ctx.message.delete()

        current_page = 0
        queue_pages = self._create_queue_embed_list(ctx, player)
        queue_message = await ctx.send(embed=queue_pages[current_page], delete_after=150)

        await queue_message.add_reaction("⏮")
        await queue_message.add_reaction("❌")
        await queue_message.add_reaction("⏭")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ("⏮", "⏭", "❌") and reaction.message.id == queue_message.id

        while True:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=150.0, check=check)
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

    @queue.command(aliases=['clear'])
    async def queue_clear(self, ctx):
        """
        Clears the queue.
        """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        player.queue.clear()
        await ctx.send('*⃣ | Queue cleared.')

    @commands.command()
    async def repeat(self, ctx):
        """
        Repeats the current track in a loop.
        """
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not player.current:
            return await ctx.send('Nothing playing.')

        player.repeat = not player.repeat
        await ctx.send(f'*⃣ | Repeat is now {player.repeat}.')
        await ctx.message.delete()


    @commands.is_owner()
    @commands.command(hidden=True)
    async def players(self, ctx):
        """
        Shows all active players. Mostly used to check before deploying a new version.
        """
        players = self.bot.lavalink.player_manager.find_all()
        players = [player for player in players if player.is_connected]
        if not players:
            await ctx.message.delete()
            return await ctx.send('No players found.', delete_after=15)

        # get server names by id
        server_names = []
        for player in players:
            server = await self.bot.fetch_guild(player.guild_id)
            if server:
                server_names.append(server.name)

        embed = nextcord.Embed(color=nextcord.Color.blurple())
        embed.title = 'Active players'
        embed.description = '\n'.join(f'{server_name}' for server_name in server_names)
        await ctx.send(embed=embed, delete_after=60)
        await ctx.message.delete()

    
    def _create_current_song_embed(self, player):
        embed = nextcord.Embed(color=nextcord.Color.blurple())
        embed.title = 'Now Playing'
        embed.description = f'[{player.current.title}]({player.current.uri})'
        if not player.current.stream:
            timestamp = str(datetime.timedelta(milliseconds=player.current.duration)).split(".")[0]
            position = str(datetime.timedelta(milliseconds=player.position)).split(".")[0]
            embed.add_field(name='Duration', value=f"{position}/{timestamp}")
        else:
            embed.add_field(name='Duration', value='LIVE 🔴')
        embed.add_field(name='Requested By', value=f"<@{player.current.requester}>")
        return embed

    def _create_queue_embed_list(self, ctx, player):
        pages = []
        for i in range(0, len(player.queue), 10):
            embed = nextcord.Embed(color=ctx.author.colour, timestamp=datetime.datetime.utcnow())
            embed.title = f"Queue ({len(player.queue)})"
            embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.avatar.url)
            embed.add_field(name='Now Playing', value=f'[{player.current.title}]({player.current.uri})')
            upcoming = '\n'.join([f'`{index + 1}.` [{track.title}]({track.uri})' for index, track in enumerate(player.queue[i:i + 10], start=i)])
            embed.add_field(name="Next up", value=upcoming, inline=False)
            pages.append(embed)
        return pages

def setup(bot):
    bot.add_cog(Music(bot))
