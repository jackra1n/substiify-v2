# substiify-v2

[![CodeFactor](https://www.codefactor.io/repository/github/jackra1n/substiify-v2/badge?s=b2b5d4f291828630b83a6a566d5d2f319b2bd3d5)]()
[![Made with Python](https://img.shields.io/badge/Made%20with-Python-ffde57.svg?longCache=true&style=flat-square&colorB=ffdf68&logo=python&logoColor=88889e)](https://www.python.org/)
[![Powered by discord.py](https://img.shields.io/badge/Powered%20by-discord.py-blue?style=flat-square&logo=appveyor)](https://github.com/Rapptz/discord.py)
[![Imports: isort](https://img.shields.io/badge/%20imports-isort-%231674b1?style=flat-square&labelColor=ef8336)](https://pycqa.github.io/isort/)



## Getting started

To run the bot you'll need docker compose.

- Copy or rename `config_example.py` in `/bot/core/` to `config.py` and fill out the fields. 
- Start the postgres container and create a database which you configured in `config.py`
- Build and start bot container

Build docker image
```bash
docker build -t substiify .
```

Run docker 
`-d`: runs container in the background
```bash
docker-compose up -d
```