import logging
from datetime import datetime

import aiohttp

from .types import Game, Platform

logger = logging.getLogger(__name__)


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
		if page_slug is None and game_info_json["catalogNs"]["mappings"]:
			page_slug = game_info_json["catalogNs"]["mappings"][0]["pageSlug"]
		if page_slug is None and game_info_json["productSlug"]:
			page_slug = game_info_json["productSlug"]

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
			if not game["promotions"]["promotionalOffers"]:
				continue
			if not game["price"]:
				continue
			if not game["price"]["totalPrice"]:
				continue
			# Check if game is free
			if game["price"]["totalPrice"]["discountPrice"] != 0:
				continue
			# Check if game has the required categories
			categories = [category["path"] for category in game["categories"]]
			must_have_categories = ["freegames", "games"]
			if not all(category in categories for category in must_have_categories):
				continue
			# Check if the game is _currently_ free
			if game["status"] != "ACTIVE":
				continue
			try:
				current_free_games.append(EpicGamesGame(game))
			except Exception as ex:
				logger.error(f"Error while creating 'Game' object: {ex}")
		return current_free_games
