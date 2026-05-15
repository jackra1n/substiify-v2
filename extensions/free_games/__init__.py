from __future__ import annotations

import logging
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

import core
from .base import Game, Platform
from .epic_games import EpicGames
from .steam import Steam

logger = logging.getLogger(__name__)

STORES: dict[str, type[Platform]] = {
	EpicGames.name: EpicGames,
	Steam.name: Steam,
}


class FreeGames(commands.Cog):
	COG_EMOJI = "🕹️"

	def __init__(self, bot: core.Substiify):
		self.bot = bot
		self.check_free_games.start()

		# Run migration to add any new stores to existing server configs
		self.bot.loop.create_task(self._migrate_existing_store_options())

	async def _migrate_existing_store_options(self):
		await self.bot.wait_until_ready()
		for store_name in STORES:
			migration_stmt = """
				INSERT INTO store_options (free_games_channel_id, store_name)
				SELECT DISTINCT so.free_games_channel_id, $1
				FROM store_options so
				WHERE NOT EXISTS (
					SELECT 1 FROM store_options so2
					WHERE so2.free_games_channel_id = so.free_games_channel_id
					AND so2.store_name = $1
				)
				ON CONFLICT (free_games_channel_id, store_name) DO NOTHING;
			"""
			await self.bot.db.pool.execute(migration_stmt, store_name)

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
	async def check_free_games(self):
		all_enabled_platforms_stmt = """SELECT DISTINCT store_name FROM store_options;"""
		all_enabled_platforms = await self.bot.db.pool.fetch(all_enabled_platforms_stmt)
		platforms = [record["store_name"] for record in all_enabled_platforms]
		logger.debug(f"Checking free games for platforms: {platforms}")

		current_free_games: list[Game] = []
		for platform in platforms:
			if platform in STORES:
				current_free_games += await STORES[platform].get_free_games()
		logger.debug(f"Found {len(current_free_games)} free games")

		freegames_and_options_stmt = """
			SELECT fgc.discord_server_id, fgc.discord_channel_id, so.store_name
			FROM free_games_channel AS fgc
			JOIN store_options AS so ON fgc.id = so.free_games_channel_id;
		"""
		freegames_and_options = await self.bot.db.pool.fetch(freegames_and_options_stmt)

		total_sent_messages = 0
		for game in current_free_games:
			if await self._is_game_in_history(game):
				continue
			logger.info(f"Starting to send new free game: {game.title}")
			await self._add_game_to_history(game)
			embed = self._create_game_embed(game)

			for fg_setting in freegames_and_options:
				channel: discord.TextChannel = self.bot.get_channel(fg_setting["discord_channel_id"])
				if not channel:
					continue
				if fg_setting["store_name"] == game.platform.name:
					try:
						await channel.send(embed=embed)
						total_sent_messages += 1
					except Exception as ex:
						srv_chnl = (
							f"[server: {fg_setting['discord_server_id']}, channel: {fg_setting['discord_channel_id']}]"
						)
						logger.error(f"Fail while sending free game for {srv_chnl} -> {ex}")

		if total_sent_messages:
			logger.info(f"Sent [{total_sent_messages}] new free games messages")

	async def _is_game_in_history(self, game: Game) -> bool:
		game_in_history_stmt = """
			SELECT 1
			FROM free_game_history
			WHERE title = $1 AND store_name = $2
			AND created_at >= $3;
		"""
		thirty_days_ago = datetime.now() - timedelta(days=30)
		result = await self.bot.db.pool.fetchrow(game_in_history_stmt, game.title, game.platform.name, thirty_days_ago)
		return result is not None

	async def _add_game_to_history(self, game: Game):
		game_insert_stmt = """
			INSERT INTO free_game_history (title, start_date, end_date, store_name, store_link)
			VALUES ($1, $2, $3, $4, $5)
			ON CONFLICT DO NOTHING;
		"""
		await self.bot.db.pool.execute(
			game_insert_stmt, game.title, game.start_date, game.end_date, game.platform.name, game.store_link
		)

	@commands.hybrid_group(aliases=["fg"], usage="freegames [settings|send]")
	@commands.cooldown(3, 30)
	async def freegames(self, ctx: commands.Context):
		if ctx.author.guild_permissions.manage_channels or ctx.author.id == self.bot.owner_id:
			await ctx.invoke(self.bot.get_command("freegames settings"))
		else:
			return await ctx.invoke(self.bot.get_command("freegames send"))

	@freegames.command()
	@commands.guild_only()
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	async def settings(self, ctx: commands.Context):
		embed = discord.Embed(title="Free Games Settings", color=core.constants.SECONDARY_COLOR)
		embed.description = "Here you can configure where free games should be sent and which platforms to check."

		channel_options = await _create_channels_select_options(ctx)
		settings_view = SettingsView(ctx, channel_options)
		await ctx.send(embed=embed, view=settings_view, delete_after=180)

	@freegames.command()
	@commands.cooldown(2, 30)
	async def send(self, ctx: commands.Context, platform: str = None):
		all_platforms: list[type[Platform]] = Platform.__subclasses__()
		if platform:
			all_platforms = [p for p in all_platforms if p.name == platform]

		total_free_games_count = 0
		for platform_cls in all_platforms:
			current_free_games: list[Game] = await platform_cls.get_free_games()
			total_free_games_count += len(current_free_games)

			for game in current_free_games:
				try:
					embed = self._create_game_embed(game)
					await ctx.send(embed=embed)
				except Exception as ex:
					logger.error(f"Fail while sending free game: {ex}")

		if total_free_games_count == 0:
			embed = discord.Embed(color=discord.Colour.dark_embed())
			embed.description = "Could not find any free games at the moment."
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
	def __init__(self, ctx: commands.Context, channel_options: list[discord.SelectOption] = None):
		self.ctx = ctx
		super().__init__()
		self.add_item(ChannelsSelector(channel_options=channel_options))

	async def interaction_check(self, interaction: discord.Interaction) -> bool:
		return interaction.user.id == self.ctx.author.id

	@discord.ui.button(label="Close", style=discord.ButtonStyle.grey, row=4)
	async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
		await interaction.message.delete()


