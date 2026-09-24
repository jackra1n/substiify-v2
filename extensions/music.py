import datetime
import logging

import aiohttp
import discord
import wavelink
from discord import ButtonStyle, Interaction, ui
from discord.ext import commands
from urllib.parse import urlparse

import core
import utils

logger = logging.getLogger(__name__)

EMBED_COLOR = core.constants.CYAN_COLOR


async def _send_music_error(channel, embed: discord.Embed):
	if not isinstance(channel, discord.abc.Messageable):
		return
	await core.best_effort(
		channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none()), "music error notice"
	)


async def _report_music_error(bot, channel, embed: discord.Embed, detail: str):
	if core.config.ERRORS_CHANNEL_ID is None:
		return
	admin_channel = bot.get_channel(core.config.ERRORS_CHANNEL_ID)
	if admin_channel is None or getattr(channel, "id", None) == admin_channel.id:
		return
	report = embed.copy()
	report.add_field(name="Channel", value=str(getattr(channel, "id", "Unavailable")), inline=False)
	report.add_field(name="Details", value=discord.utils.escape_markdown(detail)[:1024] or "Unavailable", inline=False)
	await _send_music_error(admin_channel, report)


class MusicPlayer(wavelink.Player):
	text_channel: discord.abc.Messageable | None = None
	controller_message: discord.Message | None = None


def _require_player(ctx: commands.Context) -> MusicPlayer:
	player = ctx.voice_client
	if not isinstance(player, MusicPlayer):
		raise NoPlayerFound()
	return player


def _author_voice(ctx: commands.Context) -> discord.VoiceState | None:
	return ctx.author.voice if isinstance(ctx.author, discord.Member) else None


