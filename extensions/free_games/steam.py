from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import aiohttp

from .base import Game, Platform, ProviderError

logger = logging.getLogger(__name__)

STEAM_SEARCH_URL = "https://store.steampowered.com/search/results/"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_STORE_URL = "https://store.steampowered.com/app"
STEAM_SEMAPHORE = asyncio.Semaphore(5)
STEAM_TIMEZONE = ZoneInfo("America/Los_Angeles")

END_DATE_RE = re.compile(
	r'class="game_purchase_discount_quantity[^"]*"[^>]*>\s*Free to keep when you get it before\s+(.+?)\s*\.',
	re.DOTALL | re.IGNORECASE,
)


class SteamGame(Game):
	def __init__(self, app_id: str, app_details: dict[str, Any], end_date: datetime | None = None) -> None:
		self.title: str = app_details["name"]
		self.start_date: datetime | None = None
		self.end_date: datetime | None = end_date
		price_overview = app_details.get("price_overview", {})
		initial_cents = price_overview.get("initial", 0)
		self.original_price: str = f"${initial_cents / 100:.2f}" if initial_cents else "$0.00"
		self.discount_price: str = "Free"
		self.cover_image_url: str = app_details.get("header_image", "")
		self.store_link: str = f"{STEAM_STORE_URL}/{app_id}"
		self.platform: type[Platform] = Steam


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
	async def _fetch_search_results() -> list[dict[str, Any]]:
		params = {"specials": "1", "maxprice": "free", "ndl": "1", "json": "1", "cc": "us"}
		async with aiohttp.ClientSession() as session:
			async with session.get(STEAM_SEARCH_URL, params=params) as response:
				response.raise_for_status()
				text = await response.text()
				try:
					data = json.loads(text)
				except json.JSONDecodeError as error:
					logger.error(
						f"Steam search returned non-JSON response (status {response.status}, "
						f"content-type {response.headers.get('Content-Type')!r}): {text[:200]!r}"
					)
					raise ProviderError("Steam search returned invalid JSON") from error
				items = data.get("items") if isinstance(data, dict) else None
				if not isinstance(items, list):
					raise ProviderError("Steam search response has no item list")
				return items

	@staticmethod
	def _extract_app_ids(items: list[dict[str, Any]]) -> list[str]:
		app_ids: list[str] = []
		for item in items:
			logo_url = item.get("logo") if isinstance(item, dict) else None
			if not isinstance(logo_url, str):
				continue
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
	async def _fetch_app_details(app_id: str, session: aiohttp.ClientSession) -> tuple[str, dict[str, Any] | None]:
		async with STEAM_SEMAPHORE:
			async with session.get(STEAM_APPDETAILS_URL, params={"appids": app_id, "cc": "us"}) as response:
				response.raise_for_status()
				try:
					data = json.loads(await response.text())
				except json.JSONDecodeError as error:
					raise ProviderError(f"Steam app details for {app_id} returned invalid JSON") from error
				app_data = data.get(str(app_id)) if isinstance(data, dict) else None
				if not isinstance(app_data, dict):
					raise ProviderError(f"Steam app details for {app_id} are malformed")
				if not app_data.get("success", False):
					return app_id, None
				details = app_data.get("data")
				if not isinstance(details, dict):
					raise ProviderError(f"Steam app details for {app_id} have no data")
				return app_id, details

	@staticmethod
	async def _fetch_app_details_batch(app_ids: list[str]) -> list[tuple[str, dict[str, Any]]]:
		results: list[tuple[str, dict[str, Any]]] = []
		failure: Exception | None = None
		answered = 0

		async def fetch_one(app_id: str, session: aiohttp.ClientSession) -> tuple[str, dict[str, Any] | None]:
			nonlocal answered, failure
			try:
				_, data = await Steam._fetch_app_details(app_id, session)
				answered += 1
				return app_id, data
			except Exception as ex:
				logger.error(f"Error fetching app details for {app_id}: {ex}")
				if failure is None:
					failure = ex
				return app_id, None

		async with aiohttp.ClientSession() as session:
			tasks = [fetch_one(app_id, session) for app_id in app_ids]
			responses = await asyncio.gather(*tasks)
			for app_id, data in responses:
				if data is not None:
					results.append((app_id, data))
		if failure is not None and not answered:
			# Every detail fetch failed: this is an outage, and must not look
			# like "no free games". Partial failures stay isolated per app.
			raise failure
		return results

	@staticmethod
	async def _fetch_store_page(app_id: str, session: aiohttp.ClientSession) -> tuple[str, str]:
		async with STEAM_SEMAPHORE:
			try:
				async with session.get(f"{STEAM_STORE_URL}/{app_id}/", params={"l": "english", "cc": "us"}) as response:
					response.raise_for_status()
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

		# Steam's server-rendered English deadline is Pacific wall time, without a year.
		# Choose the nearest year so December/January polls agree without moving an
		# expired promotion into next year as soon as its deadline passes.
		now = datetime.now(STEAM_TIMEZONE)
		candidates = []
		for year in (now.year - 1, now.year, now.year + 1):
			for date_format in ("%b %d %I:%M %p %Y", "%d %b %I:%M %p %Y"):
				try:
					parsed = datetime.strptime(f"{date_str} {year}", date_format)
				except ValueError:
					continue
				candidates.append(parsed.replace(tzinfo=STEAM_TIMEZONE))
				break
		if not candidates:
			logger.debug(f"Could not parse Steam end date: {date_str!r}")
			return None
		return min(candidates, key=lambda date: abs(date - now)).astimezone(UTC)

	@staticmethod
	def _is_free_promo(details: dict[str, Any]) -> bool:
		if details.get("type") != "game":
			return False
		price_overview = details.get("price_overview")
		if not isinstance(price_overview, dict):
			return False
		return price_overview.get("discount_percent", 0) == 100
