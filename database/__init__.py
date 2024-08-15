import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Self

import asyncpg
import discord

import core

from .db_constants import CHANNEL_INSERT_QUERY, MESSAGEABLE_INSERT_QUERY, SERVER_INSERT_QUERY, USER_INSERT_QUERY

if TYPE_CHECKING:
	_Pool = asyncpg.Pool[asyncpg.Record]
else:
	_Pool = asyncpg.Pool


__all__ = ("Database",)


logger: logging.Logger = logging.getLogger(__name__)


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
		pool: _Pool | None = await asyncpg.create_pool(dsn=core.config.POSTGRES_DSN)

		if pool is None:
			raise RuntimeError('Unable to intialise the Database, "create_pool" returned None.')

		self.pool = pool

		db_schema = os.path.join("resources", "CreateDatabase.sql")
		with open(db_schema) as fp:
			await self.pool.execute(fp.read())

		logger.info("Successfully initialised the Database.")

	async def _insert_foundation(self, user: discord.Member, server: discord.Guild, channel: discord.abc.Messageable):
		await self.pool.execute(USER_INSERT_QUERY, user.id, user.name, user.display_avatar.url)
		await self.pool.execute(SERVER_INSERT_QUERY, server.id, server.name)

		if pchannel := channel.parent if isinstance(channel, discord.Thread) else None:
			await self.pool.execute(MESSAGEABLE_INSERT_QUERY, pchannel.id, pchannel.name, pchannel.guild.id, None)

		p_chan_id = pchannel.id if pchannel else None
		await self.pool.execute(MESSAGEABLE_INSERT_QUERY, channel.id, channel.name, channel.guild.id, p_chan_id)

	async def _insert_server(self, guild: discord.Guild):
		await self.pool.execute(SERVER_INSERT_QUERY, guild.id, guild.name)

	async def _insert_guild_channel(self, channel: discord.abc.GuildChannel):
		await self.pool.execute(CHANNEL_INSERT_QUERY, channel.id, channel.name, channel.guild.id)
