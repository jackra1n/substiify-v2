import asyncio
import logging

import discord

import core
import database
import utils
from core.custom_logger import CustomLogFormatter, RemoveNoise

discord.utils.setup_logging(formatter=CustomLogFormatter(), level=20)
logging.getLogger("discord.gateway").addFilter(RemoveNoise())


async def main() -> None:
	utils.ux.print_system_info()

	async with database.Database() as db, core.Substiify(database=db) as substiify:
		await substiify.start(core.config.TOKEN)


if __name__ == "__main__":
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print("Exiting...")
		exit(0)
