from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

import aiohttp
import asyncpg
import discord
from discord.ext import commands, tasks

import core
from .base import Game, Platform
from .epic_games import EpicGames
from .steam import Steam

logger = logging.getLogger(__name__)

_RETRYABLE_ERRORS = (
	OSError,
	TimeoutError,
	aiohttp.ClientError,
	discord.GatewayNotFound,
	discord.ConnectionClosed,
	asyncpg.PostgresConnectionError,
	asyncpg.CannotConnectNowError,
	asyncpg.TooManyConnectionsError,
	asyncpg.QueryCanceledError,
	asyncpg.LockNotAvailableError,
)

STORES: dict[str, type[Platform]] = {
	EpicGames.name: EpicGames,
	Steam.name: Steam,
}


class FreeGames(commands.Cog):
	COG_EMOJI = "🕹️"

	def __init__(self, bot: core.Substiify) -> None:
		self.bot = bot
		self.check_free_games.add_exception_type(*_RETRYABLE_ERRORS)

	async def cog_load(self) -> None:
		self.check_free_games.start()

	async def cog_unload(self) -> None:
		self.check_free_games.cancel()

	@commands.is_owner()
	@commands.command(hidden=True)
	async def fgc(self, ctx: commands.Context, action: str):
		if action == "start":
			self.check_free_games.start()
			await ctx.message.add_reaction("✅")
		elif action == "stop":
			self.check_free_games.stop()
			await ctx.message.add_reaction("✅")

	@tasks.loop(hours=1)
	async def check_free_games(self) -> None:
		try:
			await self._check_free_games()
		except _RETRYABLE_ERRORS:
			# Let tasks.Loop back off and retry this iteration, not wait an hour.
			raise
		except Exception:
			logger.exception("Free games check failed.")

	async def _check_free_games(self) -> None:
		all_enabled_platforms_stmt = """SELECT DISTINCT store_name FROM store_options;"""
		all_enabled_platforms = await self.bot.db.pool.fetch(all_enabled_platforms_stmt)
		platforms = [record["store_name"] for record in all_enabled_platforms]
		logger.debug(f"Checking free games for platforms: {platforms}")

		current_free_games: list[Game] = []
		retry_error = None
		for platform in platforms:
			if platform not in STORES:
				continue
			try:
				current_free_games += await STORES[platform].get_free_games()
			except _RETRYABLE_ERRORS as error:
				logger.warning(
					"Transient failure checking %s for free games; the check will retry: %s", platform, error
				)
				retry_error = error
			except Exception:
				logger.exception("Failed to check %s for free games.", platform)
		logger.debug(f"Found {len(current_free_games)} free games")

		freegames_and_options_stmt = """
			SELECT fgc.discord_server_id, fgc.discord_channel_id, so.store_name
			FROM free_games_channel AS fgc
			JOIN store_options AS so ON fgc.id = so.free_games_channel_id;
		"""
		freegames_and_options = await self.bot.db.pool.fetch(freegames_and_options_stmt)

		total_sent_messages = 0
		for game in current_free_games:
			try:
				total_sent_messages += await self._send_free_game(game, freegames_and_options)
			except _RETRYABLE_ERRORS as error:
				retry_error = error
			except Exception:
				logger.exception("Failed to process free game %s.", game.title)

		if total_sent_messages:
			logger.info(f"Sent [{total_sent_messages}] new free games messages")
		if retry_error is not None:
			raise retry_error

	async def _send_free_game(self, game: Game, freegames_and_options) -> int:
		embed = self._create_game_embed(game)

		sent_messages = 0
		retry_error = None
		for fg_setting in freegames_and_options:
			channel: discord.TextChannel = self.bot.get_channel(fg_setting["discord_channel_id"])
			if not channel or fg_setting["store_name"] != game.platform.name:
				continue
			try:
				sent_messages += await self._deliver_free_game(game, channel.id, channel.send, embed)
			except _RETRYABLE_ERRORS as error:
				retry_error = error
			except Exception:
				logger.exception(
					"Failed to send free game to server %s, channel %s.",
					fg_setting["discord_server_id"],
					fg_setting["discord_channel_id"],
				)
		# Other destinations get their chance before the worker retries. Already
		# acknowledged deliveries are skipped by the durable per-channel ledger.
		if retry_error is not None:
			raise retry_error
		return sent_messages

	@check_free_games.before_loop
	async def before_check_free_games(self):
		await self.bot.wait_until_ready()

	async def _claim_delivery(self, game: Game, channel_id: int) -> UUID | None:
		claim_stmt = """
			INSERT INTO free_game_delivery (
				store_name, store_link, promotion_key, discord_channel_id, claimed_until, claim_token
			)
			SELECT $1, $2, $3, $4, NOW() + INTERVAL '2 minutes', $5
			WHERE EXISTS (
				SELECT 1 FROM free_game_delivery
				WHERE store_name = $1 AND store_link = $2 AND promotion_key = $3
					AND discord_channel_id = $4
			) OR NOT EXISTS (
				SELECT 1 FROM free_game_delivery
				WHERE store_name = $1 AND store_link = $2 AND promotion_key = 'legacy'
					AND discord_channel_id = $4 AND delivered_at >= NOW() - INTERVAL '30 days'
			)
			ON CONFLICT (store_name, store_link, promotion_key, discord_channel_id) DO UPDATE
			SET claimed_until = EXCLUDED.claimed_until, claim_token = EXCLUDED.claim_token
			WHERE (
				free_game_delivery.delivered_at IS NULL
				OR ($3 = 'undated' AND free_game_delivery.delivered_at < NOW() - INTERVAL '30 days')
			) AND (free_game_delivery.claimed_until IS NULL OR free_game_delivery.claimed_until <= NOW())
			RETURNING claim_token;
		"""
		return await self.bot.db.pool.fetchval(
			claim_stmt, game.platform.name, game.store_link, game.promotion_key, channel_id, uuid4()
		)

	async def _deliver_free_game(self, game: Game, channel_id: int, send, embed: discord.Embed) -> bool:
		token = await self._claim_delivery(game, channel_id)
		if token is None:
			return False

		delivery_key = (game.platform.name, game.store_link, game.promotion_key, channel_id, token)
		acknowledged = False
		try:
			async with asyncio.timeout(20):
				await send(embed=embed)
			ack_stmt = """
				UPDATE free_game_delivery
				SET delivered_at = NOW(), claimed_until = NULL, claim_token = NULL
				WHERE store_name = $1 AND store_link = $2 AND promotion_key = $3
					AND discord_channel_id = $4 AND claim_token = $5
				RETURNING 1;
			"""
			acknowledged = await self.bot.db.pool.fetchval(ack_stmt, *delivery_key) is not None
			if not acknowledged:
				raise RuntimeError("Free game delivery lease was lost before acknowledgement.")
			return True
		finally:
			if not acknowledged:
				# Discord may have accepted the message even if send/ack failed.
				# Retrying is deliberately at-least-once rather than losing a delivery.
				release_stmt = """
					UPDATE free_game_delivery
					SET claimed_until = NULL, claim_token = NULL
					WHERE store_name = $1 AND store_link = $2 AND promotion_key = $3
						AND discord_channel_id = $4 AND claim_token = $5;
				"""
				try:
					await self.bot.db.pool.execute(release_stmt, *delivery_key)
				except Exception:
					logger.exception("Failed to release free game lease for channel %s; it will expire.", channel_id)

	@commands.hybrid_group(aliases=["fg"], usage="freegames [settings|send]")
	@commands.guild_only()
	@commands.cooldown(3, 30)
	async def freegames(self, ctx: commands.Context):
		if ctx.author.guild_permissions.manage_channels or ctx.author.id == self.bot.owner_id:
			await ctx.invoke(self.bot.get_command("freegames settings"))
		else:
			return await ctx.invoke(self.bot.get_command("freegames send"))

	@freegames.command()
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	async def settings(self, ctx: commands.Context):
		embed = discord.Embed(title="Free Games Settings", color=core.constants.SECONDARY_COLOR)
		embed.description = "Here you can configure where free games should be sent and which platforms to check."

		channel_options = await _create_channels_select_options(ctx)
		settings_view = SettingsView(ctx, channel_options)
		await ctx.send(embed=embed, view=settings_view, delete_after=180)

	@freegames.command()
	@commands.cooldown(2, 30)
	async def send(self, ctx: commands.Context, platform: str | None = None) -> None:
		await ctx.defer()
		await self.bot.db.prepare_command_context(ctx.author, ctx.guild, ctx.channel)
		all_platforms: list[type[Platform]] = list(STORES.values())
		if platform:
			all_platforms = [p for p in all_platforms if p.name == platform]

		logger.info(f"Sending free games for platforms: {[p.name for p in all_platforms]}")

		total_free_games_count = 0
		total_sent_messages = 0
		delivery_failed = False
		fetch_failed = False
		for platform_cls in all_platforms:
			try:
				current_free_games: list[Game] = await platform_cls.get_free_games()
			except Exception:
				# Report an outage instead of "no games", but let the other stores answer.
				fetch_failed = True
				logger.exception("Failed to fetch free games from %s.", platform_cls.name)
				continue
			logger.info(f"  {platform_cls.name}: found {len(current_free_games)} free games")
			total_free_games_count += len(current_free_games)

			for game in current_free_games:
				try:
					embed = self._create_game_embed(game)
					total_sent_messages += await self._deliver_free_game(game, ctx.channel.id, ctx.send, embed)
				except Exception:
					delivery_failed = True
					logger.exception("Failed to send free game %s.", game.title)

		if total_sent_messages == 0:
			embed = discord.Embed(color=discord.Colour.dark_embed())
			if fetch_failed:
				embed.description = "Could not fetch free games at the moment. Please try again later."
			elif total_free_games_count == 0:
				embed.description = "Could not find any free games at the moment."
			elif delivery_failed:
				embed.description = "Could not send free games at the moment. Please try again later."
			else:
				embed.description = "Current free games have already been sent to this channel or are being delivered."
			await ctx.send(embed=embed)

	def _create_game_embed(self, game: Game) -> discord.Embed:
		embed = discord.Embed(title=game.title, url=game.store_link, color=core.constants.SECONDARY_COLOR)
		desc_parts: list[str] = []
		if game.original_price != "0":
			desc_parts.append(f"~~{game.original_price}~~")
		desc_parts.append(f"**{game.discount_price}**")
		if game.end_date:
			date_timestamp = discord.utils.format_dt(game.end_date, "d")
			desc_parts.append(f"until {date_timestamp}")
		else:
			desc_parts.append("now!")
		embed.description = " ".join(desc_parts)
		embed.set_thumbnail(url=game.platform.logo_path)
		embed.set_image(url=game.cover_image_url)
		return embed


