from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime


class ProviderError(Exception):
	"""A store answered with data that does not match the expected shape."""


class Game:
	title: str
	start_date: datetime | None
	end_date: datetime | None
	original_price: str
	discount_price: str | int
	cover_image_url: str
	store_link: str
	platform: type[Platform]

	@property
	def promotion_key(self) -> str:
		if self.start_date is None and self.end_date is None:
			return "undated"
		start = self.start_date.astimezone(UTC).isoformat() if self.start_date is not None else ""
		end = self.end_date.astimezone(UTC).isoformat() if self.end_date is not None else ""
		return f"{start}/{end}"


class Platform(ABC):
	api_url: str
	logo_path: str
	name: str

	@staticmethod
	@abstractmethod
	async def get_free_games() -> list[Game]:
		pass
