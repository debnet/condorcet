# coding: utf-8
import argparse
import base64
import discord
import hashlib
import hmac
import logging
import os
import re
import peewee as pw
from datetime import date, datetime
from dateutil.parser import parse as parse_date
from discord import utils
from discord.ext import commands, tasks
from string import ascii_uppercase, digits


# Discord token
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
# Discord operator
DISCORD_OPERATOR = OP = os.environ.get('DISCORD_OPERATOR') or '!'
# Discord administrator role
DISCORD_ADMIN = os.environ.get('DISCORD_ADMIN') or 'Staff'
# Discord default channel
DISCORD_CHANNEL = os.environ.get('DISCORD_CHANNEL') or 'general'
# Discord roles allowed to be granted to users
DISCORD_ROLES = os.environ.get('DISCORD_ROLES')


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
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(log_handler)

# Database handler
database = pw.SqliteDatabase('condorcet.db')


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


class Poll(pw.Model):
    """
    Poll
    """
    name = pw.CharField()
    channel_id = pw.BigIntegerField(null=True)
    winners = pw.SmallIntegerField(default=1)
    proposals = pw.BooleanField(default=False)
    open_apply = pw.BooleanField(default=True)
    open_vote = pw.BooleanField(default=False)

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.name

    class Meta:
        database = database


class Password(pw.Model):
    """
    Password
    """
    poll = pw.ForeignKeyField(Poll)
    user = pw.ForeignKeyField(User)
    password = pw.CharField(null=True)

    class Meta:
        database = database


class Candidate(pw.Model):
    """
    Candidate
    """
    poll = pw.ForeignKeyField(Poll)
    user = pw.ForeignKeyField(User)
    proposal = pw.CharField(null=True)
    indice = pw.CharField(null=True)
    winner = pw.BooleanField(default=False)
    date = pw.DateTimeField(default=datetime.now)

    class Meta:
        database = database
        indexes = (
            (('poll', 'user', 'proposal'), True),
        )


class Vote(pw.Model):
    """
    Vote
    """
    user = pw.CharField()  # No FK because user will be encrypted
    poll = pw.ForeignKeyField(Poll)
    choices = pw.CharField(null=True)
    date = pw.DateTimeField(default=datetime.now)

    class Meta:
        database = database
        indexes = (
            (('user', 'poll'), True),
        )


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


class BaseCog(commands.Cog):
    """
    Base Discord Cog with utility functions
    """
    _users = {}

    def __init__(self, bot):
        self.bot = bot
        self.users = BaseCog._users

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if not after.bot:
            await self.get_user(after)

    async def cog_command_error(self, ctx, error):
        await ctx.author.send(
            f":warning:  **Erreur :** {error} (`{ctx.message.content}` on **{ctx.message.channel.name}**)")
        logger.error(f"[{ctx.message.channel.name}] {error} ({ctx.message.content})")

    async def get_user(self, user):
        """
        Helper function to get database user from a Discord user
        :param user: Discord user
        :return: Database user
        """
        if isinstance(user, str):
            # Tries to get user id in mention
            groups = re.match(r'<[@#](\d+)>', user)
            if groups:
                user_id = int(groups[1])
                user = self.bot.get_user(user_id)
            else:
                # Search user from its username or nickname
                user = utils.find(
                    lambda u: user.lower() in (getattr(u, 'nick', u.name)).lower(),
                    self.bot.get_all_members())
        if not hasattr(user, 'id'):
            # If not a Discord user
            return None
        # Try to get user from cache
        name = getattr(user, 'nick', user.name) or user.name
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


