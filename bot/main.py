import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import asyncpg
from core import config, values
from core.bot import Substiify
from core.version import Version
from utils import util
from utils.db import Database
from utils.CustomLogger import CustomLogFormatter, RemoveNoise

logger = logging.getLogger(__name__)


def prepareFiles() -> None:
    # Create 'logs' folder if it doesn't exist
    Path(values.LOGS_PATH).mkdir(parents=True, exist_ok=True)

    if not Path(values.VERSION_CONFIG_PATH).is_file():
        Version.create_version_file()

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


async def main():
    async with Substiify() as substiify, asyncpg.create_pool(
        dsn=config.POSTGRESQL_DSN, max_inactive_connection_lifetime=0
    ) as pool:
        if pool is None:
            # thanks asyncpg...
            raise RuntimeError("Could not connect to database.")

        substiify.db = Database(substiify, pool)
        await substiify.start(config.TOKEN)


if __name__ == "__main__":
    prepareFiles()
    util.print_system_info()
    setup_logging()

    asyncio.run(main())
