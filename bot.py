import os
import hikari
import lightbulb
import logging
import json

from utils import util, store

if os.name != "nt":
    import uvloop

    uvloop.install()

util.prepareFiles()

logging.getLogger("lightbulb").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

extensions = [""]

with open(store.settings_path, "r") as settings_file:
    settings = json.load(settings_file)

if not settings["token"]:
    logger.error(f"No token in {store.settings_path}! Please add it and try again.")
    exit()

prefix = "<<"

bot = hikari.GatewayBot(
    prefix=prefix,
    token=settings["token"],
    intents=hikari.Intents.ALL,
)
