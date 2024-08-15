# substiify-v2

[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![CodeFactor](https://www.codefactor.io/repository/github/jackra1n/substiify-v2/badge?s=b2b5d4f291828630b83a6a566d5d2f319b2bd3d5)]()
[![Made with Python](https://img.shields.io/badge/Made%20with-Python-ffde57.svg?longCache=true&style=flat-square&colorB=ffdf68&logo=python&logoColor=88889e)](https://www.python.org/)
[![Powered by discord.py](https://img.shields.io/badge/Powered%20by-discord.py-blue?style=flat-square&logo=appveyor)](https://github.com/Rapptz/discord.py)



## Getting started

To run the bot you'll need docker compose.

- Copy or rename `example.env` in `/` to `.env` and fill out the fields. 
- Start the postgres container and create a database which you configured in `.env` -> `DB_NAME`
- Start the bot with `docker-compose up -d`

## Development

Increment the version in `pyproject.toml` and `core/__init__.py`.