class Music(commands.Cog):
	COG_EMOJI = "🎵"

	def __init__(self, bot: core.Substiify):
		self.bot = bot
		self._node: wavelink.Node | None = None
		self._session: aiohttp.ClientSession | None = None

	async def cog_load(self) -> None:
		uri, password = core.config.LAVALINK_NODE_URL, core.config.LAVALINK_PASSWORD
		if not uri or not password:
			logger.info("Lavalink is not configured; music commands are unavailable.")
			return
		self._session = aiohttp.ClientSession()
		try:
			self._node = wavelink.Node(
				uri=uri,
				password=password,
				session=self._session,
			)
			await wavelink.Pool.connect(client=self.bot, nodes=[self._node])
		except BaseException:
			await self.cog_unload()
			raise

	async def cog_unload(self) -> None:
		try:
			if self._node is not None:
				await self._node.close(eject=True)
		finally:
			if self._session is not None:
				await self._session.close()

	@commands.Cog.listener()
	async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
		logger.info("Wavelink Node connected: %r | Resumed: %s", payload.node, payload.resumed)

	def _create_error_embed(self, description: str, *, title: str = "Music Error") -> discord.Embed:
		embed = discord.Embed(title=title, description=description, color=discord.Color.red())
		return embed

	async def cog_command_error(self, ctx, error):
		original = error
		while getattr(original, "original", None) is not None:
			original = original.original
		if isinstance(original, MusicError) and original.__cause__ is None:
			embed = self._create_error_embed(str(original))
			await ctx.send(embed=embed)
		else:
			await self.bot.on_command_error(
				ctx,
				error,
				service_errors=(wavelink.WavelinkException,),
				error_title="Music Error",
				service_message=(
					"The music service couldn't complete that request. Please try again shortly or try another track."
				),
			)
		# Discord still dispatches the global error event after this cog handler.
		error.is_handled = True

	@commands.Cog.listener()
	async def on_voice_state_update(self, member, before: discord.VoiceState, after):
		if before.channel is not None and self.is_bot_last_vc_member(before.channel):
			player = before.channel.guild.voice_client
			if isinstance(player, wavelink.Player):
				await player.disconnect()

	def is_bot_last_vc_member(self, channel: discord.VoiceChannel | discord.StageChannel):
		if channel and self.bot.user in channel.members:
			return all(member.bot for member in channel.members)
		return False

	@commands.Cog.listener()
	async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
		await player.disconnect()

	@commands.Cog.listener()
	async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
		if isinstance(payload.player, MusicPlayer):
			await self._update_controller(payload.player)

	@commands.Cog.listener()
	async def on_wavelink_track_exception(self, payload: wavelink.TrackExceptionEventPayload):
		await self._notify_playback_failure(payload, stuck=False)

	@commands.Cog.listener()
	async def on_wavelink_track_stuck(self, payload: wavelink.TrackStuckEventPayload):
		await self._notify_playback_failure(payload, stuck=True)

	async def _notify_playback_failure(self, payload, *, stuck: bool):
		player = payload.player
		channel = getattr(player, "text_channel", None)
		failure = "stalled" if stuck else "failed"
		logger.warning(
			"Music playback %s (guild=%s, track=%s)",
			failure,
			getattr(getattr(player, "guild", None), "id", None),
			payload.track.identifier,
		)
		embed = self._create_error_embed(
			f"Playback {failure}. Please try skipping this track or playing a different search result. "
			"If this keeps happening, try again later.",
			title="Playback Error",
		)
		await _send_music_error(channel, embed)
		detail = f"Stuck threshold: {payload.threshold} ms" if stuck else str(payload.exception)
		await _report_music_error(self.bot, channel, embed, detail)

	async def _update_controller(self, player: MusicPlayer):
		if player.controller_message is None:
			return

		embed = await create_controller_embed(player)
		try:
			await player.controller_message.edit(embed=embed)
		except discord.NotFound:
			pass

	async def _search_tracks(self, query: str) -> wavelink.Search:
		is_spotify = self._is_spotify_url(query)

		if is_spotify and not core.config.SPOTIFY_URLS_ENABLED:
			raise SpotifyUnsupported()

		try:
			return await wavelink.Playable.search(query)
		except wavelink.WavelinkException as error:
			raise TrackLoadFailed(is_spotify=is_spotify) from error

	async def _connect_player(self, ctx: commands.Context) -> MusicPlayer:
		player = ctx.voice_client
		if not isinstance(player, MusicPlayer):
			voice = _author_voice(ctx)
			if voice is None or voice.channel is None:
				raise NoVoiceChannel()
			player = await voice.channel.connect(cls=MusicPlayer)
			await player.set_volume(65)
		player.text_channel = ctx.channel
		return player

	def _is_spotify_url(self, value: str) -> bool:
		if value.startswith("spotify:"):
			return True

		parsed = urlparse(value)
		if parsed.scheme not in {"http", "https"} or not parsed.netloc:
			return False

		host = parsed.netloc.lower()
		if host.startswith("www."):
			host = host[4:]

		return host in {"open.spotify.com", "play.spotify.com", "spotify.com"}

	async def cog_before_invoke(self, ctx: commands.Context):
		"""Command before-invoke handler."""
		if ctx.guild is None:
			return
		await self.ensure_voice(ctx)
		if isinstance(ctx.voice_client, MusicPlayer):
			ctx.voice_client.text_channel = ctx.channel

	async def ensure_voice(self, ctx: commands.Context):
		"""This check ensures that the bot and command author are in the same voicechannel."""
		try:
			wavelink.Pool.get_node()
		except wavelink.InvalidNodeException as error:
			raise NoNodeAccessible() from error

		command_name = ctx.command.name if ctx.command else None
		if command_name in ["players", "cleanup", "lavalink"]:
			return

		player = ctx.voice_client
		if command_name == "controller":
			if player is None:
				raise NoPlayerFound()
			return

		voice = _author_voice(ctx)
		if voice is None or voice.channel is None:
			raise NoVoiceChannel()

		if player is None:
			if command_name != "play":
				raise NoPlayerFound()
			permissions = voice.channel.permissions_for(voice.channel.guild.me)
			if not permissions.connect or not permissions.speak:
				raise NoPermissions()
			return

		if player.channel != voice.channel:
			raise DifferentVoiceChannel()

	@commands.hybrid_command(aliases=["p"], usage="play <url/query>")
	@commands.guild_only()
	async def play(self, ctx: commands.Context, *, search: str):
		"""Plays or queues a song/playlist. Can be a YouTube, Soundcloud link or a search query.

		Examples:
		`<<play All girls are the same Juice WRLD` - searches for a song and queues it
		`<<play https://www.youtube.com/watch?v=dQw4w9WgXcQ` - plays a YouTube video
		"""
		if ctx.interaction:
			await ctx.defer()
		search = search.strip("<>")

		tracks: wavelink.Search = await self._search_tracks(search)
		if not tracks:
			raise NoTracksFound()

		player = await self._connect_player(ctx)

		if player.autoplay == wavelink.AutoPlayMode.disabled:
			player.autoplay = wavelink.AutoPlayMode.partial

		stmt_cleanup = "SELECT music_cleanup FROM discord_server WHERE discord_server_id = $1"
		music_cleanup = await self.bot.db.pool.fetchval(stmt_cleanup, core.require_guild(ctx).id)

		embed = discord.Embed(color=EMBED_COLOR)
		if isinstance(tracks, wavelink.Playlist):
			queued: wavelink.Playlist | wavelink.Playable = tracks
			embed.description = f"**[{tracks}]({tracks.url})**" if tracks.url else f"**[{tracks}]({search})**"
		else:
			queued = tracks[0]
			embed.description = f"**[{queued}]({queued.uri})**"

		songs_cnt = await player.queue.put_wait(queued)
		embed.title = "Songs Queued"
		embed.title += f" ({songs_cnt})" if songs_cnt > 1 else ""

		if not player.playing:
			await player.play(player.queue.get())
		message = await ctx.send(embed=embed)
		if music_cleanup:
			await message.delete(delay=60)
		if not ctx.interaction:
			await ctx.message.delete()

	@commands.hybrid_command()
	@commands.guild_only()
	async def skip(self, ctx: commands.Context, amount: commands.Range[int, 1, None] = 1):
		"""Skips the current song."""
		player = _require_player(ctx)
		if not ctx.interaction:
			await ctx.message.delete()
		if not player.queue and not player.playing:
			await player._do_recommendation()
		else:
			del player.queue[: amount - 1]
			await player.skip()
		embed = discord.Embed(title=f"⏭️ Skipped {amount}", color=EMBED_COLOR)
		if player.current:
			embed.description = f"Now playing: **[{player.current}]({player.current.uri})**"
		embed.set_footer(text=f"By: {ctx.author}", icon_url=ctx.author.display_avatar)
		await ctx.send(embed=embed, delete_after=30)

	@commands.hybrid_command(aliases=["disconnect", "leave"])
	@commands.guild_only()
	async def stop(self, ctx: commands.Context):
		"""
		Disconnects the player from the voice channel and clears its queue.
		"""
		player = _require_player(ctx)

		if player.controller_message is not None:
			await player.controller_message.delete()
		await player.disconnect()
		embed = discord.Embed(title="⏹️ Disconnected", color=EMBED_COLOR)
		await ctx.send(embed=embed, delete_after=30)

	@commands.hybrid_command(aliases=["con", "now", "queue", "q"])
	@commands.guild_only()
	async def controller(self, ctx: commands.Context):
		"""
		Shows the music controller.
		"""
		player = _require_player(ctx)
		if player.controller_message is not None:
			await player.controller_message.delete()
		view = MusicController(player, ctx.author.id)
		embed = await create_controller_embed(player)
		player.controller_message = await ctx.send(embed=embed, view=view)

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
			embed = discord.Embed(color=EMBED_COLOR, title="*⃣ | No active players found.")
			return await ctx.send(embed=embed, delete_after=30)

		# get server names by id
		players_string: str = ""
		for player in players.values():
			players_string += f"{player.guild.name if player.guild else 'Unknown server'}, queued: "
			players_string += f"`{len(player.queue)}`, "
			players_string += "`playing` " if player.playing else "`not playing` "
			players_string += f"radio: `{player.autoplay.name}` "
			players_string += f"loop: `{player.queue.mode.name}`"
			players_string += "\n"

		embed = discord.Embed(color=EMBED_COLOR)
		embed.title = "Active players"
		embed.description = players_string
		await ctx.send(embed=embed, delete_after=60)

	@commands.is_owner()
	@commands.command(name="lavalink", aliases=["lv"], hidden=True)
	async def lavalink_stats(self, ctx: commands.Context, full: bool = False):
		"""
		Shows the Lavalink stats.
		"""
		stats: wavelink.StatsResponsePayload = await wavelink.Pool.get_node().fetch_stats()
		info: wavelink.InfoResponsePayload = await wavelink.Pool.get_node().fetch_info()

		uptime_str = utils.seconds_to_human_readable(stats.uptime / 1000)
		memory_used = utils.bytes_to_human_readable(stats.memory.used)
		memory_free = utils.bytes_to_human_readable(stats.memory.reservable)
		system_load = round((stats.cpu.system_load * 100), 1)

		embed = discord.Embed(title="Lavalink Node Info", color=EMBED_COLOR)
		embed.description = (
			f"- **Lavalink Version:** ` {info.version.semver} `\n"
			f"- **Players:** ` {stats.playing} / {stats.players} `\n"
			f"- **Uptime:** ` {uptime_str} `\n"
			f"- **CPU Cores:** ` {stats.cpu.cores} vCPU `\n"
			f"- **CPU Load:** ` {system_load}% `\n"
			f"- **Memory Usage:** ` {memory_used} / {memory_free} `\n"
			f"- **JVM:** ` {info.jvm} `"
		)

		if full:
			plugins_list_str = [f"({plugin.name} - {plugin.version})" for plugin in info.plugins]
			plugins_str = f"```ml\n{', '.join(plugins_list_str).title()}\n```"
			sources_str = f"```ml\n{', '.join(info.source_managers).title()}\n```"
			embed.add_field(name="Sources", value=sources_str, inline=False)
			embed.add_field(name="Plugins", value=plugins_str, inline=False)

		await ctx.send(embed=embed)

	@commands.hybrid_command()
	@commands.guild_only()
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	async def cleanup(self, ctx: commands.Context, enable: bool | None = None):
		"""
		Enables/disables the auto-cleanup of the music queue messages that appear after queueing a new song.
		"""
		if enable is not None:
			stmt_cleanup = "UPDATE discord_server SET music_cleanup = $1 WHERE discord_server.discord_server_id = $2"
			await self.bot.db.pool.execute(stmt_cleanup, enable, core.require_guild(ctx).id)

		embed = discord.Embed(color=discord.Color.red())
		status_string = "`disabled` <:redCross:876177262813278288>"
		if enable:
			embed = discord.Embed(color=discord.Color.green())
			status_string = "`enabled` <:greenTick:876177251832590348>"
		embed.title = "Cleanup status"
		embed.description = f"Song messages auto-cleanup is {status_string}."
		embed.set_footer(text=f"Use `{ctx.prefix}cleanup <enable/disable>` to toggle.")
		await ctx.send(embed=embed)


