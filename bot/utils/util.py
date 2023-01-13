import importlib.resources
import shutil
import platform

import discord
from core.version import Version
from utils.colors import colors, get_colored


def get_system_description() -> str:
    system_bits = (platform.machine(), platform.system(), platform.release())
    filtered_system_bits = (s.strip() for s in system_bits if s.strip())
    return " ".join(filtered_system_bits)


def print_system_info() -> None:
    system_label = get_colored("Running on:", colors.green, True)
    python_label = get_colored("Python:", colors.cyan, True)
    discord_label = get_colored("discord.py:", colors.yellow, True)
    substiify_label = get_colored("substiify:", colors.red, True)

    ascii_art = importlib.resources.read_text("utils", "art.txt")
    shell_width = shutil.get_terminal_size().columns
    center_art = shell_width - (shell_width // 15)
    for line in ascii_art.splitlines():
        print(line.center(center_art))

    system = get_system_description()
    system_length = len(system)
    longest_label = len(f'{system_label} {system}')

    rjust_len = (shell_width // 2) + (longest_label // 2)
    python_version = platform.python_version().rjust(system_length)
    discord_version = discord.__version__.rjust(system_length)
    substiify_version = Version().get().rjust(system_length)

    print()
    print(f'{system_label} {system}'.rjust(rjust_len))
    print(f'{python_label} {python_version}'.rjust(rjust_len))
    print(f'{discord_label} {discord_version}'.rjust(rjust_len))
    print(f'{substiify_label} {substiify_version}'.rjust(rjust_len))
    print()
