import os
import hikari
import lightbulb
import logging
import json

from utils import util, store

if os.name != "nt":
    import uvloop

    uvloop.install()

util.prepareFiles()

logging.getLogger("lightbulb").setLevel(logging.DEBUG)
_LOGGER = logging.getLogger(__name__)

extensions = [
    # "gif",
    # "music",
    # "duel",
    # "daydeal",
    # "epicGames",
    # "util",
    # "giveaway",
    # "fun",
    # "help",
    "util"
]


with open(store.settings_path, "r") as settings_file:
    settings = json.load(settings_file)

if not settings["token"]:
    _LOGGER.error(f"No token in {store.settings_path}! Please add it and try again.")
    exit()

prefix = "<<"


def start_bot():
    bot = lightbulb.Bot(
        prefix=prefix,
        token=settings["token"],
        intents=hikari.Intents.ALL,
    )
    if len(extensions) != 0:
        for ext in extensions:
            bot.load_extension(f"extensions.{ext}")
            _LOGGER.info(f"Loaded extension: {ext}")
    bot.run()


if __name__ == "__main__":
    start_bot()
