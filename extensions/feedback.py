import logging
from enum import Enum

import discord
from asyncpg import Record
from discord import app_commands
from discord.ext import commands

import core

logger = logging.getLogger(__name__)

ACCEPT_EMOJI = discord.PartialEmoji.from_str("greenTick:876177251832590348")
DENY_EMOJI = discord.PartialEmoji.from_str("redCross:876177262813278288")
SUGGESTION_CHANNEL_ID = 876413286978031676
BUG_CHANNEL_ID = 876412993498398740


class FeedbackType(Enum):
	BUG = "bug"
	SUGGESTION = "suggestion"


class FeedbackOutcome(Enum):
	ACCEPTED = "accepted"
	DENIED = "denied"


class Feedback(commands.Cog):
	COG_EMOJI = "📝"

	def __init__(self, bot: core.Substiify):
		self.bot = bot

	@commands.Cog.listener()
	async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
		if payload.guild_id is None or payload.member is None:
			return

		if payload.member.bot:
			return

		if payload.channel_id not in [BUG_CHANNEL_ID, SUGGESTION_CHANNEL_ID]:
			return

		if payload.emoji not in [ACCEPT_EMOJI, DENY_EMOJI]:
			return

		feedback = await self.bot.db.pool.fetchrow(
			"SELECT * FROM feedback WHERE discord_message_id = $1 AND discord_channel_id = $2",
			payload.message_id,
			payload.channel_id,
		)
		if feedback is None or feedback["accepted"] is not None:
			return

		channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
		message = await channel.fetch_message(payload.message_id)
		if message.author != self.bot.user:
			return

		feedback = await self.bot.db.pool.fetchrow(
			"""UPDATE feedback SET accepted = $1
			   WHERE id = $2 AND accepted IS NULL
			   RETURNING *""",
			payload.emoji == ACCEPT_EMOJI,
			feedback["id"],
		)
		if feedback is None:
			return

		# Only the winning decision attempts these effects; none can undo the decision.
		try:
			await self.edit_feedback_embed(feedback, message)
		except Exception:
			logger.exception("Feedback %s was decided, but its moderation embed could not be updated", feedback["id"])
		try:
			await self.send_user_reply(feedback)
		except Exception:
			logger.exception(
				"Feedback %s was decided, but its user notification failed; no retry is queued", feedback["id"]
			)
		try:
			await message.clear_reactions()
		except Exception:
			logger.exception(
				"Feedback %s was decided, but its moderation reactions could not be cleared", feedback["id"]
			)

	async def edit_feedback_embed(self, feedback: Record, message: discord.Message):
		outcome = "accepted" if feedback["accepted"] else "denied"
		color = discord.Colour.green() if outcome == "accepted" else discord.Colour.red()

		embed = message.embeds[0] if message.embeds else discord.Embed(description=f"```{feedback['content']}```")
		embed.color = color
		embed.title = f"{outcome.capitalize()} {feedback['feedback_type']} submission"

		await message.edit(embed=embed)

	async def send_user_reply(self, feedback: Record):
		feedback_type_str = feedback["feedback_type"]
		feedback_type = FeedbackType(feedback_type_str)

		is_accepted = feedback["accepted"]
		outcome = "accepted" if is_accepted else "denied"

		color = discord.Colour.green() if is_accepted else discord.Colour.red()
		emoji = ACCEPT_EMOJI if is_accepted else DENY_EMOJI

		user = self.bot.get_user(feedback["discord_user_id"]) or await self.bot.fetch_user(feedback["discord_user_id"])

		new_embed = discord.Embed(
			title=f"{feedback_type.value.capitalize()} submission",
			description=f"```{feedback['content']}```",
			color=color,
		)
		message_to_user = f"Hello {user.name}!\nYour {self.bot.user.mention} {feedback_type.value} submission has been **{outcome}** {emoji}."
		await user.send(content=message_to_user, embed=new_embed)

	@commands.cooldown(2, 100)
	@app_commands.command(
		name="feedback",
		description="Opens a modal window on discord where you can suggest an improvement to the developer team.",
	)
	async def feedback(self, interaction: discord.Interaction, feedback_type: FeedbackType):
		"""
		Allows you to report a bug or suggest a feature or an improvement to the developer team.
		After review, the bot will attempt to send you the outcome by DM.
		"""
		await interaction.response.send_modal(FeedbackModal(feedback_type))


class FeedbackSelect(discord.ui.Select):
	def __init__(self):
		super().__init__(
			placeholder="Select a type of submission...",
			options=[
				discord.SelectOption(
					label="Bug fix",
					description="Report a bug that needs to be fixed",
					emoji="🐛",
					value=FeedbackType.BUG,
				),
				discord.SelectOption(
					label="Improvement suggestion",
					description="Suggest an improvement to the bot",
					emoji="👍",
					value=FeedbackType.SUGGESTION,
				),
			],
		)

	async def callback(self, interaction: discord.Interaction):
		await interaction.response.send_modal(FeedbackModal(self.values[0]))


