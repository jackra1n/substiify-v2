import asyncio
import logging

import discord
from discord.ext import commands, tasks

from core import Substiify, config
from utils.url_rules import URLRulesCleaner, load_compiled_rules, refresh_compiled_rules

logger = logging.getLogger(__name__)
save_message: dict[int, discord.Message] = {}
reply_to_original: dict[int, int] = {}
resend_attempts: dict[int, int] = {}


class URLCleaner(commands.Cog):
	def __init__(self, bot: Substiify):
		self.bot = bot
		self.cleaner: URLRulesCleaner | None = None
		self.cooldown = commands.CooldownMapping.from_cooldown(2, 6.0, commands.BucketType.user)
		self._rules_ready = asyncio.Event()
		self._initialization_task = asyncio.create_task(self._initialize_cleaner())
		self.refresh_rules.start()

	async def _initialize_cleaner(self) -> None:
		try:
			self.cleaner = URLRulesCleaner(await load_compiled_rules())
			logger.info("Loaded URL cleaning rules")
		except Exception as exc:
			logger.error(f"Failed to initialize URL cleaning rules: {exc}")
		finally:
			self._rules_ready.set()

	async def cog_load(self) -> None:
		await self._rules_ready.wait()

	async def cog_unload(self) -> None:
		self.refresh_rules.cancel()
		if not self._initialization_task.done():
			self._initialization_task.cancel()

	@tasks.loop(hours=24)
	async def refresh_rules(self) -> None:
		await self._rules_ready.wait()
		try:
			self.cleaner = URLRulesCleaner(await refresh_compiled_rules())
			logger.info("Refreshed URL cleaning rules cache")
		except Exception as exc:
			logger.warning(f"Failed to refresh URL cleaning rules: {exc}")

	@refresh_rules.before_loop
	async def before_refresh_rules(self) -> None:
		await self.bot.wait_until_ready()
		await self._rules_ready.wait()

	def _build_tracking_embed(self, cleaned_urls: list[str], removed_trackers: list[str]) -> discord.Embed:
		embed = discord.Embed(title="Please avoid sending links containing tracking parameters.")
		cleaned_urls_str = "\n".join(cleaned_urls)
		if removed_trackers:
			tracker_list = ", ".join([f"`{tracker}`" for tracker in removed_trackers])
			verb = "are" if len(removed_trackers) > 1 else "is"
			response = f"{tracker_list} {verb} used for tracking."
		else:
			response = "Tracking elements were removed from this link."
		response += f"\n Here's the link without trackers:\n{cleaned_urls_str}"
		embed.description = response
		embed.set_footer(text="You can edit your message to remove trackers, and this message will disappear.")
		return embed

	async def _clean_urls(self, message_content: str) -> tuple[list[str], list[str]]:
		await self._rules_ready.wait()
		if self.cleaner is None:
			return [], []
		return self.cleaner.clean_message_urls(message_content)

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message):
		if message.author.bot:
			return

		if config.BOT_PREFIX and message.content.startswith(config.BOT_PREFIX):
			return

		if not message.guild:
			return

		url_cleaner_settings = await self.bot.db.pool.fetchrow(
			"SELECT * FROM url_cleaner_settings WHERE discord_server_id = $1", message.guild.id
		)
		if not url_cleaner_settings:
			return

		bucket = self.cooldown.get_bucket(message)
		retry_after = bucket.update_rate_limit()
		if retry_after:
			logger.debug(f"User {message.author.id} on cooldown, skipping URL cleaning.")
			return

		cleaned_urls, removed_trackers = self.cleaner.clean_message_urls(message.content)

		if removed_trackers:
			removed_trackers.sort()

			embed = discord.Embed(title="Please avoid sending links containing tracking parameters.")
			tracker_list = ", ".join([f"`{tracker}`" for tracker in removed_trackers])
			verb = "are" if len(removed_trackers) > 1 else "is"
			cleaned_urls_str = "\n".join(cleaned_urls)
			response = f"{tracker_list} {verb} used for tracking."
			response += f"\n Here's the link without trackers:\n{cleaned_urls_str}"
			embed.description = response
			embed.set_footer(text="You can edit your message to remove trackers, and this message will disappear.")
			try:
				reply = await message.reply(embed=embed, mention_author=False)
				save_message[message.id] = reply
				reply_to_original[reply.id] = message.id
			except discord.Forbidden:
				logger.error(
					f"Unable to send url_cleaner message in {message.guild} {message.channel}, missing permissions."
				)
				return

	@commands.Cog.listener()
	async def on_message_edit(self, before: discord.Message, after: discord.Message):
		if after.id in save_message:
			# no need to check if enabled as else we would not have saved the message
			_, removed_trackers = self.cleaner.clean_message_urls(after.content)
			if not removed_trackers:
				reply_message = save_message.pop(after.id)
				await reply_message.delete()
				reply_to_original.pop(reply_message.id, None)
				resend_attempts.pop(after.id, None)

	@commands.Cog.listener()
	async def on_message_delete(self, message: discord.Message):
		if message.id in save_message:
			reply_message = save_message.pop(message.id)
			await reply_message.delete()
			reply_to_original.pop(reply_message.id, None)
			resend_attempts.pop(message.id, None)

		# If a bot reply was deleted, attempt to resend it if the original still has trackers
		if message.id in reply_to_original:
			original_id = reply_to_original.pop(message.id)

			# Clear stale mapping if present
			if original_id in save_message and save_message[original_id].id == message.id:
				save_message.pop(original_id, None)

			# Try to fetch the original message
			try:
				original_msg = await message.channel.fetch_message(original_id)
			except discord.NotFound:
				return

			# Ensure URL cleaner is still enabled for the guild
			if not original_msg.guild:
				return
			url_cleaner_settings = await self.bot.db.pool.fetchrow(
				"SELECT * FROM url_cleaner_settings WHERE discord_server_id = $1", original_msg.guild.id
			)
			if not url_cleaner_settings:
				return

			cleaned_urls, removed_trackers = self.cleaner.clean_message_urls(original_msg.content)
			if not removed_trackers:
				return

			logger.warning(
				"Bot's URL cleanup message has been deleted, but message still has trackers! Attempting to resend"
			)

			# Limit resend attempts to avoid loops
			attempts = resend_attempts.get(original_id, 0)
			if attempts >= 3:
				return
			resend_attempts[original_id] = attempts + 1

			removed_trackers.sort()
			embed = discord.Embed(title="Please avoid sending links containing tracking parameters.")
			tracker_list = ", ".join([f"`{tracker}`" for tracker in removed_trackers])
			verb = "are" if len(removed_trackers) > 1 else "is"
			cleaned_urls_str = "\n".join(cleaned_urls)
			response = f"{tracker_list} {verb} used for tracking."
			response += f"\n Here's the link without trackers:\n{cleaned_urls_str}"
			embed.description = response
			embed.set_footer(text="You can edit your message to remove trackers, and this message will disappear.")

			try:
				await asyncio.sleep(6)
				new_reply = await original_msg.reply(embed=embed, mention_author=False)
				save_message[original_id] = new_reply
				reply_to_original[new_reply.id] = original_id
			except discord.Forbidden:
				logger.error(
					f"Unable to resend url_cleaner message in {original_msg.guild} {original_msg.channel}, missing permissions."
				)
				return

	@commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
	@commands.hybrid_command(usage="urls_cleaner <enable/disable>")
	async def urls_cleaner(self, ctx: commands.Context, enable: bool | None = None):
		"""Enable or disable the URL cleaner in the server.
		If enabled, the bot will notify users if they sent a link with tracking parameters.
		The bot will also resend the link without the tracking parameters.
		"""
		# ensure server and channel are in the database
		await self.bot.db._insert_foundation(ctx.author, ctx.guild, ctx.channel)

		if enable is None:
			enabled = await self.bot.db.pool.fetchrow(
				"SELECT * FROM url_cleaner_settings WHERE discord_server_id = $1", ctx.guild.id
			)
			if enabled:
				await ctx.send("✅ URL cleaner is **ENABLED**.")
			else:
				await ctx.send("❌ URL cleaner is **NOT** enabled.")
		elif enable:
			await self.bot.db.pool.execute(
				"INSERT INTO url_cleaner_settings (discord_server_id) VALUES ($1) ON CONFLICT DO NOTHING", ctx.guild.id
			)
			await ctx.send("✅ URL cleaner **ENABLED**.")
		elif not enable:
			await self.bot.db.pool.execute(
				"DELETE FROM url_cleaner_settings WHERE discord_server_id = $1", ctx.guild.id
			)
			await ctx.send("❌ URL cleaner **DISABLED**.")


async def setup(bot: Substiify):
	await bot.add_cog(URLCleaner(bot))
