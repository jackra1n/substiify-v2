import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from core.bot import Substiify
from extensions.karma import Karma


class VoteChannelCacheTests(unittest.IsolatedAsyncioTestCase):
	def setUp(self):
		self.channel_id = 42
		self.vote_channels: list[int] = []
		self.db = SimpleNamespace(
			pool=SimpleNamespace(fetch=AsyncMock(return_value=[]), execute=AsyncMock()),
			upsert_channel=AsyncMock(),
		)
		self.karma = Karma(cast(Substiify, SimpleNamespace(db=self.db)), self.vote_channels)
		self.channel = SimpleNamespace(id=self.channel_id, mention=f"<#{self.channel_id}>")
		self.ctx = SimpleNamespace(channel=self.channel, send=AsyncMock())

	async def enable(self, channel=None):
		await self.karma.enable.callback(self.karma, self.ctx, channel)

	async def test_enable_success_populates_cache_once(self):
		await self.enable()
		self.assertEqual(self.vote_channels, [self.channel_id])
		# A repeated enable must not duplicate the cache entry.
		await self.enable()
		self.assertEqual(self.vote_channels, [self.channel_id])

	async def test_enable_already_enabled_populates_cache_once_without_writing(self):
		self.db.pool.fetch.return_value = [{"discord_channel_id": self.channel_id, "upvote": True}]
		await self.enable()
		self.assertEqual(self.vote_channels, [self.channel_id])
		await self.enable()
		self.assertEqual(self.vote_channels, [self.channel_id])

	async def test_enable_database_failures_leave_vote_cache_unchanged(self):
		self.vote_channels.append(7)
		outage = TimeoutError("database unavailable")

		with self.subTest("failed read leaves the cache unchanged"):
			self.db.pool.fetch = AsyncMock(side_effect=outage)
			with self.assertRaises(TimeoutError):
				await self.enable()
			self.assertEqual(self.vote_channels, [7])

		with self.subTest("failed write leaves the cache unchanged"):
			self.db.pool.fetch = AsyncMock(return_value=[])
			self.db.pool.execute = AsyncMock(side_effect=outage)
			with self.assertRaises(TimeoutError):
				await self.enable()
			self.assertEqual(self.vote_channels, [7])


if __name__ == "__main__":
	unittest.main()
