# coding: utf-8
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime

import discord
from discord.ext import commands, tasks
from pyboy import PyBoy
from pyboy.utils import WindowEvent

from base import BaseCog, DISCORD_ADMIN, logger

GAME_NAME = os.environ.get("GAME_NAME") or "game"
GAME_CHANNEL = os.environ.get("GAME_CHANNEL") or "game"
GAME_SCREEN_SIZE = int(os.environ.get("GAME_SCREEN_SIZE") or 3)
GAME_TICKS = int(os.environ.get("GAME_TICKS") or 60)
GAME_SPEED = int(os.environ.get("GAME_SPEED") or 120)
GAME_MAX_HISTORY = int(os.environ.get("GAME_MAX_HISTORY") or 10)
GAME_DELAY = int(os.environ.get("GAME_DELAY") or 10)
GAME_USE_CLOCK = int(os.environ.get("GAME_USE_CLOCK") or True)

regex_time = re.compile(r"((?P<day>[1-7])\s)?(?P<hours>[1-2]?\d)[:\s](?P<minutes>[0-5]\d)")


class Emulator(BaseCog):
    """
    Emulator bot
    """

    KEY_ICONS = {
        "⬆": "up",
        "⬇": "down",
        "⬅": "left",
        "➡": "right",
        "🅰": "a",
        "🅱": "b",
        "✅": "start",
        "⏱": "speed",
    }

    KEYS = {
        "up": (WindowEvent.PRESS_ARROW_UP, WindowEvent.RELEASE_ARROW_UP, 10),
        "down": (WindowEvent.PRESS_ARROW_DOWN, WindowEvent.RELEASE_ARROW_DOWN, 10),
        "left": (WindowEvent.PRESS_ARROW_LEFT, WindowEvent.RELEASE_ARROW_LEFT, 10),
        "right": (WindowEvent.PRESS_ARROW_RIGHT, WindowEvent.RELEASE_ARROW_RIGHT, 10),
        "a": (WindowEvent.PRESS_BUTTON_A, WindowEvent.RELEASE_BUTTON_A, 10),
        "b": (WindowEvent.PRESS_BUTTON_B, WindowEvent.RELEASE_BUTTON_B, 10),
        "start": (WindowEvent.PRESS_BUTTON_START, WindowEvent.RELEASE_BUTTON_START, 10),
        "select": (WindowEvent.PRESS_BUTTON_SELECT, WindowEvent.RELEASE_BUTTON_SELECT, 10),
        "speed": (None, None, GAME_SPEED),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.game = PyBoy(GAME_NAME, window="null", cgb=True)
        self.game.set_emulation_speed(0)
        self.channel = None
        self.message = None
        self.messages = []
        self.screenshots = []
        self.last_vote = None
        self.current_time = True
        self.do_load()
        self.cron.start()

    def cog_unload(self):
        self.game.stop(save=True)

    @commands.Cog.listener()
    async def on_ready(self):
        if self.channel:
            return
        self.channel = discord.utils.get(self.bot.get_all_channels(), name=GAME_CHANNEL)
        if not self.messages:
            self.do_screenshot()
            await self.next()
            return
        try:
            self.message = await self.channel.fetch_message(self.messages[-1])
        except Exception as e:
            logger.error(f"Error when retrieving last message: {e}")
            self.do_screenshot()
            await self.next()

    @commands.command(name="time")
    @commands.has_role(DISCORD_ADMIN)
    async def _time(self, context=None, *, time: str):
        """
        Permet de changer l'heure dans le jeu (Pokemon uniquement, format [<jour:0-6>] <heures>:<minutes>)
        """
        if context and context.channel and hasattr(context.channel, "name"):
            await context.message.delete()
        if not GAME_USE_CLOCK:
            await context.author.send(":no_entry:  Ce jeu n'utilise pas d'horloge interne !")
            return
        if match := regex_time.match(time):
            self.current_time = False
            hours, minutes = match.group("hours"), match.group("minutes")
            self.game.memory[0xD4B7] = int(hours)
            self.game.memory[0xD4B8] = int(minutes)
            label = f"{hours}:{minutes}"
            if day := match.group("day"):
                self.game.memory[0xD4B6] = int(day)
                days = {
                    "1": "lundi",
                    "2": "mardi",
                    "3": "mercredi",
                    "4": "jeudi",
                    "5": "vendredi",
                    "6": "samedi",
                    "7": "dimanche",
                }
                label = f"{days[day]} {hours}:{minutes}"
            await self.channel.send(f":alarm_clock:  L'heure du jeu a été changée à **{label}** !")
        else:
            self.current_time = True
            await self.channel.send(f":alarm_clock:  L'heure du jeu suivra désormais l'heure actuelle !")

    @commands.command(name="save")
    @commands.has_role(DISCORD_ADMIN)
    async def _save(self, context=None, *, filename: str):
        """
        Permet de sauvegarder l'état du jeu dans une savestate
        """
        if context and context.channel and hasattr(context.channel, "name"):
            await context.message.delete()
        if not filename:
            return
        os.makedirs("saves", exist_ok=True)
        with open(f"saves/{filename}.state", "wb") as file:
            self.game.save_state(file)
        await self.channel.send(f":floppy_disk:  L'état du jeu a été sauvegardé dans le fichier `{filename}` !")

    @commands.command(name="load")
    @commands.has_role(DISCORD_ADMIN)
    async def _load(self, context=None, *, filename: str):
        """
        Permet de charger l'état du jeu depuis une savestate
        """
        if context and context.channel and hasattr(context.channel, "name"):
            await context.message.delete()
        if not filename:
            return
        os.makedirs("saves", exist_ok=True)
        try:
            with open(f"saves/_undo.state", "wb") as file:
                self.game.save_state(file)
            with open(f"saves/{filename}.state", "rb") as file:
                self.game.save_state(file)
            await self.channel.send(f":floppy_disk:  L'état du jeu a été chargé depuis le fichier `{filename}` !")
        except Exception as e:  # noqa
            logger.error(f"Error when manually saving state: {e}")
            return

    @commands.command(name="sequence")
    @commands.has_role(DISCORD_ADMIN)
    async def _sequence(self, context=None, *, keys: str):
        """
        Permet d'exécuter une séquence de touches dans le jeu
        """
        if context and context.channel and hasattr(context.channel, "name"):
            await context.message.delete()
        if not keys:
            return
        self.screenshots = []
        for key in keys.split():
            if key.lower() in self.KEYS:
                self.do_press(key)
        await self.next()

    async def next(self):
        while len(self.messages) >= GAME_MAX_HISTORY:
            try:
                message = await self.channel.fetch_message(self.messages.pop(0))
                await message.delete()
            except Exception as e:  # noqa
                logger.error(f"Error when deleting the oldest message: {e}")
        if self.screenshots:
            screenshot = next(iter(self.screenshots[::-1]))
            screenshot.save(
                f"{GAME_NAME}.gif",
                format="GIF",
                save_all=True,
                append_images=self.screenshots[::4],
                duration=16,
                loop=0,
            )
            try:
                subprocess.run(["gifsicle", "-O3", "--lossy=80", "--colors=256", f"{GAME_NAME}.gif", "-o", f"{GAME_NAME}.gif"])
            except Exception as e:  # noqa
                logger.error(f"Error when resizing GIF with external program: {e}")
        try:
            self.message = await self.channel.send(file=discord.File(f"{GAME_NAME}.gif"))
            for icon in self.KEY_ICONS:
                await self.message.add_reaction(icon)
            self.messages.append(self.message.id)
        except Exception as e:  # noqa
            logger.error(f"Error when sending the new message: {e}")
            self.messages.clear()
        self.do_save()

    def do_press(self, key: str, count: int = 1):
        logger.debug(f"Requested keys: {key} (x{count})")
        key_pressed, key_released, frames = self.KEYS.get(key.lower(), (None, None, None))
        for i in range(count):
            for _ in range(frames):
                if key_pressed:
                    self.game.send_input(key_pressed)
                self.game.tick()
                self.do_screenshot()
            if key_released:
                for _ in range(2):
                    self.game.send_input(key_released)
                    self.game.tick()
                    self.do_screenshot()
                for _ in range(GAME_TICKS):
                    self.game.tick()
                    self.do_screenshot()
        logger.debug(f"Screenshots: {len(self.screenshots)}")

    def do_load(self):
        if os.path.exists(f"{GAME_NAME}.state"):
            with open(f"{GAME_NAME}.state", "rb") as file:
                self.game.load_state(file)
        if os.path.exists(f"{GAME_NAME}.json"):
            with open(f"{GAME_NAME}.json", "r") as file:
                self.messages = json.load(file)

    def do_save(self):
        with open(f"{GAME_NAME}.state", "wb") as file:
            self.game.save_state(file)
        with open(f"{GAME_NAME}.json", "w") as file:
            json.dump(self.messages, file)
        self.screenshots.clear()

    def do_screenshot(self):
        img = self.game.screen.image
        img = img.resize((160 * GAME_SCREEN_SIZE, 144 * GAME_SCREEN_SIZE))
        self.screenshots.append(img)

    @tasks.loop(seconds=3)
    async def cron(self):
        if GAME_USE_CLOCK and self.game and self.current_time:
            now = datetime.now()
            self.game.memory[0xD4B6] = now.isoweekday()
            self.game.memory[0xD4B7] = now.hour
            self.game.memory[0xD4B8] = now.minute
        if not self.message:
            return
        self.message = await self.channel.fetch_message(self.message.id)
        if len(self.message.reactions) < len(self.KEY_ICONS):
            return
        counts = Counter()
        for reaction in self.message.reactions:
            icon = str(getattr(reaction.emoji, "name", reaction.emoji))[:1]
            if icon not in self.KEY_ICONS:
                continue
            counts.update({self.KEY_ICONS[icon]: reaction.count - 1})
        (key, count1), (_, count2) = counts.most_common(2)
        if not self.last_vote and count1:
            self.last_vote = datetime.now()
        if not self.last_vote or (datetime.now() - self.last_vote).total_seconds() < GAME_DELAY:
            return
        if not count1 or count1 == count2:
            return
        self.screenshots = []
        self.do_press(key, count1)
        await self.next()
        self.last_vote = None
