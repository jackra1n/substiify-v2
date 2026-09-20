import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg
import discord
from discord.ext import commands
from discord.webhook.async_ import async_context

from core.bot import Substiify
from extensions.feedback import Feedback


class CommandPreparationTests(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self):
		self.db = SimpleNamespace(prepare_command_context=AsyncMock(), pool=SimpleNamespace(execute=AsyncMock()))
		with patch("core.config.BOT_PREFIX", "!"):
			self.bot = Substiify(database=self.db)
		await self.bot._async_setup_hook()
		self.bot.command_prefix = "!"
		self.user_data = {"id": "11", "username": "listener", "discriminator": "0", "avatar": None}
		self.bot._connection.user = discord.ClientUser(
			state=self.bot._connection,
			data={**self.user_data, "id": "99", "username": "test-bot", "bot": True},
		)
		self.channel_data = {"id": "30", "type": 1, "recipients": [self.user_data]}
		self.channel = discord.DMChannel(me=self.bot.user, state=self.bot._connection, data=self.channel_data)
		self.messages = []
		self.responses = []
		self.actions = []
		self.background = []
		self.events = []
		self.callback = AsyncMock()

		@commands.hybrid_command()
		async def probe(ctx: commands.Context):
			await self.callback()
			await ctx.send("Completed", ephemeral=True)

		self.bot.add_command(probe)
		self.bot.on_command_completion = AsyncMock()
		self.bot.on_command_error = AsyncMock(wraps=self.bot.on_command_error)
		self.bot._save_command_error = AsyncMock(side_effect=self.save_error)
		# Exercise real contexts and Discord response state, replacing only network I/O.
		self.bot.http.request = AsyncMock(side_effect=AssertionError("Unexpected Discord request"))
		self.bot.http.send_message = AsyncMock(side_effect=self.send_message)
		self.bot.http.add_reaction = AsyncMock()
		self.adapter = SimpleNamespace(
			create_interaction_response=AsyncMock(side_effect=self.interaction_response),
			execute_webhook=AsyncMock(side_effect=self.followup),
		)
		self.adapter_token = async_context.set(self.adapter)
		schedule = self.bot._schedule_event

		def schedule_event(*args, **kwargs):
			task = schedule(*args, **kwargs)
			self.events.append(task)
			return task

		async def event_error(*args, **kwargs):
			# Make failures in Discord's asynchronous event handlers fail this test.
			raise

		self.bot._schedule_event = schedule_event
		self.bot.on_error = event_error

	async def asyncTearDown(self):
		tasks = self.background + self.events
		for task in tasks:
			if not task.done():
				task.cancel()
		await asyncio.gather(*tasks, return_exceptions=True)
		async_context.reset(self.adapter_token)
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
		self.actions.append("reply")
		return self.message_data(**params.payload)

	async def interaction_response(self, interaction_id, token, *, params, **kwargs):
		payload = params.payload
		self.responses.append(payload)
		self.actions.append("acknowledgement")
		response = {"interaction": {"id": str(interaction_id)}}
		if payload["type"] == discord.InteractionResponseType.channel_message.value:
			self.messages.append(payload["data"])
			response["resource"] = {"type": payload["type"], "message": self.message_data(**payload["data"])}
		return response

	async def followup(self, webhook_id, token, *, payload, **kwargs):
		self.messages.append(payload)
		self.actions.append("reply")
		return self.message_data(**payload)

	async def save_error(self, ctx, error):
		self.actions.append("persist")

	async def prefix_context(self):
		message = discord.Message(state=self.bot._connection, channel=self.channel, data=self.message_data("!probe"))
		return await self.bot.get_context(message)

	def interaction(self, command="probe", options=None):
		return discord.Interaction(
			state=self.bot._connection,
			data={
				"id": str(discord.utils.time_snowflake(discord.utils.utcnow())),
				"application_id": str(self.bot.user.id),
				"type": discord.InteractionType.application_command.value,
				"token": "offline-test-token",
				"version": 1,
				"attachment_size_limit": 10_000_000,
				"channel": self.channel_data,
				"user": self.user_data,
				"data": {"name": command, "type": 1, "options": options or []},
			},
		)

	async def finish_events(self):
		while self.events:
			await asyncio.wait_for(self.events.pop(0), timeout=2)

	def assert_preparation_error(self, original):
		self.callback.assert_not_awaited()
		self.bot.on_command_error.assert_awaited_once()
		error = self.bot.on_command_error.call_args.args[1]
		self.assertIsInstance(error, commands.CommandInvokeError)
		self.assertIs(error.original, original)
		self.assertIs(error.__cause__, original)
		self.assertEqual(len(self.messages), 1)
		if str(original):
			self.assertNotIn(str(original), str(self.messages[0]))

	async def test_prefix_timeout_replies_without_retrying_database(self):
		error = TimeoutError("private acquisition failure")
		self.db.prepare_command_context.side_effect = error
		with self.assertLogs("core.bot", level="WARNING"):
			await self.bot.invoke(await self.prefix_context())
			await self.finish_events()
		self.assert_preparation_error(error)
		self.assertIn("temporarily unavailable", self.messages[0]["embeds"][0]["description"])
		self.bot._save_command_error.assert_not_awaited()
		self.db.prepare_command_context.assert_awaited_once()

	async def test_unexpected_failure_replies_before_preserving_diagnostics(self):
		error = ValueError("private malformed database value")
		self.db.prepare_command_context.side_effect = error
		with self.assertLogs("core.bot", level="ERROR"):
			await self.bot.invoke(await self.prefix_context())
			await self.finish_events()
		self.assert_preparation_error(error)
		self.assertNotIn("temporarily unavailable", self.messages[0]["embeds"][0]["description"])
		self.assertEqual(self.actions, ["reply", "persist"])
		self.assertIs(self.bot._save_command_error.call_args.args[1], error)

	async def test_schema_failure_is_not_downgraded_to_an_outage(self):
		error = asyncpg.UndefinedTableError("private missing relation")
		self.db.prepare_command_context.side_effect = error
		with self.assertLogs("core.bot", level="ERROR"):
			await self.bot.invoke(await self.prefix_context())
			await self.finish_events()
		self.assert_preparation_error(error)
		self.assertNotIn("temporarily unavailable", self.messages[0]["embeds"][0]["description"])
		self.assertIs(self.bot._save_command_error.call_args.args[1], error)

	async def test_existing_command_error_keeps_permission_response(self):
		error = commands.CheckFailure("private denied access")
		self.db.prepare_command_context.side_effect = error
		with self.assertLogs("core.bot", level="ERROR"):
			await self.bot.invoke(await self.prefix_context())
			await self.finish_events()
		self.callback.assert_not_awaited()
		self.assertIs(self.bot.on_command_error.call_args.args[1], error)
		self.assertEqual(len(self.messages), 1)
		self.assertIn("permission", self.messages[0]["embeds"][0]["description"])
		self.assertEqual(self.actions, ["reply", "persist"])

	async def test_slow_hybrid_preparation_is_cancelled_and_answered_before_deadline(self):
		cancelled = asyncio.Event()

		async def prepare(*args):
			try:
				await asyncio.Event().wait()
			finally:
				cancelled.set()

		self.db.prepare_command_context.side_effect = prepare
		interaction = self.interaction()
		# Simulate time already spent in checks/converters before the DB hook.
		interaction.id = discord.utils.time_snowflake(discord.utils.utcnow() - timedelta(seconds=2))
		with self.assertLogs("core.bot", level="WARNING"):
			async with asyncio.timeout(1):
				await self.bot.tree._call(interaction)
				await self.finish_events()
		error = self.bot.on_command_error.call_args.args[1].original
		self.assertIsInstance(error, TimeoutError)
		self.assert_preparation_error(error)
		self.assertTrue(cancelled.is_set())
		self.assertEqual(interaction.response.type, discord.InteractionResponseType.channel_message)
		self.assertIn("temporarily unavailable", self.messages[0]["embeds"][0]["description"])
		self.assertEqual(len(self.responses), 1)
		self.bot._save_command_error.assert_not_awaited()

	async def test_successful_hybrid_preserves_initial_ephemeral_response(self):
		interaction = self.interaction()
		await self.bot.tree._call(interaction)
		await self.finish_events()
		self.assertFalse(interaction.command_failed)
		self.assertEqual(interaction.response.type, discord.InteractionResponseType.channel_message)
		self.assertEqual(len(self.responses), 1)
		self.assertEqual(self.responses[0]["data"]["flags"] & 64, 64)
		self.assertEqual(self.messages[0]["content"], "Completed")
		self.bot.on_command_error.assert_not_awaited()

	async def test_cog_initial_response_is_not_acknowledged_twice(self):
		class AcknowledgingCog(commands.Cog):
			async def cog_before_invoke(self, ctx):
				await ctx.interaction.response.send_message("Starting", ephemeral=True)

			@commands.hybrid_command()
			async def acknowledged(self, ctx: commands.Context):
				await ctx.send("Completed")

		await self.bot.add_cog(AcknowledgingCog())

		async def prepare(*args):
			await asyncio.sleep(0)

		self.db.prepare_command_context.side_effect = prepare
		interaction = self.interaction("acknowledged")
		with patch.object(self.bot, "_PREPARATION_TIMEOUT", 0):
			await self.bot.tree._call(interaction)
		await self.finish_events()
		self.assertFalse(interaction.command_failed)
		self.assertEqual(len(self.responses), 1)
		self.assertEqual([message["content"] for message in self.messages], ["Starting", "Completed"])
		self.bot.on_command_error.assert_not_awaited()

	async def test_prefix_preparation_does_not_inherit_interaction_deadline(self):
		async def prepare(*args):
			await asyncio.sleep(0)

		self.db.prepare_command_context.side_effect = prepare
		with patch.object(self.bot, "_PREPARATION_TIMEOUT", 0):
			await self.bot.invoke(await self.prefix_context())
		await self.finish_events()
		self.assertEqual([message["content"] for message in self.messages], ["Completed"])
		self.bot.on_command_error.assert_not_awaited()

	async def test_database_query_cancellation_gets_service_reply(self):
		error = asyncpg.QueryCanceledError("private database statement timeout")
		self.db.prepare_command_context.side_effect = error
		with self.assertLogs("core.bot", level="WARNING"):
			await self.bot.invoke(await self.prefix_context())
			await self.finish_events()
		self.assert_preparation_error(error)
		self.assertIn("temporarily unavailable", self.messages[0]["embeds"][0]["description"])
		self.bot._save_command_error.assert_not_awaited()

	async def test_preparation_cancellation_propagates_from_prefix_and_hybrid(self):
		for hybrid in (False, True):
			with self.subTest(hybrid=hybrid):
				started = asyncio.Event()

				async def prepare(*args):
					started.set()
					await asyncio.Event().wait()

				self.db.prepare_command_context.side_effect = prepare
				invocation = (
					self.bot.tree._call(self.interaction()) if hybrid else self.bot.invoke(await self.prefix_context())
				)
				task = asyncio.create_task(invocation)
				self.background.append(task)
				await asyncio.wait_for(started.wait(), timeout=2)
				task.cancel()
				with self.assertRaises(asyncio.CancelledError):
					await task
				await self.finish_events()
		self.callback.assert_not_awaited()
		self.bot.on_command_error.assert_not_awaited()
		self.bot._save_command_error.assert_not_awaited()
		self.assertEqual(self.messages, [])

	async def test_feedback_modal_retains_the_initial_response(self):
		await self.bot.add_cog(Feedback(self.bot))
		interaction = self.interaction("feedback", [{"name": "feedback_type", "type": 3, "value": "bug"}])
		await self.bot.tree._call(interaction)
		await self.finish_events()
		self.assertFalse(interaction.command_failed)
		self.assertEqual(interaction.response.type, discord.InteractionResponseType.modal)
		self.assertEqual(len(self.responses), 1)
		self.db.prepare_command_context.assert_not_awaited()


if __name__ == "__main__":
	unittest.main()
