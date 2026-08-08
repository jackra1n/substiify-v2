import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_PREFIX = os.getenv("BOT_PREFIX")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")
POSTGRES_DSN = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

LAVALINK_NODE_URL = os.getenv("LAVALINK_NODE_URL")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")

SPOTIFY_URLS_ENABLED = os.getenv("SPOTIFY_URLS_ENABLED", "false").lower() == "true"


def validate() -> str:
	required = {
		"BOT_TOKEN": BOT_TOKEN,
		"BOT_PREFIX": BOT_PREFIX,
		"DB_HOST": DB_HOST,
		"DB_PORT": DB_PORT,
		"DB_USER": DB_USER,
		"DB_PASSWORD": DB_PASSWORD,
		"DB_NAME": DB_NAME,
	}
	validated: dict[str, str] = {}
	for name, value in required.items():
		if value is not None and value.strip():
			validated[name] = value

	missing = required.keys() - validated.keys()
	if missing:
		raise RuntimeError(f"Missing required environment variables: {', '.join(sorted(missing))}")

	db_port = validated["DB_PORT"]
	if not db_port.isdigit() or not 1 <= int(db_port) <= 65535:
		raise RuntimeError("DB_PORT must be an integer between 1 and 65535")

	lavalink_url_configured = bool(LAVALINK_NODE_URL and LAVALINK_NODE_URL.strip())
	lavalink_password_configured = bool(LAVALINK_PASSWORD and LAVALINK_PASSWORD.strip())
	if lavalink_url_configured != lavalink_password_configured:
		raise RuntimeError("LAVALINK_NODE_URL and LAVALINK_PASSWORD must be configured together")

	return validated["BOT_TOKEN"]
