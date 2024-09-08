from __future__ import annotations

import logging
import re

import discord
from asyncpg import Record

import utils

logger = logging.getLogger(__name__)


class KasinoView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)


class KasinoBetButton(
	discord.ui.DynamicItem[discord.ui.Button], template=r"kasino_bet:(?P<kid>[0-9]+):(?P<option>[0-9]+)"
):
	def __init__(self, kasino: Record, option: int):
		gamba_emoji = discord.PartialEmoji.from_str("karmabet:817354842699857920")
		self.option = option
		self.kasino = kasino
		super().__init__(
			discord.ui.Button(
				label=f"Bet: {option}",
				emoji=gamba_emoji,
				style=discord.ButtonStyle.blurple,
				custom_id=f"kasino_bet:{kasino['id']}:{option}",
			)
		)

	@classmethod
	async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
		option = int(match["option"])
		kasino_id = int(match["kid"])
		stmt = "SELECT * FROM kasino WHERE id = $1;"
		kasino = await interaction.client.db.pool.fetchrow(stmt, kasino_id)
		return cls(kasino, option)

	async def callback(self, interaction: discord.Interaction):
		bot = interaction.client
		if self.kasino["locked"]:
			return await interaction.response.send_message(
				"The kasino is locked! No more bets are taken in. Time to wait and see...", ephemeral=True
			)

		user_karma_query = "SELECT amount FROM karma WHERE discord_user_id = $1 AND discord_server_id = $2"
		bettor_karma = await bot.db.pool.fetchval(user_karma_query, interaction.user.id, interaction.guild.id)
		if bettor_karma is None:
			return await interaction.response.send_message("You don't have any karma!", ephemeral=True)

		stmt_bet = "SELECT * FROM kasino_bet WHERE kasino_id = $1 AND discord_user_id = $2;"
		user_bet = await bot.db.pool.fetchrow(stmt_bet, self.kasino["id"], interaction.user.id)
		if user_bet and user_bet["option"] != self.option:
			return await interaction.response.send_message(
				"You can't change your choice on the bet. No chickening out!", ephemeral=True
			)

		modal = KasinoBetModal(self.kasino, bettor_karma, user_bet, self.option)
		await interaction.response.send_modal(modal)


class KasinoLockButton(discord.ui.DynamicItem[discord.ui.Button], template=r"kasino_lock:(?P<kid>[0-9]+)"):
	def __init__(self, kasino: Record):
		locked = kasino["locked"]
		self.kasino = kasino
		self.lock_settings = {
			True: ("Unlock", "🔐", discord.ButtonStyle.red),
			False: ("Lock", "🔒", discord.ButtonStyle.grey),
		}
		label, emoji, style = self.lock_settings[locked]
		super().__init__(
			discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"kasino_lock:{kasino['id']}")
		)

	@classmethod
	async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
		kasino_id = int(match["kid"])
		stmt = "SELECT * FROM kasino WHERE id = $1;"
		kasino = await interaction.client.db.pool.fetchrow(stmt, kasino_id)
		return cls(kasino)

	async def callback(self, interaction: discord.Interaction):
		bot = interaction.client
		kasino_id = self.kasino["id"]
		if not interaction.user.guild_permissions.manage_channels and not await bot.is_owner(interaction.user):
			return await interaction.response.send_message(
				"You don't have permission to lock the kasino!", ephemeral=True
			)
		is_locked = await bot.db.pool.fetchval("SELECT locked FROM kasino WHERE id = $1", kasino_id)
		if is_locked:
			label_str = f"""Are you sure you want to unlock kasino ID: `{kasino_id}`?
					To make it fair, all people who bet will get a message so they can increase their bets!
					To confirm, press the button below."""
			embed = discord.Embed(
				title="Unlock kasino", description=label_str, color=discord.Colour.from_rgb(52, 79, 235)
			)
			await interaction.response.send_message(
				embed=embed, view=KasinoConfirmUnlockView(kasino_id), ephemeral=True
			)
		else:
			await bot.db.pool.execute("UPDATE kasino SET locked = True WHERE id = $1", kasino_id)
			await _update_kasino_msg(bot, kasino_id)
			await interaction.response.send_message("Kasino locked!", ephemeral=True)


