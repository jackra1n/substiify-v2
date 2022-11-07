import os
import platform
import importlib.resources

import discord
from utils.colors import colors, get_colored


def get_system_description() -> str:
    system_bits = (platform.machine(), platform.system(), platform.release())
    filtered_system_bits = (s.strip() for s in system_bits if s.strip())
    return " ".join(filtered_system_bits)

def print_system_info() -> None:
    system_description = get_colored("Running on:", colors.green).ljust(30)
    python_version = get_colored("Python:", colors.blue).ljust(30)
    discord_version = get_colored("discord.py:", colors.yellow).ljust(30)

    ascii_art = importlib.resources.read_text("utils", "art.txt")
    shell_width = os.get_terminal_size().columns

    for line in ascii_art.splitlines():
        print(line.center(shell_width))

    print()
    print(f'{system_description} {get_system_description()}'.center(shell_width))
    print(f'{python_version} {platform.python_version()}'.center(shell_width))
    print(f'{discord_version} {discord.__version__}'.center(shell_width))
    print()
