import logging
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import discord
from discord.ext import commands

from core import Substiify, config
from utils.url_rules import DEFAULT_RULES

logger = logging.getLogger(__name__)


class _URLCleaner:
	def __init__(self, rules: list[str]):
		self.universal_rules = set()
		self.rules_by_host = {}
		self.host_rules = {}

		self.create_rules(rules)

	def escape_regexp(self, string):
		"""Escape special characters for use in regex."""
		return re.escape(string).replace(r"\*", ".*")

	def create_rules(self, rules: list[str]):
		for rule in rules:
			split_rule = rule.split("@")
			param_rule = re.compile(f"^{self.escape_regexp(split_rule[0])}$")

			if len(split_rule) == 1:
				self.universal_rules.add(param_rule)
			else:
				host_pattern = split_rule[1].replace("*.", r"(?:.*\.)?")
				host_rule = re.compile(rf"^(www\.)?{host_pattern}$")
				host_rule_str = host_rule.pattern

				if host_rule_str not in self.host_rules:
					self.host_rules[host_rule_str] = host_rule
					self.rules_by_host[host_rule_str] = set()

				self.rules_by_host[host_rule_str].add(param_rule)

	def remove_param(self, rule, param: str, params_dict, removed_params: list):
		"""Remove a specific param from params_dict if it matches the rule."""
		if re.fullmatch(rule, param):
			logger.debug(f"Removing URL param: {param}")
			removed_params.append(param)
			del params_dict[param]

	def replacer(self, url: str):
		"""Clean up the URL by removing tracking parameters based on rules."""
		try:
			parsed_url = urlparse(url)
		except ValueError:
			# if the URL is not parsable, return it as is.
			return url, []
		logger.debug(f"Cleaning URL: {url}")

		query_params = parse_qs(parsed_url.query)
		removed_params = []

		for rule in self.universal_rules:
			for param in list(query_params.keys()):
				self.remove_param(rule, param, query_params, removed_params)

		# apply host-specific rules
		hostname = parsed_url.hostname
		if hostname:
			for host_rule_str, host_rule in self.host_rules.items():
				if re.fullmatch(host_rule, hostname):
					logger.debug(f"Hostname: [{hostname}] matched host rule: {host_rule_str}")
					for rule in self.rules_by_host[host_rule_str]:
						for param in list(query_params.keys()):
							self.remove_param(rule, param, query_params, removed_params)

		if removed_params:
			new_query = urlencode(query_params, doseq=True)
			cleaned_url = urlunparse(parsed_url._replace(query=new_query))
			return cleaned_url, removed_params

		return url, []

	def clean_message_urls(self, message: str):
		"""Extract URLs from the message, clean them, and return the cleaned URLs with removed parameters."""
		url_pattern = re.compile(r"(https?://[^\s<]+)")
		cleaned_urls = []
		removed_trackers = []

		def process_url(match):
			url, removed = self.replacer(match.group(0))
			cleaned_urls.append(url)
			removed_trackers.extend(removed)

		url_pattern.sub(process_url, message)
		return cleaned_urls, removed_trackers


class URLCleaner(commands.Cog):
	def __init__(self, bot: Substiify):
		self.bot = bot
		self.cleaner = _URLCleaner(DEFAULT_RULES)
		self.cooldowns = {}

	@commands.Cog.listener()
	async def on_message(self, message: discord.Message):
		if message.author.bot:
			return

		if message.content.startswith(config.BOT_PREFIX):
			return

		# check if server has URL cleaner enabled
		url_cleaner_settings = await self.bot.db.pool.fetchrow(
			"SELECT * FROM url_cleaner_settings WHERE discord_server_id = $1", message.guild.id
		)
		if not url_cleaner_settings:
			return

		cleaned_urls, removed_trackers = self.cleaner.clean_message_urls(message.content)

		if removed_trackers:
			user_id = message.author.id
			current_time = time.time()

			if user_id in self.cooldowns:
				last_time_check = self.cooldowns[user_id]
				if current_time - last_time_check < 6:
					return
			self.cooldowns[user_id] = current_time

			embed = discord.Embed(title="Please avoid sending links containing tracking parameters.")
			tracker_list = ", ".join([f"`{tracker}`" for tracker in removed_trackers])
			verb = 'are' if len(removed_trackers) > 1 else 'is'
			response = f"{tracker_list} {verb} used for tracking."
			response += f"\n Here's the link without trackers:\n{'\n'.join(cleaned_urls)}"
			embed.description = response
			await message.reply(embed=embed)

	@commands.hybrid_command()
	async def urls_cleaner(self, ctx: commands.Context, enable: bool):
		"""Enable or disable the URL cleaner in the server.
		If enabled, the bot will notify users if they sent a link with tracking parameters.
		The bot will also resend the link without the tracking parameters.
		"""
		if enable:
			await self.bot.db.pool.execute(
				"INSERT INTO url_cleaner_settings (discord_server_id) VALUES ($1) ON CONFLICT DO NOTHING", ctx.guild.id
			)
			await ctx.send("URL cleaner enabled.")
		else:
			await self.bot.db.pool.execute(
				"DELETE FROM url_cleaner_settings WHERE discord_server_id = $1", ctx.guild.id
			)
			await ctx.send("URL cleaner disabled.")


async def setup(bot: Substiify):
	await bot.add_cog(URLCleaner(bot))
