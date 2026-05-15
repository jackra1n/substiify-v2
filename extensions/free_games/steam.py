from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp

from .base import Game, Platform

logger = logging.getLogger(__name__)

STEAM_SEARCH_URL = "https://store.steampowered.com/search/results/"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_SEARCH_SEMAPHORE = asyncio.Semaphore(5)


class SteamGame(Game):
	def __init__(self, app_id: str, app_details: dict) -> None:
		self.title: str = app_details["name"]
		self.start_date: datetime = datetime.now()
		self.end_date: datetime | None = None
		price_overview = app_details.get("price_overview", {})
		self.original_price: str = price_overview.get("initial_formatted", "$0.00")
		self.discount_price: str = "Free"
		self.cover_image_url: str = app_details.get("header_image", "")
		self.store_link: str = f"https://store.steampowered.com/app/{app_id}"
		self.platform: Platform = Steam


class Steam(Platform):
	api_url: str = STEAM_SEARCH_URL
	logo_path: str = (
		"https://upload.wikimedia.org/wikipedia/commons/thumb/8/83/Steam_icon_logo.svg/250px-Steam_icon_logo.svg.png"
	)
	name: str = "steam"

	@staticmethod
	async def get_free_games() -> list[Game]:
		search_results = await Steam._fetch_search_results()
		if not search_results:
			return []

		app_ids = Steam._extract_app_ids(search_results)
		if not app_ids:
			return []

		app_details_list = await Steam._fetch_app_details_batch(app_ids)

		current_free_games: list[Game] = []
		for app_id, details in app_details_list:
			if Steam._is_free_promo(details):
				try:
					game = SteamGame(app_id, details)
					current_free_games.append(game)
				except Exception as ex:
					logger.error(f"Error while creating SteamGame for app_id {app_id}: {ex}")
		return current_free_games

	@staticmethod
	async def _fetch_search_results() -> list[dict]:
		params = {"specials": "1", "maxprice": "free", "ndl": "1", "json": "1"}
		try:
			async with aiohttp.ClientSession() as session:
				async with session.get(STEAM_SEARCH_URL, params=params) as response:
					data = await response.json()
					return data.get("items", [])
		except Exception as ex:
			logger.error(f"Error while fetching Steam search results: {ex}")
			return []

	@staticmethod
	def _extract_app_ids(items: list[dict]) -> list[str]:
		app_ids: list[str] = []
		for item in items:
			logo_url = item.get("logo", "")
			app_id = Steam._get_app_id_from_url(logo_url)
			if app_id:
				app_ids.append(app_id)
		return app_ids

	@staticmethod
	def _get_app_id_from_url(url: str) -> str | None:
		try:
			parts = url.split("/apps/")
			if len(parts) > 1:
				return parts[1].split("/")[0]
		except Exception:
			return None
		return None

	@staticmethod
	async def _fetch_app_details(app_id: str, session: aiohttp.ClientSession) -> tuple[str, dict | None]:
		async with STEAM_SEARCH_SEMAPHORE:
			try:
				async with session.get(STEAM_APPDETAILS_URL, params={"appids": app_id}) as response:
					data = await response.json()
					app_data = data.get(str(app_id), {})
					if not app_data.get("success", False):
						return app_id, None
					return app_id, app_data.get("data")
			except Exception as ex:
				logger.error(f"Error fetching app details for {app_id}: {ex}")
				return app_id, None

	@staticmethod
	async def _fetch_app_details_batch(app_ids: list[str]) -> list[tuple[str, dict]]:
		results: list[tuple[str, dict]] = []
		async with aiohttp.ClientSession() as session:
			tasks = [Steam._fetch_app_details(app_id, session) for app_id in app_ids]
			responses = await asyncio.gather(*tasks)
			for app_id, data in responses:
				if data is not None:
					results.append((app_id, data))
		return results

	@staticmethod
	def _is_free_promo(details: dict) -> bool:
		price_overview = details.get("price_overview")
		if not price_overview:
			return False
		return price_overview.get("discount_percent", 0) == 100

	@staticmethod
	def _create_game(game_info_json: str) -> Game:
		raise NotImplementedError("Steam._create_game is not used; games are created in get_free_games()")
