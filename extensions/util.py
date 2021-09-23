from hikari.colors import Color
from lightbulb import slash_commands
from hikari import embeds, User

import typing
import logging

_LOGGER = logging.getLogger(__name__)

class Avatar(slash_commands.SlashCommand):
    description = "Shows bigger picture of an avatar of a user"
    # Options
    enabled_guilds: typing.Optional[typing.Iterable[int]] = (742698112081985588,)
    user: typing.Optional[User] = slash_commands.Option("User")

    async def callback(self, context):
        user = context.author
        if context.options["user"]:
            user = context.options["user"].value
        embed = embeds.Embed(
            title=str(user.display_name),
            description="Avatar",
            colour=Color(0x00BBFF),
        )
        embed.set_image(user.avatar_url)
        await context.respond(embed=embed)

def load(bot):
    bot.add_slash_command(Avatar)


def unload(bot):
    bot.remove_slash_command(Avatar)
