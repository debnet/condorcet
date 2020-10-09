# coding: utf-8
import discord
import peewee as pw
from datetime import date
from dateutil.parser import parse as parse_date
from discord.ext import commands, tasks
from base import DISCORD_CHANNEL, BaseCog, User, database


class Birthday(pw.Model):
    """
    Birthday
    """
    user = pw.ForeignKeyField(User, primary_key=True)
    birth_date = pw.DateField()
    date_only = pw.BooleanField(default=False)
    last_check = pw.DateField(null=True)

    class Meta:
        database = database


class HappyBirthday(BaseCog):
    """
    Happy birthday bot
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        database.create_tables((Birthday, ))
        self._check_birthday.start()

    def cog_unload(self):
        self._check_birthday.cancel()

    @commands.command(name='birthday')
    async def _birthday(self, ctx, *args):
        """
        Sauvegarde ou supprime votre date de naissance
        Usage : `!birthday [<date>]`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        if args:
            try:
                birth_date = parse_date(args[0], dayfirst=True).date()
            except:  # noqa
                await ctx.author.send(f":warning:  La date de naissance saisie n'est pas valide.")
                return
            today = date.today()
            date_only = birth_date.year == today.year
            birthday, created = Birthday.get_or_create(
                user=user, defaults=dict(birth_date=birth_date, date_only=date_only, last_check=today))
            if not created:
                birthday.birth_date, birthday.date_only, birthday.last_check = birth_date, date_only, today
                birthday.save(only=('birth_date', 'date_only', ))
            birth_date = birth_date.strftime("%d/%m") if date_only else birth_date.strftime("%d/%m/%Y")
            await ctx.author.send(f":white_check_mark:  Votre date de naissance ({birth_date}) a bien été engistrée !")
        else:
            birthday = Birthday.select().where(Birthday.user == user).first()
            if not birthday:
                await ctx.author.send(f"```usage: {ctx.prefix}birthday date```")
                return
            birthday.delete_instance()
            await ctx.author.send(f":white_check_mark:  Votre date de naissance a été supprimée !")

    @tasks.loop(hours=1)
    async def _check_birthday(self):
        """
        Event loop to announce birthdays
        """
        channel = discord.utils.get(self.bot.get_all_channels(), name=DISCORD_CHANNEL)
        if not channel:
            return
        birthdays, today = [], date.today()
        for birthday in Birthday.select().join(User).where(Birthday.last_check < today):
            if (today.day, today.month) != (birthday.birth_date.day, birthday.birth_date.month):
                continue
            if birthday.date_only:
                birthdays.append(f"<@{birthday.user.id}>")
            else:
                age = int((today - birthday.birth_date).days / 365)
                birthdays.append(f"<@{birthday.user.id}> ({age} ans)")
            birthday.last_check = today
            birthday.save(only=('last_check', ))
        if birthdays:
            await channel.send(
                f":birthday:  Nous fêtons **{len(birthdays)}** anniversaire(s) aujourd'hui ! "
                f"Joyeux anniversaire à {','.join(birthdays)} !")
