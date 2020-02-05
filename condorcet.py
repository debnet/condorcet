# coding: utf-8
import argparse
import base64
import discord
import hashlib
import logging
import os
import re
import peewee as pw
import shlex
from datetime import datetime
from discord import utils
from string import ascii_uppercase, digits


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
# User cache (avoid extra SQL queries)
USERS = {}

# Discord client
client = discord.Client()
# Database handler
database = pw.SqliteDatabase('condorcet.db')


class User(pw.Model):
    """
    User
    """
    id = pw.BigIntegerField(primary_key=True)
    name = pw.CharField()
    password = pw.CharField(null=True)
    admin = pw.BooleanField(default=False)

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
    salt = pw.CharField()
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


class Candidate(pw.Model):
    """
    Candidate
    """
    poll = pw.ForeignKeyField(Poll)
    user = pw.ForeignKeyField(User)
    proposal = pw.CharField(null=True)
    indice = pw.CharField(null=True)
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


def get_icon(indice):
    """
    Get Discord icon for indice
    :param indice: Indice
    :return: Icon
    """
    if not indice:
        return '> '
    return ICONS.get(indice, f':regional_indicator_{indice.lower()}:')


def get_salt():
    """
    Get a random salt for Fernet algorithm
    :return: Base64-encoded salt
    """
    return base64.urlsafe_b64encode(os.urandom(16)).decode()


def encrypt(message, salt, password):
    """
    Encrypt message with Fernet algorithm
    :param message: Message to encrypt
    :param salt: Salt
    :param password: Password
    :return: Base64 encrypted string
    """
    from cryptography.fernet import Fernet
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=base64.urlsafe_b64decode(salt.encode()),
        iterations=100000,
        backend=default_backend())
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    fernet = Fernet(key)
    token = fernet.encrypt(str(message).encode())
    return base64.urlsafe_b64encode(token).decode()


def hash(message):
    """
    Hash message with SHA256 algorithm
    :param message: Message to hash
    :return: Base64 hashed string
    """
    hash = hashlib.sha256()
    hash.update(str(message).encode())
    return base64.urlsafe_b64encode(hash.digest()).decode()


async def get_user(user):
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
            user = client.get_user(user_id)
        else:
            # Search user from its username or nickname
            user = utils.find(lambda u: user.lower() in (getattr(u, 'nick', u.name)).lower(), client.get_all_members())
    if not hasattr(user, 'id'):
        # If not a Discord user
        return None
    # Try to get user from cache
    name = getattr(user, 'nick', user.name) or user.name
    _user = USERS.get(user.id)
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
    USERS[_user.id] = _user
    return _user


@client.event
async def on_message(message):
    """
    Discord event: on message reception
    :param message: Message
    :return: Nothing
    """
    # Get general data from message
    author, channel, content = message.author, message.channel, message.content.strip()
    # Ignore bot own messages
    if not content or author == client.user:
        return

    # Get or create user from message
    user = await get_user(author)
    # Extract keyword function
    try:
        keyword, *args = shlex.split(content)
    except ValueError:
        keyword, *args = content.split()
    args = author, user, channel, args
    # Ignore messages not starting with keyword symbol
    if not keyword.startswith('!'):
        return
    # Logging commands for information purposes
    if hasattr(channel, 'name'):
        logger.info(f"[{user.name} #{channel.name}] {content}")
    else:
        logger.info(f"[{user.name}] {content}")

    # Tries to delete message if message is in channel
    if hasattr(channel, 'topic'):
        await message.delete()

    # Command: set password
    if keyword == '!pass':
        await _pass(*args)
        return
    # Command: candidate to poll
    if keyword == '!apply':
        await _apply(*args)
        return
    # Command: resign candidature
    if keyword == '!leave':
        await _leave(*args)
        return
    # Command: vote on poll
    if keyword == '!vote':
        await _vote(*args)
        return
    # Command: information on candidates
    if keyword == '!info':
        await _info(*args)
        return

    # At this point, all following commands are for administrators only
    if not user.admin:
        await author.send(":no_entry:  Vous n'avez pas accès à cette fonctionnalité.")
        return

    # Command: new poll and open to candidates
    if keyword == '!new':
        await _new(*args)
        return
    # Command: open poll to vote
    if keyword == '!open':
        await _open(*args)
        return
    # Command: close poll and display results
    if keyword == '!close':
        await _close(*args)
        return


