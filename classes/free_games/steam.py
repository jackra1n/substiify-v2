import logging
from datetime import datetime

from aiohttp import ClientSession
from bs4 import BeautifulSoup

from .types import Game, Platform

logger = logging.getLogger(__name__)


class SteamGame(Game):
	def __init__(self, game_info: dict) -> None:
		self.title: str = game_info["title"]
		self.start_date: datetime = datetime.now()
		self.end_date: datetime = datetime.strptime(game_info["end_date"], "%d %b, %Y")
		self.original_price: str = game_info["original_price"]
		self.discount_price: str = game_info["discount_price"]
		self.cover_image_url: str = game_info["cover_image_url"]
		self.store_link: str = game_info["store_link"]
		self.platform: Platform = Steam


class Steam(Platform):
	api_url: str = "https://store.steampowered.com/search/?maxprice=free&category1=998&supportedlang=english&specials=1&ndl=1&ignore_preferences=1&cc=us"
	logo_path: str = "https://store.akamai.steamstatic.com/public/shared/images/header/logo_steam.svg"
	name: str = "steam"

	@staticmethod
	async def get_free_games() -> list[Game]:
		async with ClientSession() as session:
			async with session.get(Steam.api_url) as response:
				html_content = await response.text()

		soup = BeautifulSoup(html_content, "html.parser")
		games = []

		search_result_rows = soup.find_all("a", class_="search_result_row")

		for row in search_result_rows:
			title = row.find("span", class_="title").text
			release_date = row.find("div", class_="search_released").text.strip()
			end_date = release_date  # As a placeholder, use the release date as the end date
			original_price = (
				row.find("div", class_="discount_original_price").text
				if row.find("div", class_="discount_original_price")
				else "Free"
			)
			final_price = (
				row.find("div", class_="discount_final_price").text
				if row.find("div", class_="discount_final_price")
				else "Free"
			)
			cover_image_url = row.find("div", class_="col search_capsule").find("img")["src"]
			store_link = row["href"]

			game_info = {
				"title": title,
				"end_date": end_date,
				"original_price": original_price,
				"discount_price": final_price,
				"cover_image_url": cover_image_url,
				"store_link": store_link,
			}

			print(game_info)

			games.append(SteamGame(game_info))

		return games
