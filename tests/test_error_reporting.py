import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from typing import cast

import aiohttp
import discord
import wavelink
from discord.ext import commands

from core.bot import Substiify
from core.custom_logger import CustomLogFormatter, PlainLogFormatter
from extensions.karma import Karma
from extensions.kasino import Kasino
from extensions.music import Music, NoVoiceChannel, TrackLoadFailed
from extensions.owner import Owner


class ConnectionLoggingTests(unittest.TestCase):
	def test_expected_retry_is_concise_but_file_keeps_traceback(self):
		try:
			raise aiohttp.ClientConnectionResetError("Cannot write to closing transport")
		except aiohttp.ClientConnectionResetError:
			import sys

			record = logging.LogRecord(
				"discord.ext.tasks",
				logging.ERROR,
				__file__,
				1,
				"Handling exception in internal background task %s. Retrying in %.2fs",
				("Owner.status_task", 1.8),
				sys.exc_info(),
			)
		# Both handler orders must preserve the full file diagnostics.
		for formatters in ((CustomLogFormatter(), PlainLogFormatter()), (PlainLogFormatter(), CustomLogFormatter())):
			outputs = {type(formatter): formatter.format(record) for formatter in formatters}
			self.assertNotIn("\n", outputs[CustomLogFormatter])
			self.assertIn("ClientConnectionResetError", outputs[CustomLogFormatter])
			self.assertIn("1.80", outputs[CustomLogFormatter])
			self.assertIn("Traceback", outputs[PlainLogFormatter])

	def test_unexpected_retry_error_keeps_traceback(self):
		try:
			raise ValueError("unexpected bug")
		except ValueError:
			import sys

			record = logging.LogRecord(
				"discord.client",
				logging.ERROR,
				__file__,
				1,
				"Attempting a reconnect in %.2fs",
				(1.0,),
				sys.exc_info(),
			)
		self.assertIn("Traceback", CustomLogFormatter().format(record))


class StatusLoggingTests(unittest.IsolatedAsyncioTestCase):
	async def status_failure(self, error: Exception) -> logging.LogRecord:
		bot = SimpleNamespace(
			is_ready=Mock(return_value=True),
			guilds=[],
			change_presence=AsyncMock(side_effect=error),
		)
		cog = Owner(cast(Substiify, bot))
		with self.assertLogs("extensions.owner", level="ERROR") as logs:
			await cog.status_task.coro(cog)
		self.assertEqual(len(logs.records), 1)
		return logs.records[0]

	async def test_disconnected_status_update_is_concise_only_on_console(self):
		record = await self.status_failure(aiohttp.ClientConnectionResetError("Cannot write to closing transport"))
		for formatters in (
			(CustomLogFormatter(), PlainLogFormatter()),
			(PlainLogFormatter(), CustomLogFormatter()),
		):
			outputs = {type(formatter): formatter.format(record) for formatter in formatters}
			self.assertNotIn("\n", outputs[CustomLogFormatter])
			self.assertIn("ClientConnectionResetError", outputs[CustomLogFormatter])
			self.assertIn("Cannot write to closing transport", outputs[CustomLogFormatter])
			self.assertIn("Traceback", outputs[PlainLogFormatter])

	async def test_unexpected_status_failure_keeps_console_traceback(self):
		record = await self.status_failure(ValueError("unexpected status bug"))
		self.assertIn("Traceback", CustomLogFormatter().format(record))
		self.assertIn("ValueError", CustomLogFormatter().format(record))


class MusicErrorReportingTests(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self):
		self.bot = SimpleNamespace(_save_command_error=AsyncMock(), get_channel=Mock(return_value=None))
		self.bot.on_command_error = Substiify.on_command_error.__get__(self.bot)
		self.ctx = SimpleNamespace(
			command=SimpleNamespace(qualified_name="play", cog=SimpleNamespace(qualified_name="Music")),
			author="listener",
			guild=None,
			message=SimpleNamespace(add_reaction=AsyncMock()),
			send=AsyncMock(),
			reply=AsyncMock(),
		)
		self.music = Music(cast(Substiify, self.bot))

	async def dispatch_error(self, error):
		await self.music.cog_command_error(self.ctx, error)
		await Substiify.on_command_error(cast(Substiify, self.bot), cast(commands.Context, self.ctx), error)

	async def test_wrapped_backend_failure_replies_and_is_persisted(self):
		try:
			try:
				raise aiohttp.ClientConnectionResetError("private backend detail")
			except aiohttp.ClientConnectionResetError as cause:
				raise TrackLoadFailed() from cause
		except TrackLoadFailed as error:
			wrapped = commands.CommandInvokeError(error)
		with self.assertLogs("core.bot", level="WARNING"):
			await self.dispatch_error(wrapped)
		embed = self.ctx.send.call_args.kwargs["embed"]
		self.assertNotIn("private backend detail", embed.description)
		self.bot._save_command_error.assert_awaited_once()
		self.ctx.send.assert_awaited_once()

	async def test_wavelink_failure_is_reported_once_without_exposing_backend_details(self):
		failure = wavelink.WavelinkException("private Lavalink detail")
		with self.assertLogs("core.bot", level="WARNING"):
			await self.dispatch_error(commands.CommandInvokeError(failure))
		self.ctx.send.assert_awaited_once()
		self.assertNotIn("private Lavalink detail", self.ctx.send.call_args.kwargs["embed"].description)
		self.bot._save_command_error.assert_awaited_once_with(self.ctx, failure)

	async def test_unexpected_music_error_is_not_swallowed(self):
		with self.assertLogs("core.bot", level="ERROR") as logs:
			await self.dispatch_error(commands.CommandInvokeError(ValueError("unexpected bug")))
		self.ctx.send.assert_awaited_once()
		self.assertIn("ValueError", "\n".join(logs.output))
		self.bot._save_command_error.assert_awaited_once()

	async def test_user_error_has_one_response_without_incident_report(self):
		await self.dispatch_error(commands.CommandInvokeError(NoVoiceChannel()))
		self.assertEqual(self.ctx.send.await_count + self.ctx.reply.await_count, 1)
		self.bot._save_command_error.assert_not_awaited()


