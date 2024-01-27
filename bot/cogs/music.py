import datetime
import logging

import discord
import wavelink
from core import config
from core.bot import Substiify
from discord import ButtonStyle, Interaction, ui
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
        if channel and self.bot.user in channel.members:
            return all(member.bot for member in channel.members)
        return False

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
        await player.disconnect()

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        player: wavelink.Player = payload.player
        await self._update_controller(player)

    async def _update_controller(self, player: wavelink.Player):
        if not hasattr(player, 'controller_message'):
            return

        embed = await create_controller_embed(player)
        try:
            await player.controller_message.edit(embed=embed)
        except discord.NotFound:
            pass

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
        if ctx.command.name in ['controller']:
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

        if player.autoplay == wavelink.AutoPlayMode.disabled:
            player.autoplay = wavelink.AutoPlayMode.partial

        search = search.strip('<>')
        if not ctx.interaction:
            await ctx.message.delete()

        tracks: wavelink.Search = await wavelink.Playable.search(search)
        if not tracks:
            raise NoTracksFound()

        if 'http' not in search or len(tracks) == 1:
            tracks = tracks[0]

        stmt_cleanup = "SELECT music_cleanup FROM discord_server WHERE discord_server_id = $1"
        music_cleanup = await self.bot.db.fetchval(stmt_cleanup, ctx.guild.id)
        delete_after = 60 if music_cleanup else None

        songs_cnt = await player.queue.put_wait(tracks)
        embed = discord.Embed(color=EMBED_COLOR)
        embed.title = 'Songs Queued'
        embed.title += f' ({songs_cnt})' if songs_cnt > 1 else ''
        if isinstance(tracks, wavelink.Playlist):
            embed.description = f'**[{tracks}]({tracks.url})**' if tracks.url else f'**{tracks}**'
        else:
            embed.description = f'**[{tracks}]({tracks.uri})**'

        if not player.playing:
            await player.play(player.queue.get())
        await ctx.send(embed=embed, delete_after=delete_after)

    @commands.hybrid_command()
    async def skip(self, ctx: commands.Context, amount: int = 1):
        """ Skips the current song. """
        player: wavelink.Player = ctx.voice_client
        if not ctx.interaction:
            await ctx.message.delete()
        if not player.queue and not player.playing:
            await player._do_recommendation()
        else:
            for _ in range(amount - 1):
                await player.queue.delete(0)
            await player.skip()
        embed = discord.Embed(title='⏭️ Skipped', color=EMBED_COLOR)
        await ctx.send(embed=embed, delete_after=30)

    @commands.hybrid_command(aliases=['disconnect', 'leave'])
    async def stop(self, ctx: commands.Context):
        """
        Disconnects the player from the voice channel and clears its queue.
        """
        player: wavelink.Player = ctx.voice_client

        if hasattr(player, 'controller_message'):
            await player.controller_message.delete()
        await player.disconnect()
        embed = discord.Embed(title='⏹️ Disconnected', color=EMBED_COLOR)
        await ctx.send(embed=embed, delete_after=30)

    @commands.hybrid_command(aliases=['con', 'queue', 'q'])
    async def controller(self, ctx: commands.Context):
        """
        Shows the music controller.
        """
        player: wavelink.Player = ctx.voice_client
        if hasattr(player, 'controller_message'):
            await player.controller_message.delete()
        view = MusicController(player, ctx)
        embed = await create_controller_embed(player)
        controller_message = await ctx.send(embed=embed, view=view)
        player.controller_message = controller_message

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
        players_string: str = ''
        for player in players.values():
            players_string += f'{player.guild.name}, queued: '
            players_string += f'`{len(player.queue)}`, '
            players_string += '`playing` ' if player.playing else '`not playing` '
            players_string += f'radio: `{player.autoplay.name}` '
            players_string += f'loop: `{player.queue.mode.name}`'
            players_string += '\n'

        embed = discord.Embed(color=EMBED_COLOR)
        embed.title = 'Active players'
        embed.description = players_string
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

        embed = discord.Embed(color=discord.Color.red())
        status_string = '`disabled` <:redCross:876177262813278288>'
        if enable:
            embed = discord.Embed(color=discord.Color.green())
            status_string = '`enabled` <:greenTick:876177251832590348>'
        embed.title = 'Cleanup status'
        embed.description = f'Song messages auto-cleanup is {status_string}.'
        embed.set_footer(text=f'Use `{ctx.prefix}cleanup <enable/disable>` to toggle.')
        await ctx.send(embed=embed)

