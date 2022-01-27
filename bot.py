import json
import logging

import nextcord
from nextcord.ext import commands

from utils import store, util
from utils.db import create_database

util.prepareFiles()
create_database()
logger = logging.getLogger(__name__)

with open(store.SETTINGS_PATH, "r") as settings:
    settings = json.load(settings)

prefix = settings["prefix"]
bot = commands.Bot(command_prefix=prefix, owner_id=276462585690193921, intents=nextcord.Intents().all())

bot.load_extension("modules.mainbot")
bot.load_extension("modules.help")

if not settings['token']:
    logger.error(f'No token in {store.SETTINGS_PATH}! Please add it and try again.')
    exit()

bot.run(settings['token'])