class MusicController(ui.View):
	def __init__(self, player: MusicPlayer, author_id: int):
		super().__init__()
		self.add_item(RadioButton(player))
		self.add_item(LoopSelect(player))
		self.player = player
		self.author_id = author_id

	async def on_timeout(self):
		message = self.player.controller_message
		if message is not None:
			self.player.controller_message = None
			try:
				await message.edit(view=None)
				await message.delete()
			except discord.NotFound:
				pass

	async def interaction_check(self, interaction: Interaction) -> bool:
		if interaction.user.id != self.author_id:
			await interaction.response.send_message(
				f"⚠️ {interaction.user.mention} **You aren't the author of this embed**", ephemeral=True
			)
			return False
		if not self.player.connected:
			raise NoPlayerFound()
		if isinstance(interaction.channel, discord.abc.Messageable):
			self.player.text_channel = interaction.channel
		await interaction.response.defer()
		return True

	async def on_error(self, interaction: Interaction, error: Exception, item: ui.Item):
		if isinstance(error, MusicError):
			description = str(error)
		else:
			description = "The music control failed. Please try again, or use the play command with a different track."
			if isinstance(error, (wavelink.WavelinkException, aiohttp.ClientConnectionError, TimeoutError)):
				logger.warning("Music controller failed (%s)", type(error).__name__)
			else:
				logger.error("Unexpected music controller failure", exc_info=(type(error), error, error.__traceback__))
		embed = discord.Embed(title="Music Error", description=description, color=discord.Color.red())
		if interaction.response.is_done():
			reply = interaction.followup.send(embed=embed, ephemeral=True)
		else:
			reply = interaction.response.send_message(embed=embed, ephemeral=True)
		await core.best_effort(reply, "music controller error")
		if not isinstance(error, MusicError):
			await _report_music_error(
				interaction.client, interaction.channel, embed, f"{type(error).__name__}: {error}"
			)

	@ui.button(label="Stop", emoji="⏹️", row=2, style=ButtonStyle.danger)
	async def leave_button(self, interaction: discord.Interaction, button: ui.Button):
		await self.player.disconnect()
		await interaction.edit_original_response(view=None)
		if self.player.controller_message is not None:
			await self.player.controller_message.delete()
		embed = discord.Embed(title="⏹️ Disconnected", color=EMBED_COLOR)
		embed.description = f"By: {interaction.user.mention}"
		if isinstance(interaction.channel, discord.abc.Messageable):
			await interaction.channel.send(embed=embed, delete_after=60)

	@ui.button(label="Skip", emoji="⏭️", row=2, style=ButtonStyle.secondary)
	async def skip_button(self, interaction: discord.Interaction, button: ui.Button):
		if not self.player.queue and not self.player.playing:
			await self.player._do_recommendation()
		else:
			await self.player.skip()

	@ui.button(label="Shuffle", emoji="🔀", row=2, style=ButtonStyle.secondary)
	async def shuffle_button(self, interaction: discord.Interaction, button: ui.Button):
		self.player.queue.shuffle()
		embed = await create_controller_embed(self.player)
		await interaction.edit_original_response(embed=embed)


