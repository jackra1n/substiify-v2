import asyncio
import logging

import discord
from asyncpg import Record
from discord import app_commands
from discord.ext import commands

import core
from core import best_effort
import utils
from database.karma import lock_karma_rows

logger = logging.getLogger(__name__)


class KasinoStateError(ValueError):
	pass


def _check_kasino(kasino: Record | None, guild_id: int, *, allow_settled: bool = False) -> None:
	if kasino is None:
		raise KasinoStateError("Kasino not found. This message may be out of date.")
	if kasino["discord_server_id"] != guild_id:
		raise KasinoStateError("That kasino is not in this server.")
	if kasino["settled_at"] is not None and not allow_settled:
		raise KasinoStateError("This kasino has already closed. No more changes are allowed.")


def _calculate_payouts(bets: list[Record], winning_option: int) -> dict[int, int]:
	if winning_option == 3:
		return {bet["discord_user_id"]: bet["amount"] for bet in bets}
	payouts = {bet["discord_user_id"]: 0 for bet in bets}
	total_pool = sum(bet["amount"] for bet in bets)
	winners = [bet for bet in bets if bet["option"] == winning_option]
	winner_pool = sum(bet["amount"] for bet in winners)
	if not winner_pool:
		return payouts
	remainders = []
	for bet in winners:
		user_id = bet["discord_user_id"]
		payouts[user_id], remainder = divmod(bet["amount"] * total_pool, winner_pool)
		remainders.append((remainder, user_id))
	# Largest remainders win the indivisible units; equal remainders favor the lower user ID.
	remainders.sort(key=lambda entry: (-entry[0], entry[1]))
	for _, user_id in remainders[: total_pool - sum(payouts.values())]:
		payouts[user_id] += 1
	return payouts


def _calculate_odds(bets_a_amount: int, bets_b_amount: int) -> tuple[float, float]:
	total_bets: float = float(bets_a_amount + bets_b_amount)
	a_odds: float = total_bets / float(bets_a_amount) if bets_a_amount else 1.0
	b_odds: float = total_bets / float(bets_b_amount) if bets_b_amount else 1.0
	return a_odds, b_odds


def _kasino_conclusion(kasino: Record, bets: list[Record]) -> discord.Embed:
	total_pool = sum(bet["amount"] for bet in bets)
	paid = sum(bet["payout"] for bet in bets)
	winner = kasino["winning_option"]
	if winner == 3:
		title = f"Cancelled: {kasino['question']}"
		description = f"All bets have been refunded.\nReturned: **{paid} karma**."
	else:
		title = f"Result: {kasino[f'option{winner}']}"
		description = f"Question: {kasino['question']}\n"
		if any(bet["option"] == winner for bet in bets):
			description += f"Distributed to the winners: **{paid} karma**."
		else:
			description += f"No one bet on the winning option. **{total_pool} karma** was forfeited; no payouts."
	embed = discord.Embed(title=title, description=description, color=core.constants.PRIMARY_COLOR)
	embed.set_footer(text=f"Closed kasino | ID: {kasino['id']}")
	return embed


async def _send_kasino_dm(bot: core.Substiify, user_id: int, embed: discord.Embed) -> None:
	async def send():
		user = bot.get_user(user_id) or await bot.fetch_user(user_id)
		await user.send(embed=embed)

	await best_effort(send())


