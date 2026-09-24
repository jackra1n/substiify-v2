import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import discord
from discord.ext import commands

from core.bot import Substiify
from extensions.giveaways import Giveaways


def http_error(cls):
	return cls(Mock(status=403, reason="Forbidden"), "denied")


class TestSafeNotify(unittest.IsolatedAsyncioTestCase):
	def setUp(self):
		self.giveaways = Giveaways.__new__(Giveaways)
		self.giveaways.bot = cast(Substiify, SimpleNamespace())

	async def test_failed_dm_fallback_is_logged(self):
		ctx = SimpleNamespace(
			interaction=None,
			author=SimpleNamespace(send=AsyncMock(side_effect=http_error(discord.Forbidden))),
			send=AsyncMock(side_effect=http_error(discord.Forbidden)),
		)
		with self.assertLogs("extensions.giveaways", level="WARNING"):
			await self.giveaways._safe_notify(cast(commands.Context, ctx), content="hi")
		ctx.author.send.assert_awaited_once()

	async def test_answered_interaction_uses_followup(self):
		interaction = SimpleNamespace(
			response=SimpleNamespace(is_done=Mock(return_value=True), send_message=AsyncMock()),
			followup=SimpleNamespace(send=AsyncMock()),
		)
		ctx = SimpleNamespace(interaction=interaction, send=AsyncMock())
		await self.giveaways._safe_notify(cast(commands.Context, ctx), content="hi")
		interaction.followup.send.assert_awaited_once()
		interaction.response.send_message.assert_not_awaited()
		ctx.send.assert_not_awaited()
