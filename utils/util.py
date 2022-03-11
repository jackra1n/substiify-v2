import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from helper.CustomLogFormatter import CustomLogFormatter
from utils import store

ignore_logs = [
    'Got a request',
]

class RemoveNoise(logging.Filter):
    def __init__(self):
        super().__init__(name='nextcord.gateway')

    def filter(self, record):
        if (record.name == 'nextcord.gateway' and 'Shard ID' in record.msg) or any(log in record.msg for log in ignore_logs):
            return False
        return True

def prepareFiles():

    default_settings = {
        "token": "",
        "version": 
        {
            "major": "0",
            "minor": "87",
        },
        "last_update": "",
        "prefix": "<<",
        "spotify_client_id": "",
        "spotify_client_secret": ""
    }

    # Create 'logs' folder if it doesn't exist
    Path('logs').mkdir(parents=True, exist_ok=True)

    # Create 'data' folder if it doesn't exist
    Path('data').mkdir(parents=True, exist_ok=True)

    # Filter out some of the logs that come from nextcord.gateway
    logging.getLogger('nextcord.gateway').addFilter(RemoveNoise())

    rootLogger = logging.getLogger()
    rootLogger.setLevel(logging.INFO)

    dt_fmt = '%Y-%m-%d %H:%M:%S'
    fileFormatter = logging.Formatter('[{asctime}] [{levelname:<7}] {name}: {message}', dt_fmt, style='{')

    fileHandler = TimedRotatingFileHandler(f'{store.LOGS_PATH}/substiify_', when="midnight", interval=1, encoding='utf-8')
    fileHandler.suffix = "%Y-%m-%d.log"
    fileHandler.setFormatter(fileFormatter)
    rootLogger.addHandler(fileHandler)

    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(CustomLogFormatter())
    rootLogger.addHandler(consoleHandler)

    logger = logging.getLogger('util.prepare_files')

    # Create 'settings.json' if it doesn't exist
    if not Path(store.SETTINGS_PATH).is_file():
        logger.info(f'Creating {store.SETTINGS_PATH}')
        with open(store.SETTINGS_PATH, 'a') as f:
            json.dump(default_settings, f, indent=2)

    # Create database file if it doesn't exist
    if not Path(store.DB_PATH).is_file():
        logger.info(f'Creating {store.DB_PATH}')
        open(store.DB_PATH, 'a')

    logger.info(f'All files ready')
