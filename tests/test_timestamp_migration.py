import os
import unittest
from datetime import UTC, datetime
from importlib.resources import files
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import asyncpg

from database import Database


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_DSN"), "Set TEST_POSTGRES_DSN to an isolated PostgreSQL database")
class TimestampMigration(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self):
		self.dsn = os.environ["TEST_POSTGRES_DSN"]
		self.schema = "test_" + uuid4().hex
		self.admin = await asyncpg.connect(self.dsn)
		await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
		await self.admin.execute(f'SET search_path TO "{self.schema}"')
		await self.admin.execute(files("database").joinpath("migrations/001_initial.sql").read_text())
		separator = "&" if "?" in self.dsn else "?"
		self.db = Database(f"{self.dsn}{separator}search_path={self.schema}&timezone=Europe/Zurich")

	async def asyncTearDown(self):
		if hasattr(self.db, "pool"):
			await self.db.__aexit__(None, None, None)
		await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
		await self.admin.close()

	async def test_database_local_application_local_and_utc_instants_survive(self):
		# The old DB default follows Zurich DST, Steam discovery follows the bot's
		# clock, Steam deadlines are Pacific, and Discord/Epic values were UTC.
		dates = [datetime(2026, 1, 15, 12), datetime(2026, 7, 15, 12)]
		for index, date in enumerate(dates, 1):
			await self.admin.execute("INSERT INTO command_history(date) VALUES($1)", date)
			await self.admin.execute("INSERT INTO command_error(date) VALUES($1)", date)
			await self.admin.execute(
				"INSERT INTO giveaway(start_date,end_date,discord_message_id) VALUES($1,$1,$2)", date, index
			)
			await self.admin.execute("INSERT INTO post(discord_message_id,created_at) VALUES($1,$2)", index, date)
			await self.admin.execute(
				"INSERT INTO kasino(question,option1,option2,discord_message_id,created_at) VALUES('q','a','b',$1,$2)",
				index,
				date,
			)
			await self.admin.execute(
				"INSERT INTO feedback(discord_message_id,feedback_type,content,created_at) VALUES($1,'bug','example',$2)",
				index,
				date,
			)
			for store in ("steam", "epicgames"):
				await self.admin.execute(
					"""INSERT INTO free_game_history(title,store_name,store_link,start_date,end_date,created_at)
					VALUES($1,$2,'https://example.invalid',$3,$3,$3)""",
					str(index),
					store,
					date,
				)
		with patch.dict(os.environ, {"LEGACY_DATABASE_TIMEZONE": "", "LEGACY_APPLICATION_TIMEZONE": "Asia/Tokyo"}):
			await self.db.setup()

		for index, date in enumerate(dates, 1):
			local = date.replace(tzinfo=ZoneInfo("Europe/Zurich")).astimezone(UTC)
			utc = date.replace(tzinfo=UTC)
			for table, column in (
				("command_history", "date"),
				("command_error", "date"),
				("giveaway", "start_date"),
				("kasino", "created_at"),
				("feedback", "created_at"),
			):
				with self.subTest(table=table, date=date):
					self.assertEqual(
						await self.db.pool.fetchval(f"SELECT {column} FROM {table} WHERE id=$1", index), local
					)
			self.assertEqual(await self.db.pool.fetchval("SELECT end_date FROM giveaway WHERE id=$1", index), utc)
			self.assertEqual(
				await self.db.pool.fetchval("SELECT created_at FROM post WHERE discord_message_id=$1", index), utc
			)
			for row in await self.db.pool.fetch("SELECT * FROM free_game_history WHERE title=$1", str(index)):
				steam = row["store_name"] == "steam"
				self.assertEqual(row["created_at"], local)
				self.assertEqual(row["start_date"], date.replace(tzinfo=ZoneInfo("Asia/Tokyo") if steam else UTC))
				self.assertEqual(
					row["end_date"], date.replace(tzinfo=ZoneInfo("America/Los_Angeles") if steam else UTC)
				)

		# Runtime defaults must remain UTC, including after the connection is reused.
		for _ in range(2):
			self.assertEqual(await self.db.pool.fetchval("SHOW TimeZone"), "UTC")
			created = await self.db.pool.fetchval("INSERT INTO command_history DEFAULT VALUES RETURNING date")
			self.assertLess(abs((datetime.now(UTC) - created).total_seconds()), 5)

	async def test_invalid_legacy_zone_leaves_existing_schema_untouched(self):
		with patch.dict(os.environ, {"LEGACY_DATABASE_TIMEZONE": "not/a/timezone"}):
			with self.assertRaises(asyncpg.InvalidParameterValueError):
				await self.db.setup()
		self.assertIsNone(await self.admin.fetchval("SELECT to_regclass('schema_migration')"))
		self.assertEqual(
			await self.admin.fetchval(
				"SELECT data_type FROM information_schema.columns WHERE table_schema=$1 AND table_name='command_history' AND column_name='date'",
				self.schema,
			),
			"timestamp without time zone",
		)

	async def test_constraint_failure_rolls_back_timestamp_conversion_and_can_retry(self):
		await self.admin.execute(
			"""INSERT INTO feedback(discord_message_id,feedback_type,content)
			VALUES(42,'bug','first'),(42,'bug','duplicate')"""
		)
		before = await self.admin.fetchval("SELECT created_at FROM feedback WHERE id=1")
		with self.assertRaises(asyncpg.UniqueViolationError):
			await self.db.setup()
		self.assertIsNone(await self.admin.fetchval("SELECT to_regclass('schema_migration')"))
		self.assertEqual(await self.admin.fetchval("SELECT created_at FROM feedback WHERE id=1"), before)
		self.assertEqual(await self.admin.fetchval("SELECT count(*) FROM feedback"), 2)
		await self.admin.execute("DELETE FROM feedback WHERE id=2")
		await self.db.setup()
		self.assertEqual(
			await self.db.pool.fetchval("SELECT created_at FROM feedback WHERE id=1"),
			before.replace(tzinfo=ZoneInfo("Europe/Zurich")),
		)


if __name__ == "__main__":
	unittest.main()
