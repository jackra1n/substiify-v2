import asyncio
import logging
from importlib.resources import files
from typing import Any, Protocol, Self

import asyncpg
import discord

from .db_constants import MESSAGEABLE_INSERT_QUERY, SERVER_INSERT_QUERY, USER_INSERT_QUERY

__all__ = ("Database",)
logger = logging.getLogger(__name__)


class _DiscordChannel(Protocol):
	@property
	def id(self) -> int: ...


class _DatabasePool(asyncpg.Pool):
	"""Apply the acquisition deadline to both explicit and convenience queries."""

	def acquire(self, *, timeout=5):
		return super().acquire(timeout=timeout)


class Database:
	pool: asyncpg.Pool

	def __init__(self, dsn: str) -> None:
		self.dsn = dsn

	async def __aenter__(self) -> Self:
		await self.setup()
		return self

	async def __aexit__(self, *args: Any) -> None:
		try:
			await asyncio.wait_for(self.pool.close(), timeout=10)
		except TimeoutError:
			self.pool.terminate()
			logger.warning("Database shutdown timed out; connections were terminated.")
		else:
			logger.info("Successfully closed Database connection.")

	async def setup(self) -> None:
		# Initialize an empty pool first so failures always leave a closable object.
		self.pool = await _DatabasePool(
			self.dsn,
			min_size=0,
			max_size=10,
			max_queries=50000,
			max_inactive_connection_lifetime=300,
			loop=None,
			connection_class=asyncpg.Connection,
			record_class=asyncpg.Record,
			timeout=5,
			command_timeout=15,
			server_settings={"timezone": "UTC", "statement_timeout": "15000", "lock_timeout": "5000"},
		)
		try:
			await self._migrate()
		except BaseException:
			self.pool.terminate()
			raise
		logger.info("Successfully initialised the Database.")

	async def _migrate(self) -> None:
		migrations = sorted(files("database").joinpath("migrations").iterdir(), key=lambda path: path.name)
		async with self.pool.acquire() as connection:
			async with connection.transaction():
				# All instances serialize migration discovery and application together.
				await connection.execute("SELECT pg_advisory_xact_lock(1937072755, 1)")
				await connection.execute("SET LOCAL statement_timeout = '60s'")
				await connection.execute(
					"CREATE TABLE IF NOT EXISTS schema_migration (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
				)
				applied = {row["version"] for row in await connection.fetch("SELECT version FROM schema_migration")}
				known = {path.name for path in migrations if path.name.endswith(".sql")}
				if applied - known:
					raise RuntimeError("Database schema is newer than this application; refusing to start.")
				for migration in migrations:
					if migration.name not in known or migration.name in applied:
						continue
					await connection.execute(migration.read_text(encoding="utf-8"), timeout=60)
					await connection.execute("INSERT INTO schema_migration(version) VALUES($1)", migration.name)
					logger.info("Applied database migration %s", migration.name)

	async def prepare_command_context(
		self,
		user: discord.User | discord.Member,
		guild: discord.Guild | None,
		channel: _DiscordChannel,
		*,
		connection: asyncpg.Connection | None = None,
	) -> None:
		if connection is None:
			async with self.pool.acquire() as connection:
				async with connection.transaction():
					await self.prepare_command_context(user, guild, channel, connection=connection)
			return
		await self.upsert_user(user, connection=connection)
		channel_guild = getattr(channel, "guild", None)
		if guild is not None and (channel_guild is None or channel_guild.id != guild.id):
			await self.upsert_server(guild, connection=connection)
		await self.upsert_channel(channel, connection=connection)

	async def upsert_user(
		self, user: discord.User | discord.Member, *, connection: asyncpg.Connection | None = None
	) -> None:
		executor = connection if connection is not None else self.pool
		await executor.execute(USER_INSERT_QUERY, user.id, user.name, user.display_avatar.url)

	async def upsert_server(self, guild: discord.Guild, *, connection: asyncpg.Connection | None = None) -> None:
		executor = connection if connection is not None else self.pool
		await executor.execute(SERVER_INSERT_QUERY, guild.id, guild.name)

	async def upsert_channel(self, channel: _DiscordChannel, *, connection: asyncpg.Connection | None = None) -> None:
		if connection is None:
			async with self.pool.acquire() as connection:
				async with connection.transaction():
					await self.upsert_channel(channel, connection=connection)
			return
		guild = getattr(channel, "guild", None)
		if guild is not None:
			await self.upsert_server(guild, connection=connection)
		parent = channel.parent if isinstance(channel, discord.Thread) else None
		if parent is not None:
			await connection.execute(MESSAGEABLE_INSERT_QUERY, parent.id, parent.name, parent.guild.id, None)
		await connection.execute(
			MESSAGEABLE_INSERT_QUERY,
			channel.id,
			getattr(channel, "name", None) or str(channel),
			guild.id if guild is not None else None,
			parent.id if parent is not None else None,
		)
