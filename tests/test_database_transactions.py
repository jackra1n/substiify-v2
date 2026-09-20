import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import asyncpg
import discord

from core.bot import Substiify
from database import Database
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
		self.assertEqual(await self.db.pool.fetchval("SELECT xmin::text FROM discord_user WHERE discord_user_id=11"), before)


if __name__ == "__main__":
	unittest.main()
