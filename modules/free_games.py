import logging
from datetime import datetime

import aiohttp
import discord
from discord.ext import commands

logger = logging.getLogger('discord')

EPIC_STORE_FREE_GAMES_API = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
EPIC_GAMES_LOGO_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/31/Epic_Games_logo.svg/50px-Epic_Games_logo.svg.png"


class Game():
    def __init__(self, game_info_json: str) -> None:
        self.title = game_info_json["title"]
        self.start_date = datetime.strptime(game_info_json["effectiveDate"].split('T')[0], "%Y-%m-%d")
        self.end_date = self._create_end_date(game_info_json)
        self.original_price = game_info_json["price"]["totalPrice"]["fmtPrice"]["originalPrice"]
        self.discount_price = self._create_discount_price(game_info_json["price"])
        self.cover_image_url = self._create_thumbnail(game_info_json["keyImages"])
        self.epic_store_link = self._create_store_link(game_info_json)

    def _create_store_link(self, game_info_json: str) -> str:
        product_string = game_info_json["productSlug"]
        if product_string is None:
            product_string = game_info_json["urlSlug"]
        return f'https://www.epicgames.com/store/en-US/p/{product_string}'

    def _create_end_date(self, game_info_json: str) -> datetime:
        date_str = game_info_json["promotions"]["promotionalOffers"][0]["promotionalOffers"][0]["endDate"]
        return datetime.strptime(date_str.split('T')[0], "%Y-%m-%d")

    def _create_discount_price(self, game_price_str: str) -> str:
        discount_price = game_price_str["totalPrice"]["fmtPrice"]["discountPrice"]
        return "Free" if discount_price == "0" else discount_price

    def _create_thumbnail(self, key_images: str) -> str:
        for image in key_images:
            if image["type"] == 'OfferImageWide':
                return image["url"]


class FreeGames(commands.Cog):

    COG_EMOJI = "🕹️"

    def __init__(self, bot):
        self.bot = bot

    @commands.cooldown(3, 30)
    @commands.command()
    async def epic(self, ctx):
        """
        Show all free games from Epic Games that are currently available.
        """
        all_games = ''
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(EPIC_STORE_FREE_GAMES_API) as response:
                    json_response = await response.json()
                    all_games = json_response["data"]["Catalog"]["searchStore"]["elements"]
        except Exception as e:
            logger.error(f'Error while getting list of all Epic games: {e}')

        current_free_games = []
        for game in all_games:
            # Check if game has promotions
            if game["promotions"] is None:
                continue
            # Check if game is free
            if game["price"]["totalPrice"]["fmtPrice"]["discountPrice"] != "0":
                continue
            # Check if the game is currently free
            if datetime.strptime(game["effectiveDate"].split('T')[0], "%Y-%m-%d") > datetime.now():
                continue
            try:    
                current_free_games.append(Game(game))
            except Exception as e:
                logger.error(f'Error while creating \'Game\' object: {e}')

        try:
            for game in current_free_games:
                start_date_str = game.start_date.strftime('%d %B %Y')
                end_date_str = game.end_date.strftime('%d %B %Y')
                embed = discord.Embed(title=game.title, url=game.epic_store_link, color=0x000000)
                embed.set_thumbnail(url=f"{EPIC_GAMES_LOGO_URL}")
                embed.add_field(name="Available", value=f'{start_date_str} to {end_date_str}', inline=False)
                embed.add_field(name="Price", value=f"~~`{game.original_price}`~~ ⟶ `{game.discount_price}`", inline=False)
                embed.set_image(url=f"{game.cover_image_url}")

                await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f'Fail while sending free game: {e}')


async def setup(bot):
    await bot.add_cog(FreeGames(bot))