class Kasino(commands.Cog):
	def __init__(self, bot: core.Substiify):
		self.bot = bot
		self.kasino_message_locks: dict[int, asyncio.Lock] = {}

	def _create_kasino_message_url(self, kasino: Record) -> str:
		return f"https://discordapp.com/channels/{kasino['discord_server_id']}/{kasino['discord_channel_id']}/{kasino['discord_message_id']}"

	@commands.hybrid_group(name="kasino", aliases=["kas"], invoke_without_command=True)
	@commands.guild_only()
	async def kasino(self, ctx: commands.Context):
		"""Karma kasino which allows people to bet on a question with two options.
		If you want to open a kasino, use the subcommand `kasino open`.
		"""
		await ctx.send_help(ctx.command)

	@kasino.command(name="open", aliases=["o"], usage='open "<question>" "<option1>" "<option2>"')
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(
		question="The qestion users will bet on.",
		op_a="The first option users can bet on.",
		op_b="The second option users can bet on.",
	)
	async def kasino_open(self, ctx: commands.Context, question: str, op_a: str, op_b: str):
		"""Opens a karma kasino which allows people to bet on a question with two options.
		Check "karma" and "votes" commands for more info on karma.
		"""
		await ctx.defer()
		await self.bot.db.prepare_command_context(ctx.author, ctx.guild, ctx.channel)
		kasino_msg = await best_effort(ctx.send(embed=discord.Embed(description="Opening kasino...")))
		if kasino_msg is None:
			return
		kasino_id = await self.bot.db.pool.fetchval(
			"""INSERT INTO kasino (discord_server_id, discord_channel_id, discord_message_id, question, option1, option2)
			VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
			ctx.guild.id,
			ctx.channel.id,
			kasino_msg.id,
			question,
			op_a,
			op_b,
		)
		await _update_kasino_msg(ctx.bot, kasino_id)

	@kasino.command(name="close", usage="close <kasino_id> <winning_option>")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(
		kasino_id="The ID of the kasino you want to close. The ID should be visible in the kasino message.",
		winner="The winning option. 1 or 2. 3 to abort.",
	)
	async def kasino_close(self, ctx: commands.Context, kasino_id: int, winner: int):
		"""Closes a karma kasino and announces the winner. To cancel the kasino, use 3 as the winner."""
		await ctx.defer()
		try:
			kasino, bets, newly_settled = await self.settle_kasino(kasino_id, ctx.guild.id, winner)
		except KasinoStateError as error:
			return await best_effort(ctx.reply(str(error), ephemeral=True))
		await _update_kasino_msg(self.bot, kasino_id)
		await best_effort(
			ctx.send(
				content=None if newly_settled else "Already closed; showing the saved result. No karma was paid again.",
				embed=_kasino_conclusion(kasino, bets),
			)
		)
		if newly_settled:
			await self._notify_kasino_results(kasino, bets)

	@kasino_close.error
	async def kasino_close_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.errors.MissingRequiredArgument):
			msg = f"You didn't provide a required argument!\nCorrect usage is `{ctx.prefix}kasino close <kasino_id> <winning_option>`"
			msg += "\nUse option `3` to close and abort the kasino (no winner)."
			embed = discord.Embed(description=msg, color=discord.Colour.red())
			await ctx.send(embed=embed)
		elif isinstance(error, commands.errors.BadArgument):
			await ctx.send(f"Bad argument: {error}")
		else:
			# Unrecognized errors keep their invocation for the central handler's diagnostics.
			return
		error.is_handled = True
		if not ctx.interaction:
			await ctx.message.delete()

	@kasino.command(name="list", aliases=["l"], usage="list")
	async def kasino_list(self, ctx: commands.Context):
		"""Lists all open kasinos on the server."""
		await ctx.defer()
		embed = discord.Embed(title="Open kasinos")
		stmt_kasinos = """SELECT * FROM kasino
			WHERE settled_at IS NULL AND discord_server_id = $1 ORDER BY id ASC"""
		all_kasinos: list[Record] = await self.bot.db.pool.fetch(stmt_kasinos, ctx.guild.id)
		embed_kasinos = "".join(
			f"`{entry['id']}` - [{entry['question']}]({self._create_kasino_message_url(entry)}){' (locked)' if entry['locked'] else ''}\n"
			for entry in all_kasinos
		)
		embed.description = embed_kasinos or "No open kasinos found."
		await ctx.send(embed=embed, delete_after=300)
		if not ctx.interaction:
			await ctx.message.delete()

	@kasino.command(name="resend", usage="resend <kasino_id>")
	@commands.cooldown(1, 30)
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(kasino_id="The ID of the kasino you want to resend.")
	async def resend_kasino(self, ctx: commands.Context, kasino_id: int):
		"""Resends a kasino message if it got lost in the channel."""
		await ctx.defer()
		try:
			kasino = await self.bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
			_check_kasino(kasino, ctx.guild.id, allow_settled=True)
		except KasinoStateError as error:
			return await best_effort(ctx.send(str(error), ephemeral=True))
		await self.bot.db.prepare_command_context(ctx.author, ctx.guild, ctx.channel)
		message = await _update_kasino_msg(self.bot, kasino_id, resend_channel=ctx.channel)
		if message is None:
			await best_effort(
				ctx.send(
					"Could not resend the kasino message. The saved state is unchanged; try again.", ephemeral=True
				)
			)
		elif ctx.interaction:
			await best_effort(ctx.send(f"Kasino message sent: {message.jump_url}", ephemeral=True))

	async def place_kasino_bet(
		self, kasino_id: int, guild_id: int, user_id: int, option: int, amount: int
	) -> tuple[int, int, bool]:
		if option not in {1, 2} or not 0 < amount <= 2**63 - 1:
			raise KasinoStateError("Choose option 1 or 2 and a positive karma amount within the supported range.")
		async with self.bot.db.pool.acquire(timeout=5) as conn:
			async with conn.transaction():
				kasino = await conn.fetchrow("SELECT * FROM kasino WHERE id = $1 FOR UPDATE", kasino_id)
				_check_kasino(kasino, guild_id)
				if kasino["locked"]:
					raise KasinoStateError("This kasino is locked. No more bets are accepted.")
				bet = await conn.fetchrow(
					"SELECT * FROM kasino_bet WHERE kasino_id = $1 AND discord_user_id = $2",
					kasino_id,
					user_id,
				)
				if bet is not None and bet["option"] != option:
					raise KasinoStateError("Your existing bet is on the other option. You cannot change sides.")
				remaining = await conn.fetchval(
					"""UPDATE karma SET amount = amount - $1
					WHERE discord_user_id = $2 AND discord_server_id = $3 AND amount >= $1
					RETURNING amount""",
					amount,
					user_id,
					guild_id,
				)
				if remaining is None:
					raise KasinoStateError("You don't have enough karma!")
				total_bet = await conn.fetchval(
					"""INSERT INTO kasino_bet (kasino_id, discord_user_id, amount, option)
					VALUES ($1, $2, $3, $4)
					ON CONFLICT (kasino_id, discord_user_id)
					DO UPDATE SET amount = kasino_bet.amount + EXCLUDED.amount
					RETURNING amount""",
					kasino_id,
					user_id,
					amount,
					option,
				)
		return total_bet, remaining, bet is not None

	async def settle_kasino(
		self, kasino_id: int, guild_id: int, winning_option: int
	) -> tuple[Record, list[Record], bool]:
		"""Persist the entire settlement before any Discord work; retries only read its result."""
		if winning_option not in {1, 2, 3}:
			raise KasinoStateError("Winner has to be 1, 2 or 3 (abort).")
		async with self.bot.db.pool.acquire(timeout=5) as conn:
			async with conn.transaction():
				kasino = await conn.fetchrow("SELECT * FROM kasino WHERE id = $1 FOR UPDATE", kasino_id)
				_check_kasino(kasino, guild_id, allow_settled=True)
				bets = await conn.fetch(
					"SELECT * FROM kasino_bet WHERE kasino_id = $1 ORDER BY discord_user_id", kasino_id
				)
				if kasino["settled_at"] is not None:
					if kasino["winning_option"] != winning_option:
						raise KasinoStateError(
							f"This kasino already closed with result {kasino['winning_option']}; its result cannot change."
						)
					return kasino, bets, False
				payouts = _calculate_payouts(bets, winning_option)
				await lock_karma_rows(conn, guild_id, payouts)
				for user_id, payout in payouts.items():
					await conn.execute(
						"UPDATE kasino_bet SET payout = $1 WHERE kasino_id = $2 AND discord_user_id = $3",
						payout,
						kasino_id,
						user_id,
					)
					if payout:
						await conn.execute(
							"""UPDATE karma SET amount = amount + $1
							WHERE discord_user_id = $2 AND discord_server_id = $3""",
							payout,
							user_id,
							guild_id,
						)
				kasino = await conn.fetchrow(
					"""UPDATE kasino SET settled_at = NOW(), winning_option = $2, locked = True
					WHERE id = $1 RETURNING *""",
					kasino_id,
					winning_option,
				)
				bets = await conn.fetch(
					"SELECT * FROM kasino_bet WHERE kasino_id = $1 ORDER BY discord_user_id", kasino_id
				)
		return kasino, bets, True

	async def set_kasino_locked(self, kasino_id: int, guild_id: int, locked: bool) -> tuple[Record, bool]:
		async with self.bot.db.pool.acquire(timeout=5) as conn:
			async with conn.transaction():
				kasino = await conn.fetchrow("SELECT * FROM kasino WHERE id = $1 FOR UPDATE", kasino_id)
				_check_kasino(kasino, guild_id)
				if kasino["locked"] == locked:
					return kasino, False
				kasino = await conn.fetchrow(
					"UPDATE kasino SET locked = $2 WHERE id = $1 RETURNING *", kasino_id, locked
				)
		return kasino, True

	async def _notify_kasino_results(self, kasino: Record, bets: list[Record]) -> None:
		# A fixed number of workers bounds Discord requests without allocating a task per bettor.
		pending = iter(bets)

		async def send_results():
			for bet in pending:
				payout = bet["payout"]
				if kasino["winning_option"] == 3:
					title = f"You have been refunded {payout} karma."
				elif payout:
					title = f"You have won {payout} karma!"
				else:
					title = f"You lost your {bet['amount']} karma bet."
				embed = discord.Embed(
					title=title,
					description=f"Question: {kasino['question']}\nOriginal bet: {bet['amount']} karma.",
					color=core.constants.PRIMARY_COLOR,
				)
				await _send_kasino_dm(self.bot, bet["discord_user_id"], embed)

		try:
			async with asyncio.timeout(60):
				await asyncio.gather(*(send_results() for _ in range(min(5, len(bets)))))
		except TimeoutError:
			logger.warning("Timed out notifying kasino %s participants; settlement is saved.", kasino["id"])


async def _update_kasino_msg(bot: core.Substiify, kasino_id: int, *, resend_channel=None) -> discord.Message | None:
	# Serialize renders on this bot so a slow pre-settlement edit cannot overwrite the conclusion.
	cog: Kasino = bot.get_cog("Kasino")
	lock = cog.kasino_message_locks.setdefault(kasino_id, asyncio.Lock())
	async with lock:
		try:
			async with asyncio.timeout(30):
				kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
				if kasino is None:
					return None
				old_message = None
				if resend_channel is not None:
					_check_kasino(kasino, resend_channel.guild.id, allow_settled=True)
					kasino_msg = await best_effort(
						resend_channel.send(embed=discord.Embed(description="Loading kasino..."))
					)
					if kasino_msg is None:
						return None
					moved = await bot.db.pool.fetchval(
						"""UPDATE kasino SET discord_channel_id = $1, discord_message_id = $2
						WHERE id = $3 AND discord_channel_id = $4 AND discord_message_id = $5
						RETURNING id""",
						resend_channel.id,
						kasino_msg.id,
						kasino_id,
						kasino["discord_channel_id"],
						kasino["discord_message_id"],
					)
					if moved is None:
						await best_effort(kasino_msg.delete())
						return None
					old_message = (kasino["discord_channel_id"], kasino["discord_message_id"])
				else:
					channel = bot.get_channel(kasino["discord_channel_id"]) or await bot.fetch_channel(
						kasino["discord_channel_id"]
					)
					kasino_msg = await channel.fetch_message(kasino["discord_message_id"])

				# Fetch one coherent current snapshot after channel/message network calls.
				async with bot.db.pool.acquire(timeout=5) as conn:
					async with conn.transaction(isolation="repeatable_read", readonly=True):
						kasino = await conn.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
						bets = await conn.fetch("SELECT * FROM kasino_bet WHERE kasino_id = $1", kasino_id)
				if kasino is None:
					return None
				if kasino["discord_message_id"] != kasino_msg.id:
					await best_effort(kasino_msg.edit(view=None))
					return None
				if kasino["settled_at"] is not None:
					embed = _kasino_conclusion(kasino, bets)
					view = None
				else:
					bets_a_amount = sum(bet["amount"] for bet in bets if bet["option"] == 1)
					bets_b_amount = sum(bet["amount"] for bet in bets if bet["option"] == 2)
					a_odds, b_odds = _calculate_odds(bets_a_amount, bets_b_amount)
					description = "Place your bets!"
					if kasino["locked"]:
						description = "The kasino is locked. No more bets are accepted."
					description += f"\n**Participants:** `{len(bets)}`"
					embed = discord.Embed(
						title=f"{'[LOCKED] ' if kasino['locked'] else ''}{kasino['question']}",
						description=description,
						color=core.constants.PRIMARY_COLOR,
					)
					embed.set_footer(text=f"On the table: {bets_a_amount + bets_b_amount} Karma | ID: {kasino_id}")
					for option, odds, amount in ((1, a_odds, bets_a_amount), (2, b_odds, bets_b_amount)):
						embed.add_field(
							name=f"**{option}:** {kasino[f'option{option}']}",
							value=f"**Odds:** 1:{odds:.3f}\n**Pool:** {amount} Karma",
						)
					view = KasinoView(kasino)
				await kasino_msg.edit(embed=embed, view=view)
				if old_message is not None:

					async def delete_old_message():
						channel_id, message_id = old_message
						channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
						message = await channel.fetch_message(message_id)
						await message.delete()

					await best_effort(delete_old_message())
				return kasino_msg
		except Exception:
			logger.warning("Could not refresh kasino %s; its database state is saved.", kasino_id, exc_info=True)
			return None


class KasinoView(discord.ui.View):
	def __init__(self, kasino: Record):
		super().__init__(timeout=None)
		self.kasino = kasino
		if kasino["settled_at"] is not None:
			return
		if not kasino["locked"]:
			self.add_item(KasinoBetButton(1))
			self.add_item(KasinoBetButton(2))
		self.add_item(KasinoLockButton(kasino))


class KasinoBetButton(discord.ui.Button):
	def __init__(self, option: int):
		gamba_emoji = discord.PartialEmoji.from_str("karmabet:817354842699857920")
		self.option = option
		super().__init__(label=f"Bet: {option}", emoji=gamba_emoji, style=discord.ButtonStyle.blurple)

	async def callback(self, interaction: discord.Interaction):
		bot: core.Substiify = interaction.client
		try:
			# Modals cannot follow a defer, so bound this lookup below Discord's response deadline.
			async with asyncio.timeout(2):
				kasino = await bot.db.pool.fetchrow(
					"""SELECT k.*, balance.amount AS bettor_karma, bet.option AS bet_option
					FROM kasino k
					LEFT JOIN karma balance ON balance.discord_user_id = $2
						AND balance.discord_server_id = k.discord_server_id
					LEFT JOIN kasino_bet bet ON bet.kasino_id = k.id AND bet.discord_user_id = $2
					WHERE k.id = $1""",
					self.view.kasino["id"],
					interaction.user.id,
				)
			_check_kasino(kasino, interaction.guild_id)
			if kasino["locked"]:
				raise KasinoStateError("This kasino is locked. No more bets are accepted.")
			if not kasino["bettor_karma"] or kasino["bettor_karma"] < 1:
				raise KasinoStateError("You don't have enough karma!")
			if kasino["bet_option"] is not None and kasino["bet_option"] != self.option:
				raise KasinoStateError("Your existing bet is on the other option. You cannot change sides.")
		except TimeoutError:
			return await best_effort(
				interaction.response.send_message("The database is busy. Please try again.", ephemeral=True)
			)
		except KasinoStateError as error:
			return await best_effort(interaction.response.send_message(str(error), ephemeral=True))
		await best_effort(interaction.response.send_modal(KasinoBetModal(kasino, kasino["bettor_karma"], self.option)))


class KasinoLockButton(discord.ui.Button):
	def __init__(self, kasino: Record):
		locked = kasino["locked"]
		self.expected_locked = locked
		self.lock_settings = {
			True: ("Unlock", "🔐", discord.ButtonStyle.red),
			False: ("Lock", "🔒", discord.ButtonStyle.grey),
		}
		label, emoji, style = self.lock_settings[locked]
		super().__init__(label=label, emoji=emoji, style=style)

	async def callback(self, interaction: discord.Interaction):
		await interaction.response.defer(ephemeral=True)
		bot: core.Substiify = interaction.client
		cog: Kasino = bot.get_cog("Kasino")
		kasino_id = self.view.kasino["id"]
		if not interaction.user.guild_permissions.manage_channels and not await bot.is_owner(interaction.user):
			return await best_effort(
				interaction.followup.send("You don't have permission to lock this kasino.", ephemeral=True)
			)
		try:
			kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", kasino_id)
			_check_kasino(kasino, interaction.guild_id)
			if kasino["locked"] != self.expected_locked:
				await best_effort(
					interaction.followup.send(
						"The kasino lock state changed. Please use the refreshed buttons.",
						ephemeral=True,
					)
				)
				await _update_kasino_msg(bot, kasino_id)
				return
			if kasino["locked"]:
				embed = discord.Embed(
					title="Unlock kasino",
					description=f"Unlock kasino {kasino_id}? Participants will be notified that they can increase their bets.",
					color=core.constants.PRIMARY_COLOR,
				)
				return await best_effort(
					interaction.followup.send(
						embed=embed,
						view=KasinoConfirmUnlockView(kasino_id),
						ephemeral=True,
					)
				)
			_, changed = await cog.set_kasino_locked(kasino_id, interaction.guild_id, True)
		except KasinoStateError as error:
			return await best_effort(interaction.followup.send(str(error), ephemeral=True))
		await best_effort(
			interaction.followup.send(
				"Kasino locked!" if changed else "Kasino is already locked.",
				ephemeral=True,
			)
		)
		await _update_kasino_msg(bot, kasino_id)


class KasinoBetModal(discord.ui.Modal):
	def __init__(self, kasino: Record, bettor_karma: int, option: int):
		title = utils.ux.strip_emotes(kasino["question"])
		if len(title) > 45:
			title = title[:42] + "..."
		super().__init__(title=title)
		self.option = option
		self.kasino_id = kasino["id"]
		option_str = kasino[f"option{option}"]
		label_str = f"Bet for option: {option_str}"
		if len(label_str) > 45:
			label_str = label_str[:42] + "..."
		self.bet_amount_input = discord.ui.TextInput(
			label=label_str, style=discord.TextStyle.short, placeholder=f"Your karma: {bettor_karma}", required=True
		)
		self.add_item(self.bet_amount_input)

	async def on_submit(self, interaction: discord.Interaction) -> None:
		await interaction.response.defer(ephemeral=True)
		bot: core.Substiify = interaction.client
		cog: Kasino = bot.get_cog("Kasino")
		try:
			amount = int(self.bet_amount_input.value)
		except ValueError:
			return await best_effort(interaction.followup.send("Invalid amount.", ephemeral=True))
		try:
			await bot.db.prepare_command_context(interaction.user, interaction.guild, interaction.channel)
			total_bet, remaining_karma, increased = await cog.place_kasino_bet(
				self.kasino_id,
				interaction.guild_id,
				interaction.user.id,
				self.option,
				amount,
			)
		except KasinoStateError as error:
			return await best_effort(interaction.followup.send(str(error), ephemeral=True))
		output = "Increased" if increased else "Added"
		output_embed = discord.Embed(
			title=f"{output} bet on option {self.option} for {amount} karma.",
			description=f"Kasino ID: {self.kasino_id}\nTotal bet: {total_bet} karma\nRemaining karma: {remaining_karma}",
			color=core.constants.PRIMARY_COLOR,
		)
		await best_effort(interaction.followup.send(embed=output_embed, ephemeral=True))
		await _update_kasino_msg(bot, self.kasino_id)


class KasinoConfirmUnlockView(discord.ui.View):
	def __init__(self, kasino_id: int):
		super().__init__(timeout=None)
		self.kasino_id = kasino_id

	@discord.ui.button(label="Unlock", style=discord.ButtonStyle.blurple)
	async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button):
		await interaction.response.defer(ephemeral=True)
		bot: core.Substiify = interaction.client
		cog: Kasino = bot.get_cog("Kasino")
		if not interaction.user.guild_permissions.manage_channels and not await bot.is_owner(interaction.user):
			return await best_effort(
				interaction.followup.send("You don't have permission to unlock this kasino.", ephemeral=True)
			)
		try:
			kasino, changed = await cog.set_kasino_locked(self.kasino_id, interaction.guild_id, False)
		except KasinoStateError as error:
			return await best_effort(interaction.followup.send(str(error), ephemeral=True))
		if not changed:
			return await best_effort(interaction.followup.send("Kasino is already unlocked.", ephemeral=True))
		await best_effort(
			interaction.followup.send(
				"Kasino unlocked. Participant notifications will be attempted.",
				ephemeral=True,
			)
		)
		await _update_kasino_msg(bot, self.kasino_id)
		kasino = await bot.db.pool.fetchrow("SELECT * FROM kasino WHERE id = $1", self.kasino_id)
		if kasino is None or kasino["settled_at"] is not None or kasino["locked"]:
			return
		kasino_members = iter(
			await bot.db.pool.fetch("SELECT discord_user_id FROM kasino_bet WHERE kasino_id = $1", self.kasino_id)
		)
		embed = discord.Embed(
			title=f"Kasino {self.kasino_id} unlocked!",
			description=f"{kasino['question']}\n[Jump to kasino]({cog._create_kasino_message_url(kasino)})",
			color=core.constants.PRIMARY_COLOR,
		)

		async def send_notifications():
			for member in kasino_members:
				await _send_kasino_dm(bot, member["discord_user_id"], embed)

		try:
			async with asyncio.timeout(60):
				await asyncio.gather(*(send_notifications() for _ in range(5)))
		except TimeoutError:
			logger.warning("Timed out notifying participants of kasino %s unlock.", self.kasino_id)


async def setup(bot: core.Substiify):
	await bot.add_cog(Kasino(bot))
