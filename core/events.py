import logging

import discord
from discord.ext import commands

import core


logger = logging.getLogger(__name__)


class Events(commands.Cog):
	def __init__(self, bot: core.Substiify):
		self.bot = bot

	async def _send_event(self, message: str) -> None:
		logger.info("%s", message)
		if core.config.EVENTS_CHANNEL_ID is None:
			return
		channel = self.bot.get_channel(core.config.EVENTS_CHANNEL_ID)
		if not isinstance(channel, discord.abc.Messageable):
			logger.warning("Events channel %s is unavailable.", core.config.EVENTS_CHANNEL_ID)
			return
		await core.best_effort(channel.send(message), "guild event notification")

	#
	# GUILD EVENTS
	#

	@commands.Cog.listener()
	async def on_guild_join(self, guild: discord.Guild):
		await self.bot.db.sync_guild(guild)
		await self._send_event(f"Joined {guild.owner}'s guild `{guild.name}` ({guild.id})")

	@commands.Cog.listener()
	async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
		await self.bot.db.upsert_server(after)

	@commands.Cog.listener()
	async def on_guild_remove(self, guild: discord.Guild):
		await self._send_event(f"Left {guild.owner}'s guild `{guild.name}` ({guild.id})")

	#
	# CHANNEL EVENTS
	#

	@commands.Cog.listener()
	async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
		await self.bot.db.upsert_channel(channel)

	@commands.Cog.listener()
	async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
		await self.bot.db.upsert_channel(after)


async def setup(bot: core.Substiify):
	await bot.add_cog(Events(bot))
