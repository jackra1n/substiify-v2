import logging
from logging.handlers import TimedRotatingFileHandler

from utils import store


def setup_file_handler():
    # Filter out some of the logs that come from discord.gateway
    # logging.getLogger('discord.gateway').addFilter(RemoveNoise())

    logger = logging.getLogger('discord')
    logger.setLevel(logging.DEBUG)

    dt_fmt = '%Y-%m-%d %H:%M:%S'
    fileFormatter = logging.Formatter('[{asctime}] [{levelname:<7}] {name}: {message}', dt_fmt, style='{')

    handler = TimedRotatingFileHandler(f'{store.LOGS_PATH}/substiify_', when="midnight", interval=1, encoding='utf-8')
    handler.suffix = "%Y-%m-%d.log"
    handler.setFormatter(fileFormatter)
    logger.addHandler(handler)


# TODO: not used for now. check if dpy 2.0 logs it a lot too
class RemoveNoise(logging.Filter):
    def __init__(self):
        super().__init__(name='discord.gateway')

    def filter(self, record):
        ignore_logs = [
            'Got a request',
            'Shard ID'
        ]

        is_ignored_message = all(log not in record.msg for log in ignore_logs)
        is_gateway_shard_log = record.name != 'discord.gateway'

        return is_gateway_shard_log and is_ignored_message
