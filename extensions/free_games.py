from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

import aiohttp
import discord
from discord.ext import commands, tasks

import core

logger = logging.getLogger(__name__)


class Game(ABC):
	title: str
	start_date: datetime
	end_date: datetime
	original_price: str
	discount_price: str
	cover_image_url: str
	store_link: str
	platform: Platform


class Platform(ABC):
	api_url: str
	logo_path: str
	name: str

	@staticmethod
	@abstractmethod
	async def get_free_games() -> list[Game]:
		pass

	@staticmethod
	@abstractmethod
	def _create_game(game_info_json: str) -> Game:
		pass


class EpicGamesGame(Game):
	def __init__(self, game_info_json: str) -> None:
		self.title: str = game_info_json["title"]
		self.start_date: datetime = self._create_start_date(game_info_json)
		self.end_date: datetime = self._create_end_date(game_info_json)
		self.original_price: str = game_info_json["price"]["totalPrice"]["fmtPrice"]["originalPrice"]
		self.discount_price: str = self._create_discount_price(game_info_json["price"])
		self.cover_image_url: str = self._create_thumbnail(game_info_json["keyImages"])
		self.store_link: str = self._create_store_link(game_info_json)
		self.platform: Platform = EpicGames

	def _create_store_link(self, game_info_json: str) -> str:
		offer_mappings = game_info_json["offerMappings"]
		page_slug = None
		if offer_mappings:
			page_slug = game_info_json["offerMappings"][0]["pageSlug"]
		if page_slug is None:
			page_slug = game_info_json["catalogNs"]["mappings"][0]["pageSlug"]
		return f"https://www.epicgames.com/store/en-US/p/{page_slug}"

	def _create_start_date(self, game_info_json: str) -> datetime:
		return self._parse_date(game_info_json, "startDate")

	def _create_end_date(self, game_info_json: str) -> datetime:
		return self._parse_date(game_info_json, "endDate")

	def _parse_date(self, game_info_json: str, date_field: str) -> datetime:
		date_str = game_info_json["promotions"]["promotionalOffers"][0]["promotionalOffers"][0][date_field]
		return datetime.strptime(date_str.split("T")[0], "%Y-%m-%d")

	def _create_discount_price(self, game_price_str: str) -> str:
		discount_price = game_price_str["totalPrice"]["discountPrice"]
		return "Free" if discount_price == 0 else discount_price

	def _create_thumbnail(self, key_images: str) -> str:
		for image in key_images:
			if "OfferImageWide" in image["type"]:
				return image["url"]
		return key_images[0]["url"]


class EpicGames(Platform):
	api_url: str = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
	logo_path: str = "https://media.discordapp.net/attachments/1073161276802482196/1073161428804055140/epic.png"
	name: str = "epicgames"

	@staticmethod
	async def get_free_games() -> list[Game]:
		"""
		Get all free games from Epic Games
		"""
		all_games = ""
		try:
			async with aiohttp.ClientSession() as session:
				async with session.get(EpicGames.api_url) as response:
					json_response = await response.json()
					all_games = json_response["data"]["Catalog"]["searchStore"]["elements"]
		except Exception as ex:
			logger.error(f"Error while getting list of all Epic games: {ex}")

		current_free_games: list[Game] = []
		for game in all_games:
			# Check if game has promotions
			if game["promotions"] is None:
				continue
			if game["price"] is None:
				continue
			if game["price"]["totalPrice"] is None:
				continue
			# Check if game is free
			if game["price"]["totalPrice"]["discountPrice"] != 0:
				continue
			# Check if game was already free
			if game["price"]["totalPrice"]["originalPrice"] == 0:
				continue
			# Check if the game is _currently_ free
			if game["status"] != "ACTIVE":
				continue
			try:
				current_free_games.append(EpicGamesGame(game))
			except Exception as ex:
				logger.error(f"Error while creating 'Game' object: {ex}")
		return current_free_games


STORES = {
	"epicgames": EpicGames,
}


class FreeGames(commands.Cog):
	COG_EMOJI = "🕹️"

	def __init__(self, bot: core.Substiify):
		self.bot = bot
		self.check_free_games.start()

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
		game_in_history_stmt = """SELECT * FROM free_game_history WHERE title = $1 AND store_name = $2;"""
		game_row = await self.bot.db.pool.fetchrow(game_in_history_stmt, game.title, game.platform.name)
		if not game_row:
			return False

		# If game was in history for more than 30 days, it will be considered as a new game
		created_at = game_row["created_at"]
		if created_at + timedelta(days=30) < datetime.now():
			return False
		return True

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
		"""
		Get free games from various platforms.
		See subcommands for more information.
		By default, this command will check if the user has manage channels permission and then send the settings menu.
		If the user doesn't have the permission, it will send the free games to the current channel.
		"""
		# check if the user has manage channels permission
		if ctx.author.guild_permissions.manage_channels or ctx.author.id == self.bot.owner_id:
			await ctx.invoke(self.bot.get_command("freegames settings"))
		else:
			return await ctx.invoke(self.bot.get_command("freegames send"))

	@freegames.command()
	@commands.guild_only()
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	async def settings(self, ctx: commands.Context):
		"""
		Show settings for the free games command.
		If you can't see the channel you want to set it's because the menu is limited to 25 options.
		In order to force the channel to show up, use the command in the channel you want to set.
		"""
		embed = discord.Embed(title="Free Games Settings", color=core.constants.SECONDARY_COLOR)
		embed.description = "Here you can configure where free games should be sent and which platforms to check."

		channel_options = await _create_channels_select_options(ctx)
		settings_view = SettingsView(ctx, channel_options)
		await ctx.send(embed=embed, view=settings_view, delete_after=180)

	@freegames.command()
	@commands.cooldown(2, 30)
	async def send(self, ctx: commands.Context, platform: str = None):
		"""
		Show all free games that are currently available.
		`:param platform:` The platform to get the free games from. If not specified, all platforms will be checked.
		Valid platforms are: `epicgames`. More platforms will be added in the future.
		"""
		platforms: list[Platform] = Platform.__subclasses__()
		if any(platform == platform.__name__.lower() for platform in platforms):
			platforms = [platform]

		total_free_games_count = 0
		for platform in platforms:
			platform: Platform
			current_free_games: list[Game] = await platform.get_free_games()
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
		date_timestamp = discord.utils.format_dt(game.end_date, "d")
		embed.description = f"~~{game.original_price}~~ **{game.discount_price}** until {date_timestamp}"
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

	# At the top add disabled and current channel options
	channel_options.append(disabled_option)
	channel_options.append(current_channel_option)

	channels_list = [channel for channel in ctx.guild.text_channels if channel != ctx.channel]
	# First add only channels where the bot can read and send messages
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

	# Then if there are less than 25 options, add the rest
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
			await bot.db._insert_guild_channel(channel)

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
			await bot.db.pool.execute(fg_settings_stmt, fg_id, "epicgames")

		self.options = await _create_channels_select_options(self.view.ctx)
		return await interaction.response.edit_message(embed=embed, view=self.view)


async def setup(bot: core.Substiify):
	await bot.add_cog(FreeGames(bot))