async def handle_poll(polls, args, author):
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
            f":no_entry:  Aucun scrutin n'est ouvert à cette fonctionnalité "
            f"ou le scrutin sélectionné n'est pas valide.")
        return
    return poll


async def _pass(author, user, channel, args):
    """
    Allow user to define a password to ensure its anonymity when voting
    Usage: `!pass <password>`
    :param author: Discord message author
    :param user: Database user
    :param channel: Discord channel
    :param args: Command arguments
    :return: Nothing
    """
    # If user already has a password
    if user.password:
        await author.send(":no_entry:  Vous avez déjà défini un mot de passe.")
        return
    # Argument parser
    parser = Parser(
        prog='!pass',
        description="Définit un mot de passe pour pouvoir voter anonymement aux scrutins.")
    parser.add_argument('password', type=str, help="Mot de passe (pour l'anonymat)")
    args = parser.parse_args(args)
    if parser.message:
        await author.send(f"```{parser.message}```")
        return
    # Encoding and saving password for the user
    user.password = hash(args.password)
    user.save(only=('password', ))
    await author.send(f":white_check_mark:  Votre mot de passe a été défini avec succès.")


async def _apply(author, user, channel, args):
    """
    Allow user to apply as a candidate to a current poll
    Usage: `!apply [--poll <poll_id> --proposal <text>]`
    :param author: Discord message author
    :param user: Database user
    :param channel: Discord channel
    :param args: Command arguments
    :return: Nothing
    """
    # Argument parser
    parser = Parser(
        prog='!apply',
        description="Permet de postuler en tant que candidat au scrutin avec ou sans proposition.")
    parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
    parser.add_argument('--proposal', '-P', type=str, help="Texte de la proposition (si autorisé par le scrutin)")
    args = parser.parse_args(args)
    if parser.message:
        await author.send(f"```{parser.message}```")
        return
    # Get active and appliable polls
    polls = Poll.select().where(Poll.open_apply & ~Poll.open_vote)
    poll = await handle_poll(polls, args, author)
    if not poll:
        return
    # Create candidate
    if poll.proposals:
        if not args.proposal:
            await author.send(
                f":no_entry:  Ce scrutin nécessite que vous ajoutiez une proposition à votre candidature, "
                f"vous pouvez le faire en utilisant le paramètre `--proposal \"<proposition>\"`.")
            return
        candidate, created = Candidate.get_or_create(user=user, poll=poll, proposal=args.proposal)
        if created:
            await author.send(
                f":white_check_mark:  Votre proposition **{args.proposal}** "
                f"(`{candidate.id}`) à l'élection de **{poll}** (`{poll.id}`) a été enregistrée !")
            if hasattr(channel, 'topic'):
                await channel.send(
                    f":raised_hand:  <@{user.id}> a ajouté la proposition **{args.proposal}** "
                    f"(`{candidate.id}`) à l'élection de **{poll.name}** (`{poll.id}`) !")
            return
        await author.send(
            f":no_entry:  Vous avez déjà ajouté la proposition **{args.proposal}** "
            f"(`{candidate.id}`) à l'élection de **{poll}** (`{poll.id}`) !")
    else:
        candidate, created = Candidate.get_or_create(user=user, poll=poll)
        if created:
            await author.send(
                f":white_check_mark:  Vous avez postulé avec succès en tant "
                f"que candidat à l'élection de **{poll}** (`{poll.id}`) !")
            if hasattr(channel, 'topic'):
                await channel.send(
                    f":raised_hand:  <@{user.id}> se porte candidat à "
                    f"l'élection de **{poll.name}** (`{poll.id}`) !")
            return
        await author.send(f":no_entry:  Vous êtes déjà candidat à l'élection de **{poll}** (`{poll.id}`) !")


