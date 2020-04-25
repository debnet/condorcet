# coding: utf-8
import argparse
import logging
import os
import re
import peewee as pw
from discord import utils
from discord.ext import commands
from string import ascii_uppercase, digits


# Discord locale
DISCORD_LOCALE = os.environ.get('DISCORD_LOCALE') or 'fr_FR'
# Discord database
DISCORD_DATABASE = os.environ.get('DISCORD_DATABASE') or 'condorcet.db'
# Discord token
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
# Discord operator
DISCORD_OPERATOR = OP = os.environ.get('DISCORD_OPERATOR') or '!'
# Discord administrator role
DISCORD_ADMIN = os.environ.get('DISCORD_ADMIN') or 'Staff'
# Discord default channel
DISCORD_CHANNEL = os.environ.get('DISCORD_CHANNEL') or 'general'


class Parser(argparse.ArgumentParser):
    """
    Custom parser to avoid script to hang when an CLI error occurs
    and keeping the error message in memory for feedback purposes
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.message = ''

    def parse_args(self, args=None, namespace=None):
        result = self.parse_known_args(args, namespace)
        if self.message:
            return
        args, argv = result
        return args

    def print_help(self, file=None):
        if self.message:
            return
        self.message = self.format_help()

    def error(self, message):
        if self.message:
            return
        self.message = self.format_usage() + message

    def exit(self, status=0, message=None):
        pass


# Log handler in CLI with date and level
log_handler = logging.StreamHandler()
log_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)7s: %(message)s'))

# Log SQL queries
pw_logger = logging.getLogger('peewee')
pw_logger.setLevel(logging.DEBUG)
pw_logger.addHandler(log_handler)

# Log application messages
logger = logging.getLogger('bot')
logger.setLevel(logging.DEBUG)
logger.addHandler(log_handler)

# Database handler
database = pw.SqliteDatabase(DISCORD_DATABASE)


class User(pw.Model):
    """
    User
    """
    id = pw.BigIntegerField(primary_key=True)
    name = pw.CharField()

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.name

    class Meta:
        database = database


class BaseCog(commands.Cog):
    """
    Base Discord Cog with utility functions
    """
    # Ranks for icons
    RANKS = ('1', '2', '3', '4', '5', '6', '7', '8', '9', '10')
    # Indices for candidates
    INDICES = ascii_uppercase + digits
    # Indices icons
    ICONS = {
        '0': ':zero:',
        '1': ':one:',
        '2': ':two:',
        '3': ':three:',
        '4': ':four:',
        '5': ':five:',
        '6': ':six:',
        '7': ':seven:',
        '8': ':eight:',
        '9': ':nine:',
        '10': ':keycap_ten:',
    }

    _users = {}

    def __init__(self, bot):
        database.create_tables((User, ))
        self.bot = bot
        self.users = BaseCog._users

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if not after.bot:
            await self.get_user(after)

    async def cog_command_error(self, ctx, error):
        if hasattr(ctx.message.channel, 'name'):
            await ctx.author.send(
                f":warning:  **Erreur :** {error} (`{ctx.message.content}` on `{ctx.message.channel.name}`)")
            logger.error(f"[{ctx.message.channel.name}] {error} ({ctx.message.content})")
        else:
            await ctx.author.send(
                f":warning:  **Erreur :** {error} (`{ctx.message.content}`)")
            logger.error(f"{error} ({ctx.message.content})")
        raise

    async def get_user(self, user):
        """
        Helper function to get database user from a Discord user
        :param user: Discord user
        :return: Database user
        """
        if user is None:
            return None
        if isinstance(user, str):
            # Tries to get user id in mention
            groups = re.match(r'<[@!#]+(\d+)>', user)
            if groups:
                user_id = int(groups[1])
                user = self.bot.get_user(user_id)
            else:
                # Search user from its username or nickname
                user = utils.find(lambda u: user.lower() in (u.nick or u.name).lower(), self.bot.get_all_members())
        if not hasattr(user, 'id'):
            # If not a Discord user
            return None
        # Try to get user from cache
        name = getattr(user, 'nick', None) or user.name
        _user = self.users.get(user.id)
        # Create user if not exists
        if not _user:
            _user, created = User.get_or_create(id=user.id, defaults=dict(name=name))
        # Update user name if changed on Discord
        if name != _user.name:
            _user.name = name
            _user.save(only=('name', ))
        # Keep Discord user
        _user.user = user
        # Cache user
        self.users[_user.id] = _user
        return _user

    def get_icon(self, indice):
        """
        Get Discord icon for indice
        :param indice: Indice
        :return: Icon
        """
        if not indice:
            return '> '
        indice = str(indice)
        return self.ICONS.get(indice, f':regional_indicator_{indice.lower()}:')