async def _create_channels_select_options(ctx: commands.Context) -> list[discord.SelectOption]:
	selected_channel_id = 0
	free_games_channel_stmt = """
		SELECT fgc.discord_server_id, fgc.discord_channel_id, so.store_name
		FROM free_games_channel AS fgc
		JOIN store_options AS so ON fgc.id = so.free_games_channel_id
		WHERE fgc.discord_server_id = $1;
	"""
	bot: core.Substiify = ctx.bot
	free_games_channel = await bot.db.pool.fetchrow(free_games_channel_stmt, ctx.guild.id)
	if free_games_channel:
		selected_channel_id = int(free_games_channel["discord_channel_id"])

	channel_options = []
	disabled_option = discord.SelectOption(
		label="Click here to disable",
		description="Free games will not be sent to this server.",
		value=0,
		emoji="❌",
		default=(selected_channel_id == 0),
	)

	bot_member = ctx.guild.get_member(bot.user.id)
	channel_emoji = bot.get_emoji(1221097471946522725)
	channel_active_emoji = bot.get_emoji(1221097459745292398)

	is_selected = selected_channel_id == ctx.channel.id
	current_channel_option = discord.SelectOption(
		label=f"{ctx.channel.name} (here)",
		value=ctx.channel.id,
		emoji=(channel_active_emoji if is_selected else channel_emoji),
		default=is_selected,
	)

	channel_options.append(disabled_option)
	channel_options.append(current_channel_option)

	channels_list = [channel for channel in ctx.guild.text_channels if channel != ctx.channel]
	for channel in channels_list[:]:
		if len(channel_options) >= 25:
			break
		can_read = channel.permissions_for(bot_member).read_messages
		can_write = channel.permissions_for(bot_member).send_messages
		if not can_read or not can_write:
			continue
		channel_option = discord.SelectOption(label=channel.name, value=channel.id, emoji=channel_emoji)
		if selected_channel_id == channel.id:
			channel_option.emoji = channel_active_emoji
			channel_option.default = True
		channel_options.append(channel_option)
		channels_list.remove(channel)

	for channel in channels_list[:]:
		if len(channel_options) >= 25:
			break
		channel_option = discord.SelectOption(label=channel.name, value=channel.id, emoji=channel_emoji)
		if selected_channel_id == channel.id:
			channel_option.default = True
			channel_option.emoji = channel_active_emoji
		if not channel.permissions_for(bot_member).read_messages:
			channel_option.description = "⚠️ Missing 'View Channel' permission"
		elif not channel.permissions_for(bot_member).send_messages:
			channel_option.description = "⚠️ Missing 'Send Messages' permission"
		channel_options.append(channel_option)
	return channel_options


