import logging

import discord
from discord.ext import commands

import core
from database import db_constants as dbc

EVENTS_CHANNEL_ID = 1131685580300877916

logger = logging.getLogger(__name__)


class Events(commands.Cog):
	def __init__(self, bot: core.Substiify):
		self.bot = bot

	@commands.Cog.listener()
	async def on_command(self, ctx: commands.Context):
		if isinstance(ctx.channel, discord.abc.PrivateChannel):
			await self.bot.db.pool.execute(
				dbc.USER_INSERT_QUERY, ctx.author.id, ctx.author.name, ctx.author.display_avatar.url
			)
			await self.bot.db._insert_server_channel(ctx.channel)
		elif isinstance(ctx.channel, (discord.abc.GuildChannel, discord.Thread)):
			await self.bot.db._insert_foundation(ctx.author, ctx.guild, ctx.channel)

	#
	# GUILD EVENTS
	#

	@commands.Cog.listener()
	async def on_guild_join(self, guild: discord.Guild):
		await self.bot.get_channel(EVENTS_CHANNEL_ID).send(f"Joined {guild.owner}'s guild `{guild.name}` ({guild.id})")
		await self.bot.db._insert_server(guild)
		await self.bot.db.pool.executemany(
			dbc.CHANNEL_INSERT_QUERY, [(channel.id, channel.name, channel.guild.id) for channel in guild.channels]
		)

	@commands.Cog.listener()
	async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
		await self.bot.db._insert_server(after)

	@commands.Cog.listener()
	async def on_guild_remove(self, guild: discord.Guild):
		await self.bot.get_channel(EVENTS_CHANNEL_ID).send(f"Left {guild.owner}'s guild `{guild.name}` ({guild.id})")

	#
	# CHANNEL EVENTS
	#

	@commands.Cog.listener()
	async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
		await self.bot.db._insert_server(channel.guild)
		await self.bot.db._insert_server_channel(channel)

	@commands.Cog.listener()
	async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
		await self.bot.db._insert_server_channel(after)


async def setup(bot: core.Substiify):
	await bot.add_cog(Events(bot))
