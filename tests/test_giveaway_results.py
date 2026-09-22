import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import asyncpg
import discord

from core.bot import Substiify
from database import Database
from extensions.giveaways import Giveaways


class CheckpointAckLostPool:
	"""Commit the first announcement checkpoint, then lose its response."""

	def __init__(self, pool):
		self.pool = pool
		self.lost = False

	def __getattr__(self, name):
		return getattr(self.pool, name)

	async def fetchval(self, query, *args):
		value = await self.pool.fetchval(query, *args)
		if not self.lost:
			self.lost = True
			raise ConnectionError("checkpoint response lost")
		return value


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_DSN"), "Set TEST_POSTGRES_DSN to an isolated PostgreSQL database")
class GiveawayResults(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self):
		self.dsn = os.environ["TEST_POSTGRES_DSN"]
		self.schema = "test_" + uuid4().hex
		self.admin = await asyncpg.connect(self.dsn)
		await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
		separator = "&" if "?" in self.dsn else "?"
		self.db = await Database(f"{self.dsn}{separator}search_path={self.schema}").__aenter__()
		self.guild = SimpleNamespace(id=20, name="guild")
		self.channel = SimpleNamespace(id=30, name="channel", guild=self.guild)
		self.host = SimpleNamespace(id=11, name="host", display_avatar=SimpleNamespace(url="avatar"))
		await self.db.prepare_command_context(self.host, self.guild, self.channel)
		self.entrant_ids = [11, 12, 12, 13]
		self.sent = []
		self.edited = []
		self.channel.send = AsyncMock(side_effect=self.send)
		self.source = SimpleNamespace(
			id=900,
			author=SimpleNamespace(id=99),
			guild=self.guild,
			channel=self.channel,
			reactions=[SimpleNamespace(emoji="🎉", users=self.entrants)],
			edit=AsyncMock(side_effect=self.edit),
		)
		self.channel.fetch_message = AsyncMock(return_value=self.source)
		self.bot = SimpleNamespace(
			db=self.db, user=SimpleNamespace(id=99), fetch_channel=AsyncMock(return_value=self.channel)
		)
		self.cog = Giveaways(cast(Substiify, self.bot))
		self.source.embeds = [self.cog.create_giveaway_embed("<@11>", "prize", 2)]
		self.ctx = SimpleNamespace(
			guild=self.guild,
			channel=self.channel,
			author=self.host,
			interaction=None,
			fetch_message=self.channel.fetch_message,
			send=AsyncMock(),
		)
		self.sample = Mock(side_effect=lambda population, count: population[:count])
		self.rng = patch("extensions.giveaways.secrets.SystemRandom", return_value=SimpleNamespace(sample=self.sample))
		self.rng.start()
		self.addCleanup(self.rng.stop)

	async def asyncTearDown(self):
		await self.db.__aexit__(None, None, None)
		await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
		await self.admin.close()

	async def entrants(self):
		for user_id in self.entrant_ids:
			yield SimpleNamespace(id=user_id, bot=False)

	async def send(self, content):
		self.sent.append(content)
		await asyncio.sleep(0)
		return SimpleNamespace(id=1000 + len(self.sent))

	async def edit(self, *, embed):
		self.source.embeds = [embed]
		self.edited.append(embed.to_dict())

	async def create_giveaway(self):
		return await self.db.pool.fetchrow(
			"""INSERT INTO giveaway(discord_user_id, end_date, prize, discord_server_id,
			discord_channel_id, discord_message_id, winners_count)
			VALUES (11, CURRENT_TIMESTAMP - INTERVAL '1 second', 'prize', 20, 30, 900, 2) RETURNING *"""
		)

	async def result(self, giveaway, version=1):
		return await self.db.pool.fetchrow(
			"SELECT * FROM giveaway_result WHERE giveaway_id = $1 AND version = $2", giveaway["id"], version
		)

	async def expire_lease(self, giveaway):
		await self.db.pool.execute(
			"UPDATE giveaway_result SET delivery_until = clock_timestamp() - INTERVAL '1 second' WHERE giveaway_id = $1 AND delivery_token IS NOT NULL",
			giveaway["id"],
		)

	async def fail_first_edit(self, *, embed):
		# Even an accepted edit with a lost response must be safe to repeat.
		await self.edit(embed=embed)
		if len(self.edited) == 1:
			raise RuntimeError("edit response lost")

	async def test_edit_failure_resumes_same_result_without_reannouncement(self):
		giveaway = await self.create_giveaway()
		self.source.edit.side_effect = self.fail_first_edit
		with self.assertRaisesRegex(RuntimeError, "edit response lost"):
			await self.cog._process_giveaway(giveaway)
		pending = await self.result(giveaway)
		self.assertIsNotNone(pending["announcement_message_id"])
		self.assertIsNone(pending["completed_at"])
		# Restart: no in-memory selection or delivery state survives.
		await Giveaways(cast(Substiify, self.bot))._process_giveaway(giveaway)
		await self.cog._process_giveaway(giveaway)
		completed = await self.result(giveaway)
		self.assertEqual(self.sample.call_count, 1)
		self.assertEqual(len(self.sent), 1)
		self.assertEqual(completed["winner_ids"], pending["winner_ids"])
		self.assertIsNotNone(completed["completed_at"])
		self.assertEqual(self.edited[0], self.edited[1])
		self.assertEqual(sum(field["name"].startswith("Congratulations") for field in self.edited[1]["fields"]), 1)

	async def test_concurrent_workers_select_and_announce_once(self):
		giveaway = await self.create_giveaway()
		await asyncio.gather(*(self.cog._process_giveaway(giveaway) for _ in range(6)))
		result = await self.result(giveaway)
		self.assertEqual(result["winner_ids"], [11, 12])
		self.assertEqual(self.sample.call_count, 1)
		self.assertEqual(len(self.sent), 1)
		self.assertIsNotNone(result["completed_at"])

	async def test_empty_result_cannot_gain_late_entrants(self):
		giveaway = await self.create_giveaway()
		self.entrant_ids = []
		self.source.edit.side_effect = self.fail_first_edit
		with self.assertRaises(RuntimeError):
			await self.cog._process_giveaway(giveaway)
		self.entrant_ids = [11, 12]
		await self.cog._process_giveaway(giveaway)
		result = await self.result(giveaway)
		self.assertEqual(result["winner_ids"], [])
		self.assertIsNotNone(result["completed_at"])
		self.assertEqual(len(self.sent), 1)
		self.assertIn("no one entered", self.sent[0])
		self.assertEqual(self.sample.call_count, 0)

	async def test_lost_selection_response_never_redraws(self):
		giveaway = await self.create_giveaway()
		select = self.cog._select_giveaway_result

		async def lose_response(*args, **kwargs):
			await select(*args, **kwargs)
			raise ConnectionError("selection commit response lost")

		self.cog._select_giveaway_result = lose_response
		with self.assertRaises(ConnectionError):
			await self.cog._process_giveaway(giveaway)
		selected = await self.result(giveaway)
		self.assertEqual(self.sent, [])
		await Giveaways(cast(Substiify, self.bot))._process_giveaway(giveaway)
		self.assertEqual((await self.result(giveaway))["winner_ids"], selected["winner_ids"])
		self.assertEqual(self.sample.call_count, 1)
		self.assertEqual(len(self.sent), 1)

	async def test_lost_announcement_checkpoint_response_resumes_without_send(self):
		giveaway = await self.create_giveaway()
		proxy_bot = SimpleNamespace(**vars(self.bot))
		proxy_bot.db = SimpleNamespace(pool=CheckpointAckLostPool(self.db.pool))
		with self.assertRaises(ConnectionError):
			await Giveaways(cast(Substiify, proxy_bot))._process_giveaway(giveaway)
		checkpoint = await self.result(giveaway)
		self.assertIsNotNone(checkpoint["announcement_message_id"])
		await self.expire_lease(giveaway)
		await self.cog._process_giveaway(giveaway)
		self.assertEqual((await self.result(giveaway))["winner_ids"], checkpoint["winner_ids"])
		self.assertEqual(self.sample.call_count, 1)
		self.assertEqual(len(self.sent), 1)

	async def test_lost_discord_ack_can_repeat_only_the_same_result_after_lease(self):
		giveaway = await self.create_giveaway()

		async def lose_discord_response(content):
			await self.send(content)
			raise TimeoutError("Discord accepted the announcement, but its response was lost")

		self.channel.send.side_effect = lose_discord_response
		with self.assertRaises(TimeoutError):
			await self.cog._process_giveaway(giveaway)
		await self.cog._process_giveaway(giveaway)
		self.assertEqual(len(self.sent), 1)
		self.channel.send.side_effect = self.send
		await self.expire_lease(giveaway)
		await self.cog._process_giveaway(giveaway)
		self.assertEqual(len(self.sent), 2)
		self.assertEqual(self.sent[0], self.sent[1])
		self.assertEqual(self.sample.call_count, 1)
		self.assertIsNotNone((await self.result(giveaway))["completed_at"])

	async def test_cancellation_blocks_stale_settlement_and_reroll(self):
		giveaway = await self.create_giveaway()
		await Giveaways.stop.callback(self.cog, self.ctx, self.source.id)
		await self.cog._process_giveaway(giveaway)
		await Giveaways.reroll.callback(self.cog, self.ctx, self.source.id)
		self.assertIsNone(await self.result(giveaway))
		self.assertEqual(self.sent, [])
		self.assertEqual(self.sample.call_count, 0)
		self.assertIsNotNone(
			await self.db.pool.fetchval("SELECT cancelled_at FROM giveaway WHERE id = $1", giveaway["id"])
		)

	async def test_active_delivery_cannot_be_cancelled_and_recovers_after_crash(self):
		giveaway = await self.create_giveaway()
		selected = await self.cog._select_giveaway_result(giveaway["id"], self.entrant_ids, 2)
		claims = await asyncio.gather(
			*(self.cog._claim_giveaway_delivery(giveaway["id"], selected["version"]) for _ in range(3))
		)
		self.assertEqual(sum(claim is not None for claim in claims), 1)
		await Giveaways.stop.callback(self.cog, self.ctx, self.source.id)
		await self.cog._process_giveaway(giveaway)
		self.assertEqual(self.sent, [])
		await self.expire_lease(giveaway)
		await Giveaways(cast(Substiify, self.bot))._process_giveaway(giveaway)
		await Giveaways.stop.callback(self.cog, self.ctx, self.source.id)
		self.assertIsNone(
			await self.db.pool.fetchval("SELECT cancelled_at FROM giveaway WHERE id = $1", giveaway["id"])
		)
		self.assertEqual((await self.result(giveaway))["winner_ids"], selected["winner_ids"])
		self.assertEqual(self.sample.call_count, 1)
		self.assertEqual(len(self.sent), 1)

	async def test_explicit_reroll_preserves_original_and_fences_old_delivery(self):
		giveaway = await self.create_giveaway()
		await self.cog._process_giveaway(giveaway)
		original = await self.result(giveaway)
		self.sample.side_effect = lambda population, count: list(reversed(population))[:count]
		await Giveaways.reroll.callback(self.cog, self.ctx, self.source.id)
		rerolled = await self.result(giveaway, 2)
		self.assertEqual(dict(await self.result(giveaway)), dict(original))
		self.assertNotEqual(rerolled["winner_ids"], original["winner_ids"])
		last_embed = self.edited[-1]
		await self.cog._deliver_giveaway_result(giveaway, original, self.channel, self.source)
		await self.cog._process_giveaway(giveaway)
		self.assertEqual(len(self.sent), 2)
		self.assertEqual(len(self.edited), 2)
		self.assertEqual(self.edited[-1], last_embed)
		self.assertEqual(self.sample.call_count, 2)
		self.assertIsNotNone(rerolled["completed_at"])

	async def test_pending_result_cannot_be_manually_rerolled(self):
		giveaway = await self.create_giveaway()
		self.source.edit.side_effect = self.fail_first_edit
		with self.assertRaises(RuntimeError):
			await self.cog._process_giveaway(giveaway)
		await Giveaways.reroll.callback(self.cog, self.ctx, self.source.id)
		self.assertIsNone(await self.result(giveaway, 2))
		self.assertEqual(self.sample.call_count, 1)
		self.assertEqual(len(self.sent), 1)

	async def test_historical_bot_owned_giveaway_can_be_explicitly_rerolled(self):
		await Giveaways.reroll.callback(self.cog, self.ctx, self.source.id)
		giveaway = await self.db.pool.fetchrow("SELECT * FROM giveaway WHERE discord_message_id = 900")
		self.assertIsNotNone((await self.result(giveaway))["completed_at"])
		self.assertEqual(len(self.sent), 1)
		await self.cog._process_giveaway(giveaway)
		self.assertEqual(len(self.sent), 1)

	async def test_historical_empty_result_with_removed_host_can_be_rerolled(self):
		self.source.embeds[0].remove_field(0)
		self.source.embeds[0].set_footer(text="No one won the giveaway (no one entered)")
		await Giveaways.reroll.callback(self.cog, self.ctx, self.source.id)
		giveaway = await self.db.pool.fetchrow("SELECT * FROM giveaway WHERE discord_message_id = 900")
		result = await self.result(giveaway)
		self.assertEqual(result["winner_ids"], [11, 12])
		self.assertIsNotNone(result["completed_at"])
		self.assertEqual(len(self.sent), 1)

	async def test_unowned_historical_message_and_wrong_channel_are_rejected(self):
		self.source.author.id = 98
		await Giveaways.reroll.callback(self.cog, self.ctx, self.source.id)
		self.assertEqual(await self.db.pool.fetchval("SELECT count(*) FROM giveaway"), 0)
		self.source.author.id = 99
		giveaway = await self.create_giveaway()
		self.ctx.channel = SimpleNamespace(id=31, guild=self.guild)
		await Giveaways.stop.callback(self.cog, self.ctx, self.source.id)
		await Giveaways.reroll.callback(self.cog, self.ctx, self.source.id)
		self.assertIsNone(
			await self.db.pool.fetchval("SELECT cancelled_at FROM giveaway WHERE id = $1", giveaway["id"])
		)
		self.assertIsNone(await self.result(giveaway))
		self.assertEqual(self.sent, [])

	async def test_deleted_source_is_retained_and_reported_only_once(self):
		giveaway = await self.create_giveaway()
		selected = await self.cog._select_giveaway_result(giveaway["id"], self.entrant_ids, 2)
		self.channel.fetch_message.side_effect = discord.NotFound(
			SimpleNamespace(status=404, reason="Not Found"), {"message": "Unknown Message", "code": 10008}
		)
		with self.assertLogs("extensions.giveaways", level="WARNING") as logs:
			await self.cog._process_giveaway(giveaway)
			await self.cog._process_giveaway(giveaway)
		self.assertEqual(len(logs.records), 1)
		self.assertIsNotNone(
			await self.db.pool.fetchval("SELECT unavailable_at FROM giveaway WHERE id = $1", giveaway["id"])
		)
		self.assertEqual((await self.result(giveaway))["winner_ids"], selected["winner_ids"])
		self.assertEqual(self.sample.call_count, 1)
		self.assertEqual(self.sent, [])


if __name__ == "__main__":
	unittest.main()
