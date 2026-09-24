import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from typing import cast

import aiohttp
import asyncpg

from extensions.free_games import FreeGames
from extensions.free_games.base import ProviderError
from extensions.free_games.epic_games import EpicGames
from extensions.free_games.steam import STEAM_SEARCH_URL, Steam
from core.bot import Substiify


class _FakeResponse:
	def __init__(self, *, status: int = 200, text: str = "", payload: dict | None = None):
		self.status = status
		self.headers = {"Content-Type": "application/json"}
		self._text = text
		self._payload = payload

	def raise_for_status(self) -> None:
		if self.status >= 400:
			raise aiohttp.ClientResponseError(
				SimpleNamespace(real_url="http://stub"), (), status=self.status, message="stub HTTP error"
			)

	async def text(self) -> str:
		return self._text

	async def json(self) -> dict | None:
		return self._payload


class _FakeRequest:
	def __init__(self, produce):
		self._produce = produce

	async def __aenter__(self) -> _FakeResponse:
		return self._produce()

	async def __aexit__(self, *exc_info) -> bool:
		return False


class _FakeSession:
	def __init__(self, handler):
		self._handler = handler

	def get(self, url: str, **kwargs) -> _FakeRequest:
		return _FakeRequest(lambda: self._handler(url, kwargs.get("params")))

	async def __aenter__(self) -> "_FakeSession":
		return self

	async def __aexit__(self, *exc_info) -> bool:
		return False


def _fake_transport(handler):
	"""Route store HTTP fetches through handler(url, params), which returns a
	_FakeResponse or raises to simulate a transport failure."""
	return patch("aiohttp.ClientSession", lambda: _FakeSession(handler))


class FreeGameRetries(unittest.IsolatedAsyncioTestCase):
	async def test_transient_database_failures_retry_without_waiting_an_hour(self):
		attempts = 0

		async def fetch(_query):
			nonlocal attempts
			attempts += 1
			if attempts == 1:
				raise TimeoutError("pool exhausted")
			if attempts == 2:
				raise asyncpg.QueryCanceledError("statement timeout")
			if attempts == 4:
				cog.check_free_games.stop()
			return []

		bot = SimpleNamespace(db=SimpleNamespace(pool=SimpleNamespace(fetch=fetch)), wait_until_ready=AsyncMock())
		cog = FreeGames(cast(Substiify, bot))
		# Exercise the real scheduler: only shorten its backoff, not the hourly
		# interval. Catching either error as success would hang until that hour.
		with patch("discord.ext.tasks.ExponentialBackoff.delay", return_value=0):
			task = cog.check_free_games.start()
			try:
				await asyncio.wait_for(task, timeout=2)
			finally:
				cog.check_free_games.cancel()
		self.assertEqual(attempts, 4)
		self.assertFalse(cog.check_free_games.failed())

	async def test_store_fetch_outage_reaches_the_retry_scheduler(self):
		searches = 0

		async def fetch(_query):
			if "DISTINCT store_name" in _query:
				return [{"store_name": "steam"}]
			return []

		def handler(_url, _params):
			nonlocal searches
			searches += 1
			if searches == 1:
				raise TimeoutError("steam unreachable")
			cog.check_free_games.stop()
			return _FakeResponse(text=json.dumps({"items": []}))

		bot = SimpleNamespace(db=SimpleNamespace(pool=SimpleNamespace(fetch=fetch)), wait_until_ready=AsyncMock())
		cog = FreeGames(cast(Substiify, bot))
		# The store fetch failure must reach the real scheduler backoff instead of
		# being swallowed into an "empty" poll that sleeps for the hourly interval.
		with _fake_transport(handler), patch("discord.ext.tasks.ExponentialBackoff.delay", return_value=0):
			task = cog.check_free_games.start()
			try:
				await asyncio.wait_for(task, timeout=2)
			finally:
				cog.check_free_games.cancel()
		self.assertEqual(searches, 2)
		self.assertFalse(cog.check_free_games.failed())


