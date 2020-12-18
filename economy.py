# coding: utf-8
import asyncio
import discord
import os
import peewee as pw
from datetime import date, datetime, timedelta
from discord.ext import commands, tasks
from discord.utils import escape_mentions
from random import choice, randint, sample, seed
from base import DISCORD_ADMIN, BaseCog, Parser, User, database


# Discord economy constants
DISCORD_MONEY_SYMBOL = os.environ.get('DISCORD_MONEY_SYMBOL') or '$'
DISCORD_MONEY_NAME = os.environ.get('DISCORD_MONEY_NAME') or 'dollar'
DISCORD_MONEY_START = float(os.environ.get('DISCORD_MONEY_START') or 5.0)
DISCORD_MONEY_MULT = float(os.environ.get('DISCORD_MONEY_MULT') or 0.001)
DISCORD_MONEY_MINI = float(os.environ.get('DISCORD_MONEY_MINI') or 0.1)
DISCORD_MONEY_RATE = float(os.environ.get('DISCORD_MONEY_RATE') or 0.99)
DISCORD_MONEY_WAGE = float(os.environ.get('DISCORD_MONEY_WAGE') or 0.1)
DISCORD_MONEY_LIMIT = float(os.environ.get('DISCORD_MONEY_LIMIT') or 1000)
DISCORD_MONEY_CREATE = float(os.environ.get('DISCORD_MONEY_CREATE') or 10.0)
DISCORD_LOTO_CHANNEL = os.environ.get('DISCORD_LOTO_CHANNEL') or 'loto'
DISCORD_LOTO_PRICE = float(os.environ.get('DISCORD_LOTO_PRICE') or 1.0)
DISCORD_LOTO_LIMIT = float(os.environ.get('DISCORD_LOTO_LIMIT') or 1000.0)
DISCORD_LOTO_COUNT = int(os.environ.get('DISCORD_LOTO_COUNT') or 5)
DISCORD_LOTO_START = float(os.environ.get('DISCORD_LOTO_START') or 100.0)
DISCORD_LOTO_EXTRA = float(os.environ.get('DISCORD_LOTO_EXTRA') or 10.0)


class Currency(pw.Model):
    """
    Currency
    """
    symbol = pw.CharField(unique=True)
    name = pw.CharField(null=True)
    user = pw.ForeignKeyField(User, null=True)
    value = pw.FloatField(default=0.0)
    rate = pw.FloatField(default=1.0)

    class Meta:
        database = database


class Balance(pw.Model):
    """
    Account
    """
    user = pw.ForeignKeyField(User)
    currency = pw.ForeignKeyField(Currency)
    value = pw.FloatField(default=0.0)
    date = pw.DateTimeField(default=datetime.now)

    class Meta:
        database = database
        indexes = (
            (('user', 'currency'), True),
        )


class LotoDraw(pw.Model):
    """
    Loto Draw
    """
    date = pw.DateField(default=date.today, unique=True)
    draw = pw.CharField(null=True)
    value = pw.FloatField(default=0.0)

    class Meta:
        database = database


class LotoGrid(pw.Model):
    """
    Loto Grid
    """
    user = pw.ForeignKeyField(User)
    date = pw.DateField(default=date.today)
    draw = pw.CharField()
    rank = pw.IntegerField(null=True)
    gain = pw.FloatField(null=True)

    class Meta:
        database = database


