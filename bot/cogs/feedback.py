import logging
from enum import Enum

import discord
from asyncpg import Record
from core.bot import Substiify
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

ACCEPT_EMOJI = discord.PartialEmoji.from_str('greenTick:876177251832590348')
DENY_EMOJI = discord.PartialEmoji.from_str('redCross:876177262813278288')
SUGGESTION_CHANNEL_ID = 876413286978031676
BUG_CHANNEL_ID = 876412993498398740


class FeedbackType(Enum):
	BUG = 'bug'
	SUGGESTION = 'suggestion'


class FeedbackOutcome(Enum):
	ACCEPTED = 'accepted'
	DENIED = 'denied'


class Feedback(commands.Cog):
	COG_EMOJI = '📝'

	def __init__(self, bot: Substiify):
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

		message = await self.bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
		if message.author != self.bot.user:
			return

		stmt_update_feedback = """UPDATE feedback SET accepted = $1 WHERE discord_message_id = $2"""
		accepted = payload.emoji == ACCEPT_EMOJI
		await self.bot.db.execute(stmt_update_feedback, accepted, payload.message_id)
		feedback = await self.bot.db.fetchrow(
			'SELECT * FROM feedback WHERE discord_message_id = $1', payload.message_id
		)

		await self.edit_feedback_embed(feedback)
		await self.send_user_reply(feedback)

		await message.clear_reactions()

	async def edit_feedback_embed(self, feedback: Record):
		outcome = 'accepted' if feedback['accepted'] else 'denied'
		color = discord.Colour.green() if outcome == 'accepted' else discord.Colour.red()

		channel = await self.bot.fetch_channel(feedback['discord_channel_id'])
		message = await channel.fetch_message(feedback['discord_message_id'])
		embed = message.embeds[0]
		embed.color = color
		embed.title = f'{outcome.capitalize()} {feedback["feedback_type"]} submission'

		await message.edit(embed=embed)

	async def send_user_reply(self, feedback: Record):
		feedback_type_str = feedback['feedback_type']
		feedback_type = FeedbackType(feedback_type_str)

		is_accepted = feedback['accepted']
		outcome = 'accepted' if is_accepted else 'denied'

		color = discord.Colour.green() if is_accepted else discord.Colour.red()
		emoji = ACCEPT_EMOJI if is_accepted else DENY_EMOJI

		user = self.bot.get_user(feedback['discord_user_id']) or await self.bot.fetch_user(feedback['discord_user_id'])

		new_embed = discord.Embed(
			title=f'{feedback_type.value.capitalize()} submission',
			description=f'```{feedback["content"]}```',
			color=color,
		)
		message_to_user = f'Hello {user.name}!\nYour {self.bot.user.mention} {feedback_type.value} submission has been **{outcome}** {emoji}.'
		await user.send(content=message_to_user, embed=new_embed)

	@commands.cooldown(2, 100)
	@app_commands.command(
		name='feedback',
		description='Opens a modal window on discord where you can suggest an improvement to the developer team.',
	)
	async def feedback(self, interaction: discord.Interaction, feedback_type: FeedbackType):
		"""
		Allows you to report a bug or suggest a feature or an improvement to the developer team.
		After submitting your bug, you will get a message from the bot with the outcome of your submission.
		"""
		await interaction.response.send_modal(FeedbackModal(feedback_type))


class FeedbackSelect(discord.ui.Select):
	def __init__(self):
		super().__init__(
			placeholder='Select a type of submission...',
			options=[
				discord.SelectOption(
					label='Bug fix',
					description='Report a bug that needs to be fixed',
					emoji='🐛',
					value=FeedbackType.BUG,
				),
				discord.SelectOption(
					label='Improvement suggestion',
					description='Suggest an improvement to the bot',
					emoji='👍',
					value=FeedbackType.SUGGESTION,
				),
			],
		)

	async def callback(self, interaction: discord.Interaction):
		await interaction.response.send_modal(FeedbackModal(self.values[0]))


class FeedbackModal(discord.ui.Modal):
	def __init__(self, feedback_type: FeedbackType):
		super().__init__(title='Suggestions & Feedback')
		self.feedback_type = FeedbackType(feedback_type)
		self.feedback = discord.ui.TextInput(
			label=self.feedback_type.value.capitalize(),
			style=discord.TextStyle.long,
			placeholder='Write your bug fix or improvement suggestion here...',
			required=True,
			min_length=10,
			max_length=300,
		)
		self.add_item(self.feedback)

	async def on_submit(self, interaction: discord.Interaction):
		channel_id = BUG_CHANNEL_ID if self.feedback_type == FeedbackType.BUG else SUGGESTION_CHANNEL_ID
		channel: discord.TextChannel = interaction.client.get_channel(channel_id)
		embed = discord.Embed(
			title=f'New {self.feedback_type.value} submission',
			description=f'```{self.feedback.value}```',
			color=0x1E9FE3,
		)
		embed.set_footer(text=interaction.user, icon_url=interaction.user.avatar)
		message = await channel.send(embed=embed)

		stmt_feedback = """INSERT INTO feedback
                           (feedback_type, content, discord_user_id, discord_server_id, discord_channel_id, discord_message_id)
                           VALUES ($1, $2, $3, $4, $5, $6)"""
		await interaction.client.db.execute(
			stmt_feedback,
			self.feedback_type.value,
			self.feedback.value,
			interaction.user.id,
			interaction.guild.id,
			channel_id,
			message.id,
		)

		await message.add_reaction(ACCEPT_EMOJI)
		await message.add_reaction(DENY_EMOJI)
		await interaction.response.send_message(f'Thank you for submitting the {self.feedback_type.value}!')

	async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
		await interaction.response.send_message('Oops! Something went wrong.', ephemeral=True)
		logger.error(type(error), error, error.__traceback__)


async def setup(bot: Substiify):
	await bot.add_cog(Feedback(bot))
