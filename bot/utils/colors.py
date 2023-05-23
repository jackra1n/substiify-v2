PREF = "\033["
RESET = f"{PREF}0m"


class Colors:
    black = "30m"
    red = "31m"
    green = "32m"
    yellow = "33m"
    blue = "34m"
    magenta = "35m"
    cyan = "36m"
    white = "37m"


def print_colored(text, color=Colors.white, is_bold=False):
    print(get_colored(text, color, is_bold))


def get_colored(text, color=Colors.white, is_bold=False):
    return f'{PREF}{int(is_bold)};{color}{text}{RESET}'
