import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from typing import cast

import aiohttp
from discord.ext import commands

from core.bot import Substiify
from core.custom_logger import CustomLogFormatter, PlainLogFormatter
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
		self.assertIn("music service", embed.description)
		self.assertNotIn("private backend detail", embed.description)
		self.bot._save_command_error.assert_awaited_once()

	async def test_unexpected_music_error_is_not_swallowed(self):
		with self.assertLogs("core.bot", level="ERROR") as logs:
			await self.dispatch_error(commands.CommandInvokeError(ValueError("unexpected bug")))
		self.assertIn("couldn't complete", self.ctx.send.call_args.kwargs["embed"].description)
		self.assertIn("ValueError", "\n".join(logs.output))
		self.bot._save_command_error.assert_awaited_once()

	async def test_user_error_has_one_response_without_incident_report(self):
		await self.dispatch_error(commands.CommandInvokeError(NoVoiceChannel()))
		self.assertEqual(self.ctx.send.await_count + self.ctx.reply.await_count, 1)
		self.bot._save_command_error.assert_not_awaited()


if __name__ == "__main__":
	unittest.main()
