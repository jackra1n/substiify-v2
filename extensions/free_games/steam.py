from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

import aiohttp

from .base import Game, Platform

logger = logging.getLogger(__name__)

STEAM_SEARCH_URL = "https://store.steampowered.com/search/results/"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_STORE_URL = "https://store.steampowered.com/app"
STEAM_SEMAPHORE = asyncio.Semaphore(5)

END_DATE_RE = re.compile(
	r'class="game_purchase_discount_quantity[^"]*"[^>]*>\s*Free to keep when you get it before\s+(.+?)\s*\.',
	re.DOTALL | re.IGNORECASE,
)


class SteamGame(Game):
	def __init__(self, app_id: str, app_details: dict, end_date: datetime | None = None) -> None:
		self.title: str = app_details["name"]
		self.start_date: datetime = datetime.now()
		self.end_date: datetime | None = end_date
		price_overview = app_details.get("price_overview", {})
		initial_cents = price_overview.get("initial", 0)
		self.original_price: str = f"${initial_cents / 100:.2f}" if initial_cents else "$0.00"
		self.discount_price: str = "Free"
		self.cover_image_url: str = app_details.get("header_image", "")
		self.store_link: str = f"{STEAM_STORE_URL}/{app_id}"
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

		free_promo_ids = [app_id for app_id, details in app_details_list if Steam._is_free_promo(details)]

		store_pages = {}
		if free_promo_ids:
			store_pages = await Steam._fetch_store_pages_batch(free_promo_ids)

		current_free_games: list[Game] = []
		for app_id, details in app_details_list:
			if not Steam._is_free_promo(details):
				continue
			try:
				end_date = Steam._parse_end_date_from_html(store_pages.get(app_id, ""))
				game = SteamGame(app_id, details, end_date=end_date)
				current_free_games.append(game)
			except Exception as ex:
				logger.error(f"Error while creating SteamGame for app_id {app_id}: {ex}")
		return current_free_games

	@staticmethod
	async def _fetch_search_results() -> list[dict]:
		params = {"specials": "1", "maxprice": "free", "ndl": "1", "json": "1", "cc": "us"}
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
		async with STEAM_SEMAPHORE:
			try:
				async with session.get(STEAM_APPDETAILS_URL, params={"appids": app_id, "cc": "us"}) as response:
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
	async def _fetch_store_page(app_id: str, session: aiohttp.ClientSession) -> tuple[str, str]:
		async with STEAM_SEMAPHORE:
			try:
				async with session.get(f"{STEAM_STORE_URL}/{app_id}/") as response:
					html = await response.text()
					return app_id, html
			except Exception as ex:
				logger.error(f"Error fetching store page for {app_id}: {ex}")
				return app_id, ""

	@staticmethod
	async def _fetch_store_pages_batch(app_ids: list[str]) -> dict[str, str]:
		results: dict[str, str] = {}
		async with aiohttp.ClientSession() as session:
			tasks = [Steam._fetch_store_page(app_id, session) for app_id in app_ids]
			responses = await asyncio.gather(*tasks)
			for app_id, html in responses:
				if html:
					results[app_id] = html
		return results

	@staticmethod
	def _parse_end_date_from_html(html: str) -> datetime | None:
		if not html:
			return None
		match = END_DATE_RE.search(html)
		if not match:
			return None

		date_str = match.group(1).strip()
		date_str = re.sub(r"\s*@\s*", " ", date_str)
		date_str = re.sub(r"(\d)(am|pm)", r"\1 \2", date_str, flags=re.IGNORECASE)
		date_str = date_str.upper()

		now = datetime.now()
		try:
			parsed = datetime.strptime(date_str, "%d %b %I:%M %p")
		except ValueError:
			logger.debug(f"Could not parse Steam end date: {date_str!r}")
			return None

		parsed = parsed.replace(year=now.year)
		if parsed < now - timedelta(days=1):
			parsed = parsed.replace(year=now.year + 1)

		return parsed

	@staticmethod
	def _is_free_promo(details: dict) -> bool:
		if details.get("type") != "game":
			return False
		price_overview = details.get("price_overview")
		if not price_overview:
			return False
		return price_overview.get("discount_percent", 0) == 100

	@staticmethod
	def _create_game(game_info_json: str) -> Game:
		raise NotImplementedError("Steam._create_game is not used; games are created in get_free_games()")
