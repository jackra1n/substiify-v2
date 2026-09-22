# substiify-v2

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Made with Python](https://img.shields.io/badge/Made%20with-Python-ffde57.svg?longCache=true&style=flat-square&colorB=ffdf68&logo=python&logoColor=88889e)](https://www.python.org/)
[![Powered by discord.py](https://img.shields.io/badge/Powered%20by-discord.py-blue?style=flat-square&logo=appveyor)](https://github.com/Rapptz/discord.py)

## Getting started

Requires Docker Compose and the **Server Members**, **Presence**, and **Message Content** intents.

- Copy `example.env` to `.env`; set `BOT_TOKEN`, `BOT_PREFIX`, `BOT_OWNER_ID`, and `DB_PASSWORD`. Keep the database defaults for Compose.
- Channel IDs are optional. Set both `LAVALINK_NODE_URL` and `LAVALINK_PASSWORD` to empty strings unless using Lavalink for music.

```sh
docker compose up -d
```

Uses the published release image; PostgreSQL is initialized automatically.

### Tests

Install development dependencies with `uv sync --locked --dev`, then run:

```sh
uv run --locked python -m unittest discover -s tests -v
```

Database tests are skipped unless `TEST_POSTGRES_DSN` points to a dedicated PostgreSQL test database:

```sh
TEST_POSTGRES_DSN=postgresql://user:password@localhost:5432/substiify_test \
  uv run --locked python -m unittest discover -s tests -v
```

The test user must be able to create schemas. Tests apply migrations in isolated schemas and drop them afterward; do not use the production database. CI runs the full suite with PostgreSQL 18 on pushes and pull requests.
