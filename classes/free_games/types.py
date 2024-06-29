from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Game(ABC):
	title: str
	start_date: datetime
	end_date: datetime
	original_price: str
	discount_price: str
	cover_image_url: str
	store_link: str
	platform: Platform


@dataclass
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
