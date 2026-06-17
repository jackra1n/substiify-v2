import logging
import os
from logging.handlers import RotatingFileHandler


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
	source_line = purple + "{lineno}" + reset + "@"
	format_suffix = light_blue + "{name}" + reset + ": {message} "

	dt_fmt = "%Y-%m-%d %H:%M:%S"

	FORMATS = {
		logging.DEBUG: format_prefix + purple + level_name + format_suffix,
		logging.INFO: format_prefix + dark_grey + level_name + format_suffix,
		logging.WARNING: format_prefix + yellow + level_name + source_line + format_suffix,
		logging.ERROR: format_prefix + red + level_name + source_line + format_suffix,
		logging.CRITICAL: format_prefix + bold_red + level_name + source_line + format_suffix,
	}

	def format(self, record):
		log_fmt = self.FORMATS.get(record.levelno)
		formatter = logging.Formatter(log_fmt, self.dt_fmt, style="{")
		return formatter.format(record)


class PlainLogFormatter(logging.Formatter):
	"""Same layout as CustomLogFormatter but without ANSI colors, for log files."""

	dt_fmt = "%Y-%m-%d %H:%M:%S"

	base_fmt = "[{asctime}] [{levelname:<7}] {name}: {message}"
	source_fmt = "[{asctime}] [{levelname:<7}] {lineno}@{name}: {message}"

	FORMATS = {
		logging.DEBUG: base_fmt,
		logging.INFO: base_fmt,
		logging.WARNING: source_fmt,
		logging.ERROR: source_fmt,
		logging.CRITICAL: source_fmt,
	}

	def format(self, record):
		log_fmt = self.FORMATS.get(record.levelno, self.base_fmt)
		formatter = logging.Formatter(log_fmt, self.dt_fmt, style="{")
		return formatter.format(record)


def add_rotating_file_handler(
	log_dir: str = "logs",
	filename: str = "substiify.log",
	level: int = logging.INFO,
	max_bytes: int = 5 * 1024 * 1024,
	backup_count: int = 5,
) -> RotatingFileHandler:
	"""Attach a rotating file handler to the root logger.

	Rotates at `max_bytes` (default 5 MB), keeping `backup_count` old files
	(substiify.log.1 ... substiify.log.5).
	"""
	os.makedirs(log_dir, exist_ok=True)
	handler = RotatingFileHandler(
		filename=os.path.join(log_dir, filename),
		maxBytes=max_bytes,
		backupCount=backup_count,
		encoding="utf-8",
	)
	handler.setFormatter(PlainLogFormatter())
	handler.setLevel(level)
	logging.getLogger().addHandler(handler)
	return handler


class RemoveNoise(logging.Filter):
	def __init__(self):
		super().__init__(name="discord.gateway")

	def filter(self, record: logging.LogRecord) -> bool:
		if "successfully RESUMED session" in record.msg:
			return False
		return True
