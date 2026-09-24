from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .base import Game, Platform, ProviderError

logger = logging.getLogger(__name__)


class EpicGamesGame(Game):
	def __init__(self, game_info_json: dict[str, Any]) -> None:
		self.title: str = game_info_json["title"]
		self.start_date: datetime = self._create_start_date(game_info_json)
		self.end_date: datetime = self._create_end_date(game_info_json)
		self.original_price: str = game_info_json["price"]["totalPrice"]["fmtPrice"]["originalPrice"]
		self.discount_price: str | int = self._create_discount_price(game_info_json["price"])
		self.cover_image_url: str = self._create_thumbnail(game_info_json["keyImages"])
		self.store_link: str = self._create_store_link(game_info_json)
		self.platform: type[Platform] = EpicGames

	def _create_store_link(self, game_info_json: dict[str, Any]) -> str:
		offer_mappings = game_info_json["offerMappings"]
		page_slug = None
		if offer_mappings:
			page_slug = game_info_json["offerMappings"][0]["pageSlug"]
		if page_slug is None and game_info_json["catalogNs"]["mappings"]:
			page_slug = game_info_json["catalogNs"]["mappings"][0]["pageSlug"]
		if page_slug is None and game_info_json["productSlug"]:
			page_slug = game_info_json["productSlug"]
		if page_slug is None:
			raise ValueError("Epic game has no store page slug")
		if "bundles" in [category["path"] for category in game_info_json["categories"]]:
			page_slug = "bundles/" + page_slug
		else:
			page_slug = "p/" + page_slug

		return f"https://www.epicgames.com/store/en-US/{page_slug}"

	def _create_start_date(self, game_info_json: dict[str, Any]) -> datetime:
		return self._parse_date(game_info_json, "startDate")

	def _create_end_date(self, game_info_json: dict[str, Any]) -> datetime:
		return self._parse_date(game_info_json, "endDate")

	def _parse_date(self, game_info_json: dict[str, Any], date_field: str) -> datetime:
		date_str = game_info_json["promotions"]["promotionalOffers"][0]["promotionalOffers"][0][date_field]
		return datetime.fromisoformat(date_str).astimezone(UTC)

	def _create_discount_price(self, game_price: dict[str, Any]) -> str | int:
		discount_price = game_price["totalPrice"]["discountPrice"]
		return "Free" if discount_price == 0 else discount_price

	def _create_thumbnail(self, key_images: list[dict[str, Any]]) -> str:
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
		# Transport and HTTP failures propagate so callers can retry instead of
		# mistaking an outage for an empty listing.
		async with aiohttp.ClientSession() as session:
			session: aiohttp.ClientSession
			async with session.get(EpicGames.api_url) as response:
				response.raise_for_status()
				try:
					json_response = await response.json()
				except ValueError as error:
					raise ProviderError("Epic returned invalid JSON") from error
		try:
			all_games = json_response["data"]["Catalog"]["searchStore"]["elements"]
		except (KeyError, TypeError) as error:
			raise ProviderError("Epic response has no game listing") from error
		if not isinstance(all_games, list):
			raise ProviderError("Epic game listing is not a list")

		current_free_games: list[Game] = []
		for game in all_games:
			try:
				if _is_current_free_game(game):
					current_free_games.append(EpicGamesGame(game))
			except (KeyError, TypeError, IndexError, ValueError) as ex:
				logger.error(f"Skipping malformed Epic game entry: {ex!r}")
		return current_free_games


def _is_current_free_game(game: dict[str, Any]) -> bool:
	if not game["promotions"] or not game["promotions"]["promotionalOffers"]:
		return False
	if not game["price"] or not game["price"]["totalPrice"]:
		return False
	if game["price"]["totalPrice"]["discountPrice"] != 0:
		return False
	categories = [category["path"] for category in game["categories"]]
	if not all(category in categories for category in ("freegames", "games")):
		return False
	return game["status"] == "ACTIVE"
