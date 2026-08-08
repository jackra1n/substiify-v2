import asyncio
import logging

import discord
from discord.ext import commands, tasks

from core import Substiify, config
from utils.url_rules import URLRulesCleaner, load_compiled_rules, refresh_compiled_rules

logger = logging.getLogger(__name__)
MAX_TRACKED_MESSAGES = 3000


class _ReplyTracker:
	def __init__(self, limit: int) -> None:
		self.limit = limit
		self.replies: dict[int, discord.Message] = {}
		self.original_by_reply: dict[int, int] = {}
		self.resend_attempts: dict[int, int] = {}

	def remember(self, original_id: int, reply: discord.Message, *, reset_attempts: bool = True) -> None:
		previous_reply = self.replies.pop(original_id, None)
		if previous_reply is not None:
			self.original_by_reply.pop(previous_reply.id, None)

		self.replies[original_id] = reply
		self.original_by_reply[reply.id] = original_id
		if reset_attempts:
			self.resend_attempts.pop(original_id, None)

		while len(self.replies) > self.limit:
			oldest_original_id = next(iter(self.replies))
			self.pop_original(oldest_original_id)

	def get_reply(self, original_id: int) -> discord.Message | None:
		return self.replies.get(original_id)

	def pop_original(self, original_id: int) -> discord.Message | None:
		reply = self.replies.pop(original_id, None)
		if reply is not None:
			self.original_by_reply.pop(reply.id, None)
		self.resend_attempts.pop(original_id, None)
		return reply

	def pop_reply(self, reply_id: int) -> int | None:
		original_id = self.original_by_reply.pop(reply_id, None)
		if original_id is None:
			return None
		reply = self.replies.get(original_id)
		if reply is not None and reply.id == reply_id:
			self.replies.pop(original_id)
		return original_id

	def attempts(self, original_id: int) -> int:
		return self.resend_attempts.get(original_id, 0)

	def clear_attempts(self, original_id: int) -> None:
		self.resend_attempts.pop(original_id, None)

	def increment_attempts(self, original_id: int) -> None:
		self.resend_attempts[original_id] = self.attempts(original_id) + 1

	def __len__(self) -> int:
		return len(self.replies)


class URLCleaner(commands.Cog):
	def __init__(self, bot: Substiify):
		self.bot = bot
		self.cleaner: URLRulesCleaner | None = None
		self.cooldown = commands.CooldownMapping.from_cooldown(2, 6.0, commands.BucketType.user)
		self._replies = _ReplyTracker(MAX_TRACKED_MESSAGES)
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
		if bucket is None:
			return

		retry_after = bucket.update_rate_limit()
		if retry_after:
			logger.debug(f"User {message.author.id} on cooldown, skipping URL cleaning.")
			return

		cleaned_urls, removed_trackers = await self._clean_urls(message.content)

		if removed_trackers:
			removed_trackers.sort()
			embed = self._build_tracking_embed(cleaned_urls, removed_trackers)
			try:
				reply = await message.reply(embed=embed, mention_author=False)
				self._replies.remember(message.id, reply)
			except discord.Forbidden:
				logger.error(
					f"Unable to send url_cleaner message in {message.guild} {message.channel}, missing permissions."
				)
				return

	@commands.Cog.listener()
	async def on_message_edit(self, before: discord.Message, after: discord.Message):
		reply_message = self._replies.get_reply(after.id)
		if reply_message is not None:
			_, removed_trackers = await self._clean_urls(after.content)
			if not removed_trackers:
				self._replies.pop_original(after.id)
				await reply_message.delete()

	@commands.Cog.listener()
	async def on_message_delete(self, message: discord.Message):
		reply_message = self._replies.pop_original(message.id)
		if reply_message is not None:
			await reply_message.delete()

		original_id = self._replies.pop_reply(message.id)
		if original_id is not None:
			try:
				original_msg = await message.channel.fetch_message(original_id)
			except discord.NotFound:
				self._replies.clear_attempts(original_id)
				return

			if not original_msg.guild:
				self._replies.clear_attempts(original_id)
				return
			url_cleaner_settings = await self.bot.db.pool.fetchrow(
				"SELECT * FROM url_cleaner_settings WHERE discord_server_id = $1", original_msg.guild.id
			)
			if not url_cleaner_settings:
				self._replies.clear_attempts(original_id)
				return

			cleaned_urls, removed_trackers = await self._clean_urls(original_msg.content)
			if not removed_trackers:
				self._replies.clear_attempts(original_id)
				return

			logger.warning(
				"Bot's URL cleanup message has been deleted, but message still has trackers! Attempting to resend"
			)

			if self._replies.attempts(original_id) >= 3:
				self._replies.clear_attempts(original_id)
				return
			self._replies.increment_attempts(original_id)

			removed_trackers.sort()
			embed = self._build_tracking_embed(cleaned_urls, removed_trackers)

			try:
				await asyncio.sleep(6)
				new_reply = await original_msg.reply(embed=embed, mention_author=False)
				self._replies.remember(original_id, new_reply, reset_attempts=False)
			except discord.Forbidden:
				logger.error(
					f"Unable to resend url_cleaner message in {original_msg.guild} {original_msg.channel}, missing permissions."
				)
			finally:
				if self._replies.get_reply(original_id) is None:
					self._replies.clear_attempts(original_id)

	@commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
	@commands.guild_only()
	@commands.hybrid_command(usage="urls_cleaner <enable/disable>")
	async def urls_cleaner(self, ctx: commands.Context, enable: bool | None = None):
		"""Enable or disable the URL cleaner in the server.
		If enabled, the bot will notify users if they sent a link with tracking parameters.
		The bot will also resend the link without the tracking parameters.
		"""
		if not isinstance(ctx.author, discord.Member) or ctx.guild is None:
			await ctx.send("This command can only be used in a server.")
			return

		guild_id = ctx.guild.id
		await self.bot.db._insert_foundation(ctx.author, ctx.guild, ctx.channel)

		if enable is None:
			enabled = await self.bot.db.pool.fetchrow(
				"SELECT * FROM url_cleaner_settings WHERE discord_server_id = $1", guild_id
			)
			if enabled:
				await ctx.send("✅ URL cleaner is **ENABLED**.")
			else:
				await ctx.send("❌ URL cleaner is **NOT** enabled.")
		elif enable:
			await self.bot.db.pool.execute(
				"""INSERT INTO url_cleaner_settings (discord_server_id)
				   VALUES ($1)
				   ON CONFLICT (discord_server_id) DO NOTHING""",
				guild_id,
			)
			await ctx.send("✅ URL cleaner **ENABLED**.")
		elif not enable:
			await self.bot.db.pool.execute("DELETE FROM url_cleaner_settings WHERE discord_server_id = $1", guild_id)
			await ctx.send("❌ URL cleaner **DISABLED**.")


async def setup(bot: Substiify):
	await bot.add_cog(URLCleaner(bot))