class MusicController(ui.View):
    def __init__(self, player: wavelink.Player, ctx: commands.Context):
        super().__init__()
        self.add_item(RadioButton(player))
        self.add_item(LoopSelect(player))
        self.player = player
        self.ctx = ctx

    async def on_timeout(self):
        if hasattr(self.player, 'controller_message'):
            try:
                await self.player.controller_message.edit(view=None)
                await self.player.controller_message.delete()
                del self.player.controller_message
            except discord.NotFound:
                pass
 
    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user != self.ctx.author:
            await interaction.response.send_message(f'⚠️ {interaction.user.mention} **You aren\'t the author of this embed**', ephemeral=True)
            return False
        return True

    @ui.button(label='Stop', emoji='⏹️', row=2, style=ButtonStyle.danger)
    async def leave_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.player.disconnect()
        await interaction.response.edit_message(view=None)
        if hasattr(self.player, 'controller_message'):
            await self.player.controller_message.delete()
        embed = discord.Embed(title='⏹️ Disconnected', color=EMBED_COLOR)
        embed.description = f'By: {interaction.user.mention}'
        await interaction.channel.send(embed=embed, delete_after=60)

    @ui.button(label='Skip', emoji='⏭️', row=2, style=ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self.player.queue and not self.player.playing:
            await self.player._do_recommendation()
        else:
            await self.player.skip()
        await interaction.response.defer()

    @ui.button(label='Shuffle', emoji='🔀', row=2, style=ButtonStyle.secondary)
    async def shuffle_button(self, interaction: discord.Interaction, button: ui.Button):
        self.player.queue.shuffle()
        embed = await create_controller_embed(self.player)
        await interaction.response.edit_message(embed=embed)

class LoopSelect(ui.Select):
    def __init__(self, player: wavelink.Player):
        mode = player.queue.mode
        options=[
            discord.SelectOption(label='No Loop', value='normal', emoji='❌', default=(mode == wavelink.QueueMode.normal)),
            discord.SelectOption(label='Loop', value='loop', emoji='🔂', default=(mode == wavelink.QueueMode.loop)),
            discord.SelectOption(label='Loop All', value='loop_all', emoji='🔁', default=(mode == wavelink.QueueMode.loop_all)),
        ]
        super().__init__(row=1, placeholder='Select Loop Mode', options=options)
        self.player = player

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        self.player.queue.mode = wavelink.QueueMode[value]
        await interaction.response.defer()

class RadioButton(ui.Button):
    def __init__(self, player: wavelink.Player):
        btn_style = ButtonStyle.secondary
        if player.autoplay == wavelink.AutoPlayMode.enabled:
            btn_style = ButtonStyle.green
        super().__init__(label='Radio', emoji='📻', row=2, style=btn_style)
        self.player = player

    async def callback(self, interaction: discord.Interaction):
        if self.player.autoplay != wavelink.AutoPlayMode.enabled:
            self.player.autoplay = wavelink.AutoPlayMode.enabled
            self.style = ButtonStyle.green
        else:
            self.player.autoplay = wavelink.AutoPlayMode.partial
            self.style = ButtonStyle.secondary
        await interaction.response.edit_message(view=self.view)

async def create_controller_embed(player: wavelink.Player):
    embed = discord.Embed(title='🎚️ Music Controller', color=EMBED_COLOR)
    now_playing = '⏸️ Paused'
    position = '00:00/00:00'
    if player.playing:
        embed.set_thumbnail(url=player.current.artwork)
        now_playing = f'[{player.current}]({player.current.uri})'
        current_position = str(datetime.timedelta(milliseconds=player.position)).split(".")[0]
        song_length = str(datetime.timedelta(milliseconds=player.current.length)).split(".")[0]
        position = f'`{current_position}/{song_length}`'
    embed.add_field(name='Now Playing', value=now_playing, inline=False)
    embed.add_field(name='Position', value=position)
    upcoming = '\n'.join([f'`{index + 1}.` {track.title}' for index, track in enumerate(player.queue[:5])])
    if len(player.queue) > 5:
        upcoming += f'\n`... and {len(player.queue) - 5} more`'
    elif not upcoming:
        upcoming = '`No songs in queue`'
    embed.add_field(name="Next up ", value=upcoming, inline=False)
    return embed


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
