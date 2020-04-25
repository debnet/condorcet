# coding: utf-8
import argparse
import os
from discord.ext import commands
from base import BaseCog, Parser


# Discord roles allowed to be granted to users
DISCORD_ROLES = os.environ.get('DISCORD_ROLES')


class RoleManager(BaseCog):
    """
    Role manager bot
    """

    @commands.command(name='roles')
    @commands.guild_only()
    async def _roles(self, ctx, *args):
        """
        Permet de s'attribuer un ou plusieurs rôles.
        Usage : `!roles <role> [<role> ...]`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Get roles
        list_roles = [r.split("=") for r in DISCORD_ROLES.split(",")] if DISCORD_ROLES else []
        help_roles = ",\n".join(f"- {rolename} ({shortcut})" for (shortcut, rolename) in list_roles)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de s'attribuer un ou plusieurs rôles.",
            epilog=f"Rôles disponibles :\n{help_roles}",
            formatter_class=argparse.RawTextHelpFormatter)
        parser.add_argument('roles', metavar='role', type=str, nargs='*', help="Rôle")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Collect all allowed roles
        roles = {}
        for role in ctx.guild.roles:
            for shortcut, rolename in list_roles:
                if role.name.lower() == rolename.lower():
                    roles[shortcut] = role
        # Collect selected roles
        new_roles = []
        selected_roles = map(str.lower, args.roles)
        for shortcut, role in roles.items():
            if shortcut.lower() in selected_roles:
                new_roles.append(role)
            elif role.name.lower() in selected_roles:
                new_roles.append(role)
        if not new_roles:
            help_roles = ", ".join(f"**{rolename}** ({shortcut})" for (shortcut, rolename) in list_roles)
            await ctx.author.send(f":warning:  Vous devez sélectionner un ou plusieurs rôles parmi : {help_roles}")
            return
        # Clear roles
        old_roles = list(roles.values())
        await ctx.author.remove_roles(*old_roles)
        # Add roles
        await ctx.author.add_roles(*new_roles)
        role_names = ', '.join(role.name for role in new_roles)
        await ctx.author.send(f":scroll:  Vous avez désormais accès aux rôles suivants : **{role_names}** !")