class LoopSelect(ui.Select):
	def __init__(self, player: wavelink.Player):
		mode = player.queue.mode
		options = [
			discord.SelectOption(
				label="No Loop", value="normal", emoji="❌", default=(mode == wavelink.QueueMode.normal)
			),
			discord.SelectOption(label="Loop", value="loop", emoji="🔂", default=(mode == wavelink.QueueMode.loop)),
			discord.SelectOption(
				label="Loop All", value="loop_all", emoji="🔁", default=(mode == wavelink.QueueMode.loop_all)
			),
		]
		super().__init__(row=1, placeholder="Select Loop Mode", options=options)
		self.player = player

	async def callback(self, interaction: discord.Interaction):
		value = self.values[0]
		self.player.queue.mode = wavelink.QueueMode[value]


class RadioButton(ui.Button):
	def __init__(self, player: wavelink.Player):
		btn_style = ButtonStyle.secondary
		if player.autoplay == wavelink.AutoPlayMode.enabled:
			btn_style = ButtonStyle.green
		super().__init__(label="Radio", emoji="📻", row=2, style=btn_style)
		self.player = player

	async def callback(self, interaction: discord.Interaction):
		if self.player.autoplay != wavelink.AutoPlayMode.enabled:
			self.player.autoplay = wavelink.AutoPlayMode.enabled
			self.style = ButtonStyle.green
		else:
			self.player.autoplay = wavelink.AutoPlayMode.partial
			self.style = ButtonStyle.secondary
		await interaction.edit_original_response(view=self.view)


