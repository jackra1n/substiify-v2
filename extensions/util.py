import asyncio
import platform
import random

import discord
import psutil
from discord import MessageType
from discord.ext import commands

import core
import utils


class Util(commands.Cog):
	COG_EMOJI = "📦"

	def __init__(self, bot: core.Substiify):
		self.bot = bot

	async def _cooldown_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.CommandOnCooldown):
			embed = discord.Embed(
				title="Slow it down!",
				description=f"Try again in {error.retry_after:.2f}s.",
				color=discord.Colour.red(),
			)

			await ctx.send(embed=embed, delete_after=30)
		if isinstance(error, commands.MissingRequiredArgument):
			await ctx.send("Missing the suggestion description", delete_after=30)
		await ctx.message.delete()

	@commands.cooldown(6, 5)
	@commands.hybrid_command(aliases=["av", "pfp"])
	async def avatar(self, ctx: commands.Context, member: discord.Member | discord.User | None = None):
		"""
		Enlarge and view your profile picture or another member
		"""
		member = member or ctx.author
		current_avatar = member.display_avatar
		embed = discord.Embed(
			title=f"{member.display_name}'s avatar",
			url=current_avatar.url,
			color=core.constants.CYAN_COLOR,
		)
		embed.set_image(url=current_avatar.url)
		await ctx.send(embed=embed)

	@avatar.error
	async def avatar_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.MemberNotFound):
			await ctx.send("Member not found", delete_after=30)

	@commands.group(aliases=["c"], invoke_without_command=True)
	@commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
	async def clear(self, ctx: commands.Context, amount: int | None = None):
		"""
		Clears messages within the current channel.
		"""
		if ctx.message.type == MessageType.reply:
			if message := ctx.message.reference.resolved:
				await message.delete()
				await ctx.message.delete()
			return
		if amount is None:
			return await ctx.send("Please specify the amount of messages to delete.", delete_after=30)

		if amount >= 100:
			return await ctx.send("Cannot delete more than 100 messages at a time!")
		if not isinstance(
			ctx.channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread)
		):
			return await ctx.send("Messages can only be cleared in a server channel.")
		await ctx.channel.purge(limit=amount + 1)

	@clear.command(aliases=["bot", "b"])
	@commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
	async def clear_bot(self, ctx: commands.Context, amount: int):
		"""Clears the bot's messages even in DMs"""
		bots_messages = [
			message async for message in ctx.channel.history(limit=amount + 1) if message.author == self.bot.user
		]

		if len(bots_messages) <= 100 and isinstance(ctx.channel, discord.TextChannel):
			await ctx.message.delete()
			await ctx.channel.delete_messages(bots_messages)

		elif isinstance(ctx.channel, discord.DMChannel):
			for message in bots_messages:
				await message.delete()
				await asyncio.sleep(0.75)

	@clear.error
	async def clear_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.MissingRequiredArgument):
			await ctx.send("Please put an amount to clear.")

	@commands.command(aliases=["dink"])
	async def ping(self, ctx: commands.Context):
		"""
		Shows the ping of the bot
		"""
		title = "Donk! 🏓" if "dink" in ctx.message.content.lower() else "Pong! 🏓"
		desc = f"⏱️Ping: `{round(self.bot.latency * 1000)}`ms"
		embed = discord.Embed(title=title, description=desc, color=core.constants.PRIMARY_COLOR)
		await ctx.send(embed=embed)

	@commands.command(name="specialThanks", hidden=True)
	async def special_thanks(self, ctx: commands.Context):
		peeople_who_helped = [
			"<@205704051856244736>",  # @sprutz
			"<@299478604809764876>",  # @thebadgod
			"<@291291715598286848>",  # @joniiiiii
			"<@231151428167663616>",  # @acurisu
			"<@153929916977643521>",  # @battlerush
		]
		random.shuffle(peeople_who_helped)
		embed = discord.Embed(
			title="Special thanks for any help to those people",
			description=" ".join(peeople_who_helped),
			color=core.constants.PRIMARY_COLOR,
		)

		await ctx.send(embed=embed)
		await ctx.message.delete()

	@commands.command()
	@commands.cooldown(3, 30)
	async def info(self, ctx: commands.Context):
		"""
		Shows different technical information about the bot
		"""
		content = ""
		uptime_in_seconds = (discord.utils.utcnow() - self.bot.start_time).total_seconds()
		bot_uptime = utils.seconds_to_human_readable(uptime_in_seconds)

		commit_hash, commit_date = utils.ux.get_last_commit_info()
		if commit_hash != "unknown" and commit_date != "unknown":
			bot_version = f"{self.bot.version} [{commit_hash}] ({commit_date})"
		elif commit_hash != "unknown":
			bot_version = f"{self.bot.version} [{commit_hash}]"
		else:
			bot_version = f"{self.bot.version} [commit info unavailable]"

		cpu_percent = psutil.cpu_percent()
		ram = psutil.virtual_memory()
		ram_used = utils.bytes_to_human_readable((ram.total - ram.available))
		ram_percent = psutil.virtual_memory().percent
		proc = psutil.Process()

		with proc.oneshot():
			memory = proc.memory_full_info()
			content = (
				f"**Instance uptime:** `{bot_uptime}`\n"
				f"**Version:** `{bot_version}` \n"
				f"**Python:** `{platform.python_version()}`\n"
				f"**discord.py:** `{discord.__version__}`\n\n"
				f"**CPU:** `{cpu_percent}%`\n"
				f"**Process RAM:** `{utils.bytes_to_human_readable(memory.uss)}`\n"
				f"**Total RAM:** `{ram_used} ({ram_percent}%)`\n\n"
				f"**Made by:** <@{self.bot.owner_id}>"
			)

		embed = discord.Embed(
			title=f"Info about {ctx.me.display_name}", description=content, color=core.constants.PRIMARY_COLOR
		)
		embed.set_thumbnail(url=ctx.me.display_avatar.url)
		embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar)
		await ctx.send(embed=embed)


async def setup(bot: core.Substiify):
	await bot.add_cog(Util(bot))