async def _leave(author, user, channel, args):
    """
    Allow user to apply as a candidate to a current poll
    Usage: `!leave [--poll <poll_id>]`
    :param author: Discord message author
    :param user: Database user
    :param channel: Discord channel
    :param args: Command arguments
    :return: Nothing
    """
    # Argument parser
    parser = Parser(
        prog='!leave',
        description="Permet de retirer sa candidature au scrutin.")
    parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
    parser.add_argument('--proposal', '-P', type=int, help="Identifiant de la proposition")
    args = parser.parse_args(args)
    if parser.message:
        await author.send(f"```{parser.message}```")
        return
    # Get active and appliable polls
    polls = Poll.select().where(Poll.open_apply & ~Poll.open_vote)
    poll = await handle_poll(polls, args, author)
    if not poll:
        return
    # Delete candidate
    if poll.proposals:
        if not args.proposal:
            await author.send(
                f":no_entry:  Vous devez fournir l'identifiant de la "
                f"proposition à retirer à l'aide du paramètre `--proposal <id>`.")
            return
        candidate = Candidate.get_or_none(user=user, poll=poll, id=args.proposal)
        if candidate:
            candidate.delete()
            await author.send(
                f":white_check_mark:  Vous avez retiré avec succès votre proposition "
                f"**{candidate.proposal}** à l'élection de **{poll}** (`{poll.id}`) !")
            if hasattr(channel, 'topic'):
                await channel.send(
                    f":door:  <@{user.id}> retire sa proposition **{candidate.proposal}** "
                    f"à l'élection de **{poll}** (`{poll.id}`) !")
            return
        await author.send(f":no_entry:  Vous n'avez pas cette proposition à l'élection de **{poll}** (`{poll.id}`) !")
    else:
        candidate = Candidate.get_or_none(user=user, poll=poll)
        if candidate:
            candidate.delete()
            await author.send(
                f":white_check_mark:  Vous vous êtes retiré avec succès en tant "
                f"que candidat à l'élection de **{poll}** !")
            if hasattr(channel, 'topic'):
                await channel.send(f":door:  <@{user.id}> se retire en tant que candidat l'élection de **{poll}** !")
            return
        await author.send(f":no_entry:  Vous n'êtes pas candidat à l'élection de **{poll}** (`{poll.id}`) !")


async def _vote(author, user, channel, args):
    """
    Vote on a poll
    Usage: `!vote <candidat> [<candidat> ...] --password <password> [--poll <poll_id>]`
    :param author: Discord message author
    :param user: Database user
    :param channel: Discord channel
    :param args: Command arguments
    :return: Nothing
    """
    # Argument parser
    parser = Parser(
        prog='!vote',
        description="Permet de voter à un scruting donné.")
    parser.add_argument('candidates', metavar='candidat', type=str, nargs='+',
                        help="Candidats (par ordre de préférence du plus ou moins apprécié)")
    parser.add_argument('--password', '-P', type=str, required=True, help="Mot de passe (pour l'anonymat)")
    parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
    args = parser.parse_args(args)
    if parser.message:
        await author.send(f"```{parser.message}```")
        return
    # Get active and votable polls
    polls = Poll.select().where(~Poll.open_apply & Poll.open_vote)
    poll = await handle_poll(polls, args, author)
    if not poll:
        return
    # Check if all candidates where selected and sorted
    candidates = list(map(str.upper, args.candidates))
    possibles = Candidate.select(Candidate.indice).where(
        Candidate.indice.is_null(False) & (Candidate.poll == poll)
    ).order_by(Candidate.indice.asc())
    possibles = {c.indice for c in possibles}
    if possibles != set(candidates) or len(possibles) != len(candidates):
        await author.send(f":no_entry:  Vous n'avez pas sélectionné et/ou classé l'ensemble des candidats !")
        return
    # Create new password for user
    if not user.password:
        user.password = hash(args.password)
        user.save(only=('password', ))
    # ... or verify user password
    elif hash(args.password) != user.password:
        await author.send(
            f":no_entry:  Votre mot de passe de scrutin est incorrect ou n'a pas encore configuré, "
            f"utilisez la commande `!pass` pour le définir !")
        return
    # Encrypt user with password and save vote choices
    encrypted, choices = encrypt(user.id, poll.salt, args.password), ' '.join(candidates)
    vote, created = Vote.get_or_create(user=encrypted, poll=poll, defaults=dict(choices=choices))
    if not created:
        vote.choices = choices
        vote.save(only=('choices', ))
    await author.send(f":ballot_box:  Merci pour votre vote !")


