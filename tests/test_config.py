import os
import runpy
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ext import commands

import core.config
from core.bot import Substiify
from core.events import Events
from extensions.feedback import FeedbackModal, FeedbackType
from database import Database
from extensions.music import _report_music_error


class TestConfigSnowflakes(unittest.TestCase):
	def test_blank_owner_rejected(self):
		with patch.dict(os.environ, {"BOT_OWNER_ID": "   "}):
			with self.assertRaises(RuntimeError):
				runpy.run_path(core.config.__file__)["validate"]()

	def test_bad_id_rejected(self):
		invalid_cases = ["abc", "0", str(1 << 64)]
		for raw in invalid_cases:
			with self.subTest(raw=raw):
				with patch.dict(os.environ, {"ERRORS_CHANNEL_ID": raw}):
					with self.assertRaises(RuntimeError):
						core.config._env_id("ERRORS_CHANNEL_ID")

	def test_optional_channel_blank_disables_destination(self):
		with patch.dict(os.environ, {"ERRORS_CHANNEL_ID": "  \t  "}):
			parsed = core.config._env_id("ERRORS_CHANNEL_ID")
			self.assertIsNone(parsed)

	def test_missing_ids_have_no_fallbacks(self):
		with patch.dict(os.environ, {}, clear=True), patch("dotenv.load_dotenv"):
			config = runpy.run_path(core.config.__file__)
		for name in (
			"BOT_OWNER_ID",
			"ERRORS_CHANNEL_ID",
			"EVENTS_CHANNEL_ID",
			"SUGGESTION_CHANNEL_ID",
			"BUG_CHANNEL_ID",
		):
			with self.subTest(name=name):
				self.assertIsNone(config[name])
		with self.assertRaisesRegex(RuntimeError, "BOT_OWNER_ID"):
			config["validate"]()

	def test_bot_cannot_fall_back_to_owner_discovery(self):
		with patch.multiple("core.config", BOT_PREFIX="!", BOT_OWNER_ID=None):
			with self.assertRaisesRegex(RuntimeError, "BOT_OWNER_ID"):
				Substiify(database=cast(Database, SimpleNamespace()))


class TestOperationalRouting(unittest.IsolatedAsyncioTestCase):
	async def test_configured_owner_controls_authorization(self):
		owner_id = 987654321012345678
		with patch("core.config.BOT_PREFIX", "!"), patch("core.config.BOT_OWNER_ID", owner_id):
			bot = Substiify(database=cast(Database, SimpleNamespace()))
		try:
			bot.application_info = AsyncMock(side_effect=AssertionError("Unexpected owner discovery"))
			self.assertTrue(await bot.is_owner(cast(discord.User, SimpleNamespace(id=owner_id))))
			self.assertFalse(await bot.is_owner(cast(discord.User, SimpleNamespace(id=owner_id + 1))))
		finally:
			await bot.close()

	async def test_disabled_errors_channel_skips_discord_send(self):
		bot = SimpleNamespace(get_channel=Mock(), _save_command_error=AsyncMock())
		ctx = SimpleNamespace(
			guild=None,
			author="User#0001",
			command=SimpleNamespace(qualified_name="test", cog=None),
			message=SimpleNamespace(add_reaction=AsyncMock()),
			send=AsyncMock(),
			reply=AsyncMock(),
		)
		with patch("core.config.ERRORS_CHANNEL_ID", None), self.assertLogs("core.bot", level="ERROR"):
			await Substiify.on_command_error(bot, ctx, commands.CommandInvokeError(ValueError("boom")))
			bot.get_channel.assert_not_called()
			bot._save_command_error.assert_awaited_once()
			ctx.send.assert_awaited_once()

			await _report_music_error(bot, None, discord.Embed(title="Error"), "detail")
			bot.get_channel.assert_not_called()

	async def test_configured_errors_channel_routes_to_target(self):
		fake_channel = Mock(spec=discord.abc.Messageable, send=AsyncMock())
		bot = SimpleNamespace(get_channel=Mock(return_value=fake_channel), _save_command_error=AsyncMock())
		ctx = SimpleNamespace(
			guild=None,
			author="User#0001",
			command=SimpleNamespace(qualified_name="test", cog=None),
			message=SimpleNamespace(add_reaction=AsyncMock()),
			send=AsyncMock(),
			reply=AsyncMock(),
		)
		target_channel_id = 999888777666
		with patch("core.config.ERRORS_CHANNEL_ID", target_channel_id), self.assertLogs("core.bot", level="ERROR"):
			await Substiify.on_command_error(bot, ctx, commands.CommandInvokeError(ValueError("boom")))
			bot.get_channel.assert_called_once_with(target_channel_id)
			fake_channel.send.assert_awaited_once()

	async def test_disabled_events_channel_skips_discord_send(self):
		bot = SimpleNamespace(get_channel=Mock())
		events = Events(bot)
		with patch("core.config.EVENTS_CHANNEL_ID", None), self.assertLogs("core.events", level="INFO"):
			await events._send_event("Test guild join")
			bot.get_channel.assert_not_called()

	async def test_disabled_feedback_target_returns_unavailable_without_fetching(self):
		modal = FeedbackModal(FeedbackType.BUG)
		client = SimpleNamespace(get_channel=Mock(), fetch_channel=AsyncMock())
		interaction = SimpleNamespace(
			client=client,
			user=SimpleNamespace(id=1, display_avatar=None),
			guild=None,
			response=SimpleNamespace(is_done=Mock(return_value=False), send_message=AsyncMock()),
			followup=SimpleNamespace(send=AsyncMock()),
		)
		with patch("core.config.BUG_CHANNEL_ID", None):
			await modal.on_submit(interaction)
			client.get_channel.assert_not_called()
			client.fetch_channel.assert_not_called()
			interaction.response.send_message.assert_awaited_once()
			self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])


if __name__ == "__main__":
	unittest.main()
