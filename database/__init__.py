import asyncio
import logging
import os
from functools import wraps
from typing import TYPE_CHECKING, Any, Self

import asyncpg
import discord

import core

from .db_constants import MESSAGEABLE_INSERT_QUERY, SERVER_INSERT_QUERY, USER_INSERT_QUERY

if TYPE_CHECKING:
	_Pool = asyncpg.Pool[asyncpg.Record]
else:
	_Pool = asyncpg.Pool


__all__ = ("Database",)


logger: logging.Logger = logging.getLogger(__name__)


def transaction(database_action):
	@wraps(database_action)
	async def wrapper(self, *args, **kwargs):
		async with self.pool.acquire() as connection:
			async with connection.transaction():
				return await database_action(self, *args, **kwargs, connection=connection)

	return wrapper


class Database:
	pool: _Pool

	async def __aenter__(self) -> Self:
		await self.setup()
		return self

	async def __aexit__(self, *args: Any) -> None:
		try:
			await asyncio.wait_for(self.pool.close(), timeout=10)
		except TimeoutError:
			logger.warning("Unable to gracefully shutdown database connection, forcefully continuing.")
		else:
			logger.info("Successfully closed Database connection.")

	async def setup(self) -> None:
		pool: _Pool | None = await asyncpg.create_pool(dsn=core.config.POSTGRESQL_DSN)

		if pool is None:
			raise RuntimeError('Unable to intialise the Database, "create_pool" returned None.')

		self.pool = pool

		db_schema = os.path.join("resources", "CreateDatabase.sql")
		with open(db_schema) as fp:
			await self.pool.execute(fp.read())

		logger.info("Successfully initialised the Database.")

	@transaction
	async def execute(self, query, *args, connection: asyncpg.Connection = None, **kwargs) -> str:
		return await connection.execute(query, *args, **kwargs)

	@transaction
	async def executemany(self, query, *args, connection: asyncpg.Connection = None, **kwargs) -> None:
		return await connection.executemany(query, *args, **kwargs)

	@transaction
	async def fetch(self, query, *args, connection: asyncpg.Connection = None, **kwargs) -> list:
		return await connection.fetch(query, *args, **kwargs)

	@transaction
	async def fetchrow(self, query, *args, connection: asyncpg.Connection = None, **kwargs) -> asyncpg.Record | None:
		return await connection.fetchrow(query, *args, **kwargs)

	@transaction
	async def fetchval(self, query, *args, connection: asyncpg.Connection = None, **kwargs) -> Any | None:
		return await connection.fetchval(query, *args, **kwargs)

	async def _insert_foundation(
		db: asyncpg.Connection, user: discord.Member, server: discord.Guild, channel: discord.abc.Messageable
	):
		await db.execute(USER_INSERT_QUERY, user.id, user.name, user.display_avatar.url)
		await db.execute(SERVER_INSERT_QUERY, server.id, server.name)

		if pchannel := channel.parent if isinstance(channel, discord.Thread) else None:
			await db.execute(MESSAGEABLE_INSERT_QUERY, pchannel.id, pchannel.name, pchannel.guild.id, None)

		p_chan_id = pchannel.id if pchannel else None
		await db.execute(MESSAGEABLE_INSERT_QUERY, channel.id, channel.name, channel.guild.id, p_chan_id)
