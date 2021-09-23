import os
import logging
import json
from pathlib import Path

import hikari
import lightbulb

from utils import util, store


def create_bot() -> lightbulb.Bot:
    util.prepareFiles()

    logging.getLogger("lightbulb").setLevel(logging.DEBUG)
    _LOGGER = logging.getLogger(__name__)

    # Gather all slash command files.
    extensions = Path("./extensions").glob("*.py")

    with open(store.settings_path, "r") as settings_file:
        settings = json.load(settings_file)

    if not settings["token"]:
        _LOGGER.error(
            f"No token in {store.settings_path}! Please add it and try again."
        )
        exit()

    prefix = "<<"

    bot = lightbulb.Bot(
        prefix=prefix,
        token=settings["token"],
        intents=hikari.Intents.ALL,
    )

    for ext in extensions:
        bot.load_extension(f"extensions.{ext}")
        _LOGGER.info(f"Loaded extension: {ext}")

    return bot


if __name__ == "__main__":
    if os.name != "nt":
        # uvloop is only available on UNIX systems, but instead of coding
        # for the OS, we include this if statement to make life easier.
        import uvloop

        uvloop.install()

    create_bot().run()
