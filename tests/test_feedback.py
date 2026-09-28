import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import discord

import core
from extensions.feedback import ACCEPT_EMOJI, Feedback

BUG_CHANNEL_ID = 100
OWNER_ID = 1


class FeedbackModerationTests(unittest.IsolatedAsyncioTestCase):
	async def asyncSetUp(self):
		self.fetchrow = AsyncMock(return_value=None)
		bot = SimpleNamespace(owner_id=OWNER_ID, db=SimpleNamespace(pool=SimpleNamespace(fetchrow=self.fetchrow)))
		self.feedback = Feedback(cast(core.Substiify, bot))
		patcher = patch.object(core.config, "BUG_CHANNEL_ID", BUG_CHANNEL_ID)
		patcher.start()
		self.addCleanup(patcher.stop)

	async def react(self, member_id: int, manage_guild: bool):
		member = SimpleNamespace(
			id=member_id, bot=False, guild_permissions=discord.Permissions(manage_guild=manage_guild)
		)
		payload = SimpleNamespace(
			guild_id=10, member=member, channel_id=BUG_CHANNEL_ID, emoji=ACCEPT_EMOJI, message_id=1000
		)
		await self.feedback.on_raw_reaction_add(cast(discord.RawReactionActionEvent, payload))

	async def test_regular_member_cannot_decide_feedback(self):
		await self.react(member_id=2, manage_guild=False)
		self.fetchrow.assert_not_awaited()

	async def test_server_manager_can_decide_feedback(self):
		await self.react(member_id=2, manage_guild=True)
		self.fetchrow.assert_awaited_once()

	async def test_owner_can_decide_feedback(self):
		await self.react(member_id=OWNER_ID, manage_guild=False)
		self.fetchrow.assert_awaited_once()