async def _info(author, user, channel, args):
    """
    Show candidates for a
    Usage: `!info [--poll <poll_id>]`
    :param author: Discord message author
    :param user: Database user
    :param channel: Discord channel
    :param args: Command arguments
    :return: Nothing
    """
    # Argument parser
    parser = Parser(
        prog='!info',
        description="Permet de consulter la liste des candidats au scrutin.")
    parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
    args = parser.parse_args(args)
    if parser.message:
        await author.send(f"```{parser.message}```")
        return
    # Get active and appliable polls
    polls = Poll.select()
    poll = await handle_poll(polls, args, author)
    if not poll:
        return
    # Build message
    message = [f"Voici la liste des candidats actuels au scrutin **{poll}** (`{poll.id}`) :"]
    for candidate in Candidate.select().join(User).order_by(Candidate.indice.asc(), User.name.asc()):
        if poll.proposals:
            message.append(f"{get_icon(candidate.indice)}  **{candidate.proposal}** (par {candidate.user.name})")
        else:
            message.append(f"{get_icon(candidate.indice)}  **{candidate.user.name}**")
    message = '\n'.join(message)
    # Send message
    if user.admin and hasattr(channel, 'topic'):
        await channel.send(message)
    else:
        await author.send(message)


async def _new(author, user, channel, args):
    """
    Create a new poll and open it for candidates
    Usage: `!new <name> [--winners <count> --proposals]`
    :param author: Discord message author
    :param user: Database user
    :param channel: Discord channel
    :param args: Command arguments
    :return: Nothing
    """
    # Argument parser
    parser = Parser(
        prog='!new',
        description="Permet de créer un nouveau scrutin et l'ouvre aux candidatures.")
    parser.add_argument('name', type=str, help="Nom du scrutin")
    parser.add_argument('--winners', '-w', type=int, help="Nombre de vainqueurs")
    parser.add_argument('--proposals', '-p', action='store_true', help="Propositions ?")
    args = parser.parse_args(args)
    if parser.message:
        await author.send(f"```{parser.message}```")
        return
    # Create new poll
    poll = Poll.create(name=args.name, salt=get_salt(), winners=args.winners or 1, proposals=args.proposals)
    # Message to user/channel
    message = f":ballot_box:  Le scrutin **{poll}** (`{poll.id}`) a été créé et ouvert aux candidatures, " \
              f"vous pouvez utiliser la commande `!apply` pour vous présenter (ou `!leave` pour vous retirer) !"
    if hasattr(channel, 'topic'):
        await channel.send(message)
    else:
        await author.send(message)


async def _open(author, user, channel, args):
    """
    Open an existing poll to vote
    Usage: `!open [--poll <poll_id>]`
    :param author: Discord message author
    :param user: Database user
    :param channel: Discord channel
    :param args: Command arguments
    :return: Nothing
    """
    # Argument parser
    parser = Parser(
        prog='!open',
        description="Ferme la soumission des candidatures et ouvre l'accès au vote pour un scrutin.")
    parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
    args = parser.parse_args(args)
    if parser.message:
        await author.send(f"```{parser.message}```")
        return
    # Get active and votable polls
    polls = Poll.select().where(Poll.open_apply & ~Poll.open_vote)
    poll = await handle_poll(polls, args, author)
    if not poll:
        return
    # Update poll
    poll.open_apply = False
    poll.open_vote = True
    poll.save(only=('open_apply', 'open_vote', ))
    # Assign letter to every candidate
    for i, candidate in enumerate(Candidate.select().join(User).order_by(User.name.asc())):
        candidate.indice = INDICES[i]
        candidate.save(only=('indice', ))
    # Message to user/channel
    message = (
        f":ballot_box:  Les candidatures au scrutin **{poll}** (`{poll.id}`) sont désormais fermées et les votes "
        f"sont ouverts, vous pouvez voter en utilisant la commande `!vote` et voir les candidats avec `!info` !")
    if hasattr(channel, 'topic'):
        await channel.send(message)
    else:
        await author.send(message)


async def _close(author, user, channel, args):
    """
    Close an existing poll and display results
    Usage: `!close [--poll <poll_id>]`
    :param author: Discord message author
    :param user: Database user
    :param channel: Discord channel
    :param args: Command arguments
    :return: Nothing
    """
    # Argument parser
    parser = Parser(
        prog='!close',
        description="Ferme le vote à un scrutin et affiche les résultats.")
    parser.add_argument('--poll', '-p', type=str, help="Identifiant de scrutin")
    args = parser.parse_args(args)
    if parser.message:
        await author.send(f"```{parser.message}```")
        return
    # TODO:


if __name__ == '__main__':
    database.create_tables((User, Poll, Candidate, Vote))
    client.run(os.environ.get('DISCORD_TOKEN'))
