from __future__ import annotations

import html
import io
import json
import textwrap
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .config import Game, Paths
from .util import RiftLiftError


STORE_URL = "https://www.meta.com/experiences/pcvr/{app_id}/"
USER_AGENT = "RiftLift/0.2 (+https://github.com/Villagers654/RiftLift)"


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capturing = False
        self._chunks: list[str] = []
        self.documents: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "script" and values.get("type", "").lower() == "application/ld+json":
            self._capturing = True
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._capturing:
            return
        self._capturing = False
        try:
            value = json.loads("".join(self._chunks))
        except json.JSONDecodeError:
            return
        if isinstance(value, dict):
            self.documents.append(value)


@dataclass(frozen=True, slots=True)
class CatalogMetadata:
    name: str
    store_url: str
    description: str
    developer: str
    publisher: str
    genres: list[str]
    image_url: str


def _name(value: object, entities: dict[str, dict[str, Any]]) -> str:
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("name"), str):
        return value["name"]
    reference = value.get("@id")
    entity = entities.get(reference, {}) if isinstance(reference, str) else {}
    return str(entity.get("name") or "")


def parse_catalog_html(payload: str, app_id: str) -> CatalogMetadata:
    parser = _JsonLdParser()
    parser.feed(payload)
    graph: list[dict[str, Any]] = []
    for document in parser.documents:
        values = document.get("@graph", [document])
        if isinstance(values, list):
            graph.extend(value for value in values if isinstance(value, dict))
    entities = {
        value["@id"]: value
        for value in graph
        if isinstance(value.get("@id"), str)
    }
    application = next(
        (
            value
            for value in graph
            if str(value.get("sku", "")) == app_id
            and "SoftwareApplication" in (
                value.get("@type") if isinstance(value.get("@type"), list) else [value.get("@type")]
            )
        ),
        None,
    )
    if application is None:
        raise RiftLiftError(f"Meta's store page has no catalog metadata for app {app_id}")
    images = application.get("image", [])
    if not isinstance(images, list):
        images = [images]
    image_url = ""
    for image in images:
        if isinstance(image, str):
            image_url = image
        elif isinstance(image, dict):
            image_url = str(image.get("contentUrl") or image.get("url") or image.get("@id") or "")
        if image_url:
            break
    categories = application.get("applicationSubCategory", [])
    if isinstance(categories, str):
        categories = [categories]
    return CatalogMetadata(
        name=str(application.get("name") or ""),
        store_url=str(application.get("url") or STORE_URL.format(app_id=app_id)),
        description=html.unescape(str(application.get("description") or "")).strip(),
        developer=_name(application.get("creator"), entities),
        publisher=_name(application.get("publisher"), entities),
        genres=[str(value) for value in categories if str(value).strip()],
        image_url=html.unescape(image_url),
    )


def fetch_catalog_metadata(app_id: str) -> CatalogMetadata:
    request = urllib.request.Request(STORE_URL.format(app_id=app_id), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except (OSError, TimeoutError) as error:
        raise RiftLiftError(f"could not read Meta catalog metadata: {error}") from error
    return parse_catalog_html(payload, app_id)


def _request_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except (OSError, TimeoutError) as error:
        raise RiftLiftError(f"could not download Meta catalog artwork: {error}") from error


def _portrait(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    background = ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(max(size) / 35))
    foreground = source.convert("RGB").copy()
    foreground.thumbnail((int(size[0] * 0.94), int(size[1] * 0.94)), Image.Resampling.LANCZOS)
    background.paste(foreground, ((size[0] - foreground.width) // 2, (size[1] - foreground.height) // 2))
    return background


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _logo(name: str, size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text = "\n".join(textwrap.wrap(name, width=24))
    font_size = 150
    while font_size >= 28:
        font = _font(font_size)
        box = draw.multiline_textbbox((0, 0), text, font=font, align="center", stroke_width=5)
        if box[2] - box[0] <= size[0] - 20 and box[3] - box[1] <= size[1] - 20:
            break
        font_size -= 4
    box = draw.multiline_textbbox((0, 0), text, font=font, align="center", stroke_width=5)
    at = ((size[0] - (box[2] - box[0])) // 2, (size[1] - (box[3] - box[1])) // 2 - box[1])
    draw.multiline_text(at, text, font=font, fill="white", stroke_width=5, stroke_fill="black", align="center")
    return image


def generate_artwork(paths: Paths, game: Game, image_payload: bytes) -> dict[str, str]:
    destination = paths.data / "artwork" / game.slug
    destination.mkdir(parents=True, exist_ok=True)
    try:
        source = Image.open(io.BytesIO(image_payload))
        source.load()
    except (OSError, ValueError) as error:
        raise RiftLiftError(f"Meta catalog artwork could not be decoded: {error}") from error
    images = {
        "grid": ImageOps.fit(source.convert("RGB"), (920, 430), method=Image.Resampling.LANCZOS),
        "portrait": _portrait(source, (600, 900)),
        "hero": ImageOps.fit(source.convert("RGB"), (1920, 620), method=Image.Resampling.LANCZOS),
        "icon": ImageOps.fit(source.convert("RGB"), (256, 256), method=Image.Resampling.LANCZOS),
        "logo": _logo(game.name, (1200, 400)),
    }
    result: dict[str, str] = {}
    for kind, image in images.items():
        target = destination / f"{kind}.png"
        image.save(target, format="PNG", optimize=True)
        result[kind] = str(target)
    return result


def populate_game_metadata(paths: Paths, game: Game, *, refresh: bool = False) -> Game:
    complete = all((game.description, game.developer, game.store_url, game.artwork))
    if complete and not refresh:
        return game
    metadata = fetch_catalog_metadata(game.app_id)
    if metadata.name:
        game.name = metadata.name
    game.store_url = metadata.store_url
    game.description = metadata.description
    game.developer = metadata.developer
    game.publisher = metadata.publisher
    game.genres = metadata.genres
    if metadata.image_url and (refresh or not game.artwork):
        game.artwork = generate_artwork(paths, game, _request_bytes(metadata.image_url))
    game.save(paths)
    return game
