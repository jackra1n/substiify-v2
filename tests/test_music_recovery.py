import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import aiohttp
import discord
import wavelink
from aiohttp import web
from aiohttp.test_utils import TestServer

from core.bot import Substiify
from extensions.music import Music, MusicPlayer, _LavalinkWebSocket


class MusicRecoveryTests(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self):
		self.bot = await self.enterAsyncContext(discord.Client(intents=discord.Intents.none()))
		self.session = await self.enterAsyncContext(aiohttp.ClientSession())
		self.node = wavelink.Node(uri="http://localhost:2333", password="test", session=self.session)
		self.music = Music(cast(Substiify, self.bot))
		self.enterContext(patch.object(wavelink.Node, "_destroy_player", new=AsyncMock()))
		self.change_voice_state = self.enterContext(patch.object(discord.Guild, "change_voice_state", new=AsyncMock()))
		self.track = wavelink.Playable(
			{
				"encoded": "test-track",
				"info": {
					"identifier": "test-track",
					"isSeekable": True,
					"author": "test artist",
					"length": 60000,
					"isStream": False,
					"position": 0,
					"title": "test song",
					"sourceName": "local",
				},
				"pluginInfo": {},
				"userData": {},
			}
		)

	def player(self, guild_id: int, *, node: wavelink.Node | None = None) -> MusicPlayer:
		node = node or self.node
		guild = discord.Guild(data=cast(Any, {"id": str(guild_id), "name": "test"}), state=self.bot._connection)
		channel = discord.VoiceChannel(
			state=self.bot._connection,
			guild=guild,
			data=cast(
				Any,
				{
					"id": str(guild_id + 100),
					"name": "voice",
					"type": 2,
					"position": 0,
					"bitrate": 64000,
					"user_limit": 0,
				},
			),
		)
		player = MusicPlayer(self.bot, channel, nodes=[node])
		player._guild = guild
		player._connected = True
		player._current = self.track
		player.queue.put(self.track)
		node._players[guild.id] = player
		self.bot._connection._add_voice_client(guild.id, player)
		return player

	async def ready(self, *, resumed: bool):
		await self.music.on_wavelink_node_ready(
			wavelink.NodeReadyEventPayload(node=self.node, resumed=resumed, session_id="reconnected")
		)

	async def test_fresh_session_releases_all_stale_voice_clients(self):
		players = [self.player(1), self.player(2)]
		await self.ready(resumed=False)
		self.assertEqual(self.node.players, {})
		self.assertEqual(self.bot.voice_clients, [])
		for player in players:
			self.assertFalse(player.connected)
			self.assertFalse(player.playing)
			assert player.guild is not None
			self.assertIsNone(player.guild.voice_client)

	async def test_resumed_session_preserves_playback_and_queue(self):
		player = self.player(1)
		player.queue.mode = wavelink.QueueMode.loop_all
		await self.ready(resumed=True)
		assert player.guild is not None
		self.assertIs(player.guild.voice_client, player)
		self.assertIs(self.node.players[1], player)
		self.assertTrue(player.playing)
		self.assertIs(player.current, self.track)
		self.assertEqual(list(player.queue), [self.track])
		self.assertEqual(player.queue.mode, wavelink.QueueMode.loop_all)

	async def test_restart_does_not_disconnect_another_nodes_player(self):
		other_node = wavelink.Node(uri="http://localhost:2444", password="test", session=self.session)
		stale = self.player(1)
		unaffected = self.player(2, node=other_node)
		await self.ready(resumed=False)
		assert stale.guild is not None and unaffected.guild is not None
		self.assertIsNone(stale.guild.voice_client)
		self.assertIs(unaffected.guild.voice_client, unaffected)
		self.assertTrue(unaffected.playing)
		self.assertIs(other_node.players[2], unaffected)

	async def test_failed_discord_leave_does_not_prevent_other_guild_recovery(self):
		players = [self.player(1), self.player(2)]
		response = SimpleNamespace(status=503, reason="Service Unavailable")
		self.change_voice_state.side_effect = [discord.HTTPException(response, "unavailable"), None]
		with self.assertLogs("core.delivery", level="WARNING"):
			await self.ready(resumed=False)
		self.assertEqual(self.node.players, {})
		self.assertEqual(self.bot.voice_clients, [])
		for player in players:
			self.assertFalse(player.connected)
			assert player.guild is not None
			self.assertIsNone(player.guild.voice_client)


class LavalinkWebSocketTests(unittest.IsolatedAsyncioTestCase):
	async def test_server_shutdown_reaches_wavelinks_reconnect_branch(self):
		async def websocket(request):
			socket = web.WebSocketResponse()
			await socket.prepare(request)
			await socket.send_json({"op": "ready"})
			await socket.close(code=aiohttp.WSCloseCode.GOING_AWAY)
			return socket

		app = web.Application()
		app.router.add_get("/ws", websocket)
		async with TestServer(app) as server, aiohttp.ClientSession(ws_response_class=_LavalinkWebSocket) as session:
			async with session.ws_connect(server.make_url("/ws")) as socket:
				message = await asyncio.wait_for(socket.receive(), timeout=5)
				self.assertEqual(message.json(), {"op": "ready"})
				closed = await asyncio.wait_for(socket.receive(), timeout=5)
				self.assertIn(closed.type, (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING))
				self.assertTrue(socket.closed)
				self.assertEqual(socket.close_code, aiohttp.WSCloseCode.GOING_AWAY)
