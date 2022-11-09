import logging


class CustomLogFormatter(logging.Formatter):
    """Logging Formatter to add colors and count warning / errors"""

    dark_grey = "\033[30;1m"
    red = "\033[1;31m"
    green = "\033[1;32m"
    yellow = "\033[1;33m"
    light_blue = "\033[34;1m"
    purple = "\033[1;35m"
    bold_red = "\033[5m\033[1;31m"
    reset = "\033[0m"

    format_prefix = "[" + green + "{asctime}" + reset + "] ["
    level_name = "{levelname:<7}" + reset + "] "
    source_line = purple + "line {lineno} in" + reset + " -> "
    format_suffix = light_blue + "{name}" + reset + ": {message} "

    dt_fmt = '%Y-%m-%d %H:%M:%S'

    FORMATS = {
        logging.DEBUG: format_prefix + dark_grey + level_name + format_suffix,
        logging.INFO: format_prefix + dark_grey + level_name + format_suffix,
        logging.WARNING: format_prefix + yellow + level_name + source_line + format_suffix,
        logging.ERROR: format_prefix + red + level_name + source_line + format_suffix,
        logging.CRITICAL: format_prefix + bold_red + level_name + source_line + format_suffix
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, self.dt_fmt, style='{')
        return formatter.format(record)


class RemoveNoise(logging.Filter):
    def __init__(self):
        super().__init__(name='discord.gateway')

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name not in 'discord.gateway' or "successfully RESUMED session" not in record.msg
