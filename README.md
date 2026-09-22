# substiify-v2

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Made with Python](https://img.shields.io/badge/Made%20with-Python-ffde57.svg?longCache=true&style=flat-square&colorB=ffdf68&logo=python&logoColor=88889e)](https://www.python.org/)
[![Powered by discord.py](https://img.shields.io/badge/Powered%20by-discord.py-blue?style=flat-square&logo=appveyor)](https://github.com/Rapptz/discord.py)

## Getting started

Requires Docker with Compose.

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications), enable all three **Privileged Gateway Intents**, and invite it to your server with the `bot` and `applications.commands` scopes.
2. Copy `example.env` to `.env`. Set `BOT_TOKEN`, `BOT_PREFIX`, `BOT_OWNER_ID` (your Discord account ID), and `DB_PASSWORD`. Keep the other database values for Compose. Channel IDs can stay blank to disable those destinations.
3. For music, configure `LAVALINK_NODE_URL` and `LAVALINK_PASSWORD` for your Lavalink server. Otherwise, set both to empty strings.
4. Start the bot and database:

   ```sh
   docker compose up -d
   ```

Compose uses the published release image and creates the PostgreSQL database on first start. No manual database setup is needed.

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
