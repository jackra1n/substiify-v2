import importlib.resources
import platform
import re
import string
import sys
import subprocess

import colorlog
import discord

import core

__all__ = (
    "print_system_info",
    "strip_emotes"
)


def print_system_info() -> None:
    raw_art = _read_art()

    system_bits = (platform.machine(), platform.system(), platform.release())
    filtered_system_bits = (s.strip() for s in system_bits if s.strip())
    bot_version = f'{core.__version__} [{get_last_commit_hash()}]'

    args = {
        "system_description": " ".join(filtered_system_bits),
        "python_version": platform.python_version(),
        "discord_version": discord.__version__,
        "substiify_version": bot_version
    }
    args.update(colorlog.escape_codes.escape_codes)
    
    art_str = string.Template(raw_art).substitute(args)
    sys.stdout.write(art_str)
    sys.stdout.flush()


def _read_art() -> str:
    with importlib.resources.files("utils.ux").joinpath("art.txt").open() as file:
        return file.read()


def strip_emotes(string: str) -> str:
    discord_emote_pattern = re.compile(r"<a?:[a-zA-Z0-9_]+:[0-9]+>")
    return discord_emote_pattern.sub('', string)

def get_last_commit_hash() -> str:
    git_log_cmd = ['git', 'log', '-1', '--pretty=format:"%h"']
    return subprocess.check_output(git_log_cmd).decode('utf-8').strip('"')