import discord
import geopandas as gpd
import math
import os
import peewee as pw
import random
import re
import requests
from datetime import datetime, time
from dataclasses import dataclass
from io import BytesIO

import matplotlib.pyplot as plt
from discord.ext import commands, tasks
from shapely import Point
from PIL import Image, ImageDraw

from base import BaseCog, User, database, logger, DISCORD_ADMIN


GEOGUESSR_CHANNEL = os.environ.get("GEOGUESSR_CHANNEL") or "geoguessr"
GEOGUESSR_IMAGES = os.environ.get("GEOGUESSR_IMAGES") or "images"
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
WORLD_DATA = os.environ.get("WORLD_DATA") or "france.shp.zip"

GOOGLE_STREETVIEW_ENDPOINT = "https://maps.googleapis.com/maps/api/streetview"
GOOGLE_MAPS_ENDPOINT = "https://maps.googleapis.com/maps/api/staticmap"
GOOGLE_ADDRESS_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_URL = "https://maps.google.com/?q="

regex_coords = re.compile(r"(?P<lat>-?\d+(\.?\d+)?)[,\s]+(?P<lng>-?\d+(\.?\d+)?)")


class Place(pw.Model):
    date = pw.DateTimeField(default=datetime.now, index=True)
    city = pw.CharField()
    department = pw.CharField()
    region = pw.CharField()
    lat = pw.FloatField()
    lng = pw.FloatField()
    clues = pw.SmallIntegerField(default=0)

    @property
    def coords(self):
        return Coordinates(self.lat, self.lng)

    class Meta:
        database = database


class Guess(pw.Model):
    place = pw.ForeignKeyField(Place)
    user = pw.ForeignKeyField(User)
    lat = pw.FloatField()
    lng = pw.FloatField()
    address = pw.CharField()
    distance = pw.FloatField(null=True)
    score = pw.IntegerField(null=True)
    clues = pw.SmallIntegerField(default=0)
    date = pw.DateTimeField(default=datetime.now)

    @property
    def coords(self):
        return Coordinates(self.lat, self.lng)

    class Meta:
        database = database
        indexes = ((("place", "user"), True),)


@dataclass
class Coordinates:
    lat: float
    lng: float
    city: str = None
    department: str = None
    region: str = None

    def within(self, polygon) -> bool:
        return Point(self.lng, self.lat).within(polygon)

    def distance(self, coords):
        lat1, lng1 = math.radians(self.lat), math.radians(self.lng)
        lat2, lng2 = math.radians(coords.lat), math.radians(coords.lng)
        dlat, dlng = lat2 - lat1, lng2 - lng1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt((1 - a)))
        r = 6371000.0
        return c * r


