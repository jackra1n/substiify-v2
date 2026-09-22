import errno
import logging
import os
from copy import copy
from logging.handlers import RotatingFileHandler

import aiohttp


_RETRY_MESSAGES = {
	"discord.client": "Attempting a reconnect in %.2fs",
	"discord.ext.tasks": "Handling exception in internal background task %s. Retrying in %.2fs",
	"extensions.owner": "Failed to update the bot status.",
}
_CONNECTION_ERRNOS = {
	errno.ECONNABORTED,
	errno.ECONNREFUSED,
	errno.ECONNRESET,
	errno.EHOSTUNREACH,
	errno.ENETDOWN,
	errno.ENETUNREACH,
	errno.EPIPE,
	errno.ETIMEDOUT,
}


def _is_expected_retry(record: logging.LogRecord) -> bool:
	if not record.exc_info or record.name not in _RETRY_MESSAGES or record.msg != _RETRY_MESSAGES[record.name]:
		return False

	error = record.exc_info[1]
	if isinstance(
		error,
		(
			TimeoutError,
			aiohttp.ClientConnectorDNSError,
			ConnectionResetError,
			ConnectionAbortedError,
			ConnectionRefusedError,
			BrokenPipeError,
			aiohttp.ServerDisconnectedError,
		),
	):
		return True
	if isinstance(error, aiohttp.WSServerHandshakeError):
		return error.status in (502, 503, 504)
	# Exact types keep TLS/proxy errors and arbitrary OSError subclasses diagnostic.
	return (
		isinstance(error, OSError)
		and type(error) in (OSError, aiohttp.ClientOSError, aiohttp.ClientConnectorError)
		and error.errno in _CONNECTION_ERRNOS
	)


class CustomLogFormatter(logging.Formatter):
	"""Colored console logs with concise reasons for known network retries."""

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
		if _is_expected_retry(record):
			# Other handlers must receive the original exception and traceback,
			# including when a file handler has already populated exc_text.
			record = copy(record)
			error = record.exc_info[1]
			reason = " ".join(str(error).split())
			if not reason:
				reason = "connection timed out" if isinstance(error, TimeoutError) else "connection interrupted"
			record.msg = f"{record.getMessage()}: {type(error).__name__}: {reason}"
			record.args = ()
			record.exc_info = None
			record.exc_text = None
			record.stack_info = None

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
