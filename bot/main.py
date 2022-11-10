import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from core import bot, config, values
from core.version import Version
from utils import db, util
from utils.CustomLogger import CustomLogFormatter, RemoveNoise

logger = logging.getLogger(__name__)


def prepareFiles() -> None:
    # Create 'logs' folder if it doesn't exist
    Path(values.LOGS_PATH).mkdir(parents=True, exist_ok=True)

    # Create 'data' folder if it doesn't exist
    Path('bot/data').mkdir(parents=True, exist_ok=True)

    if not Path(values.VERSION_CONFIG_PATH).is_file():
        logger.info(f'Creating {values.VERSION_CONFIG_PATH}')
        Version.create_version_file()

    # Create database file if it doesn't exist
    if not Path(values.DB_PATH).is_file():
        logger.info(f'Creating {values.DB_PATH}')
        open(values.DB_PATH, 'a')

    logger.info('All system files ready')


def setup_logging() -> None:
    logging.getLogger('discord.gateway').addFilter(RemoveNoise())
    log = logging.getLogger()
    log.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(CustomLogFormatter())
    log.addHandler(stream_handler)

    file_handler = TimedRotatingFileHandler(f'{values.LOGS_PATH}/substiify_', when="midnight", interval=1, encoding='utf-8')
    file_formatter = logging.Formatter('[{asctime}] [{levelname:<7}] {name}: {message}', '%Y-%m-%d %H:%M:%S', style='{')
    file_handler.suffix = "%Y-%m-%d.log"
    file_handler.setFormatter(file_formatter)
    log.addHandler(file_handler)
    logger.info('Logging setup finished')


if not config.TOKEN:
    logger.error('No token in config.py! Please add it and try again.')
    exit()

if __name__ == "__main__":
    util.print_system_info()
    prepareFiles()
    setup_logging()
    db.create_database()

    substiify = bot.Substiify()
    substiify.run(config.TOKEN, log_handler=None)
