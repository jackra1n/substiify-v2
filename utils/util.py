import json
import logging

from pathlib import Path

from utils import store


def prepareFiles():
    logger = logging.getLogger('discord')

    default_settings = {
        "token": "",
        "prefix": "<<",
        "version":
        {
            "major": "0",
            "minor": "87",
        },
        "last_update": "",
        "spotify_client_id": "",
        "spotify_client_secret": ""
    }

    # Create 'logs' folder if it doesn't exist
    Path('logs').mkdir(parents=True, exist_ok=True)

    # Create 'data' folder if it doesn't exist
    Path('data').mkdir(parents=True, exist_ok=True)

    # Create 'settings.json' if it doesn't exist
    if not Path(store.SETTINGS_PATH).is_file():
        logger.info(f'Creating {store.SETTINGS_PATH}')
        with open(store.SETTINGS_PATH, 'a') as f:
            json.dump(default_settings, f, indent=2)

    # Create database file if it doesn't exist
    if not Path(store.DB_PATH).is_file():
        logger.info(f'Creating {store.DB_PATH}')
        open(store.DB_PATH, 'a')

    logger.info('All files ready')
