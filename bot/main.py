import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from core import bot, config, values
from core.version import Version
from helper.CustomLogFormatter import CustomLogFormatter
from utils import db

logger = logging.getLogger('discord')


def prepareFiles() -> None:
    # Create 'logs' folder if it doesn't exist
    Path('bot/logs').mkdir(parents=True, exist_ok=True)

    # Create 'data' folder if it doesn't exist
    Path('bot/data').mkdir(parents=True, exist_ok=True)

    if not Path(values.VERSION_CONFIG_PATH).is_file():
        logger.info(f'Creating {values.VERSION_CONFIG_PATH}')
        Version.create_version_file()

    # Create database file if it doesn't exist
    if not Path(values.DB_PATH).is_file():
        logger.info(f'Creating {values.DB_PATH}')
        open(values.DB_PATH, 'a')

    logger.info('All files ready')


def setup_logger_file() -> None:
    logger.setLevel(logging.DEBUG)
    handler = TimedRotatingFileHandler(f'{values.LOGS_PATH}/substiify_', when="midnight", interval=1, encoding='utf-8')
    handler.suffix = "%Y-%m-%d.log"
    handler.setFormatter(CustomLogFormatter())
    logger.addHandler(handler)


if not config.TOKEN:
    logger.error('No token in config.py! Please add it and try again.')
    exit()

if __name__ == "__main__":
    prepareFiles()
    setup_logger_file()
    db.create_database()

    substiify = bot.Substiify()
    substiify.run(config.TOKEN, log_formatter=CustomLogFormatter())
