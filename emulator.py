# coding: utf-8
import json
import os
from collections import Counter

import discord
from discord.ext import commands, tasks
from pyboy import PyBoy, WindowEvent

from base import BaseCog


GAME_NAME = os.environ.get('GAME_NAME') or 'game'
GAME_CHANNEL = os.environ.get('GAME_CHANNEL') or 'game'
GAME_SCREEN_SIZE = int(os.environ.get('GAME_SCREEN_SIZE') or 3)
GAME_TICKS = int(os.environ.get('GAME_TICKS') or 120)
GAME_MAX_HISTORY = int(os.environ.get('GAME_HISTORY') or 5)
GAME_DELAY = int(os.environ.get('GAME_DELAY') or 5)


class Emulator(BaseCog):
    """
    Emulator bot
    """
    KEY_ICONS = {
        '⬆': 'up',
        '⬇': 'down',
        '⬅': 'left',
        '➡': 'right',
        '🅰': 'A',
        '🅱': 'B',
        '✅': 'start',
        '⏫': 'up+',
        '⏬': 'down+',
        '⏪': 'left+',
        '⏩': 'right+',
        '⏱': 'speed',
    }

    KEYS = {
        'up': (WindowEvent.PRESS_ARROW_UP, WindowEvent.RELEASE_ARROW_UP, 2),
        'up+': (WindowEvent.PRESS_ARROW_UP, WindowEvent.RELEASE_ARROW_UP, 4),
        'down': (WindowEvent.PRESS_ARROW_DOWN, WindowEvent.RELEASE_ARROW_DOWN, 2),
        'down+': (WindowEvent.PRESS_ARROW_DOWN, WindowEvent.RELEASE_ARROW_DOWN, 4),
        'left': (WindowEvent.PRESS_ARROW_LEFT, WindowEvent.RELEASE_ARROW_LEFT, 2),
        'left+': (WindowEvent.PRESS_ARROW_LEFT, WindowEvent.RELEASE_ARROW_LEFT, 4),
        'right': (WindowEvent.PRESS_ARROW_RIGHT, WindowEvent.RELEASE_ARROW_RIGHT, 2),
        'right+': (WindowEvent.PRESS_ARROW_RIGHT, WindowEvent.RELEASE_ARROW_RIGHT, 4),
        'A': (WindowEvent.PRESS_BUTTON_A, WindowEvent.RELEASE_BUTTON_A, 2),
        'B': (WindowEvent.PRESS_BUTTON_B, WindowEvent.RELEASE_BUTTON_B, 2),
        'start': (WindowEvent.PRESS_BUTTON_START, WindowEvent.RELEASE_BUTTON_START, 2),
        'select': (WindowEvent.PRESS_BUTTON_SELECT, WindowEvent.RELEASE_BUTTON_SELECT, 2),
        'speed': (None, None, 90),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.game = PyBoy(f'{GAME_NAME}.gb', window_type='headless')
        self.channel = None
        self.message = None
        self.messages = []
        self.key = None
        self.load()
        self.cron.start()

    def cog_unload(self):
        self.game.stop(save=True)

    @commands.Cog.listener()
    async def on_ready(self):
        if self.channel:
            return
        self.channel = discord.utils.get(self.bot.get_all_channels(), name=GAME_CHANNEL)
        if not self.messages:
            await self.next()
        else:
            self.message = await self.channel.fetch_message(self.messages[-1])

    async def next(self):
        while len(self.messages) >= GAME_MAX_HISTORY:
            message = await self.channel.fetch_message(self.messages.pop(0))
            await message.delete()
        for _ in range(GAME_TICKS):
            self.game.tick()
        self.image()
        self.message = await self.channel.send(file=discord.File(f'{GAME_NAME}.jpg'))
        for icon in self.KEY_ICONS:
            await self.message.add_reaction(icon)
        self.messages.append(self.message.id)
        self.save()

    def press(self):
        key_pressed, key_released, frames = self.KEYS.get(self.key, (None, None, None))
        for _ in range(frames):
            if key_pressed:
                self.game.send_input(key_pressed)
            self.game.tick()
        for _ in range(2):
            if key_released:
                self.game.send_input(key_released)
            self.game.tick()

    def load(self):
        if os.path.exists(f'{GAME_NAME}.state'):
            with open(f'{GAME_NAME}.state', 'rb') as file:
                self.game.load_state(file)
        if os.path.exists(f'{GAME_NAME}.json'):
            with open(f'{GAME_NAME}.json', 'r') as file:
                self.messages = json.load(file)

    def save(self):
        with open(f'{GAME_NAME}.state', 'wb') as file:
            self.game.save_state(file)
        with open(f'{GAME_NAME}.json', 'w') as file:
            json.dump(self.messages, file)

    def image(self, size=GAME_SCREEN_SIZE):
        img = self.game.screen_image()
        if size > 1:
            img = img.resize((160 * size, 144 * size))
        img.save(f'{GAME_NAME}.jpg')

    @tasks.loop(seconds=GAME_DELAY)
    async def cron(self):
        if not self.message:
            return
        self.message = await self.channel.fetch_message(self.message.id)
        if len(self.message.reactions) != len(self.KEY_ICONS):
            return
        counts = Counter()
        for reaction in self.message.reactions:
            icon = str(getattr(reaction.emoji, 'name', reaction.emoji))[:1]
            if icon not in self.KEY_ICONS:
                continue
            counts.update({self.KEY_ICONS[icon]: reaction.count - 1})
        (key, count1), (_, count2) = counts.most_common(2)
        if not count1 or count1 == count2:
            return
        self.key = key
        self.press()
        await self.next()
