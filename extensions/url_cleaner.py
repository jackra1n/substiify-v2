from core import Substiify
import discord
from discord.ext import commands

import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

URL_REGEX = re.compile(
    r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
)

TRACKING_PARAMS = {
    'youtube': ['si', 'utm_source', 'utm_medium', 'utm_campaign'],
    'spotify.com': ['si', 'utm_source', 'utm_medium', 'utm_campaign'],
}

class URLCleaner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.content.startswith(Substiify.get_prefix(message)):
            return


def clean_url(text: str) -> str:
    urls = URL_REGEX.findall(text)
    for url in urls:
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        query_params = parse_qs(parsed_url.query)

        for pattern in TRACKING_PARAMS:
            if pattern in domain:
                for param in TRACKING_PARAMS[pattern]:
                    query_params.pop(param, None)
                
                new_query = urlencode(query_params, doseq=True)
                new_url = urlunparse((parsed_url.scheme, parsed_url.netloc, parsed_url.path, 
                                      parsed_url.params, new_query, parsed_url.fragment))
                return new_url
    return None


async def setup(bot: Substiify):
    await bot.add_cog(URLCleaner(bot))