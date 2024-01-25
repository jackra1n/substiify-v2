import logging
from datetime import datetime

import aiohttp
import discord
from core.bot import Substiify
from discord.ext import commands

logger = logging.getLogger(__name__)

EPIC_STORE_FREE_GAMES_API = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
EPIC_GAMES_LOGO_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/31/Epic_Games_logo.svg/50px-Epic_Games_logo.svg.png"


class Game():
    def __init__(self, game_info_json: str) -> None:
        self.title: str = game_info_json["title"]
        self.start_date: datetime = self._create_start_date(game_info_json)
        self.end_date: datetime = self._create_end_date(game_info_json)
        self.original_price: str = game_info_json["price"]["totalPrice"]["fmtPrice"]["originalPrice"]
        self.discount_price: str = self._create_discount_price(game_info_json["price"])
        self.cover_image_url: str = self._create_thumbnail(game_info_json["keyImages"])
        self.epic_store_link: str = self._create_store_link(game_info_json)

    def _create_store_link(self, game_info_json: str) -> str:
        page_slug = game_info_json["offerMappings"][0]["pageSlug"]
        if page_slug is None:
            page_slug = game_info_json["catalogNs"]["mappings"][0]["pageSlug"]
        return f'https://www.epicgames.com/store/en-US/p/{page_slug}'

    def _create_start_date(self, game_info_json: str) -> datetime:
        return self._parse_date(game_info_json, "startDate")

    def _create_end_date(self, game_info_json: str) -> datetime:
        return self._parse_date(game_info_json, "endDate")

    def _parse_date(self, game_info_json: str, date_field: str) -> datetime:
        date_str = game_info_json["promotions"]["promotionalOffers"][0]["promotionalOffers"][0][date_field]
        return datetime.strptime(date_str.split('T')[0], "%Y-%m-%d")

    def _create_discount_price(self, game_price_str: str) -> str:
        discount_price = game_price_str["totalPrice"]["fmtPrice"]["discountPrice"]
        return "Free" if discount_price == "0" else discount_price

    def _create_thumbnail(self, key_images: str) -> str:
        for image in key_images:
            if "OfferImageWide" in image["type"]:
                return image["url"]
        return key_images[0]["url"]
        


class FreeGames(commands.Cog):

    COG_EMOJI = "🕹️"

    def __init__(self, bot: Substiify):
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
        except Exception as ex:
            logger.error(f'Error while getting list of all Epic games: {ex}')

        current_free_games: list[Game] = []
        for game in all_games:
            # Check if game has promotions
            if game["promotions"] is None:
                continue
            # Check if game is free
            if game["price"]["totalPrice"]["fmtPrice"]["discountPrice"] != "0":
                continue
            # Check if the game is currently free
            if game["status"] != "ACTIVE":
                continue
            try:
                current_free_games.append(Game(game))
            except Exception as ex:
                logger.error(f'Error while creating \'Game\' object: {ex}')

        if not current_free_games:
            embed = discord.Embed(color=0x000000)
            embed.description = 'Could not find any currently free games'
            await ctx.send(embed=embed, delete_after=60)

        for game in current_free_games:
            try:
                embed = discord.Embed(title=game.title, url=game.epic_store_link, color=0x000000)
                embed.set_thumbnail(url=f"{EPIC_GAMES_LOGO_URL}")
                available_string = f'started <t:{int(game.start_date.timestamp())}:R>, ends <t:{int(game.end_date.timestamp())}:R>'
                embed.add_field(name="Available", value=available_string, inline=False)
                price_field = f"~~`{game.original_price}`~~ ⟶ `{game.discount_price}`" if game.original_price != "0" else f"`{game.discount_price}`"
                embed.add_field(name="Price", value=price_field, inline=False)
                embed.set_image(url=game.cover_image_url)

                await ctx.send(embed=embed)
            except Exception as ex:
                logger.error(f'Fail while sending free game: {ex}')


async def setup(bot):
    await bot.add_cog(FreeGames(bot))