class FreeGameFetchOutages(unittest.IsolatedAsyncioTestCase):
	async def test_epic_distinguishes_http_failure_from_empty_listing(self):
		def outage(_url, _params):
			return _FakeResponse(status=503)

		with _fake_transport(outage):
			with self.assertRaises(aiohttp.ClientError):
				await EpicGames.get_free_games()

		def empty(_url, _params):
			return _FakeResponse(payload={"data": {"Catalog": {"searchStore": {"elements": []}}}})

		with _fake_transport(empty):
			self.assertEqual(await EpicGames.get_free_games(), [])

	async def test_steam_distinguishes_http_failure_from_empty_listing(self):
		def outage(_url, _params):
			return _FakeResponse(status=503)

		with _fake_transport(outage):
			with self.assertRaises(aiohttp.ClientError):
				await Steam.get_free_games()

		def empty(_url, _params):
			return _FakeResponse(text=json.dumps({"items": []}))

		with _fake_transport(empty):
			self.assertEqual(await Steam.get_free_games(), [])

	async def test_malformed_store_payloads_are_provider_errors(self):
		cases = [
			(EpicGames, _FakeResponse(payload={"data": None})),
			(EpicGames, _FakeResponse(payload={"data": {"Catalog": {"searchStore": {"elements": "nope"}}}})),
			(Steam, _FakeResponse(text="<html>blocked</html>")),
			(Steam, _FakeResponse(text=json.dumps({"unexpected": []}))),
		]
		for store, response in cases:
			with self.subTest(store=store.name, response=response._payload or response._text):
				with _fake_transport(lambda _url, _params: response), self.assertRaises(ProviderError):
					await store.get_free_games()

	async def test_malformed_epic_entry_is_skipped(self):
		payload = {"data": {"Catalog": {"searchStore": {"elements": [{"title": "broken"}]}}}}
		with _fake_transport(lambda _url, _params: _FakeResponse(payload=payload)):
			with self.assertLogs("extensions.free_games.epic_games", level="ERROR"):
				self.assertEqual(await EpicGames.get_free_games(), [])

	async def test_steam_detail_fetch_outage_is_not_an_empty_listing(self):
		search_results = _FakeResponse(
			text=json.dumps(
				{
					"items": [
						{"logo": "https://cdn.steamstatic.com/steam/apps/111/header.jpg"},
						{"logo": "https://cdn.steamstatic.com/steam/apps/222/header.jpg"},
					]
				}
			)
		)

		def total_outage(url, params):
			if url == STEAM_SEARCH_URL:
				return search_results
			raise TimeoutError("steam app details unreachable")

		with _fake_transport(total_outage):
			with self.assertRaises(TimeoutError):
				await Steam.get_free_games()

		def partial_outage(url, params):
			if url == STEAM_SEARCH_URL:
				return search_results
			if params["appids"] == "222":
				return _FakeResponse(text=json.dumps({"222": {"success": True, "data": {"type": "demo"}}}))
			raise TimeoutError("steam app details unreachable")

		# Per-app failures stay isolated as long as Steam answered anything at all.
		with _fake_transport(partial_outage):
			self.assertEqual(await Steam.get_free_games(), [])

	async def test_manual_send_reports_unavailable_fetch_instead_of_no_games(self):
		fetched = []
		outage = True

		class StoreA:
			name = "a"

			@staticmethod
			async def get_free_games() -> list:
				fetched.append("a")
				if outage:
					raise aiohttp.ClientError("store outage")
				return []

		class StoreB:
			name = "b"

			@staticmethod
			async def get_free_games() -> list:
				fetched.append("b")
				return []

		bot = SimpleNamespace(
			db=SimpleNamespace(
				pool=SimpleNamespace(fetch=AsyncMock(return_value=[])),
				prepare_command_context=AsyncMock(),
			),
			wait_until_ready=AsyncMock(),
		)
		cog = FreeGames(cast(Substiify, bot))

		async def run_send() -> str:
			ctx = SimpleNamespace(
				defer=AsyncMock(),
				send=AsyncMock(),
				channel=SimpleNamespace(id=1),
				author=None,
				guild=None,
			)
			with patch("extensions.free_games.STORES", {"a": StoreA, "b": StoreB}):
				await cog.send.callback(cog, ctx)
			ctx.send.assert_awaited_once()
			return ctx.send.call_args.kwargs["embed"].description

		outage_description = await run_send()
		# A failed store must not prevent the other store from answering.
		self.assertCountEqual(fetched, ["a", "b"])

		fetched.clear()
		outage = False
		# An outage and a genuinely empty listing must not get the same report.
		self.assertNotEqual(outage_description, await run_send())
		self.assertCountEqual(fetched, ["a", "b"])


if __name__ == "__main__":
	unittest.main()
