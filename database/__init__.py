import asyncio
import logging
import os
from typing import Any, Self

import asyncpg
import discord


from .db_constants import CHANNEL_INSERT_QUERY, MESSAGEABLE_INSERT_QUERY, SERVER_INSERT_QUERY, USER_INSERT_QUERY


__all__ = ("Database",)


logger: logging.Logger = logging.getLogger(__name__)


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
		except asyncio.TimeoutError:
			logger.warning("Unable to gracefully shutdown database connection, forcefully continuing.")
		else:
			logger.info("Successfully closed Database connection.")

	async def setup(self) -> None:
		try:
			self.pool = await asyncpg.create_pool(dsn=self.dsn)
		except Exception as exc:
			logger.error("Failed to connect to Postgres.")
			raise RuntimeError("Database initialization failed; see previous error for details.") from exc

		db_schema = os.path.join("resources", "CreateDatabase.sql")
		with open(db_schema) as fp:
			await self.pool.execute(fp.read())

		logger.info("Successfully initialised the Database.")

	async def prepare_command_context(
		self,
		user: discord.User | discord.Member,
		guild: discord.Guild | None,
		channel: discord.abc.Messageable,
	) -> None:
		async with self.pool.acquire() as connection:
			async with connection.transaction():
				if guild is None:
					await connection.execute(USER_INSERT_QUERY, user.id, user.name, user.display_avatar.url)
					await self._insert_server_channel(channel, connection=connection)
				else:
					await self._insert_foundation(user, guild, channel, connection=connection)

	async def _insert_foundation(
		self,
		user: discord.User | discord.Member,
		server: discord.Guild,
		channel: discord.abc.Messageable,
		*,
		connection: asyncpg.Connection | None = None,
	) -> None:
		executor = connection or self.pool
		await executor.execute(USER_INSERT_QUERY, user.id, user.name, user.display_avatar.url)
		await self._insert_server(server, connection=connection)

		if pchannel := channel.parent if isinstance(channel, discord.Thread) else None:
			await executor.execute(MESSAGEABLE_INSERT_QUERY, pchannel.id, pchannel.name, pchannel.guild.id, None)

		p_chan_id = pchannel.id if pchannel else None
		await executor.execute(MESSAGEABLE_INSERT_QUERY, channel.id, channel.name, channel.guild.id, p_chan_id)

	async def _insert_server(self, guild: discord.Guild, *, connection: asyncpg.Connection | None = None) -> None:
		executor = connection or self.pool
		await executor.execute(SERVER_INSERT_QUERY, guild.id, guild.name)

	async def _insert_server_channel(
		self,
		channel: discord.abc.Messageable,
		*,
		connection: asyncpg.Connection | None = None,
	) -> None:
		server_id = channel.guild.id if channel.guild else None
		channel_name = getattr(channel, "name", None) or str(channel)
		executor = connection or self.pool
		await executor.execute(CHANNEL_INSERT_QUERY, channel.id, channel_name, server_id)
