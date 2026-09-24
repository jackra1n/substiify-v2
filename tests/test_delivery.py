import unittest
from unittest.mock import Mock

import discord

from core import best_effort


async def fail(error):
	raise error


class TestBestEffort(unittest.IsolatedAsyncioTestCase):
	async def test_returns_result(self):
		async def ok():
			return 1

		self.assertEqual(await best_effort(ok()), 1)

	async def test_delivery_failure_is_logged(self):
		error = discord.HTTPException(Mock(status=500, reason="boom"), "boom")
		with self.assertLogs("core.delivery", level="WARNING"):
			self.assertIsNone(await best_effort(fail(error)))

	async def test_programming_error_propagates(self):
		with self.assertRaises(ValueError):
			await best_effort(fail(ValueError()))
