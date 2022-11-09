import os
import platform
import importlib.resources

import discord
from utils.colors import colors, get_colored
from core.version import Version


def get_system_description() -> str:
    system_bits = (platform.machine(), platform.system(), platform.release())
    filtered_system_bits = (s.strip() for s in system_bits if s.strip())
    return " ".join(filtered_system_bits)


def print_system_info() -> None:
    system_label = get_colored("Running on:", colors.green, True)
    python_version = get_colored("Python:", colors.cyan, True)
    discord_version = get_colored("discord.py:", colors.yellow, True)
    substiify_version = get_colored("substiify:", colors.red, True)

    ascii_art = importlib.resources.read_text("utils", "art.txt")
    shell_width = os.get_terminal_size().columns
    for line in ascii_art.splitlines():
        print(line.center(shell_width))

    system = get_system_description()
    system_length = len(system)
    longest_label = len(f'{system_label} {system}')

    rjust_len = (shell_width // 2) + (longest_label // 2)

    print()
    print(f'{system_label} {get_system_description()}'.rjust(rjust_len))
    print(f'{python_version} {platform.python_version().rjust(system_length)}'.rjust(rjust_len))
    print(f'{discord_version} {discord.__version__.rjust(system_length)}'.rjust(rjust_len))
    print(f'{substiify_version} {Version().get().rjust(system_length)}'.rjust(rjust_len))
    print()
