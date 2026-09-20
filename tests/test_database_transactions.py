import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import cast
from uuid import uuid4
from unittest.mock import AsyncMock

import asyncpg
import discord

from core.bot import Substiify
from database import Database
from extensions.free_games import FreeGames
from extensions.free_games.base import Game
from extensions.karma import Karma, KasinoStateError


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_DSN"), "Set TEST_POSTGRES_DSN to an isolated PostgreSQL database")
class DatabaseTransactions(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self):
		self.dsn = os.environ["TEST_POSTGRES_DSN"]
		self.schema = "test_" + uuid4().hex
		self.admin = await asyncpg.connect(self.dsn)
		await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
		separator = "&" if "?" in self.dsn else "?"
		self.db = await Database(f"{self.dsn}{separator}search_path={self.schema}").__aenter__()
		self.guild = cast(discord.Guild, SimpleNamespace(id=20, name="guild"))
		self.channel = SimpleNamespace(id=30, name="channel", guild=self.guild)
		self.users = [
			cast(discord.User, SimpleNamespace(id=i, name=str(i), display_avatar=SimpleNamespace(url="avatar")))
			for i in (11, 12, 13)
		]
		for user in self.users:
			await self.db.prepare_command_context(user, self.guild, self.channel)
		await self.db.pool.executemany(
			"INSERT INTO karma(discord_user_id,discord_server_id,amount) VALUES($1,20,10)",
			[(user.id,) for user in self.users],
		)
		self.cog = Karma(cast(Substiify, SimpleNamespace(db=self.db)), [])

	async def asyncTearDown(self):
		await self.db.__aexit__(None, None, None)
		await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
		await self.admin.close()

	async def create_kasino(self):
		return await self.db.pool.fetchval(
			"""INSERT INTO kasino(question,option1,option2,discord_server_id,discord_channel_id,discord_message_id)
			VALUES('question','a','b',20,30,999) RETURNING id"""
		)

	async def test_concurrent_donations_cannot_overspend(self):
		results = await asyncio.gather(
			self.cog.donate_karma(self.users[0], self.users[1], self.guild, 8),
			self.cog.donate_karma(self.users[0], self.users[2], self.guild, 8),
		)
		self.assertEqual(sorted(results), [False, True])
		self.assertEqual(await self.db.pool.fetchval("SELECT amount FROM karma WHERE discord_user_id=11"), 2)
		self.assertEqual(await self.db.pool.fetchval("SELECT sum(amount) FROM karma"), 30)

	async def test_settlement_is_conserved_and_idempotent(self):
		kasino_id = await self.create_kasino()
		for user, option in zip(self.users, (1, 1, 2)):
			await self.cog.place_kasino_bet(kasino_id, 20, user.id, option, 1)
		results = await asyncio.gather(*(self.cog.settle_kasino(kasino_id, 20, 1) for _ in range(2)))
		self.assertEqual(sorted(result[2] for result in results), [False, True])
		self.assertEqual(sum(bet["payout"] for bet in results[0][1]), 3)
		await self.cog.settle_kasino(kasino_id, 20, 1)
		self.assertEqual(await self.db.pool.fetchval("SELECT sum(amount) FROM karma"), 30)
		with self.assertRaises(KasinoStateError):
			await self.cog.place_kasino_bet(kasino_id, 20, 11, 1, 1)
		with self.assertRaises(KasinoStateError):
			await self.cog.set_kasino_locked(kasino_id, 20, False)

	async def test_failed_payout_rolls_back_every_balance(self):
		kasino_id = await self.create_kasino()
		for user in self.users:
			await self.cog.place_kasino_bet(kasino_id, 20, user.id, 1, 1)
		# Force a real database failure after an earlier participant could be credited.
		await self.db.pool.execute(
			"ALTER TABLE karma ADD CONSTRAINT reject_second_payout CHECK (discord_user_id <> 12 OR amount <= 9)"
		)
		with self.assertRaises(asyncpg.CheckViolationError):
			await self.cog.settle_kasino(kasino_id, 20, 3)
		self.assertEqual(await self.db.pool.fetchval("SELECT sum(amount) FROM karma"), 27)
		self.assertIsNone(await self.db.pool.fetchval("SELECT settled_at FROM kasino WHERE id=$1", kasino_id))
		self.assertEqual(await self.db.pool.fetchval("SELECT count(*) FROM kasino_bet WHERE payout IS NOT NULL"), 0)

	async def test_unchanged_metadata_does_not_rewrite_rows(self):
		before = await self.db.pool.fetchval("SELECT xmin::text FROM discord_user WHERE discord_user_id=11")
		await self.db.prepare_command_context(self.users[0], self.guild, self.channel)
		self.assertEqual(
			await self.db.pool.fetchval("SELECT xmin::text FROM discord_user WHERE discord_user_id=11"), before
		)

	async def test_failed_delivery_retries_only_missing_destination(self):
		second = SimpleNamespace(id=31, name="second", guild=self.guild)
		await self.db.upsert_channel(second)
		channels = {
			30: SimpleNamespace(id=30, send=AsyncMock(side_effect=TimeoutError())),
			31: SimpleNamespace(id=31, send=AsyncMock()),
		}
		cog = FreeGames(cast(Substiify, SimpleNamespace(db=self.db, get_channel=channels.get)))
		game = Game()
		game.title = "promotion"
		game.start_date = game.end_date = None
		game.store_link = "https://example.invalid/game"
		game.platform = SimpleNamespace(name="store", logo_path="https://example.invalid/logo")
		game.original_price = "$10"
		game.discount_price = "Free"
		game.cover_image_url = "https://example.invalid/cover"
		settings = [
			{"discord_channel_id": channel_id, "discord_server_id": 20, "store_name": "store"}
			for channel_id in channels
		]
		with self.assertRaises(TimeoutError):
			await cog._send_free_game(game, settings)
		channels[30].send.side_effect = None
		self.assertEqual(await cog._send_free_game(game, settings), 1)
		self.assertEqual(await cog._send_free_game(game, settings), 0)
		self.assertEqual(channels[30].send.await_count, 2)
		self.assertEqual(channels[31].send.await_count, 1)

	async def test_delivery_claims_are_exclusive_and_expire(self):
		cog = FreeGames(cast(Substiify, SimpleNamespace(db=self.db)))
		game = Game()
		game.start_date = game.end_date = None
		game.store_link = "https://example.invalid/game"
		game.platform = SimpleNamespace(name="store")
		claims = await asyncio.gather(*(cog._claim_delivery(game, 30) for _ in range(3)))
		self.assertEqual(sum(claim is not None for claim in claims), 1)
		previous = next(claim for claim in claims if claim is not None)
		await self.db.pool.execute("UPDATE free_game_delivery SET claimed_until=NOW()-INTERVAL '1 second'")
		replacement = await cog._claim_delivery(game, 30)
		self.assertIsNotNone(replacement)
		self.assertNotEqual(previous, replacement)


if __name__ == "__main__":
	unittest.main()
