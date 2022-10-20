pref = "\033["
reset = f"{pref}0m"


class colors:
    black = "30m"
    red = "31m"
    green = "32m"
    yellow = "33m"
    blue = "34m"
    magenta = "35m"
    cyan = "36m"
    white = "37m"


def print_colored(text, color=colors.white, is_bold=False):
    print(get_colored(text, color, is_bold))


def get_colored(text, color=colors.white, is_bold=False):
    return f'{pref}{int(is_bold)};{color}{text}{reset}'
