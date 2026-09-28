import importlib.resources
import os
import platform
import re
import string
import sys

from colorlog.escape_codes import escape_codes
import discord

import core

__all__ = ("bot_version", "print_system_info", "strip_emotes")


def print_system_info() -> None:
	try:
		raw_art = _read_art()
	except Exception:
		raw_art = "Art loading failed."

	system_bits = (platform.machine(), platform.system(), platform.release())
	filtered_system_bits = (s.strip() for s in system_bits if s.strip())

	args = {
		"system_description": " ".join(filtered_system_bits),
		"python_version": platform.python_version(),
		"discord_version": discord.__version__,
		"substiify_version": bot_version(),
	}
	args.update(escape_codes)

	art_str = string.Template(raw_art).substitute(args)
	sys.stdout.write(art_str)
	sys.stdout.flush()


def _read_art() -> str:
	try:
		return importlib.resources.files("utils.ux").joinpath("art.txt").read_text(encoding="utf-8")
	except (FileNotFoundError, ModuleNotFoundError, TypeError):
		return "ASCII Art File Not Found"


def strip_emotes(string: str) -> str:
	discord_emote_pattern = re.compile(r"<a?:[a-zA-Z0-9_]+:[0-9]+>")
	return discord_emote_pattern.sub("", string)


def bot_version() -> str:
	version = core.__version__
	if commit := os.getenv("GIT_COMMIT", "")[:7]:
		version += f" [{commit}]"
	if commit_date := os.getenv("GIT_COMMIT_DATE", "")[:10]:
		version += f" ({commit_date})"
	return version