class KasinoBetModal(discord.ui.Modal):
	def __init__(self, kasino: Record, bettor_karma: int, user_bet: Record, option: int):
		title = utils.ux.strip_emotes(kasino["question"])
		if len(title) > 45:
			title = title[:42] + "..."
		super().__init__(title=title)
		self.option = option
		self.kasino = kasino
		self.bettor_karma = bettor_karma
		self.user_bet = user_bet
		option_str = kasino[f"option{option}"]
		label_str = f"Bet for option: {option_str}"
		if len(label_str) > 45:
			label_str = label_str[:42] + "..."
		self.bet_amount_input = discord.ui.TextInput(
			label=label_str, style=discord.TextStyle.short, placeholder=f"Your karma: {bettor_karma}", required=True
		)
		self.add_item(self.bet_amount_input)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		bot = interaction.client
		kasino_id: int = self.kasino["id"]
		amount: int = self.bet_amount_input.value
		bettor_karma: int = await bot.db.pool.fetchval(
			"SELECT amount FROM karma WHERE discord_user_id = $1 AND discord_server_id = $2",
			interaction.user.id,
			interaction.guild.id,
		)
		user_bet: Record = self.user_bet

		try:
			amount = int(amount)
		except ValueError:
			return await interaction.response.send_message("Invalid amount", ephemeral=True)
		if amount < 1:
			return await interaction.response.send_message("You tried to bet < 1 karma! Silly you!", ephemeral=True)

		if self.kasino["locked"]:
			return await interaction.response.send_message(
				"The kasino is locked! No more bets are taken in. Time to wait and see...", ephemeral=True
			)

		if bettor_karma < amount:
			return await interaction.response.send_message("You don't have enough karma!", ephemeral=True)

		total_bet = amount
		output = "added"

		if user_bet is not None:
			total_bet = user_bet["amount"] + amount
			output = "increased"

		stmt_bet = """INSERT INTO kasino_bet (kasino_id, discord_user_id, amount, option) VALUES ($1, $2, $3, $4)
                      ON CONFLICT (kasino_id, discord_user_id) DO UPDATE SET amount = kasino_bet.amount + $3;"""
		stmt_update_user_karma = (
			"UPDATE karma SET amount = karma.amount - $1 WHERE discord_user_id = $2 AND discord_server_id = $3;"
		)
		async with bot.db.pool.acquire() as conn:
			async with conn.transaction():
				await conn.execute(stmt_bet, kasino_id, interaction.user.id, amount, self.option)
				await conn.execute(stmt_update_user_karma, amount, interaction.user.id, interaction.guild.id)

		output_embed = discord.Embed(color=discord.Colour.from_rgb(209, 25, 25))
		output_embed.title = f"**Successfully {output} bet on option {self.option}, on kasino with ID {kasino_id} for {amount} karma! Total bet is now: {total_bet} Karma**"
		output_embed.color = discord.Colour.from_rgb(52, 79, 235)
		output_embed.description = f"Remaining karma: {bettor_karma - amount}"

		await interaction.response.send_message(embed=output_embed, ephemeral=True)
		logger.info(f"Bet[user: {interaction.user}, amount: {amount}, option: {self.option}, kasino: {kasino_id}]")
		await _update_kasino_msg(bot, kasino_id)


