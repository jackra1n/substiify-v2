from hikari.colors import Color
from lightbulb import slash_commands
from hikari import embeds, User

import typing
import logging

_LOGGER = logging.getLogger(__name__)


class Avatar(slash_commands.SlashCommand):
    description = "Shows bigger picture of an avatar of a user"
    # Options
    user: typing.Optional[User] = slash_commands.Option("User")

    async def callback(self, ctx):
        user: User = ctx.member
        if ctx.option_values.user:
            user = ctx.get_guild().get_member(int(ctx.option_values.user))
        embed = embeds.Embed(
            title=str(user.display_name),
            description="Avatar",
            colour=Color(0x00BBFF),
        )
        embed.set_image(user.avatar_url)
        await ctx.respond(embed=embed)


def load(bot):
    bot.add_slash_command(Avatar)


def unload(bot):
    bot.remove_slash_command(Avatar)