class ChannelsSelector(discord.ui.Select):
	def __init__(self, channel_options: list[discord.SelectOption] = None):
		options = channel_options or []
		super().__init__(placeholder="Select a channel", options=options)

	async def callback(self, interaction: discord.Interaction):
		bot: core.Substiify = self.view.ctx.bot

		channel: discord.TextChannel = interaction.guild.get_channel(int(self.values[0]))
		embed = discord.Embed(title="Free Games Settings", color=core.constants.SECONDARY_COLOR)
		embed.description = "Here you can configure where free games should be sent and which platforms to check."

		if int(self.values[0]) == 0:
			fg_stmt = """DELETE FROM free_games_channel WHERE discord_server_id = $1;"""
			await bot.db.pool.execute(fg_stmt, interaction.guild.id)
		elif not channel.permissions_for(interaction.guild.me).read_messages:
			embed.description += f"\n\n**⚠️ Can't set channel to {channel.mention}. Missing 'View Channel' permission.**"
		elif not channel.permissions_for(interaction.guild.me).send_messages:
			embed.description += (
				f"\n\n**⚠️ Can't set channel to {channel.mention}. Missing 'Send Messages' permission.**"
			)

		else:
			await bot.db._insert_server_channel(channel)

			fg_stmt = """
				INSERT INTO free_games_channel (discord_server_id, discord_channel_id) VALUES ($1, $2)
				ON CONFLICT (discord_server_id) DO UPDATE SET discord_channel_id = $2
				RETURNING id;
			"""
			result = await bot.db.pool.fetch(fg_stmt, interaction.guild.id, channel.id)
			fg_id = int(result[0]["id"])

			fg_settings_stmt = """
				INSERT INTO store_options (free_games_channel_id, store_name) VALUES ($1, $2)
				ON CONFLICT (free_games_channel_id, store_name) DO NOTHING;
			"""
			for store_name in STORES:
				await bot.db.pool.execute(fg_settings_stmt, fg_id, store_name)

		self.options = await _create_channels_select_options(self.view.ctx)
		return await interaction.response.edit_message(embed=embed, view=self.view)


async def setup(bot: core.Substiify):
	await bot.add_cog(FreeGames(bot))
