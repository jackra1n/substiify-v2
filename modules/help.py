from typing import Optional, Set

import nextcord
from nextcord import Embed
from nextcord.ext import commands


class Help(commands.MinimalHelpCommand):
    """Shows help info for commands and cogs"""

    def get_command_signature(self, command):
        return f"{self.context.clean_prefix}{command.qualified_name} {command.signature}"

    # help
    async def send_bot_help(self, mapping: dict):
        embed = await self._help_embed(
            title="Bot Commands",
            description=self.context.bot.description,
            mapping=mapping,
            set_author=True,
        )
        self.response = await self.get_destination().send(embed=embed)

    # help <cog>
    async def send_cog_help(self, cog: commands.Cog):
        embed = await self.cog_help_embed(cog)
        await self.get_destination().send(embed=embed)

    # help <group>
    async def send_group_help(self, group):
        embed = self.create_command_help_embed(group)
        sub_commands = [c.name for c in group.commands]
        embed.add_field(name="\u200b", value=f"```asciidoc\n= Subcommands =\n{', '.join(sub_commands)}```")
        if len(command_chain := group.full_parent_name) > 0:
            command_chain = group.full_parent_name + " "
        embed.set_footer(text=f"This command has subcommands. Check their help page with {self.context.clean_prefix}help {command_chain}{group.name} <subcommand>")
        await self.context.send(embed=embed)

    async def cog_help_embed(self, cog: Optional[commands.Cog]) -> Embed:
        if cog is None:
            return await self._help_embed(
                title=f"No category",
                command_set=self.get_bot_mapping()[None]
            )
        emoji = getattr(cog, "COG_EMOJI", None)
        return await self._help_embed(
            title=f"{emoji} {cog.qualified_name}" if emoji else cog.qualified_name,
            description=cog.description,
            command_set=cog.get_commands()
        )

    # help <command>
    async def send_command_help(self, command: commands.Command):
        emoji = getattr(command.cog, "COG_EMOJI", None)
        embed = await self._help_embed(
            title=f"{emoji} {command.qualified_name}" if emoji else command.qualified_name,
            description=command.help,
            command_set=command.commands if isinstance(command, commands.Group) else None
        )
        await self.get_destination().send(embed=embed)

    async def _help_embed(
        self, title: str, description: Optional[str] = None, mapping: Optional[str] = None,
        command_set: Optional[Set[commands.Command]] = None, set_author: bool = False
    ) -> Embed:
        embed = Embed(title=title, color=0x1E9FE3)
        if description:
            embed.description = description
        if set_author:
            avatar = self.context.bot.user.avatar or self.context.bot.user.default_avatar
            embed.set_author(name=self.context.bot.user.name, icon_url=avatar.url)
        if command_set:
            # show help about all commands in the set
            filtered = await self.filter_commands(command_set, sort=True)
            for command in filtered:
                embed.add_field(
                    name=self.get_command_signature(command),
                    value=command.short_doc or "...",
                    inline=False
                )
        elif mapping:
            # add a short description of commands in each cog
            for cog, cmds in sorted(mapping.items(), key=lambda e: len(e[1]), reverse=True):
                cmds = [ c for c in cmds if not c.hidden ]
                if len(cmds) > 0:
                    cmd_list = f"```md\n"
                    for com in sorted(cmds, key=lambda e: e.name):
                        prefix = "*" if await self.can_run_cmd(com) else ">"
                        cmd_list += f"{prefix} {com}\n"
                    cmd_list += "```"

                    name = cog.qualified_name if cog else "No category"
                    emoji = getattr(cog, "COG_EMOJI", None)
                    cog_label = f"{emoji} {name}" if emoji else name
                    embed.add_field(name=cog_label, value=cmd_list)
        return embed

    def create_command_help_embed(self, command):
        command_name = command.name
        # command path
        if len(command.full_parent_name) > 0:
            command_name = command.full_parent_name.replace(" ", " > ") + " > " + command_name

        help_msg = command.help
        if help_msg is None:
            help_msg = "No command information"

        if command.aliases is None or len(command.aliases) == 0:
            aliases_msg = "[n/a]"
        else:
            aliases_msg = ", ".join(command.aliases)

        if command.usage is None:
            usage = "[n/a]"
        else:
            usage = command.usage
            if len(command.full_parent_name) > 0:
                usage = command.full_parent_name + " " + usage
            usage = self.context.clean_prefix + usage

        embed = Embed(title=command_name, color=0x1E9FE3)
        embed.add_field(name="Info", value=help_msg.replace("{prefix}", self.context.clean_prefix), inline=False)
        embed.add_field(name="Aliases", value=f"```asciidoc\n{aliases_msg}```")
        embed.add_field(name="Usage", value=f"```asciidoc\n{usage}```", inline=False)
        return embed

    async def can_run_cmd(self, cmd):
        try:
            return await cmd.can_run(self.context)
        except nextcord.ext.commands.CommandError:
            return False

def setup(bot):
    bot.help_command = Help()