class Condorcet(BaseCog):
    """
    Condorcet voting system bot
    """

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
    }

    @commands.command(name='pass')
    async def _pass(self, ctx, *args):
        """
        Allow user to define a password to ensure its anonymity when voting
        Usage: `!pass <password>`
        :param ctx: Discord context
        :param args: Command arguments
        :return: Nothing
        """
        if ctx.channel and hasattr(ctx.channel, 'topic'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Définit un mot de passe pour pouvoir voter anonymement aux scrutins.")
        parser.add_argument('password', type=str, help="Mot de passe (pour l'anonymat)")
        parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Get active and appliable polls
        polls = Poll.select().where(Poll.open_apply | Poll.open_vote)
        poll = await self.handle_poll(polls, args, ctx.author)
        if not poll:
            return
        # Encoding and saving password for the user
        password, created = Password.get_or_create(poll=poll, user=user, defaults=dict(
            password=self.hash(args.password)))
        if not created:
            # If user already has a password
            await ctx.author.send(":no_entry:  Vous avez déjà défini un mot de passe pour ce scruting.")
            return
        await ctx.author.send(f":white_check_mark:  Votre mot de passe de scrutin a été défini avec succès.")

    @commands.command(name='apply')
    async def _apply(self, ctx, *args):
        """
        Allow user to apply as a candidate to a current poll
        Usage: `!apply [--poll <poll_id> --proposal <text>]`
        :param ctx: Discord context
        :param args: Command arguments
        :return: Nothing
        """
        if ctx.channel and hasattr(ctx.channel, 'topic'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de postuler en tant que candidat au scrutin avec ou sans proposition.")
        parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
        parser.add_argument('--proposal', '-P', type=str, help="Texte de la proposition (si autorisé par le scrutin)")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Get active and appliable polls
        polls = Poll.select().where(Poll.open_apply & ~Poll.open_vote)
        poll = await self.handle_poll(polls, args, ctx.author)
        if not poll:
            return
        channel = poll.channel or ctx.channel
        # Create candidate
        if poll.proposals:
            if not args.proposal:
                await ctx.author.send(
                    f":no_entry:  Ce scrutin nécessite que vous ajoutiez une proposition à votre candidature, "
                    f"vous pouvez le faire en utilisant le paramètre `--proposal \"<proposition>\"`.")
                return
            candidate, created = Candidate.get_or_create(user=user, poll=poll, proposal=args.proposal)
            if created:
                await ctx.author.send(
                    f":white_check_mark:  Votre proposition **{args.proposal}** "
                    f"(`{candidate.id}`) au scrutin de **{poll}** (`{poll.id}`) a été enregistrée !")
                if channel and hasattr(channel, 'topic'):
                    await channel.send(
                        f":raised_hand:  <@{user.id}> a ajouté la proposition **{args.proposal}** "
                        f"(`{candidate.id}`) au scrutin de **{poll.name}** (`{poll.id}`) !")
                return
            await ctx.author.send(
                f":no_entry:  Vous avez déjà ajouté la proposition **{args.proposal}** "
                f"(`{candidate.id}`) à l'élection de **{poll}** (`{poll.id}`) !")
        else:
            candidate, created = Candidate.get_or_create(user=user, poll=poll)
            if created:
                await ctx.author.send(
                    f":white_check_mark:  Vous avez postulé avec succès en tant "
                    f"que candidat au scrutin de **{poll}** (`{poll.id}`) !")
                if channel and hasattr(channel, 'topic'):
                    await channel.send(
                        f":raised_hand:  <@{user.id}> se porte candidat "
                        f"au scrutin de **{poll.name}** (`{poll.id}`) !")
                return
            await ctx.author.send(f":no_entry:  Vous êtes déjà candidat à l'élection de **{poll}** (`{poll.id}`) !")

    @commands.command(name='leave')
    async def _leave(self, ctx, *args):
        """
        Allow user to apply as a candidate to a current poll
        Usage: `!leave [--poll <poll_id>]`
        :param ctx: Discord context
        :param args: Command arguments
        :return: Nothing
        """
        if ctx.channel and hasattr(ctx.channel, 'topic'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de retirer sa candidature au scrutin.")
        parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
        parser.add_argument('--proposal', '-P', type=int, help="Identifiant de la proposition")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Get active and appliable polls
        polls = Poll.select().where(Poll.open_apply & ~Poll.open_vote)
        poll = await self.handle_poll(polls, args, ctx.author)
        if not poll:
            return
        channel = poll.channel or ctx.channel
        # Delete candidate
        if poll.proposals:
            if not args.proposal:
                await ctx.author.send(
                    f":no_entry:  Vous devez fournir l'identifiant de la "
                    f"proposition à retirer à l'aide du paramètre `--proposal <id>`.")
                return
            candidate = Candidate.get_or_none(user=user, poll=poll, id=args.proposal)
            if candidate:
                candidate.delete_instance()
                await ctx.author.send(
                    f":white_check_mark:  Vous avez retiré avec succès votre proposition "
                    f"**{candidate.proposal}** au scrutin de **{poll}** (`{poll.id}`) !")
                if channel and hasattr(channel, 'topic'):
                    await channel.send(
                        f":door:  <@{user.id}> retire sa proposition **{candidate.proposal}** "
                        f"au scrutin de **{poll}** (`{poll.id}`) !")
                return
            await ctx.author.send(
                f":no_entry:  Vous n'avez pas cette proposition à l'élection de **{poll}** (`{poll.id}`) !")
        else:
            candidate = Candidate.get_or_none(user=user, poll=poll)
            if candidate:
                candidate.delete_instance()
                await ctx.author.send(
                    f":white_check_mark:  Vous vous êtes retiré avec succès en tant "
                    f"que candidat à l'élection de **{poll}** !")
                if channel and hasattr(channel, 'topic'):
                    await channel.send(
                        f":door:  <@{user.id}> se retire en tant que candidat l'élection de **{poll}** !")
                return
            await ctx.author.send(
                f":no_entry:  Vous n'êtes pas candidat à l'élection de **{poll}** (`{poll.id}`) !")

    @commands.command(name='vote')
    async def _vote(self, ctx, *args):
        """
        Vote on a poll
        Usage: `!vote <candidat> [<candidat> ...] --password <password> [--poll <poll_id>]`
        :param ctx: Discord context
        :param args: Command arguments
        :return: Nothing
        """
        if ctx.channel and hasattr(ctx.channel, 'topic'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de voter à un scruting donné.")
        parser.add_argument('password', type=str, help="Mot de passe (pour l'anonymat)")
        parser.add_argument(
            'candidates', metavar='candidat', type=str, nargs='+',
            help="Candidats (par ordre de préférence du plus ou moins apprécié)")
        parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Get active and votable polls
        polls = Poll.select().where(~Poll.open_apply & Poll.open_vote)
        poll = await self.handle_poll(polls, args, ctx.author)
        if not poll:
            return
        # Check if all candidates where selected and sorted
        candidates = list(map(str.upper, args.candidates))
        possibles = Candidate.select(Candidate.indice).where(
            Candidate.indice.is_null(False) & (Candidate.poll == poll)
        ).order_by(Candidate.indice.asc())
        possibles = {c.indice for c in possibles}
        if possibles != set(candidates) or len(possibles) != len(candidates):
            await ctx.author.send(f":no_entry:  Vous n'avez pas sélectionné et/ou classé l'ensemble des candidats !")
            return
        # Create new password for user
        password, created = Password.get_or_create(poll=poll, user=user, defaults=dict(
            password=self.hash(args.password)))
        # ... or verify user password
        if not created and self.hash(args.password) != password.password:
            await ctx.author.send(
                f":no_entry:  Votre mot de passe de scrutin est incorrect ou n'a pas encore configuré, "
                f"utilisez la commande `{ctx.prefix}pass` pour le définir !")
            return
        # Encrypt user with password and save vote choices
        encrypted, choices = self.encrypt(args.password, user.id), ' '.join(candidates)
        vote, created = Vote.get_or_create(user=encrypted, poll=poll, defaults=dict(choices=choices))
        if not created:
            vote.choices = choices
            vote.save(only=('choices', ))
        await ctx.author.send(f":ballot_box:  Merci pour votre vote !")

    @commands.command(name='info')
    async def _info(self, ctx, *args):
        """
        Show candidates for a
        Usage: `!info [--poll <poll_id>]`
        :param ctx: Discord context
        :param args: Command arguments
        :return: Nothing
        """
        if ctx.channel and hasattr(ctx.channel, 'topic'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de consulter la liste des candidats au scrutin.")
        parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Get active and appliable polls
        polls = Poll.select()
        poll = await self.handle_poll(polls, args, ctx.author)
        if not poll:
            return
        # Build message
        message = [f"Voici la liste des candidats actuels au scrutin **{poll}** (`{poll.id}`) :"]
        for candidate in Candidate.select(Candidate, User).join(User).order_by(Candidate.indice.asc(), User.name.asc()):
            if poll.proposals:
                message.append(
                    f"{self.get_icon(candidate.indice)}  **{candidate.proposal}** (par {candidate.user.name})")
            else:
                message.append(
                    f"{self.get_icon(candidate.indice)}  **{candidate.user.name}**")
        message = '\n'.join(message)
        # Send message
        is_admin = any(role.name == DISCORD_ADMIN for role in ctx.author.roles)
        if is_admin and hasattr(ctx.channel, 'topic'):
            channel = poll.channel or ctx.channel
            await channel.send(message)
        else:
            await ctx.author.send(message)

    @commands.command(name='new')
    @commands.has_role(DISCORD_ADMIN)
    async def _new(self, ctx, *args):
        """
        Create a new poll and open it for candidates
        Usage: `!new <name> [--winners <count> --proposals]`
        :param ctx: Discord context
        :param args: Command arguments
        :return: Nothing
        """
        if ctx.channel and hasattr(ctx.channel, 'topic'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Permet de créer un nouveau scrutin et l'ouvre aux candidatures.")
        parser.add_argument('name', type=str, help="Nom du scrutin")
        parser.add_argument('--winners', '-w', type=int, help="Nombre de vainqueurs")
        parser.add_argument('--proposals', '-p', action='store_true', help="Propositions ?")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Create new poll
        poll = Poll.create(name=args.name, winners=args.winners or 1, proposals=args.proposals)
        # Message to user/channel
        message = (
            f":ballot_box:  Le scrutin **{poll}** (`{poll.id}`) a été créé et ouvert aux candidatures, "
            f"vous pouvez utiliser la commande `{ctx.prefix}apply` pour vous présenter (ou `{ctx.prefix}leave` pour vous retirer) !")
        if hasattr(ctx.channel, 'topic'):
            # Save channel for announcements
            poll.channel_id = ctx.channel.id
            poll.save(only=('channel_id', ))
            await ctx.channel.send(message)
        else:
            await ctx.author.send(message)

    @commands.command(name='open')
    @commands.has_role(DISCORD_ADMIN)
    async def _open(self, ctx, *args):
        """
        Open an existing poll to vote
        Usage: `!open [--poll <poll_id>]`
        :param ctx: Discord context
        :param args: Command arguments
        :return: Nothing
        """
        if ctx.channel and hasattr(ctx.channel, 'topic'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Ferme la soumission des candidatures et ouvre l'accès au vote pour un scrutin.")
        parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Get active and votable polls
        polls = Poll.select().where(Poll.open_apply & ~Poll.open_vote)
        poll = await self.handle_poll(polls, args, ctx.author)
        if not poll:
            return
        channel = poll.channel or ctx.channel
        # Update poll
        poll.open_apply = False
        poll.open_vote = True
        poll.save(only=('open_apply', 'open_vote', ))
        # Assign letter to every candidate
        for i, candidate in enumerate(Candidate.select(Candidate, User).join(User).order_by(User.name.asc())):
            candidate.indice = self.INDICES[i]
            candidate.save(only=('indice', ))
        # Message to user/channel
        message = (
            f":ballot_box:  Les candidatures au scrutin **{poll}** (`{poll.id}`) "
            f"sont désormais fermées et les votes sont ouverts, vous pouvez voter en "
            f"utilisant la commande `{ctx.prefix}vote` et voir les candidats avec `{ctx.prefix}info` !")
        if channel and hasattr(channel, 'topic'):
            await channel.send(message)
        else:
            await ctx.author.send(message)

    @commands.command(name='close')
    @commands.has_role(DISCORD_ADMIN)
    async def _close(self, ctx, *args):
        """
        Close an existing poll and display results
        Usage: `!close [--poll <poll_id>]`
        :param ctx: Discord context
        :param args: Command arguments
        :return: Nothing
        """
        if ctx.channel and hasattr(ctx.channel, 'topic'):
            await ctx.message.delete()
        user = await self.get_user(ctx.author)
        # Argument parser
        parser = Parser(
            prog=f'{ctx.prefix}{ctx.command.name}',
            description="Ferme le vote à un scrutin et affiche les résultats.")
        parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
        args = parser.parse_args(args)
        if parser.message:
            await ctx.author.send(f"```{parser.message}```")
            return
        # Get active and votable polls
        polls = Poll.select().where(~Poll.open_apply & Poll.open_vote)
        poll = await self.handle_poll(polls, args, ctx.author)
        if not poll:
            return
        channel = poll.channel or ctx.channel
        # Update poll
        poll.open_apply = False
        poll.open_vote = False
        poll.save(only=('open_apply', 'open_vote', ))
        # Compute results
        self.get_results(poll, save=True)
        # Display winners
        votes = Vote.select(Vote.id).where(Vote.poll == poll).count()  # Count total votes
        candidates = Candidate.select(Candidate.id).where(Candidate.poll == poll).count()  # Count total candidates
        winners = Candidate.select(Candidate, User).join(User).where(
            Candidate.poll == poll, Candidate.winner
        ).order_by(Candidate.proposal.asc(), User.name.asc())
        winners = ', '.join([
            f"{self.get_icon(winner.indice)}  **{winner.proposal}** (par <@{winner.user_id}>)" if poll.proposals else
            f"{self.get_icon(winner.indice)}  <@{winner.user_id}>" for winner in winners])
        message = (
            f":trophy:  Les élections de **{poll}** sont désormais terminées, "
            f"il y a eu **{votes}** votes pour **{candidates}** candidatures. "
            f"Merci à tous pour votre participation !\n")
        if poll.winners > 1:
            message += f"Les vainqueurs sont : {winners} ! Félicitations !"
        else:
            message += f"Le vainqueur est : {winners} ! Félicitations !"
        if channel and hasattr(channel, 'topic'):
            await channel.send(message)
        else:
            await ctx.author.send(message)

    def get_icon(self, indice):
        """
        Get Discord icon for indice
        :param indice: Indice
        :return: Icon
        """
        if not indice:
            return '> '
        return self.ICONS.get(indice, f':regional_indicator_{indice.lower()}:')

    def encrypt(self, password, *messages):
        """
        Encrypt message with HMAC algorithm
        :param password: Password
        :param messages: Messages to encrypt
        :return: Base64 encrypted string
        """
        encrypted = hmac.new(key=str(password).encode(), digestmod=hashlib.sha256)
        for message in messages:
            encrypted.update(str(message).encode())
        return base64.urlsafe_b64encode(encrypted.digest()).decode()

    def hash(self, *messages):
        """
        Hash message with SHA256 algorithm
        :param messages: Messages to hash
        :return: Base64 hashed string
        """
        hashed = hashlib.sha256()
        for message in messages:
            hashed.update(str(message).encode())
        return base64.urlsafe_b64encode(hashed.digest()).decode()

    async def handle_poll(self, polls, args, author):
        """
        Handle poll common usage in commands
        :param polls: Poll queryset
        :param args: Command arguments
        :param author: Author
        :return: Poll instance or nothing
        """
        if not args.poll:
            # If there is more than 1 poll
            if polls.count() > 1:
                await author.send(
                    f":warning:  Il y a actuellement plus d'un scrutin en cours. "
                    f"Veuillez fournir un identifiant de scrutin via l'argument `--poll`.")
                return
            # Get the only poll available
            poll = polls.first()
        else:
            # Get the targetted poll
            poll = polls.where(Poll.id == args.poll).first()
        if not poll:
            await author.send(
                f":no_entry:  Aucun scrutin n'est ouvert à cette "
                f"fonctionnalité ou le scrutin sélectionné n'est pas valide.")
            return
        # Get Discord channel
        poll.channel = None if not poll.channel_id else (
            discord.utils.get(self.bot.get_all_channels(), id=poll.channel_id))
        return poll

    def get_results(self, poll, save=False):
        """
        Compute Schulze ballot results
        :param poll: Poll instance
        :param save: Save results
        :return: Results
        """
        votes = {}
        for vote in Vote.select().where(Vote.poll == poll):
            votes.setdefault(vote.choices, 0)
            votes[vote.choices] += 1
        inputs = []
        for choices, count in votes.items():
            inputs.append(dict(count=count, ballot=[[choice] for choice in choices.split()]))
        if poll.winners == 1:
            from py3votecore.schulze_method import SchulzeMethod
            outputs = SchulzeMethod(
                inputs,
                ballot_notation=SchulzeMethod.BALLOT_NOTATION_GROUPING
            ).as_dict()
            if save:
                winner = outputs['winner']
                Candidate.update(winner=True).where(
                    Candidate.poll == poll, Candidate.indice == winner
                ).execute()
        else:
            from py3votecore.schulze_stv import SchulzeSTV
            outputs = SchulzeSTV(
                inputs,
                required_winners=poll.winners,
                ballot_notation=SchulzeSTV.BALLOT_NOTATION_GROUPING
            ).as_dict()
            if save:
                winners = outputs['winners']
                Candidate.update(winner=True).where(
                    Candidate.poll == poll, Candidate.indice.in_(winners)
                ).execute()
        return outputs


class HappyBirthday(BaseCog):
    """
    Happy birthday bot
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._check_birthday.start()

    def cog_unload(self):
        self._check_birthday.cancel()

    @commands.command(name='birthday')
    async def _birthday(self, ctx, *args):
        """
        Save or remove birthday from database
        Usage: `!birthday <date>`
        :param ctx: Discord context
        :param args: Command arguments
        :return: Nothing
        """
        logger.info(", ".join(map(str, self.bot.get_all_channels())))
        if ctx.channel and hasattr(ctx.channel, 'topic'):
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


class RoleManager(BaseCog):
    """
    Role manager bot
    """

    @commands.command(name='role')
    @commands.guild_only()
    async def _role(self, ctx, *args):
        if ctx.channel and hasattr(ctx.channel, 'topic'):
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
        parser.add_argument('roles', metavar='role', type=str, nargs='+', help="Rôle")
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


if __name__ == '__main__':
    database.create_tables((User, Poll, Password, Candidate, Vote, Birthday))
    bot = commands.Bot(command_prefix=DISCORD_OPERATOR)
    bot.add_cog(Condorcet(bot))
    bot.add_cog(HappyBirthday(bot))
    bot.add_cog(RoleManager(bot))
    bot.run(DISCORD_TOKEN)
