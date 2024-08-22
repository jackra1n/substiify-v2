from core import Substiify
import discord
from discord.ext import commands

import re
from extensions.url_rules import DEFAULT_RULES
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


class _URLCleaner:
    def __init__(self, rules):
        self.universal_rules = set()
        self.rules_by_host = {}
        self.host_rules = {}

        self.create_rules(rules)

    def escape_regexp(self, string):
        """Escape special characters for use in regex."""
        return re.escape(string).replace(r'\*', '.*')

    def create_rules(self, rules):
        for rule in rules:
            split_rule = rule.split('@')
            param_rule = re.compile(f"^{self.escape_regexp(split_rule[0])}$")

            if len(split_rule) == 1:
                self.universal_rules.add(param_rule)
            else:
                host_rule = re.compile(f"^(www\\.)?{self.escape_regexp(split_rule[1])}$")
                host_rule_str = host_rule.pattern

                if host_rule_str not in self.host_rules:
                    self.host_rules[host_rule_str] = host_rule
                    self.rules_by_host[host_rule_str] = set()

                self.rules_by_host[host_rule_str].add(param_rule)

    def remove_param(self, rule, param, params_dict):
        """Remove a specific param from params_dict if it matches the rule."""
        if re.fullmatch(rule, param):
            del params_dict[param]

    def replacer(self, url):
        """Clean up the URL by removing tracking parameters based on rules."""
        try:
            parsed_url = urlparse(url)
        except ValueError:
            # If the URL is not parsable, return it as is.
            return url

        query_params = parse_qs(parsed_url.query)

        # Apply universal rules
        for rule in self.universal_rules:
            for param in list(query_params.keys()):
                self.remove_param(rule, param, query_params)

        # Apply host-specific rules
        hostname = parsed_url.hostname
        if hostname:
            for host_rule_str, host_rule in self.host_rules.items():
                if re.fullmatch(host_rule, hostname):
                    for rule in self.rules_by_host[host_rule_str]:
                        for param in list(query_params.keys()):
                            self.remove_param(rule, param, query_params)

        # Rebuild the URL without the removed parameters
        new_query = urlencode(query_params, doseq=True)
        cleaned_url = urlunparse(parsed_url._replace(query=new_query))

        return cleaned_url

    def clean_message_urls(self, message):
        """Replace URLs in the message with cleaned URLs."""
        url_pattern = re.compile(r'(https?://[^\s<]+)')
        return url_pattern.sub(lambda match: self.replacer(match.group(0)), message)

class URLCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cleaner = _URLCleaner(DEFAULT_RULES)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.content.startswith(Substiify.get_prefix(message)):
            return
        
    @commands.hybrid_command()
    async def urls_setting(self, ctx: commands.Context):

        pass


async def setup(bot: Substiify):
    await bot.add_cog(URLCleaner(bot))