async def create_controller_embed(player: wavelink.Player):
	embed = discord.Embed(title="🎚️ Music Controller", color=EMBED_COLOR)
	now_playing = "⏸️ Paused"
	position = "00:00/00:00"
	current = player.current
	if player.playing and current is not None:
		embed.set_thumbnail(url=current.artwork)
		now_playing = f"[{current.author} - {current.title}]({current.uri})"
		current_position = str(datetime.timedelta(milliseconds=player.position)).split(".")[0]
		song_length = str(datetime.timedelta(milliseconds=current.length)).split(".")[0]
		position = f"`{current_position}/{song_length}`"
	embed.add_field(name="Now Playing", value=now_playing, inline=False)
	embed.add_field(name="Position", value=position)
	upcoming = "\n".join([f"`{index + 1}.` {track.title}" for index, track in enumerate(player.queue[:5])])
	if len(player.queue) > 5:
		upcoming += f"\n`... and {len(player.queue) - 5} more`"
	elif not upcoming:
		upcoming = "`No songs in queue`"
	embed.add_field(name="Next up ", value=upcoming, inline=False)
	return embed


class MusicError(commands.CommandError):
	pass


class NoVoiceChannel(MusicError):
	def __init__(self):
		super().__init__("You are not in a voice channel.")


class NoPermissions(MusicError):
	def __init__(self):
		super().__init__("I do not have the permissions to join your voice channel.")


class NoPlayerFound(MusicError):
	def __init__(self):
		super().__init__("No active player found.")


class NoTracksFound(MusicError):
	def __init__(self):
		super().__init__("Could not find any tracks with that query. Please try again.")


class SpotifyUnsupported(MusicError):
	def __init__(self, message: str | None = None):
		super().__init__(
			message
			or "Spotify links are unsupported right now. Please use a search query, YouTube link, or SoundCloud link instead."
		)


class TrackLoadFailed(MusicError):
	def __init__(self, *, is_spotify: bool = False):
		message = "I couldn't load that track. Please try a search query, YouTube link, or SoundCloud link instead."
		if is_spotify:
			message = "Spotify links are not working right now. Please use a search query, YouTube link, or SoundCloud link instead."
		super().__init__(message)


class DifferentVoiceChannel(MusicError):
	def __init__(self):
		super().__init__("You are not in the same voice channel as the bot.")


class NoNodeAccessible(MusicError):
	def __init__(self):
		super().__init__("No playing agent is available at the moment. Please try again later or contact support.")


async def setup(bot: core.Substiify):
	url = core.config.LAVALINK_NODE_URL
	password = core.config.LAVALINK_PASSWORD

	if url and url.strip() and password and password.strip():
		await bot.add_cog(Music(bot))
	else:
		logger.warning("Lavalink is not configured. Skipping Music cog.")
