import logging
import random
from random import shuffle

import discord
from discord.ext import commands

from core.bot import Substiify

logger = logging.getLogger(__name__)


class Fun(commands.Cog):
	COG_EMOJI = "🎱"

	def __init__(self, bot: Substiify):
		self.bot = bot

	@commands.command(name="teams", aliases=["team"])
	async def teams(self, ctx: commands.Context, *, players: str = None):
		"""
		Create two teams from the current members of the voice channel or passed names for you to play custom games.
		"""
		if ctx.author.voice:
			players_list = [member for member in ctx.author.voice.channel.members if not member.bot]
		elif players:
			players_list = players.split(",") if "," in players else players.split(" ")
		else:
			return await ctx.send("You must be in a voice channel or provide a list of players.")

		if len(players_list) < 4:
			return await ctx.send("You must have at least 4 members to use this command.")

		shuffle(players_list)
		team_1 = players_list[: len(players_list) // 2]
		team_2 = players_list[len(players_list) // 2 :]

		embed = discord.Embed(title="Teams", color=0x00FFFF)
		embed.add_field(name="Team 1", value="\n".join([f"{member} " for member in team_1]))
		embed.add_field(name="Team 2", value="\n".join([f"{member} " for member in team_2]))
		if ctx.author.voice and players:
			embed.set_footer(text="Did you know that if you are in a voice channel you can just type `<<teams`?")
		await ctx.send(embed=embed)

	@commands.cooldown(6, 5)
	@commands.command(name="8ball", aliases=["eightball"], usage="8ball <question>")
	async def eightball(self, ctx: commands.Context, *, question: str):
		"""
		AKA 8ball, Ask the bot a question that you dont want the answer to.
		"""
		responses = [
			"It is certain.",
			"It is decidedly so.",
			"Without a doubt.",
			"Yes - definitely.",
			"You may rely on it.",
			"As I see it, yes.",
			"Most likely.",
			"Outlook good.",
			"Yes.",
			"Signs point to yes.",
			"Reply hazy, try again.",
			"Ask again later.",
			"Better not tell you now.",
			"Cannot predict now.",
			"Concentrate and ask again.",
			"Don't count on it.",
			"My reply is no.",
			"My sources say no.",
			"Outlook not so good.",
			"Very doubtful.",
		]
		response = random.choice(responses)
		embed = discord.Embed(title=response, description=f"Question: {question}", colour=discord.Colour.orange())
		embed.set_footer(text=f"Question by {ctx.author}", icon_url=ctx.author.avatar)
		await ctx.send(embed=embed)


async def setup(bot: Substiify):
	await bot.add_cog(Fun(bot))
