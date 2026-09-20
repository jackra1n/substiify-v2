import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from typing import cast

import asyncpg

from extensions.free_games import FreeGames
from core.bot import Substiify


class FreeGameRetries(unittest.IsolatedAsyncioTestCase):
	async def test_transient_database_failures_retry_without_waiting_an_hour(self):
		attempts = 0

		async def fetch(_query):
			nonlocal attempts
			attempts += 1
			if attempts == 1:
				raise TimeoutError("pool exhausted")
			if attempts == 2:
				raise asyncpg.QueryCanceledError("statement timeout")
			if attempts == 4:
				cog.check_free_games.stop()
			return []

		bot = SimpleNamespace(db=SimpleNamespace(pool=SimpleNamespace(fetch=fetch)), wait_until_ready=AsyncMock())
		cog = FreeGames(cast(Substiify, bot))
		# Exercise the real scheduler: only shorten its backoff, not the hourly
		# interval. Catching either error as success would hang until that hour.
		with patch("discord.ext.tasks.ExponentialBackoff.delay", return_value=0):
			task = cog.check_free_games.start()
			try:
				await asyncio.wait_for(task, timeout=2)
			finally:
				cog.check_free_games.cancel()
		self.assertEqual(attempts, 4)
		self.assertFalse(cog.check_free_games.failed())


if __name__ == "__main__":
	unittest.main()