class KasinoConfirmUnlockView(discord.ui.View):
	def __init__(self, kasino_id: int):
		super().__init__(timeout=None)
		self.kasino_id = kasino_id

	@discord.ui.button(label="Unlock", style=discord.ButtonStyle.blurple)
	async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
		bot = interaction.client
		if not interaction.user.guild_permissions.manage_channels and not await bot.is_owner(interaction.user):
			return await interaction.response.send_message(
				"You don't have permission to unlock the kasino!", ephemeral=True
			)
		kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", self.kasino_id)
		is_locked = kasino["locked"]
		if not is_locked:
			return await interaction.response.send_message("Kasino is already unlocked!", ephemeral=True)
		await bot.db.pool.execute("UPDATE kasino SET locked = False WHERE id = $1", self.kasino_id)
		kasino_msg = await _update_kasino_msg(bot, self.kasino_id)
		kasino_members = await bot.db.pool.fetch(
			"SELECT discord_user_id FROM kasino_bet WHERE kasino_id = $1", self.kasino_id
		)
		embed = discord.Embed(
			title=f"🎲 Kasino `[ID: {self.kasino_id}]` unlocked!",
			description=f"{kasino['question']}\n[Jump to kasino]({kasino_msg.jump_url})",
			color=discord.Colour.from_rgb(52, 79, 235),
		)
		embed.set_footer(text=f"Unlocked by {interaction.user}", icon_url=interaction.user.display_avatar)

		for member in kasino_members:
			user = bot.get_user(member["discord_user_id"]) or await bot.fetch_user(member["discord_user_id"])
			await user.send(embed=embed)
		await interaction.response.send_message("Kasino unlocked! All kasino members messaged.", ephemeral=True)


async def _update_kasino_msg(bot, kasino_id: int) -> discord.Message:
	kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
	kasino_channel = await bot.fetch_channel(kasino["discord_channel_id"])
	kasino_msg = await kasino_channel.fetch_message(kasino["discord_message_id"])

	# FIGURE OUT AMOUNTS AND ODDS
	stmt_kasino_bets_sum = """SELECT SUM(amount) FROM kasino_bet WHERE kasino_id = $1 AND option = $2"""
	bets_a_amount: int = await bot.db.pool.fetchval(stmt_kasino_bets_sum, kasino_id, 1) or 0
	bets_b_amount: int = await bot.db.pool.fetchval(stmt_kasino_bets_sum, kasino_id, 2) or 0
	a_odds, b_odds = _calculate_odds(bets_a_amount, bets_b_amount)

	# CREATE MESSAGE
	description = "The kasino has been opened! Place your bets! :game_die:"
	if kasino["locked"]:
		description = "The kasino is locked! No more bets are taken in. Time to wait and see..."

	participants = await bot.db.pool.fetchval("SELECT COUNT(*) FROM kasino_bet WHERE kasino_id = $1", kasino_id)
	description += f"\n**Participants:** `{participants}`"

	title = f":game_die: {kasino['question']}"
	color = discord.Colour.from_rgb(52, 79, 235)
	if kasino["locked"]:
		title = f"[LOCKED] {title}"
		color = discord.Colour.from_rgb(209, 25, 25)

	embed = discord.Embed(title=title, description=description, color=color)
	embed.set_footer(text=f"On the table: {bets_a_amount + bets_b_amount} Karma | ID: {kasino_id}")
	embed.set_thumbnail(url="https://cdn.betterttv.net/emote/602548a4d47a0b2db8d1a3b8/3x.gif")
	embed.add_field(
		name=f'**1:** {kasino["option1"]}', value=f"**Odds:** 1:{round(a_odds, 3)}\n**Pool:** {bets_a_amount} Karma"
	)
	embed.add_field(
		name=f'**2:** {kasino["option2"]}', value=f"**Odds:** 1:{round(b_odds, 3)}\n**Pool:** {bets_b_amount} Karma"
	)

	kasino_view = KasinoView()
	if not kasino["locked"]:
		kasino_view.add_item(KasinoBetButton(kasino, 1))
		kasino_view.add_item(KasinoBetButton(kasino, 2))
	kasino_view.add_item(KasinoLockButton(kasino))

	await kasino_msg.edit(embed=embed, view=kasino_view)
	return kasino_msg


def _calculate_odds(bets_a_amount: int, bets_b_amount: int) -> tuple[float, float]:
	total_bets: float = float(bets_a_amount + bets_b_amount)
	a_odds: float = total_bets / float(bets_a_amount) if bets_a_amount else 1.0
	b_odds: float = total_bets / float(bets_b_amount) if bets_b_amount else 1.0
	return a_odds, b_odds
