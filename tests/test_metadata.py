import io
from pathlib import Path

from PIL import Image

from riftlift.config import Game, Paths
from riftlift.metadata import generate_artwork, parse_catalog_html


def test_parse_meta_json_ld_catalog() -> None:
    payload = '''
    <script type="application/ld+json">{
      "@graph": [
        {"@id": "developer", "name": "Example Lab"},
        {"@id": "publisher", "name": "Example Publisher"},
        {
          "@type": ["SoftwareApplication", "Product"],
          "sku": "123456789",
          "name": "Example VR",
          "url": "https://www.meta.com/experiences/pcvr/example/123456789/",
          "description": "A VR adventure.",
          "creator": {"@id": "developer"},
          "publisher": {"@id": "publisher"},
          "applicationSubCategory": ["Action", "Narrative"],
          "image": [{"contentUrl": "https://cdn.example/art.webp"}]
        }
      ]
    }</script>
    '''
    result = parse_catalog_html(payload, "123456789")
    assert result.name == "Example VR"
    assert result.developer == "Example Lab"
    assert result.publisher == "Example Publisher"
    assert result.genres == ["Action", "Narrative"]
    assert result.image_url == "https://cdn.example/art.webp"


def test_generate_all_steam_artwork_sizes(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    game = Game("example", "Example VR", "123", "example", str(tmp_path), "game.exe", [])
    source = Image.new("RGB", (1280, 720), "#b02020")
    payload = io.BytesIO()
    source.save(payload, format="PNG")
    artwork = generate_artwork(paths, game, payload.getvalue())
    expected = {
        "grid": (920, 430),
        "portrait": (600, 900),
        "hero": (1920, 620),
        "logo": (1200, 400),
        "icon": (256, 256),
    }
    assert set(artwork) == set(expected)
    for kind, size in expected.items():
        with Image.open(artwork[kind]) as image:
            assert image.size == size