class SettingsView(discord.ui.View):
	def __init__(self, ctx: commands.Context, channel_options: list[discord.SelectOption] | None = None) -> None:
		self.ctx = ctx
		super().__init__()
		self.add_item(ChannelsSelector(channel_options=channel_options))

	async def interaction_check(self, interaction: discord.Interaction) -> bool:
		return interaction.user.id == self.ctx.author.id

	@discord.ui.button(label="Close", style=discord.ButtonStyle.grey, row=4)
	async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
		await interaction.message.delete()


class ChannelsSelector(discord.ui.Select):
	def __init__(self, channel_options: list[discord.SelectOption] | None = None) -> None:
		options = channel_options or []
		super().__init__(placeholder="Select a channel", options=options)

	async def callback(self, interaction: discord.Interaction):
		bot: core.Substiify = self.view.ctx.bot

		channel = interaction.guild.get_channel(int(self.values[0]))
		embed = discord.Embed(title="Free Games Settings", color=core.constants.SECONDARY_COLOR)
		embed.description = "Here you can configure where free games should be sent and which platforms to check."

		if int(self.values[0]) == 0:
			fg_stmt = """DELETE FROM free_games_channel WHERE discord_server_id = $1;"""
			await bot.db.pool.execute(fg_stmt, interaction.guild.id)
		elif not isinstance(channel, discord.TextChannel):
			embed.description += "\n\n**⚠️ That channel no longer exists. Please pick another one.**"
		elif not channel.permissions_for(interaction.guild.me).read_messages:
			embed.description += f"\n\n**⚠️ Can't set channel to {channel.mention}. Missing 'View Channel' permission.**"
		elif not channel.permissions_for(interaction.guild.me).send_messages:
			embed.description += (
				f"\n\n**⚠️ Can't set channel to {channel.mention}. Missing 'Send Messages' permission.**"
			)

		else:
			async with bot.db.pool.acquire(timeout=5) as connection:
				async with connection.transaction():
					await bot.db.upsert_channel(channel, connection=connection)
					fg_stmt = """
						INSERT INTO free_games_channel (discord_server_id, discord_channel_id) VALUES ($1, $2)
						ON CONFLICT (discord_server_id) DO UPDATE SET discord_channel_id = $2
						RETURNING id;
					"""
					fg_id = await connection.fetchval(fg_stmt, interaction.guild.id, channel.id)

					fg_settings_stmt = """
						INSERT INTO store_options (free_games_channel_id, store_name) VALUES ($1, $2)
						ON CONFLICT (free_games_channel_id, store_name) DO NOTHING;
					"""
					for store_name in STORES:
						await connection.execute(fg_settings_stmt, fg_id, store_name)

		self.options = await _create_channels_select_options(self.view.ctx)
		return await interaction.response.edit_message(embed=embed, view=self.view)


async def setup(bot: core.Substiify):
	await bot.add_cog(FreeGames(bot))
