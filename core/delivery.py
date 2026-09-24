import asyncio
import logging
from collections.abc import Awaitable

import aiohttp
import discord

logger = logging.getLogger(__name__)

DELIVERY_ERRORS = (discord.HTTPException, aiohttp.ClientConnectionError, TimeoutError, OSError)


async def best_effort[T](operation: Awaitable[T], what: str = "Discord message") -> T | None:
	"""Discord delivery must never undo work that already succeeded, e.g. a committed database operation."""
	try:
		async with asyncio.timeout(10):
			return await operation
	except DELIVERY_ERRORS as error:
		logger.warning("Could not deliver %s (%s: %s)", what, type(error).__name__, error)
		return None
