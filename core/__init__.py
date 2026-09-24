from . import config as config
from . import constants as constants
from .bot import require_guild as require_guild
from .bot import Substiify as Substiify
from .delivery import best_effort as best_effort

try:
	from importlib.resources import files as _files

	__version__ = _files(__package__) / "VERSION"
	__version__ = __version__.read_text(encoding="utf-8").strip()
except Exception:  # pragma: no cover
	__version__ = "0.0.0"

__author__ = "jackra1n"