class FeedbackModal(discord.ui.Modal):
	def __init__(self, feedback_type: FeedbackType):
		super().__init__(title="Suggestions & Feedback")
		self.feedback_type = FeedbackType(feedback_type)
		self.feedback = discord.ui.TextInput(
			label=self.feedback_type.value.capitalize(),
			style=discord.TextStyle.long,
			placeholder="Write your bug fix or improvement suggestion here...",
			required=True,
			min_length=10,
			max_length=300,
		)
		self.add_item(self.feedback)

	async def on_submit(self, interaction: discord.Interaction):
		await interaction.response.defer(ephemeral=True, thinking=True)
		channel_id = BUG_CHANNEL_ID if self.feedback_type == FeedbackType.BUG else SUGGESTION_CHANNEL_ID
		channel = interaction.client.get_channel(channel_id) or await interaction.client.fetch_channel(channel_id)
		embed = discord.Embed(
			title=f"New {self.feedback_type.value} submission",
			description=f"```{self.feedback.value}```",
			color=core.constants.CYAN_COLOR,
		)
		embed.set_footer(text=str(interaction.user), icon_url=interaction.user.display_avatar)

		try:
			async with interaction.client.db.pool.acquire(timeout=5) as connection:
				async with connection.transaction():
					await interaction.client.db.prepare_command_context(
						interaction.user, interaction.guild, channel, connection=connection
					)
					feedback_id = await connection.fetchval(
						"""INSERT INTO feedback
						   (feedback_type, content, discord_user_id, discord_server_id,
						    discord_channel_id, discord_message_id)
						   VALUES ($1, $2, $3, $4, $5, NULL) RETURNING id""",
						self.feedback_type.value,
						self.feedback.value,
						interaction.user.id,
						interaction.guild.id if interaction.guild is not None else None,
						channel.id,
					)
		except Exception:
			logger.exception("Could not confirm feedback draft persistence for user %s", interaction.user.id)
			await self._respond(interaction, "I could not confirm your feedback was saved. Please try again later.")
			return

		try:
			message = await channel.send(embed=embed)
		except Exception:
			logger.exception(
				"Feedback %s is saved, but moderation send failed; delivery is unconfirmed and no retry is queued",
				feedback_id,
			)
			await self._respond(
				interaction,
				f"Your feedback is saved (reference {feedback_id}), but I could not confirm delivery to the moderation "
				"channel. It may not be reviewed until an administrator resolves this; no automatic retry is queued.",
			)
			return

		try:
			tracked_id = await interaction.client.db.pool.fetchval(
				"""UPDATE feedback SET discord_message_id = $1
				   WHERE id = $2 AND discord_message_id IS NULL RETURNING id""",
				message.id,
				feedback_id,
			)
			if tracked_id is None:
				raise RuntimeError("Feedback draft was not available to track the moderation message")
		except Exception:
			logger.exception(
				"Feedback %s is saved, but tracking of moderation message %s in channel %s could not be confirmed",
				feedback_id,
				message.id,
				channel.id,
			)
			try:
				await message.delete()
			except Exception:
				logger.exception(
					"Compensating deletion failed for feedback %s message %s; an untracked message may remain",
					feedback_id,
					message.id,
				)
			else:
				logger.warning(
					"Deleted moderation message %s after feedback %s tracking failure", message.id, feedback_id
				)
				try:
					# A lost DB response may have hidden a successful update.
					await interaction.client.db.pool.execute(
						"UPDATE feedback SET discord_message_id = NULL WHERE id = $1 AND discord_message_id = $2",
						feedback_id,
						message.id,
					)
				except Exception:
					logger.exception(
						"Feedback %s is retained, but may still reference deleted message %s", feedback_id, message.id
					)
			await self._respond(
				interaction,
				f"Your feedback is saved (reference {feedback_id}), but its moderation delivery could not be confirmed. "
				"An administrator must resolve this; no automatic retry is queued.",
			)
			return

		reactions_ready = True
		for emoji in (ACCEPT_EMOJI, DENY_EMOJI):
			try:
				await message.add_reaction(emoji)
			except Exception:
				reactions_ready = False
				logger.exception(
					"Feedback %s is tracked, but moderation reaction %s could not be added", feedback_id, emoji
				)
		response = (
			f"Thank you! Your {self.feedback_type.value} is saved and posted for review (reference {feedback_id})."
		)
		if not reactions_ready:
			response += (
				" The moderation reaction controls could not be fully added; an administrator may need to restore them."
			)
		await self._respond(interaction, response)

	async def _respond(self, interaction: discord.Interaction, content: str):
		try:
			if interaction.response.is_done():
				await interaction.followup.send(content, ephemeral=True)
			else:
				await interaction.response.send_message(content, ephemeral=True)
		except Exception:
			logger.exception("Could not send feedback submission response to user %s", interaction.user.id)

	async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
		logger.exception(
			"Feedback modal failed for user %s",
			interaction.user.id,
			exc_info=(type(error), error, error.__traceback__),
		)
		await self._respond(interaction, "Something went wrong, and I could not confirm your feedback submission.")


async def setup(bot: core.Substiify):
	await bot.add_cog(Feedback(bot))
