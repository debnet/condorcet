# coding: utf-8
import locale

from discord import Intents
from discord.ext import commands
from base import DISCORD_LOCALE, DISCORD_OPERATOR, DISCORD_TOKEN
from condorcet import Condorcet
from birthday import HappyBirthday
from rolemanager import RoleManager
from economy import Economy
from emulator import Emulator
from geoguessr import Geoguessr


if __name__ == "__main__":
    locale.setlocale(locale.LC_ALL, DISCORD_LOCALE)
    intents = Intents.default()
    intents.message_content = True
    intents.members = True
    intents.presences = True
    bot = commands.Bot(command_prefix=DISCORD_OPERATOR, intents=intents)
    bot.add_cog(Condorcet(bot))
    bot.add_cog(HappyBirthday(bot))
    bot.add_cog(RoleManager(bot))
    bot.add_cog(Economy(bot))
    bot.add_cog(Emulator(bot))
    bot.add_cog(Geoguessr(bot))
    bot.run(DISCORD_TOKEN)
