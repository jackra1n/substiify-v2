from logging.handlers import TimedRotatingFileHandler
from utils.store import store
from pathlib import Path

import logging
import json


def prepareFiles():

    default_settings = {"token": "", "version": "0.6"}

    # Create 'logs' folder if it doesn't exist
    Path("logs").mkdir(parents=True, exist_ok=True)

    # Create 'data' folder if it doesn't exist
    Path("data").mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("util.prepare_files")

    # Create 'settings.json' if it doesn't exist
    if not Path(store.settings_path).is_file():
        logger.info(f"Creating {store.settings_path}")
        with open(store.settings_path, "a") as f:
            json.dump(default_settings, f, indent=2)

    # Create database file if it doesn't exist
    if not Path(store.db_path).is_file():
        logger.info(f"Creating {store.db_path}")
        open(store.db_path, "a")

    logger.info(f"All files ready")


# if bot is 'substiffy alpha' change prefix
def prefix(bot, message):
    return prefixById(bot)


def prefixById(bot):
    if bot.user.id == 742380498986205234:
        return "§§"
    return "<<"
