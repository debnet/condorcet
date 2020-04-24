# coding: utf-8
import locale
from discord.ext import commands
from base import DISCORD_LOCALE, DISCORD_OPERATOR, DISCORD_TOKEN
from condorcet import Condorcet
from birthday import HappyBirthday
from rolemanager import RoleManager
from economy import Economy


if __name__ == '__main__':
    locale.setlocale(locale.LC_ALL, DISCORD_LOCALE)
    bot = commands.Bot(command_prefix=DISCORD_OPERATOR)
    bot.add_cog(Condorcet(bot))
    bot.add_cog(HappyBirthday(bot))
    bot.add_cog(RoleManager(bot))
    bot.add_cog(Economy(bot))
    bot.run(DISCORD_TOKEN)
