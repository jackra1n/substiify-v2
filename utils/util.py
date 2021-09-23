from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from utils import store

import logging
import json


def prepareFiles() -> None:

    default_settings = {"token": "", "version": "0.6"}

    # Create 'logs' folder if it doesn't exist
    Path("logs").mkdir(parents=True, exist_ok=True)

    # Create 'data' folder if it doesn't exist
    Path("data").mkdir(parents=True, exist_ok=True)

    _LOGGER = logging.getLogger("util.prepare_files")

    # Create 'settings.json' if it doesn't exist
    if not Path(store.settings_path).is_file():
        _LOGGER.info(f"Creating {store.settings_path}")
        with open(store.settings_path, "a") as f:
            json.dump(default_settings, f, indent=2)

    # Create database file if it doesn't exist
    if not Path(store.db_path).is_file():
        _LOGGER.info(f"Creating {store.db_path}")
        open(store.db_path, "a")

    _LOGGER.info(f"All files ready")


def prefix(bot, message) -> str:
    return prefixById(bot)


# if bot is 'substiffy alpha' change prefix
def prefixById(bot) -> str:
    if bot.user.id == 742380498986205234:
        return "§§"
    return "<<"