class FakeErrorsChannel(discord.abc.Messageable):
	def __init__(self):
		self.send = AsyncMock()


class KarmaErrorReportingTests(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self):
		self.db = SimpleNamespace(
			prepare_command_context=AsyncMock(),
			pool=SimpleNamespace(execute=AsyncMock(), fetch=AsyncMock(return_value=[])),
			upsert_channel=AsyncMock(),
		)
		self.enterContext(patch("core.config.ERRORS_CHANNEL_ID", 30))
		with patch.multiple("core.config", BOT_PREFIX="!", BOT_OWNER_ID=12):
			self.bot = Substiify(database=self.db)
		await self.bot._async_setup_hook()
		self.bot.command_prefix = "!"
		self.user_data = {
			"id": str(self.bot.owner_id),
			"username": "owner",
			"discriminator": "0",
			"avatar": None,
		}
		self.bot._connection.user = discord.ClientUser(
			state=self.bot._connection,
			data={**self.user_data, "id": "99", "username": "test-bot", "bot": True},
		)
		self.channel_data = {"id": "30", "type": 1, "recipients": [self.user_data]}
		self.channel = discord.DMChannel(me=self.bot.user, state=self.bot._connection, data=self.channel_data)
		self.karma = Karma(cast(Substiify, self.bot), [])
		await self.bot.add_cog(self.karma)
		await self.bot.add_cog(Kasino(self.bot))
		self.messages = []
		self.events = []
		self.errors_channel = FakeErrorsChannel()
		self.bot.get_channel = Mock(return_value=self.errors_channel)
		self.bot.on_command_completion = AsyncMock()
		self.bot.on_command_error = AsyncMock(wraps=self.bot.on_command_error)
		self.bot._save_command_error = AsyncMock()
		# Exercise real contexts and Discord response state, replacing only network I/O.
		self.bot.http.request = AsyncMock(side_effect=AssertionError("Unexpected Discord request"))
		self.bot.http.send_message = AsyncMock(side_effect=self.send_message)
		self.bot.http.send_typing = AsyncMock()
		self.bot.http.add_reaction = AsyncMock()
		self.bot.http.delete_message = AsyncMock()
		schedule = self.bot._schedule_event

		def schedule_event(*args, **kwargs):
			task = schedule(*args, **kwargs)
			self.events.append(task)
			return task

		self.bot._schedule_event = schedule_event

	async def asyncTearDown(self):
		for task in self.events:
			if not task.done():
				task.cancel()
		await asyncio.gather(*self.events, return_exceptions=True)
		await self.bot.close()

	def message_data(self, content="", **extra):
		return {
			"id": str(discord.utils.time_snowflake(discord.utils.utcnow())),
			"channel_id": str(self.channel.id),
			"type": 0,
			"content": content,
			"author": self.user_data,
			**extra,
		}

	async def send_message(self, channel_id, *, params):
		self.messages.append(params.payload)
		return self.message_data(**params.payload)

	async def invoke(self, content, *, author=None):
		data = self.message_data(content, **({"author": author} if author is not None else {}))
		message = discord.Message(state=self.bot._connection, channel=self.channel, data=data)
		await self.bot.invoke(await self.bot.get_context(message))
		await self.finish_events()

	async def finish_events(self):
		while self.events:
			await asyncio.wait_for(self.events.pop(0), timeout=2)

	async def test_missing_kasino_argument_replies_once_without_central_report(self):
		await self.invoke("!kasino close")
		self.assertEqual(len(self.messages), 1)
		self.bot._save_command_error.assert_not_awaited()
		self.errors_channel.send.assert_not_awaited()

	async def test_bad_kasino_argument_replies_once_without_central_report(self):
		await self.invoke("!kasino close abc 1")
		self.assertEqual(len(self.messages), 1)
		self.bot._save_command_error.assert_not_awaited()
		self.errors_channel.send.assert_not_awaited()

	async def test_unrecognized_kasino_error_keeps_invocation_for_central_fallback(self):
		# A non-owner fails the command checks, so nothing is locally handled.
		author = {**self.user_data, "id": "11"}
		with self.assertLogs("core.bot", level="ERROR"):
			await self.invoke("!kasino close 1 2", author=author)
		self.assertEqual(len(self.messages), 1)
		self.bot.http.delete_message.assert_not_awaited()
		self.bot._save_command_error.assert_awaited_once()

	async def test_known_donate_user_error_stays_local_without_report(self):
		await self.invoke("!karma donate")
		self.assertEqual(len(self.messages), 1)
		self.bot._save_command_error.assert_not_awaited()
		self.errors_channel.send.assert_not_awaited()

	async def test_unexpected_donate_failure_reaches_central_reporting_only(self):
		failure = ValueError("private donation bug")
		with patch.object(Karma, "_find_guild_user", Mock(side_effect=failure)):
			with self.assertLogs("core.bot", level="ERROR"):
				await self.invoke("!karma donate boom 5")
		self.assertEqual(len(self.messages), 1)
		self.assertNotIn(str(failure), str(self.messages))
		self.bot._save_command_error.assert_awaited_once()
		self.errors_channel.send.assert_awaited_once()
		report = self.errors_channel.send.call_args.kwargs["embed"]
		self.assertIn(str(failure), report.description)


if __name__ == "__main__":
	unittest.main()
