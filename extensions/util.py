import asyncio
import datetime
import logging
import platform
import random
import secrets

import discord
import psutil
from discord import MessageType, app_commands
from discord.ext import commands, tasks

import core
import utils

logger = logging.getLogger(__name__)


class Util(commands.Cog):
	COG_EMOJI = "📦"

	def __init__(self, bot: core.Substiify):
		self.bot = bot
		self.giveaway_task.start()

	@commands.hybrid_group(aliases=["give"])
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	async def giveaway(self, ctx: commands.Context):
		"""
		Create and manage giveaways.

		Prefer slash commands:
		/giveaway create channel: #channel duration: 10m|2h|1d prize: "..." [hosted_by]

		Prefix (alias):
		<<give c <#channel> <duration> <prize> [@host]
		"""
		await ctx.send_help(ctx.command)

	@giveaway.command(aliases=["c"], usage="create <channel> <duration> <prize> [hosted_by] [winners]")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(
		channel="In which channel should the Giveaway be hosted?",
		duration="For how long should the Giveaway be hosted? Type number followed by (m|h|d). Example: `10m`",
		prize="What is the prize of the Giveaway?",
		hosted_by="Who is hosting the Giveaway? If not specified, the author of the command will be the host.",
		winners="How many winners should be selected? Default: 1",
	)
	async def create(
		self,
		ctx: commands.Context,
		channel: discord.TextChannel,
		duration: str,
		prize: str,
		hosted_by: discord.Member = None,
		winners: int = 1,
	):
		"""
		Create a giveaway. Requires Manage Channels.

		Slash (recommended):
		/giveaway create channel: #channel duration: 10m|2h|1d prize: "..." [hosted_by] [winners]

		Prefix examples:
		<<giveaway create <#channel> <duration> <prize> [@host]
		<<give c <#channel> <duration> <prize> [@host]
		"""
		if hosted_by is None or hosted_by.bot:
			hosted_by = ctx.author

		channel = await self.bot.fetch_channel(channel.id)
		perms = channel.permissions_for(ctx.me)
		missing = []
		if not perms.send_messages:
			missing.append("Send Messages")
		if not perms.add_reactions:
			missing.append("Add Reactions")
		if not perms.read_message_history:
			missing.append("Read Message History")
		if missing:
			missing_list = ", ".join(missing)
			embed = discord.Embed(
				description=f"I need these permissions in {channel.mention}: {missing_list}", color=discord.Colour.red()
			)
			return await self._safe_notify(ctx, embed=embed)

		time = self.convert(duration)
		# Check if Time is valid
		if time == -1:
			await self._safe_notify(
				ctx, embed=discord.Embed(description="The Time format was wrong", color=discord.Colour.red())
			)
			return
		elif time == -2:
			await self._safe_notify(
				ctx, embed=discord.Embed(description="The Time was not conventional number", color=discord.Colour.red())
			)
			return

		setup_complete = f"Setup finished. Giveaway for **'{prize}'** will be in {channel.mention}"
		await self._safe_notify(ctx, embed=discord.Embed(description=setup_complete))

		end = datetime.datetime.utcnow() + datetime.timedelta(seconds=time)
		end_string = end.strftime("%d.%m.%Y %H:%M")

		# Validate winners
		if winners < 1 or winners > 10:
			return await self._safe_notify(
				ctx,
				embed=discord.Embed(
					description="Please choose a winners count between 1 and 10.", color=discord.Colour.red()
				),
			)

		embed = self.create_giveaway_embed(hosted_by, prize, winners)
		base_desc = embed.description or ""
		embed.description = f"{base_desc}\nReact with :tada: to enter!\nEnds <t:{int(end.replace(tzinfo=datetime.timezone.utc).timestamp())}:R>"
		embed.set_footer(text=f"Giveaway ends on {end_string}")

		new_msg = await channel.send(embed=embed)
		stmt = """INSERT INTO giveaway (discord_user_id, end_date, prize, discord_server_id, discord_channel_id, discord_message_id)
                  VALUES ($1, $2, $3, $4, $5, $6)"""
		await self.bot.db.pool.execute(stmt, hosted_by.id, end, prize, ctx.guild.id, channel.id, new_msg.id)
		try:
			await new_msg.add_reaction("🎉")
		except discord.Forbidden:
			embed = discord.Embed(
				description="I couldn't add the 🎉 reaction due to missing permissions.", color=discord.Colour.red()
			)
			await self._safe_notify(ctx, embed=embed)

	@giveaway.command(usage="reroll <message_id>")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(message_id="The ID of the discord giveaway message you want to reroll.")
	async def reroll(self, ctx: commands.Context, message_id: int):
		"""
		Allows you to reroll a giveaway if something went wrong.
		"""
		try:
			msg = await ctx.fetch_message(message_id)
		except Exception:
			await self._safe_notify(
				ctx,
				embed=discord.Embed(
					description="The message couldn't be found in this channel", color=discord.Colour.red()
				),
			)
			return

		reaction = discord.utils.find(lambda r: str(r.emoji) == "🎉", msg.reactions)
		users = [] if reaction is None else [u async for u in reaction.users() if not u.bot]

		prize = await self.get_giveaway_prize(msg)
		winners = self.get_giveaway_winners(msg)
		giveaway_host = msg.embeds[0].fields[0].value
		embed = self.create_giveaway_embed(giveaway_host, prize, winners)

		await self.pick_winner(users, msg.channel, prize, embed, msg, winners)
		await msg.edit(embed=embed)
		await ctx.message.delete()

	@giveaway.command(name="list", usage="list")
	async def giveaway_list(self, ctx: commands.Context):
		"""
		Lists all active giveaways.
		"""
		giveaways = await self.bot.db.pool.fetch("SELECT * FROM giveaway WHERE discord_server_id = $1", ctx.guild.id)
		if len(giveaways) == 0:
			return await ctx.send("There are no active giveaways")

		embed = discord.Embed(title="Active Giveaways", description="")
		for giveaway in giveaways:
			end_date = giveaway["end_date"]
			embed.description += f"[{giveaway['prize']}](https://discord.com/channels/{giveaway['discord_server_id']}/{giveaway['discord_channel_id']}/{giveaway['discord_message_id']}) - Ends <t:{int(end_date.replace(tzinfo=datetime.timezone.utc).timestamp())}:R>\n"
		await ctx.send(embed=embed)

	@commands.command(name="giveawayInfo", hidden=True)
	@commands.is_owner()
	async def giveaway_info(self, ctx: commands.Context):
		"""
		Shows information about he giveaway task.
		"""
		if self.giveaway_task.time is None:
			times_string = "None"
		else:
			times_string = [f"{time}\n" for time in self.giveaway_task.time]
		embed = discord.Embed(title="Giveaway Task", description="")
		embed.add_field(name="Running", value=f"`{self.giveaway_task.is_running()}`", inline=False)
		embed.add_field(
			name="Current UTC time", value=f"`{datetime.datetime.now(datetime.timezone.utc)}`", inline=False
		)
		embed.add_field(name="Next iteration", value=f"`{self.giveaway_task.next_iteration}`", inline=False)
		embed.add_field(name="Last iteration", value=f"`{self.giveaway_task._last_iteration}`", inline=False)
		embed.add_field(name="Times", value=times_string, inline=False)
		await ctx.send(embed=embed)

	@giveaway.command(aliases=["cancel"], usage="stop <message_id>")
	@commands.check_any(commands.has_permissions(manage_channels=True), commands.is_owner())
	@app_commands.describe(message_id="The ID of the discord giveaway message you want to stop.")
	async def stop(self, ctx: commands.Context, message_id: int):
		"""
		Allows you to stop a giveaway. Takes the ID of the giveaway message as an argument.
		"""
		# delete giveaway from db
		giveaway = await self.bot.db.pool.execute("DELETE FROM giveaway WHERE discord_message_id = $1", message_id)
		if giveaway == "DELETE 0":
			return await ctx.send("The message ID provided was wrong")
		msg = await ctx.fetch_message(message_id)
		new_embed = discord.Embed(title="Giveaway Cancelled", description="The giveaway has been cancelled!")
		await msg.edit(embed=new_embed)
		await ctx.send("Giveaway has been cancelled", delete_after=30)
		await ctx.message.delete()

	@tasks.loop(seconds=30)
	async def giveaway_task(self):
		giveaways = await self.bot.db.pool.fetch("SELECT * FROM giveaway")
		for giveaway in giveaways:
			now = datetime.datetime.utcnow()
			end_date = giveaway["end_date"]
			if now < end_date:
				continue
			channel = await self.bot.fetch_channel(giveaway["discord_channel_id"])
			try:
				msg = await channel.fetch_message(giveaway["discord_message_id"])
			except discord.NotFound:
				await self.bot.db.pool.execute("DELETE FROM giveaway WHERE id = $1", giveaway["id"])
				return await channel.send(
					"Could not find the giveaway message! Deleting the giveaway.", delete_after=180
				)
			author_id = giveaway["discord_user_id"]
			author = self.bot.get_user(author_id) or await self.bot.fetch_user(author_id)
			reaction = discord.utils.find(lambda r: str(r.emoji) == "🎉", msg.reactions)
			users = [] if reaction is None else [u async for u in reaction.users() if not u.bot]
			prize = giveaway["prize"]
			winners = self.get_giveaway_winners(msg)
			embed = self.create_giveaway_embed(author, prize, winners)

			await self.pick_winner(users, channel, prize, embed, msg, winners)
			await msg.edit(embed=embed)
			await self.bot.db.pool.execute("DELETE FROM giveaway WHERE id = $1", giveaway["id"])

	async def pick_winner(
		self,
		users: list[discord.Member],
		channel: discord.TextChannel,
		prize: str,
		embed: discord.Embed,
		source_message: discord.Message | None = None,
		winners_count: int = 1,
	):
		# Check if User list is not empty
		if len(users) <= 0:
			message_text = "No one won the giveaway (no one entered)"
			if source_message is not None and source_message.guild is not None:
				message_url = f"https://discord.com/channels/{source_message.guild.id}/{source_message.channel.id}/{source_message.id}"
				jump = f" — [Jump to giveaway]({message_url})"
			else:
				jump = ""
			embed.remove_field(0)
			embed.set_footer(text=message_text)
			announce = discord.Embed(description=f"{message_text}{jump}", color=core.constants.PRIMARY_COLOR)
			await channel.send(embed=announce)
		else:
			unique = list({u.id: u for u in users}.values())
			k = max(1, min(winners_count or 1, len(unique)))
			try:
				sysrand = secrets.SystemRandom()
				winners = sysrand.sample(unique, k)
			except Exception:
				winners = [secrets.choice(unique)]
			mentions = ", ".join(w.mention for w in winners)
			if k == 1:
				embed.add_field(name=f"Congratulations on winning '{prize}'", value=winners[0].mention)
				win_text = f"Congratulations {winners[0].mention}! You won **{prize}**!"
			else:
				embed.add_field(name=f"Congratulations on winning '{prize}'", value=mentions)
				win_text = f"Congratulations {mentions}! You won **{prize}**!"
			if source_message is not None and source_message.guild is not None:
				message_url = f"https://discord.com/channels/{source_message.guild.id}/{source_message.channel.id}/{source_message.id}"
				win_text = f"{win_text} — [Jump to giveaway]({message_url})"
			await channel.send(win_text)

	async def get_giveaway_prize(self, msg: discord.Message):
		return msg.embeds[0].description.split("Win **")[1].split("**!")[0]

	def get_giveaway_winners(self, msg: discord.Message):
		# Prefer a dedicated embed field "Winners:" if present
		try:
			for field in msg.embeds[0].fields:
				if str(field.name).strip().lower() == "winners:":
					value = int(str(field.value).strip())
					return max(1, min(value, 10))
		except Exception:
			pass
		return 1

	def convert(self, time):
		pos = ["m", "h", "d"]
		time_dict = {"m": 60, "h": 3600, "d": 24 * 3600}
		unit = time[-1]
		if unit not in pos:
			return -1
		try:
			time_val = int(time[:-1])
		except Exception:
			return -2
		return time_val * time_dict[unit]

	def create_giveaway_embed(self, author: discord.Member, prize, winners):
		embed = discord.Embed(
			title=":tada: Giveaway :tada:",
			description=f"Win **{prize}**!",
			color=core.constants.CYAN_COLOR,
		)
		host = author.mention if isinstance(author, (discord.Member, discord.User)) else author
		embed.add_field(name="Hosted By:", value=host)
		embed.add_field(name="Winners:", value=str(winners))
		return embed

	async def _safe_notify(
		self,
		ctx: commands.Context,
		*,
		content: str | None = None,
		embed: discord.Embed | None = None,
		delete_after: float | None = None,
	):
		# For slash invocations, prefer ephemeral interaction responses
		interaction = getattr(ctx, "interaction", None)
		if interaction is not None:
			try:
				await interaction.response.send_message(content=content, embed=embed, ephemeral=True)
				return
			except Exception:
				try:
					await interaction.followup.send(content=content, embed=embed, ephemeral=True)
					return
				except Exception:
					pass
		# For prefix, try sending in-channel, then DM fallback
		try:
			await ctx.send(content=content, embed=embed, delete_after=delete_after)
		except discord.Forbidden:
			try:
				await ctx.author.send(content=content, embed=embed)
			except Exception:
				pass

	async def _cooldown_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.CommandOnCooldown):
			embed = discord.Embed(
				title="Slow it down!",
				description=f"Try again in {error.retry_after:.2f}s.",
				color=discord.Colour.red(),
			)

			await ctx.send(embed=embed, delete_after=30)
		if isinstance(error, commands.MissingRequiredArgument):
			await ctx.send("Missing the suggestion description", delete_after=30)
		await ctx.message.delete()

	@commands.cooldown(6, 5)
	@commands.hybrid_command(aliases=["av", "pfp"])
	async def avatar(self, ctx: commands.Context, member: discord.Member | discord.User | None = None):
		"""
		Enlarge and view your profile picture or another member
		"""
		member = member or ctx.author
		current_avatar = member.display_avatar
		embed = discord.Embed(
			title=f"{member.display_name}'s avatar",
			url=current_avatar.url,
			color=core.constants.CYAN_COLOR,
		)
		embed.set_image(url=current_avatar.url)
		await ctx.send(embed=embed)

	@avatar.error
	async def avatar_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.MemberNotFound):
			await ctx.send("Member not found", delete_after=30)

	@commands.group(aliases=["c"], invoke_without_command=True)
	@commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
	async def clear(self, ctx: commands.Context, amount: int = None):
		"""
		Clears messages within the current channel.
		"""
		if ctx.message.type == MessageType.reply:
			if message := ctx.message.reference.resolved:
				await message.delete()
				await ctx.message.delete()
			return
		if amount is None:
			return await ctx.send("Please specify the amount of messages to delete.", delete_after=30)

		if amount >= 100:
			return await ctx.send("Cannot delete more than 100 messages at a time!")
		await ctx.channel.purge(limit=amount + 1)

	@clear.command(aliases=["bot", "b"])
	@commands.check_any(commands.has_permissions(manage_messages=True), commands.is_owner())
	async def clear_bot(self, ctx: commands.Context, amount: int):
		"""Clears the bot's messages even in DMs"""
		bots_messages = [
			message async for message in ctx.channel.history(limit=amount + 1) if message.author == self.bot.user
		]

		if len(bots_messages) <= 100 and isinstance(ctx.channel, discord.TextChannel):
			await ctx.message.delete()
			await ctx.channel.delete_messages(bots_messages)

		elif isinstance(ctx.channel, discord.DMChannel):
			for message in bots_messages:
				await message.delete()
				await asyncio.sleep(0.75)

	@clear.error
	async def clear_error(self, ctx: commands.Context, error):
		if isinstance(error, commands.MissingRequiredArgument):
			await ctx.send("Please put an amount to clear.")

	@commands.command(aliases=["dink"])
	async def ping(self, ctx: commands.Context):
		"""
		Shows the ping of the bot
		"""
		title = "Donk! 🏓" if "dink" in ctx.message.content.lower() else "Pong! 🏓"
		desc = f"⏱️Ping: `{round(self.bot.latency * 1000)}`ms"
		embed = discord.Embed(title=title, description=desc, color=core.constants.PRIMARY_COLOR)
		await ctx.send(embed=embed)

	@commands.command(name="specialThanks", hidden=True)
	async def special_thanks(self, ctx: commands.Context):
		peeople_who_helped = [
			"<@205704051856244736>",  # @sprutz
			"<@299478604809764876>",  # @thebadgod
			"<@291291715598286848>",  # @joniiiiii
			"<@231151428167663616>",  # @acurisu
			"<@153929916977643521>",  # @battlerush
		]
		random.shuffle(peeople_who_helped)
		embed = discord.Embed(
			title="Special thanks for any help to those people",
			description=" ".join(peeople_who_helped),
			color=core.constants.PRIMARY_COLOR,
		)

		await ctx.send(embed=embed)
		await ctx.message.delete()

	@commands.command()
	@commands.cooldown(3, 30)
	async def info(self, ctx: commands.Context):
		"""
		Shows different technical information about the bot
		"""
		content = ""
		uptime_in_seconds = (discord.utils.utcnow() - self.bot.start_time).total_seconds()
		bot_uptime = utils.seconds_to_human_readable(uptime_in_seconds)

		commit_hash, commit_date = utils.ux.get_last_commit_info()
		if commit_hash != "unknown" and commit_date != "unknown":
			bot_version = f"{self.bot.version} [{commit_hash}] ({commit_date})"
		elif commit_hash != "unknown":
			bot_version = f"{self.bot.version} [{commit_hash}]"
		else:
			bot_version = f"{self.bot.version} [commit info unavailable]"

		cpu_percent = psutil.cpu_percent()
		ram = psutil.virtual_memory()
		ram_used = utils.bytes_to_human_readable((ram.total - ram.available))
		ram_percent = psutil.virtual_memory().percent
		proc = psutil.Process()

		with proc.oneshot():
			memory = proc.memory_full_info()
			content = (
				f"**Instance uptime:** `{bot_uptime}`\n"
				f"**Version:** `{bot_version}` \n"
				f"**Python:** `{platform.python_version()}`\n"
				f"**discord.py:** `{discord.__version__}`\n\n"
				f"**CPU:** `{cpu_percent}%`\n"
				f"**Process RAM:** `{utils.bytes_to_human_readable(memory.uss)}`\n"
				f"**Total RAM:** `{ram_used} ({ram_percent}%)`\n\n"
				f"**Made by:** <@{self.bot.owner_id}>"
			)

		embed = discord.Embed(
			title=f"Info about {self.bot.user.display_name}", description=content, color=core.constants.PRIMARY_COLOR
		)
		embed.set_thumbnail(url=self.bot.user.display_avatar.url)
		embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar)
		await ctx.send(embed=embed)


async def setup(bot: core.Substiify):
	await bot.add_cog(Util(bot))
