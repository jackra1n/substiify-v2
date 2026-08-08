import asyncio
import logging

import discord

import core
import database
import utils
from core.custom_logger import CustomLogFormatter, RemoveNoise, add_rotating_file_handler

discord.utils.setup_logging(formatter=CustomLogFormatter(), level=20)
add_rotating_file_handler(level=logging.INFO)
logging.getLogger("discord.gateway").addFilter(RemoveNoise())


async def main() -> None:
	bot_token = core.config.validate()
	utils.ux.print_system_info()

	async with database.Database(core.config.POSTGRES_DSN) as db, core.Substiify(database=db) as substiify:
		await substiify.start(bot_token)


if __name__ == "__main__":
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print("Exiting...")
		exit(0)