class Geoguessr(BaseCog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        database.create_tables((Place, Guess))
        self.world = gpd.read_file(WORLD_DATA)
        self.current, self.last_message = None, None
        self._new_place.start()
        self._new_clue.start()

    def cog_unload(self):
        self._new_place.cancel()
        self._new_clue.cancel()

    @commands.command(name="guess")
    async def _guess(self, context=None, *, message: str):
        """
        Permet de fournir une proposition à l'énigme Geoguessr du jour
        """
        if context and context.channel and hasattr(context.channel, "name"):
            await context.message.delete()
        user = await self.get_user(context.author)
        place = Place.select().order_by(Place.date.desc()).first()
        if not place:
            await context.author.send(":no_entry_sign:  Il n'y a actuellement pas de lieu Geoguessr à trouver !")
            return
        if match := regex_coords.match(message.strip()):
            address, lat, lng = "", float(match.group("lat")), float(match.group("lng"))
        elif result := self.get_coords(message):
            address, lat, lng = result
        else:
            await context.author.send(":no_entry_sign:  Cette adresse n'existe pas, veuillez réessayer !")
            return
        distance = Coordinates(lat, lng).distance(place.coords)
        score = round(max(0, (5000 - distance) / (1 + place.clues / 5)), 0)
        Guess.insert(
            place=place,
            user=user,
            lat=lat,
            lng=lng,
            address=address,
            distance=distance,
            score=score,
            clues=place.clues,
            date=datetime.now(),
        ).on_conflict_replace().execute()
        today = datetime.today()
        await context.author.send(
            f":white_check_mark:  Votre proposition a bien été enregistrée pour le Geoguessr du {today:%A %d %B %Y} !\n"
            f"**Adresse:** `{address or 'N/A'}` - **Coordonnées:** `{lat}, {lng}` - "
            f"**Indices:** `{place.clues}`\n**Voir le lieu:** {GOOGLE_URL}{lat},{lng}"
        )

    @commands.command(name="place")
    @commands.has_role(DISCORD_ADMIN)
    async def _place(self, context=None, *args):
        """
        Permet de forcer une nouvelle énigme Geoguessr
        """
        if context and context.channel and hasattr(context.channel, "name"):
            await context.message.delete()
        channel = discord.utils.get(self.bot.get_all_channels(), name=GEOGUESSR_CHANNEL)
        if not channel:
            return
        place = Place.select().order_by(Place.date.desc()).first()
        if place:
            self.last_message = await channel.send(
                f":checkered_flag:  **Terminé !** La réponse était dans la ville de **{place.city}** "
                f"({place.department}, {place.region}) !\n*Voir le lieu:* {GOOGLE_URL}{place.lat},{place.lng}"
            )
            messages = []
            guesses = Guess.select().filter(Guess.place == place).order_by(Guess.score.desc())
            for index, guess in enumerate(guesses, start=1):
                messages.append(
                    f"- {self.ICONS.get(str(index), '')}  <@{guess.user_id}> - "
                    f"Distance: `{round(guess.distance)} m` - Indices: `{guess.clues}` - Score: **{guess.score} points**"
                )
            if messages:
                self.last_message = await channel.send(
                    ":trophy:  Voici le classement des participants du jour:\n" + "\n".join(messages)
                )
                # await self.last_message.pin(reason="Classement des participants")
        coords = self.create()
        Place.create(
            city=coords.city,
            department=coords.department,
            region=coords.region,
            lat=coords.lat,
            lng=coords.lng,
        )
        today = datetime.today()
        self.last_message = await channel.send(
            f":map:  **Geoguessr du {today:%A %d %B %Y} !** Trouvez le lieu des photographies en moins de 24h.\n"
            f":information:  Utilisez la commande `!guess <adresse>` ou `!guess <lat>,<lng>` pour proposer une réponse.",
            files=(
                discord.File(f"images/_.jpg"),
                discord.File(f"images/N.jpg"),
                discord.File(f"images/E.jpg"),
                discord.File(f"images/S.jpg"),
                discord.File(f"images/W.jpg"),
            ),
        )
        # await self.last_message.pin(reason=f"Geoguessr du {today:%A %d %B %Y}")

    @commands.command(name="clue")
    @commands.has_role(DISCORD_ADMIN)
    async def _clue(self, context=None, *args):
        """
        Permet de forcer un indice pour l'énigme Geoguessr du jour
        """
        if context and context.channel and hasattr(context.channel, "name"):
            await context.message.delete()
        channel = discord.utils.get(self.bot.get_all_channels(), name=GEOGUESSR_CHANNEL)
        if not channel:
            return
        place = Place.select().order_by(Place.date.desc()).first()
        if not place:
            return
        if not self.current:
            df = self.world
            predicate = (df.NAME == place.city) & (df.DEPARTMENT == place.department) & (df.REGION == place.region)
            self.current = df[predicate].iloc[0].to_dict()
        if place.clues == 0:
            self.last_message = await channel.send(
                f":bulb:  C'est l'heure du premier indice ! J'espère qu'il vous mettra sur la bonne piste.\n"
                f":one:  La région dans laquelle vous devez chercher est **{place.region}**."
            )
            # await self.last_message.pin(reason="Indice n°1")
        elif place.clues == 1:
            self.last_message = await channel.send(
                f":bulb:  Voici le deuxième indice ! Ça vous aidera à réduire votre champ de recherche.\n"
                f":two:  Le département dans lequel vous devez chercher est **{place.department}**."
            )
            # await self.last_message.pin(reason="Indice n°2")
        elif place.clues == 2:
            fig, ax = plt.subplots()
            poly = gpd.GeoSeries(self.current["geometry"])
            poly.plot(color="C1", ax=ax)
            neighbours = self.world[self.world.geometry.touches(self.current["geometry"])]
            if not neighbours.empty:
                poly = gpd.GeoSeries(neighbours.geometry)
                poly.plot(color="C0", ax=ax)
            ax.axis("off")
            fig.savefig(f"{GEOGUESSR_IMAGES}/1.jpg", bbox_inches="tight")
            self.last_message = await channel.send(
                f":bulb:  Vous n'avez pas encore trouvé ? Ce troisième indice devrait vous donner un coup de pouce.\n"
                f":three:  Voici la forme de l'aire de la ville que vous cherchez ainsi que celle de ses voisines :",
                file=discord.File(f"{GEOGUESSR_IMAGES}/1.jpg"),
            )
            # await self.last_message.pin(reason="Indice n°3")
        elif place.clues == 3:
            pop, area = self.current["POPULATION"], round(self.current["AREA"], 2)
            self.last_message = await channel.send(
                f":bulb:  C'est pas simple hein ? Avec ce quatrième indice vous devriez trouver plus facilement !\n"
                f":four:  La ville que vous cherchez comptait **{pop} habitants** répartis sur environ **{area} km²**."
            )
            # await self.last_message.pin(reason="Indice n°4")
        elif place.clues == 4:
            self.last_message = await channel.send(
                f":bulb:  C'est bientôt fini ! Voici le dernier indice, avec ça vous savez où chercher désormais !\n"
                f":five:  La ville dans laquelle les photos ont été prises est **{place.city}**."
            )
            # await self.last_message.pin(reason="Indice n°5")
        else:
            return
        place.clues += 1
        place.save(only=("clues",))

    @tasks.loop(time=time(0, 0))
    async def _new_place(self):
        await self._place(context=None)

    @tasks.loop(
        time=(
            time(10, 0),
            time(12, 0),
            time(14, 0),
            time(16, 0),
            time(18, 0),
        )
    )
    async def _new_clue(self):
        await self._clue(context=None)

    def get_coords(self, address: str) -> tuple[str, int, int]:
        """
        Find coordinates relative to an address
        :param address: Plain text address
        :return: Tuple with formatted address then latitude and longitude
        """
        response = requests.get(
            GOOGLE_ADDRESS_ENDPOINT,
            params={
                "address": address,
                "components": "country:FR",
                "key": GOOGLE_API_KEY,
            },
        ).json()
        if not response or response["status"] != "OK":
            return
        result = response["results"][0]
        return (
            result.get("formatted_address", ""),
            result["geometry"]["location"]["lat"],
            result["geometry"]["location"]["lng"],
        )

    def find_image(self, radius: int = 1000, **kwargs) -> Coordinates:
        """
        Try to find an image at random location.
        :param radius: Radius (in meters) to search for an image.
        :return: Country data and coordinates of the image.
        """
        image_found = False
        while not image_found:
            country = self.world.sample(n=1, weights="DENSITY")
            min_lng, min_lat, max_lng, max_lat = country.total_bounds
            coords = Coordinates(random.uniform(min_lat, max_lat), random.uniform(min_lng, max_lng))
            if coords.within(country.geometry.values[0]):
                image_found, coords = self.has_image(coords, radius)
        self.current = city = country.iloc[0].to_dict()
        coords.city, coords.department, coords.region = city["NAME"], city["DEPARTMENT"], city["REGION"]
        logger.info(f"{coords.city} ({coords.department} - {coords.region}): ({coords.lat}, {coords.lng})")
        return coords

    def has_image(self, coords: Coordinates, radius: int = 1000, **kwargs) -> tuple[bool, Coordinates]:
        """
        Check if the location has an image.
        :param coords: Coordinates.
        :param radius: Radius (in meters) to search for an image.
        :return: Tuple containing a boolean indicating if an image was found and the coordinates.
        """
        response = requests.get(
            f"{GOOGLE_STREETVIEW_ENDPOINT}/metadata",
            params={
                "location": f"{coords.lat},{coords.lng}",
                "radius": radius,
                "key": GOOGLE_API_KEY,
            },
        ).json()
        if response["status"] == "OVER_QUERY_LIMIT":
            raise Exception("You have exceeded your daily quota or per-second quota for this API.")
        if response["status"] == "REQUEST_DENIED":
            raise Exception("Your request was denied by the server. Check your API key.")
        if response["status"] == "UNKNOWN_ERROR":
            raise Exception("An unknown error occurred on the server.")
        if "Google" not in response["copyright"]:
            return False, coords
        image_found = response["status"] == "OK"
        if "location" in response:
            coords = Coordinates(response["location"]["lat"], response["location"]["lng"])
        return image_found, coords

    def get_image(
        self,
        coords: Coordinates,
        size: str = "640x640",
        heading: int = 0,
        pitch: int = 0,
        fov: int = 90,
        **kwargs,
    ) -> bytes:
        """
        Get an image from Google Street View Static API.
        :param coords: Coordinates.
        :param size: Image size.
        :param heading: Heading, defaults to 0.
        :param pitch: Pitch, defaults to 0.
        :param fov: Field of view, defaults to 90.
        :return: Image in bytes.
        """
        response = requests.get(
            GOOGLE_STREETVIEW_ENDPOINT,
            params={
                "location": f"{coords.lat},{coords.lng}",
                "size": size,
                "heading": heading,
                "pitch": pitch,
                "fov": fov,
                "key": GOOGLE_API_KEY,
            },
        )
        return Image.open(BytesIO(response.content))

    def get_map(
        self,
        center: Coordinates,
        zoom: int = 18,
        size: str = "640x640",
        scale: int = 1,
        format: str = "JPEG",
        maptype: str = "satellite",
        **kwargs,
    ):
        """
        Get a satellite image from Google Maps API.
        :param center: Coordinates.
        :param zoom: Zoom level
        :param size: Image size.
        :param scale: Image scale.
        :param format: Image file format.
        :param maptype: Map type
        """
        response = requests.get(
            GOOGLE_MAPS_ENDPOINT,
            params={
                "center": f"{center.lat},{center.lng}",
                "zoom": zoom,
                "size": size,
                "scale": scale,
                "format": format,
                "maptype": maptype,
                "key": GOOGLE_API_KEY,
            },
        )
        return Image.open(BytesIO(response.content)).convert("RGB")

    def create(self, directory: str = GEOGUESSR_IMAGES, **kwargs):
        """
        Create all images (satellite & streetview) from coordinates.
        :param directory: Output directory.
        """
        os.makedirs(directory, exist_ok=True)
        coords = self.find_image(**kwargs)
        img = self.get_map(coords)
        img.save(f"{directory}/_.jpg")
        for direction, heading in zip("NESW", range(0, 360, 90)):
            img = self.get_image(coords, heading=heading, **kwargs)
            draw = ImageDraw.Draw(img)
            draw.rectangle((0, 0, 12, 12), fill=(0, 0, 0))
            draw.text((0, 0), direction)
            img.save(f"{directory}/{direction}.jpg")
        return coords
