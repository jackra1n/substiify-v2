import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from extensions.util import Util


class ClearReplies(unittest.IsolatedAsyncioTestCase):
	async def test_unavailable_reply_never_falls_through_to_bulk_deletion(self):
		unresolved = discord.MessageReference(message_id=1, channel_id=2)
		deleted = discord.MessageReference(message_id=1, channel_id=2)
		deleted.resolved = discord.DeletedReferencedMessage(deleted)
		for reference in (None, unresolved, deleted):
			with self.subTest(reference=reference):
				ctx = SimpleNamespace(
					message=SimpleNamespace(type=discord.MessageType.reply, reference=reference, delete=AsyncMock()),
					channel=Mock(spec=discord.TextChannel, purge=AsyncMock()),
					send=AsyncMock(),
				)
				await Util.clear.callback(Util(None), ctx, amount=10)
				ctx.channel.purge.assert_not_awaited()
				ctx.message.delete.assert_not_awaited()
				ctx.send.assert_not_awaited()

	async def test_resolved_reply_deletes_only_target_and_command(self):
		target = Mock(spec=discord.Message, delete=AsyncMock())
		reference = discord.MessageReference(message_id=1, channel_id=2)
		reference.resolved = target
		ctx = SimpleNamespace(
			message=SimpleNamespace(type=discord.MessageType.reply, reference=reference, delete=AsyncMock()),
			channel=Mock(spec=discord.TextChannel, purge=AsyncMock()),
			send=AsyncMock(),
		)
		await Util.clear.callback(Util(None), ctx, amount=10)
		target.delete.assert_awaited_once()
		ctx.message.delete.assert_awaited_once()
		ctx.channel.purge.assert_not_awaited()
		ctx.send.assert_not_awaited()
