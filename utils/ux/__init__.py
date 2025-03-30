import importlib.resources
import platform
import re
import string
import subprocess
import sys

import colorlog
import discord

import core

__all__ = ("print_system_info", "strip_emotes")


def print_system_info() -> None:
	try:
		raw_art = _read_art()
	except Exception:
		raw_art = "Art loading failed."

	system_bits = (platform.machine(), platform.system(), platform.release())
	filtered_system_bits = (s.strip() for s in system_bits if s.strip())

	commit_hash, commit_date = get_last_commit_info()

	if commit_hash != "unknown" and commit_date != "unknown":
		bot_version = f"{core.__version__} [{commit_hash}] ({commit_date})"
	elif commit_hash != "unknown":
		bot_version = f"{core.__version__} [{commit_hash}]"
	else:
		bot_version = f"{core.__version__} [commit info unavailable]"

	args = {
		"system_description": " ".join(filtered_system_bits),
		"python_version": platform.python_version(),
		"discord_version": discord.__version__,
		"substiify_version": bot_version,
	}
	try:
		args.update(colorlog.escape_codes.escape_codes)
	except AttributeError:
		pass

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


def get_last_commit_info() -> tuple[str, str]:
	git_log_cmd = ["git", "log", "-1", "--pretty=format:%h|%cs"]
	try:
		output = subprocess.check_output(git_log_cmd, stderr=subprocess.PIPE).decode("utf-8").strip()
		parts = output.split("|", 1)
		if len(parts) == 2:
			return parts[0], parts[1]  # hash, date
		else:
			return "unknown", "unknown"
	except (subprocess.CalledProcessError, FileNotFoundError):
		return "unknown", "unknown"
	except Exception:
		return "unknown", "unknown"