class Economy(BaseCog):
    """
    Economy system bot
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        database.create_tables((Currency, Balance, LotoDraw, LotoGrid))
        Currency.get_or_create(symbol=DISCORD_MONEY_SYMBOL, name=DISCORD_MONEY_NAME)
        LotoDraw.get_or_create(defaults=dict(value=DISCORD_LOTO_START))
        self.currencies = {}
        self.balances = {}
        self.messages = {}
        self.seeds = []
        self._pay_wage.start()
        self._rate_money.start()
        self._draw_loto.start()

    def cog_unload(self):
        self._pay_wage.cancel()
        self._rate_money.cancel()
        self._draw_loto.cancel()

    @commands.Cog.listener()
    async def on_message(self, message):
        """
        Give a small amount of money at each message
        """
        if message.author.bot or not message.guild:
            return
        user = await self.get_user(message.author)
        value = round(len(escape_mentions(message.content).split()) * DISCORD_MONEY_MULT, 5)
        if value <= 0.0:
            return
        symbol, name = DISCORD_MONEY_SYMBOL, DISCORD_MONEY_NAME
        currency = self.get_currency(symbol, create=True, name=name)
        balance = self.get_balance(user, currency)
        balance.value += value
        Balance.update(value=Balance.value + value).where(Balance.id == balance.id).execute()

    @commands.command(name='give')
    async def _give(self, ctx, *args):
        """
        Permet de donner de l'argent à un autre utilisateur.
        Usage : `!give <montant> <symbole> <utilisateur>`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de donner de l'argent à un autre utilisateur.")
        parser.add_argument('amount', type=int, help="Quantité d'argent")
        parser.add_argument('symbol', type=str, help="Symbole de la devise")
        parser.add_argument('user', type=str, help="Utilisateur")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        if not args.amount > 0:
            await ctx.author.send(f":no_entry:  La quantité ne peut être négative ou nulle.")
            return
        if args.amount > DISCORD_MONEY_LIMIT:
            await ctx.author.send(
                f":no_entry:  Il n'est pas possible d'échanger plus de "
                f"**{round(DISCORD_MONEY_LIMIT,2):n} unités** d'une même devise en une seule fois.")
            return
        # Check currency
        currency = self.get_currency(args.symbol)
        if not currency:
            await ctx.author.send(f":no_entry:  La devise sélectionnée n'existe pas.")
            return
        # Check target
        target = await self.get_user(args.user)
        if not target or target.user.bot or target == user or target == currency.user:
            await ctx.author.send(f":no_entry:  Le destinataire n'est pas valide.")
            return
        # Check balance
        if currency.user != user:
            source = self.get_balance(user, currency)
            if source.value < args.amount:
                await ctx.author.send(
                    f":no_entry:  Vous n'avez pas assez d'argent sur votre compte : vous avez actuellement "
                    f"**{round(source.value, 2):n} {currency.symbol}** "
                    f"et il vous faut **{round(args.amount, 2):n} {currency.symbol}**.")
                return
            source.value -= args.amount
            Balance.update(value=Balance.value - args.amount).where(Balance.id == source.id).execute()
        # Decrease currency rate
        if currency.user:
            currency.rate = max(DISCORD_MONEY_MINI, currency.rate * DISCORD_MONEY_RATE)
            Currency.update(rate=currency.rate).where(Currency.id == currency.id).execute()
        # Give money
        balance = self.get_balance(target, currency)
        balance.value += args.amount
        Balance.update(value=Balance.value + args.amount).where(Balance.id == balance.id).execute()
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.channel.send(
                f":moneybag:  <@{user.id}> a donné **{args.amount:n} {currency.symbol}** "
                f"({currency.name}) à <@{target.id}> !")
        else:
            await ctx.author.send(
                f":moneybag:  Vous avez donné **{args.amount:n} {currency.symbol}** "
                f"({currency.name}) à **{target.name}** !")
            await target.user.send(
                f":moneybag:  **{user.name}** vous a donné **{args.amount:n} "
                f"{currency.symbol}** ({currency.name}) !")

    @commands.command(name='store')
    async def _store(self, ctx, *args):
        """
        Permet d'alimenter une devise pour augmenter sa valeur.
        Usage : `!store <symbole> <montant>`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet d'alimenter une devise pour augmenter sa valeur.")
        parser.add_argument('symbol', type=str, help="Symbole de la devise")
        parser.add_argument('amount', type=float, help="Quantité d'argent")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check positive
        if not args.amount > 0:
            await ctx.author.send(f":no_entry:  La quantité ne peut être négative ou nulle.")
            return
        # Check currency
        currency = self.get_currency(args.symbol)
        if not currency:
            await ctx.author.send(f":no_entry:  La devise sélectionnée n'existe pas.")
            return
        # Check ownership
        if not currency.user:
            await ctx.author.send(f":no_entry:  Il n'est pas possible d'alimenter cette devise.")
            return
        # Check balance
        base = self.get_currency(DISCORD_MONEY_SYMBOL)
        balance = self.get_balance(user, base)
        if balance.value < args.amount:
            await ctx.author.send(
                f":no_entry:  Vous n'avez pas assez d'argent sur votre compte : vous avez actuellement "
                f"**{round(balance.value,2):n} {currency.symbol}** "
                f"et il vous faut **{round(args.amount,2):n} {currency.symbol}**.")
            return
        # Transfert money
        balance.value -= args.amount
        Balance.update(value=Balance.value - args.amount).where(Balance.id == balance.id).execute()
        currency.value += args.amount
        Currency.update(value=Currency.value + args.amount).where(Currency.id == currency.id).execute()
        await ctx.author.send(
            f":white_check_mark:  Vous avez transféré **{args.amount:n} {base.symbol}** ({base.name}) sur la devise "
            f"**{currency.name}** ({currency.symbol}) ! Valeur totale : **{round(currency.value,2):n} {base.symbol}**.")

    @commands.command(name='create')
    async def _create(self, ctx, *args):
        """
        Permet de créer une nouvelle devise.
        Usage : `!create <symbole> "<nom>" [<montant>]`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de créer une nouvelle devise.")
        parser.add_argument('symbol', type=str, help="Symbole de la devise")
        parser.add_argument('name', type=str, help="Nom de la devise")
        parser.add_argument('amount', type=int, nargs='?', help="Investissement initial")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Update balance
        if args.amount and args.amount < DISCORD_MONEY_CREATE:
            await ctx.author.send(
                f":no_entry:  Vous devez investir au minimum **{DISCORD_MONEY_CREATE:n} {DISCORD_MONEY_SYMBOL}** "
                f"lors de la création de votre nouvelle devise.")
            return
        value = args.amount or DISCORD_MONEY_CREATE
        # Check balance
        base_currency = self.get_currency(DISCORD_MONEY_SYMBOL)
        balance = self.get_balance(user, base_currency)
        if balance.value < value:
            await ctx.author.send(
                f":no_entry:  Vous n'avez pas assez d'argent sur votre compte : vous avez actuellement "
                f"**{round(balance.value,2):n} {base_currency.symbol}** "
                f"et il vous faut **{round(value,2):n} {base_currency.symbol}**.")
            return
        balance.value -= value
        Balance.update(value=Balance.value - value).where(Balance.id == balance.id).execute()
        # Try create currency
        currency = self.get_currency(args.symbol, create=True, name=args.name, user=user, value=value)
        if currency.user != user:
            await ctx.author.send(f":no_entry:  Cette devise ne vous appartient pas.")
            return
        await ctx.author.send(
            f":white_check_mark:  Votre nouvelle devise **{args.name}** ({args.symbol}) a été créée avec succès !\n"
            f"Vous pouvez désormais en distribuer autant que vous le voulez avec `{ctx.prefix}give`, lui donner de la "
            f"valeur en l'approvisionnant avec `{ctx.prefix}store` et consulter son cours avec `{ctx.prefix}rate`.")

    @commands.command(name='rename')
    async def _rename(self, ctx, *args):
        """
        Permet de renommer une devise existante.
        Usage : `!rename <symbole> "<nom>"`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de créer une nouvelle devise.")
        parser.add_argument('symbol', type=str, help="Symbole de la devise")
        parser.add_argument('name', type=str, help="Nom de la devise")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check currency
        currency = self.get_currency(args.symbol)
        if currency.user != user:
            await ctx.author.send(f":no_entry:  Cette devise ne vous appartient pas.")
            return
        # Change name if needed
        if currency.name != args.name:
            currency.name = args.name
            currency.save(only=('name', ))
        await ctx.author.send(
            f":white_check_mark:  Vous avez renommé votre devise **{currency.name}** ({currency.symbol}) avec succès !")

    @commands.command(name='delete')
    async def _delete(self, ctx, *args):
        """
        Permet de supprimer une devise créée.
        Usage : `!delete <symbole>`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de supprimer une devise créée.",
            epilog="Attention ! La suppression d'une devise est définitive "
                   "et ses investissements ne seront pas remboursés.")
        parser.add_argument('symbol', type=str, help="Symbole de la devise")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check currency
        currency = self.get_currency(args.symbol)
        if not currency:
            await ctx.author.send(f":no_entry:  La devise sélectionnée n'existe pas.")
            return
        if currency.user != user:
            await ctx.author.send(f":no_entry:  Cette devise ne vous appartient pas.")
            return
        # Delete balances and currency
        Balance.delete().where(Balance.currency == currency).execute()
        currency.delete_instance()
        # Empty caches
        self.currencies.clear()
        self.balances.clear()
        await ctx.author.send(
            f":white_check_mark:  La devise **{currency.name}** ({currency.symbol}) a été supprimée avec succès !")

    @commands.command(name='rate')
    async def _rate(self, ctx, *args):
        """
        Permet de consulter le taux d'une devise.
        Usage : `!rate <symbole>`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de consulter le taux d'une devise.")
        parser.add_argument('symbol', type=str, help="Symbole de la devise")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check currency
        currency = self.get_currency(args.symbol)
        if not currency:
            await ctx.author.send(f":no_entry:  La devise sélectionnée n'existe pas.")
            return
        # Get currency infos
        base = self.get_currency(DISCORD_MONEY_SYMBOL)
        total = Balance.select(pw.fn.SUM(Balance.value)).where(Balance.currency == currency).scalar() or 0.0
        rate = round(currency.value * currency.rate / (total or 1), 5)
        # Display infos
        messages = [
            f"**{currency.name}** ({currency.symbol}), créée par **{currency.user.name}**" if currency.user else
            f"**{currency.name}** ({currency.symbol}), devise de base générale",
            f"Nombre en circulation : **{round(total,2):n}**"]
        if currency != base:
            messages.extend([
                f"Taux actuel : **{round(currency.rate, 2):.0%}**",
                f"Valeur totale : **{round(currency.value,2):n} {base.symbol}**",
                f"Valeur individuelle : **{round(rate,2):n} {base.symbol}**"])
        messages.append(f"Classement des 10 plus grosses fortunes en **{currency.name}** :")
        balances = Balance.select(Balance, User).join(User).where(
            Balance.currency == currency, Balance.value > 0.001
        ).order_by(Balance.value.desc()).limit(10)
        for indice, balance in zip(self.RANKS, balances):
            indice = self.get_icon(indice)
            if currency == base:
                messages.append(f"{indice}  {balance.user.name} : **{round(balance.value,2):n} {currency.symbol}**")
            else:
                messages.append(
                    f"{indice}  {balance.user.name} : **{round(balance.value,2):n} {currency.symbol}** "
                    f"soit **~{round(balance.value * rate,2):n} {base.symbol}**")
        await ctx.author.send("\n".join(messages))

    @commands.command(name='money')
    async def _money(self, ctx, *args):
        """
        Permet de consulter votre compte en banque.
        Usage : `!money [<utilisateur>]`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de consulter votre compte en banque.")
        parser.add_argument('user', type=str, nargs='?', help="Utilisateur")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check user
        target = await self.get_user(args.user)
        if args.user and not target:
            await ctx.author.send(f":no_entry:  L'utilisateur ciblé n'existe pas.")
            return
        # Display infos
        if target:
            messages = [f"**{target.name}** a actuellement :"]
        else:
            messages = ["Vous avez actuellement :"]
            target = user
        balances = Balance.select(Balance, Currency).join(Currency).where(
            Balance.user == target, Balance.value > 0.001
        ).order_by(pw.fn.Lower(Currency.name))
        for balance in balances:
            messages.append(f"> **{round(balance.value,2):n} {balance.currency.symbol}** ({balance.currency.name})")
        chunks, remaining = [], 2000
        for message in messages:
            length = len(message) + 1
            if length > remaining:
                await ctx.author.send("\n".join(chunks))
                chunks, remaining = [], 2000
            chunks.append(message)
            remaining -= length
        if chunks:
            await ctx.author.send("\n".join(chunks))

    @commands.command(name='market')
    async def _market(self, ctx, *args):
        """
        Permet de consulter l'ensemble des devises existantes.
        Usage : `!market [<utilisateur>]`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de consulter l'ensemble des devises existantes.")
        parser.add_argument('user', type=str, nargs='?', help="Utilisateur")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check user
        target = await self.get_user(args.user)
        if args.user and not target:
            await ctx.author.send(f":no_entry:  L'utilisateur ciblé n'existe pas.")
            return
        # Display infos
        base = self.get_currency(DISCORD_MONEY_SYMBOL)
        messages = [
            f"Voici les devises créées par **{target.name}** :"
            if target else "Voici toutes les devises existantes :"]
        currencies = (
            Currency.select(Currency, User, pw.fn.SUM(Balance.value).alias('total')).join(User, pw.JOIN.LEFT_OUTER)
        ).switch(Currency).join(Balance, pw.JOIN.LEFT_OUTER).group_by(Currency).order_by(pw.fn.Lower(Currency.name))
        if target:
            currencies = currencies.where(Currency.user == target)
        for currency in currencies:
            total = currency.total or 0
            value = (currency.value * currency.rate) / (total or 1)
            if currency.user:
                if target:
                    messages.append(
                        f"> **{currency.name}** ({currency.symbol}) avec "
                        f"**{round(total, 2):n}** unités en circulation d'une valeur de "
                        f"**{round(value, 2):n} {base.symbol}** (taux: {round(currency.rate,2):.0%})")
                else:
                    messages.append(
                        f"> **{currency.name}** ({currency.symbol}) créée par **{currency.user.name}** avec "
                        f"**{round(total,2):n}** unités en circulation d'une valeur de "
                        f"**{round(value,2):n} {base.symbol}** (taux: {round(currency.rate,2):.0%})")
            else:
                messages.append(
                    f"> **{currency.name}** ({currency.symbol}) devise principale avec "
                    f"**{round(total, 2):n}** unités en circulation")
        chunks, remaining = [], 2000
        for message in messages:
            length = len(message) + 1
            if length > remaining:
                await ctx.author.send("\n".join(chunks))
                chunks, remaining = [], 2000
            chunks.append(message)
            remaining -= length
        if chunks:
            await ctx.author.send("\n".join(chunks))

    @commands.command(name='sell')
    async def _sell(self, ctx, *args):
        """
        Permet de vendre une autre devise sur le marché global.
        Usage : `!sell <montant> <symbole>`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de vendre une autre devise sur le marché global.")
        parser.add_argument('amount', type=int, help="Quantité d'argent")
        parser.add_argument('symbol', type=str, help="Symbole de la devise")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check positive
        if not args.amount > 0:
            await ctx.author.send(f":no_entry:  La quantité ne peut être négative ou nulle.")
            return
        # Check currency
        base_currency = self.get_currency(DISCORD_MONEY_SYMBOL)
        currency = self.get_currency(args.symbol)
        if not currency:
            await ctx.author.send(f":no_entry:  La devise sélectionnée n'existe pas.")
            return
        if currency == base_currency:
            await ctx.author.send(f":no_entry:  La devise principale (**{base_currency.name}**) ne peut être vendue.")
            return
        # Check balance
        balance = self.get_balance(user, currency)
        if balance.value < args.amount:
            await ctx.author.send(
                f":no_entry:  Vous n'avez pas assez d'argent sur votre compte : vous avez actuellement "
                f"**{round(balance.value,2):n} {currency.symbol}** "
                f"et il vous faut **{round(args.amount,2):n} {currency.symbol}**.")
            return
        # Get currency rate
        total = Balance.select(pw.fn.SUM(Balance.value)).where(Balance.currency == currency).scalar() or 0.0
        value = round(args.amount * (currency.value * currency.rate / (total or 1)), 5)
        rate = round(args.amount / (total - args.amount), 2) if total - args.amount else 0.0
        rate = max(0.0, min(rate, 2.0 - currency.rate))
        # Update balance
        balance.value -= args.amount
        Balance.update(value=Balance.value - args.amount).where(Balance.id == balance.id).execute()
        base_balance = self.get_balance(user, base_currency)
        base_balance.value += value
        Balance.update(value=Balance.value + value).where(Balance.id == base_balance.id).execute()
        # Update currency
        currency.value -= value
        currency.rate += rate
        Currency.update(
            value=Currency.value - value,
            rate=Currency.rate + rate
        ).where(Currency.id == currency.id).execute()
        # Message to user
        await ctx.author.send(
            f":moneybag:  Vous avez vendu **{args.amount:n} {currency.symbol}** ({currency.name}) "
            f"pour une valeur de **{round(value,2):n} {base_currency.symbol}** ({base_currency.name}) !")

    @commands.command(name='buy')
    async def _buy(self, ctx, *args):
        """
        Permet d'acheter une quantité d'une devise quelconque au taux actuel
        Usage : `!buy <nombre> <symbol>`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Joue une quantité d'argent à la machine à sous.")
        parser.add_argument('amount', type=int, help="Quantité d'argent")
        parser.add_argument('symbol', type=str, help="Symbole de la devise")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check positive
        if not args.amount > 0:
            await ctx.author.send(f":no_entry:  La quantité ne peut être négative ou nulle.")
            return
        # Check currency
        base_currency = self.get_currency(DISCORD_MONEY_SYMBOL)
        currency = self.get_currency(args.symbol)
        if not currency:
            await ctx.author.send(f":no_entry:  La devise sélectionnée n'existe pas.")
            return
        if currency.user == user:
            await ctx.author.send(f":no_entry:  Cette devise vous appartient, vous ne pouvez pas en acheter.")
            return
        if not currency.user:
            await ctx.author.send(f":no_entry:  La devise principale (**{base_currency.name}**) ne peut être achetée.")
        # Get currency rate
        total = Balance.select(pw.fn.SUM(Balance.value)).where(Balance.currency == currency).scalar() or 0.0
        value = round(args.amount * (currency.value * currency.rate / (total or 1)), 5)
        rate = round(args.amount / (total + args.amount), 2) if total + args.amount else 0.0
        rate = max(0.0, min(rate, currency.rate))
        # Check balance
        base_balance = self.get_balance(user, base_currency)
        if base_balance.value < value:
            await ctx.author.send(
                f":no_entry:  Vous n'avez pas assez d'argent sur votre compte : vous avez actuellement "
                f"**{round(base_balance.value,2):n} {currency.symbol}** "
                f"et il vous faut **{round(value,2):n} {currency.symbol}**.")
            return
        # Update balance
        base_balance.value -= value
        Balance.update(value=Balance.value - value).where(Balance.id == base_balance.id).execute()
        balance = self.get_balance(user, currency)
        balance.value += args.amount
        Balance.update(value=Balance.value + args.amount).where(Balance.id == balance.id).execute()
        # Update currency
        currency.value += value
        currency.rate -= rate
        Currency.update(
            value=Currency.value + value,
            rate=Currency.rate - rate,
        ).where(Currency.id == currency.id).execute()
        # Message to user
        await ctx.author.send(
            f":moneybag:  Vous avez acheté **{args.amount:n} {currency.symbol}** ({currency.name}) "
            f"pour une valeur de **{round(value,2):n} {base_currency.symbol}** ({base_currency.name}) !")

    @commands.command(name='slot')
    async def _slot(self, ctx, *args):
        """
        Joue une quantité d'argent à la machine à sous.
        Usage : `!slot <montant> [<symbole>]`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Joue une quantité d'argent à la machine à sous.")
        parser.add_argument('amount', type=int, help="Quantité d'argent")
        parser.add_argument('symbol', type=str, nargs='?', help="Symbole de la devise")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check positive
        if not args.amount > 0:
            await ctx.author.send(f":no_entry:  La quantité ne peut être négative ou nulle.")
            return
        # Check currency
        currency = self.get_currency(args.symbol or DISCORD_MONEY_SYMBOL)
        if not currency:
            await ctx.author.send(f":no_entry:  La devise sélectionnée n'existe pas.")
            return
        if currency.user == user:
            await ctx.author.send(f":no_entry:  Cette devise vous appartient, vous ne pouvez pas la jouer.")
            return
        # Check balance
        balance = self.get_balance(user, currency)
        if balance.value < args.amount:
            await ctx.author.send(
                f":no_entry:  Vous n'avez pas assez d'argent sur votre compte : vous avez actuellement "
                f"**{round(balance.value,2):n} {currency.symbol}** "
                f"et il vous faut **{round(args.amount,2):n} {currency.symbol}**.")
            return
        # Place the bet
        balance.value -= args.amount
        Balance.update(value=Balance.value - args.amount).where(Balance.id == balance.id).execute()
        # Play the slots
        slots = {
            1: ':apple:',
            2: ':tangerine:',
            3: ':lemon:',
            4: ':four_leaf_clover:',
            5: ':bell:',
            6: ':gem:'}
        multipliers = {
            (1, 1, 1): 2.0,
            (2, 2, 2): 3.0,
            (3, 3, 3): 4.0,
            (4, 4, 4): 5.0,
            (5, 5, 5): 10.0,
            (6, 6, 6): 15.0}
        values = list(slots.keys())
        seed(self.seeds.pop(0) if self.seeds else None)
        results = choice(values), choice(values), choice(values)
        result = args.amount * multipliers.get(results, 1.0 if len(set(results)) < len(results) else 0.0)
        if result:
            balance.value += result
            Balance.update(value=Balance.value + result).where(Balance.id == balance.id).execute()
        # Add loss to loto
        if not result:
            value = args.amount
            if currency.symbol != DISCORD_MONEY_SYMBOL:
                total = Balance.select(pw.fn.SUM(Balance.value)).where(Balance.currency == currency).scalar() or 0.0
                value = round(args.amount * (currency.value * currency.rate / (total or 1)), 5)
                # Reduce value of currency
                subvalue = args.amount * (currency.value / (total or 1))
                currency.value -= subvalue
                Currency.update(value=Currency.value - subvalue).where(Currency.id == currency.id).execute()
            LotoDraw.update(value=LotoDraw.value + value).where(LotoDraw.date == date.today()).execute()
        # Create display message
        slot1, slot2, slot3 = sorted(results, reverse=True)
        messages = ["C'est parti !", f"{slots[slot1]}", f"{slots[slot2]}", f"{slots[slot3]}"]
        if ctx.channel and hasattr(ctx.channel, 'name'):
            endpoint = ctx.channel
            if result > args.amount:
                messages.append(f"<@{user.id}> a remporté **{round(result,2):n} {currency.symbol}** ! :smile:")
            elif result:
                messages.append(f"<@{user.id}> a récupéré sa mise de **{round(result,2):n} {currency.symbol}**. :slight_smile:")
            else:
                messages.append(f"<@{user.id}> a perdu **{round(args.amount,2):n} {currency.symbol}** ! :frowning:")
        else:
            endpoint = ctx.author
            if result > args.amount:
                messages.append(f"Vous remportez **{round(result,2):n} {currency.symbol}** ! :smile:")
            elif result:
                messages.append(f"Vous récupérez votre mise de **{round(result,2):n} {currency.symbol}** ! :slight_smile:")
            else:
                messages.append(f"Vous perdez **{round(args.amount,2):n} {currency.symbol}** ! :frowning:")
        # Display slot machine
        message = await endpoint.send(messages[0])
        for i in range(1, len(results) + 1):
            for value in sample(values, len(values)):
                content = '  '.join(messages[:i] + [slots[value]])
                await message.edit(content=content)
                await asyncio.sleep(0.5)
        content = '  '.join(messages)
        await message.edit(content=content)

    @commands.command(name='price')
    async def _price(self, ctx, *args):
        """
        Permet de connaître le montant d'une grille de loto et de sa cagnotte actuelle.
        Usage : `!price`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de connaître le montant d'une grille de loto et de sa cagnotte actuelle.")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Calculate price
        loto = LotoDraw.get_or_none(LotoDraw.date == date.today())
        currency = self.get_currency(DISCORD_MONEY_SYMBOL)
        price = round(DISCORD_LOTO_PRICE + round(loto.value / DISCORD_LOTO_LIMIT, 1), 1)
        await ctx.author.send(
            f":game_die:  Une grille de loto coûte **~{round(price,2):n} {currency.symbol}**.\n"
            f":money_bag:  Le montant de la cagnotte est estimé à **~{round(loto.value,2):n} {currency.symbol}**.")

    @commands.command(name='loto')
    async def _loto(self, ctx, *args):
        """
        Permet d'enregistrer une participation au tirage du loto du jour.
        Usage : `!loto <nombre> <nombre> <nombre> <nombre> <nombre>`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet d'enregistrer une participation au tirage du loto du jour.")
        parser.add_argument(
            'numbers', metavar='number', type=int, nargs=DISCORD_LOTO_COUNT, help=f"Numéros du tirage (entre 1 et 49)")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Check numbers
        numbers = sorted(set(args.numbers))
        if len(numbers) != DISCORD_LOTO_COUNT or not all(1 <= n <= 49 for n in numbers):
            await ctx.author.send(
                f":no_entry:  Vous devez sélectionner **{DISCORD_LOTO_COUNT} numéros distincts** "
                f"ayant une valeur comprise **entre 1 et 49**.")
            return
        # Check loto
        loto = LotoDraw.get_or_none(LotoDraw.date == date.today())
        if not loto:
            await ctx.author.send(
                f":no_entry:  Il n'y a pas encore de tirage LOTO prévu pour aujourd'hui. "
                f"Veuillez patienter jusqu'à ce que le tirage de la veille soit réalisé.")
            return
        # Check balance
        currency = self.get_currency(DISCORD_MONEY_SYMBOL)
        balance = self.get_balance(user, currency)
        price = round(DISCORD_LOTO_PRICE + round(loto.value / DISCORD_LOTO_LIMIT, 1), 1)
        if balance.value < price:
            await ctx.author.send(
                f":no_entry:  Vous n'avez pas assez d'argent sur votre compte : une grille coûte "
                f"**{round(price,2):n} {currency.symbol}** et vous n'avez actuellement que "
                f"**{round(balance.value,2):n} {currency.symbol}**).")
            return
        # Pay and create grid
        balance.value -= price
        Balance.update(value=Balance.value - price).where(Balance.id == balance.id).execute()
        grid = LotoGrid.create(user=user, draw=' '.join(map(str, numbers)))
        # Display information
        draw = ' - '.join(f"{d:02}" for d in numbers)
        for i in range(10):
            draw = draw.replace(str(i), self.get_icon(str(i)))
        await ctx.author.send(
            f":white_check_mark:  Vous avez acheté avec succès une grille pour le tirage du "
            f"**{grid.date:%A %d %B %Y}** avec les numéros suivants : **{draw}**")

    def get_currency(self, symbol, create=False, name='', value=0.0, user=None):
        """
        Get currency from its symbol (create if not exists)
        """
        if symbol not in self.currencies:
            currency = Currency.select(Currency, User).join(User, pw.JOIN.LEFT_OUTER).where(
                Currency.symbol == symbol).first()
            if not currency:
                if create:
                    self.currencies[symbol] = currency = Currency.create(
                        symbol=symbol, name=name, value=value, user=user)
                    return currency
                else:
                    return None
            self.currencies[symbol] = currency
        return self.currencies.get(symbol)

    def get_balance(self, user, currency):
        """
        Get balance for a user and a currency
        """
        if (user.id, currency.symbol) not in self.balances:
            self.balances[user.id, currency.symbol], created = Balance.get_or_create(
                user=user, currency=currency, defaults=dict(
                    value=DISCORD_MONEY_START if currency.symbol == DISCORD_MONEY_SYMBOL else 0.0))
        return self.balances.get((user.id, currency.symbol))

    @commands.command(name='seed')
    @commands.has_role(DISCORD_ADMIN)
    async def _seed(self, ctx, *args):
        """
        Modifie la graine du générateur de nombres pseudo-aléatoire (admin uniquement).
        Usage : `!seed [<nombre>]`
        """
        if ctx.channel and hasattr(ctx.channel, 'name'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de modifier la graine du générateur de nombres pseudo-aléatoire.")
        parser.add_argument('seed', type=int, nargs='?', help="Seed")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        if args.seed:
            self.seeds.append(args.seed)
        else:
            self.seeds.clear()
        seeds = ', '.join(map(str, self.seeds))
        await ctx.author.send(f":game_die:  Graine(s) configurée(s) : **{seeds or 'aucune'}**")

    @commands.command(name='draw')
    @commands.has_role(DISCORD_ADMIN)
    async def _draw(self, ctx=None):
        """
        Force le tirage du loto pour la journée courante (admin uniquement).
        Usage : `!draw`
        """
        if ctx and ctx.channel and hasattr(ctx.channel, 'name'):
            channel = ctx.channel
            await ctx.message.delete()
        else:
            channel = discord.utils.get(self.bot.get_all_channels(), name=DISCORD_LOTO_CHANNEL)
            if not channel:
                return
        draw_date = datetime.today() if ctx else datetime.today() - timedelta(days=1)
        loto = LotoDraw.select().where(LotoDraw.date == draw_date, LotoDraw.draw.is_null()).first()
        if not loto:
            return
        seed(self.seeds.pop(0) if self.seeds else None)
        loto_draw = set(sample(list(range(1, 50)), k=DISCORD_LOTO_COUNT))
        loto.draw = ' '.join(map(str, sorted(loto_draw)))
        # Winner ranks
        ranks = {i: [] for i in range(DISCORD_LOTO_COUNT + 1)}
        for grid in LotoGrid.select(LotoGrid, User).join(User).where(LotoGrid.date == draw_date):
            grid_draw = set(map(int, grid.draw.split()))
            ranks[len(loto_draw & grid_draw)].append(grid)
        # Total to gain
        old_price = round(DISCORD_LOTO_PRICE + round(loto.value / DISCORD_LOTO_LIMIT, 1), 1)
        total_gain = loto.value + LotoGrid.select().where(
            LotoGrid.date == draw_date, LotoGrid.gain.is_null()
        ).count() * old_price
        # Gain rates
        n_max = DISCORD_LOTO_COUNT
        rates = {n: 2 ** (-n_max - 1 + n) + (2 ** -n_max) / n_max for n in range(n_max, 0, -1)}
        # Apply gains
        currency = self.get_currency(DISCORD_MONEY_SYMBOL)
        given_gain, gains = 0.0, {}
        for rank in range(DISCORD_LOTO_COUNT, 0, -1):
            grids = ranks.get(rank)
            if not grids:
                continue
            rate = rates.get(rank, 0.0)
            gains[rank] = gain = (total_gain * rate) / len(grids)
            given_gain += gain * len(grids)
            LotoGrid.update(rank=rank, gain=gain).where(LotoGrid.id << [g.id for g in grids]).execute()
            for grid in grids:
                Balance.update(value=Balance.value + gain).where(
                    Balance.currency == currency, Balance.user_id == grid.user_id
                ).execute()
        LotoGrid.update(rank=0, gain=0).where(LotoGrid.date == draw_date, LotoGrid.rank.is_null()).execute()
        self.currencies.clear()
        self.balances.clear()
        # Save draw and create new draw
        loto.save(only=('draw', ))
        extra_value = 0.0 if ranks[DISCORD_LOTO_COUNT] else DISCORD_LOTO_EXTRA
        new_value = max(total_gain - given_gain + extra_value, DISCORD_LOTO_START)
        loto, created = LotoDraw.get_or_create(
            date=date.today() + timedelta(days=1) if ctx else date.today(),
            defaults=dict(value=new_value))
        new_price = round(DISCORD_LOTO_PRICE + round(loto.value / DISCORD_LOTO_LIMIT, 1), 1)
        # Display results
        draw = ' - '.join(f"{d:02}" for d in sorted(loto_draw))
        for i in range(10):
            draw = draw.replace(str(i), self.get_icon(str(i)))
        winners_by_rank = {rank: [grid.user_id for grid in ranks[rank]] for rank in gains.keys()}
        nb_winners = len(set.union(*map(set, winners_by_rank.values()))) if winners_by_rank else 0
        messages = [
            f":game_die: Bonjour à tous, voici les résultats LOTO du **{draw_date:%A %d %B %Y}** :",
            f"La cagnotte totale était de **{round(total_gain,2):n} {currency.symbol}**.",
            f"Tirage : **{draw}**"]
        if nb_winners:
            messages.append(
                f"Félicitations à nos **{nb_winners} gagnant(s)** qui se partagent "
                f"**{round(given_gain,2):n} {currency.symbol}** :")
            for rank, winners in winners_by_rank.items():
                if not winners:
                    continue
                gain = gains.get(rank, 0.0)
                list_winners = ", ".join(f"<@{w}> (_x{winners.count(w)}_)" for w in set(winners))
                messages.append(
                    f"> **{rank} numéro(s)** pour **{round(gain,2):n} {currency.symbol}** : {list_winners}")
        messages.append(
            f"La cagnotte du tirage d'aujourd'hui démarre donc à **{round(loto.value,2):n} {currency.symbol}**.")
        if old_price != new_price:
            messages.append(
                f":warning:  Attention ! Le prix de la grille évoluant avec la valeur de la cagnotte, "
                f"elle coûte désormais **{round(new_price,2):n} {currency.symbol}** ({currency.name}).")
        await channel.send('\n'.join(messages))

    @tasks.loop(hours=1)
    async def _pay_wage(self):
        """
        Event loop to add the hourly wage to all balances
        """
        # Update wage for every balance
        current_date = datetime.now()
        currency = Currency.select().where(Currency.symbol == DISCORD_MONEY_SYMBOL)
        Balance.update(value=Balance.value + DISCORD_MONEY_WAGE, date=current_date).where(
            Balance.date <= current_date - timedelta(hours=1), Balance.currency << currency
        ).execute()
        # Clear cache
        self.balances.clear()

    @tasks.loop(hours=1)
    async def _rate_money(self):
        """
        Event loop to random rate the custom currencies
        """
        currencies = Currency.select().where(
            Currency.symbol != DISCORD_MONEY_SYMBOL
        ).order_by(pw.fn.Lower(Currency.name))
        for currency in currencies:
            mini, maxi = int(-currency.rate * 10), int((2.0 - currency.rate) * 10)
            seed(self.seeds.pop(0) if self.seeds else None)
            currency.rate += randint(mini, maxi) / 100.0
            currency.rate = round(max(currency.rate, DISCORD_MONEY_MINI), 2)
            currency.save(only=('rate', ))
        # Clear cache
        self.currencies.clear()

    @tasks.loop(hours=1)
    async def _draw_loto(self):
        """
        Event loop for lotto draw results
        """
        await self._draw()
