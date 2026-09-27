import io
from pathlib import Path

from PIL import Image

from riftlift import __version__
from riftlift.config import Game, Paths
from riftlift.metadata import (
    USER_AGENT,
    CatalogMetadata,
    _composite,
    fetch_owned_metadata,
    generate_artwork,
    parse_catalog_html,
    parse_steam_catalog,
    populate_game_metadata,
)


def test_user_agent_tracks_package_version() -> None:
    assert USER_AGENT.startswith(f"RiftLift/{__version__} ")


def test_fetch_owned_metadata_only_hits_the_network_once(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    calls = []

    def fake_fetch(app_id: str) -> CatalogMetadata:
        calls.append(app_id)
        return CatalogMetadata(
            "Lone Echo", "https://example/lone-echo", "", "Ready At Dawn", "", [], ""
        )

    monkeypatch.setattr("riftlift.metadata.fetch_catalog_metadata", fake_fetch)

    first = fetch_owned_metadata(paths, "123456789")
    second = fetch_owned_metadata(paths, "123456789")

    assert calls == ["123456789"]
    assert (
        first
        == second
        == CatalogMetadata(
            "Lone Echo", "https://example/lone-echo", "", "Ready At Dawn", "", [], ""
        )
    )


def test_fetch_owned_metadata_refresh_bypasses_the_cache(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    calls = []

    def fake_fetch(app_id: str) -> CatalogMetadata:
        calls.append(app_id)
        return CatalogMetadata(
            f"Lone Echo #{len(calls)}",
            "https://example/lone-echo",
            "",
            "Ready At Dawn",
            "",
            [],
            "",
        )

    monkeypatch.setattr("riftlift.metadata.fetch_catalog_metadata", fake_fetch)

    first = fetch_owned_metadata(paths, "123456789")
    second = fetch_owned_metadata(paths, "123456789", refresh=True)

    assert calls == ["123456789", "123456789"]
    assert first.name == "Lone Echo #1"
    assert second.name == "Lone Echo #2"


def test_parse_meta_json_ld_catalog() -> None:
    payload = """
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
    """
    result = parse_catalog_html(payload, "123456789")
    assert result.name == "Example VR"
    assert result.developer == "Example Lab"
    assert result.publisher == "Example Publisher"
    assert result.genres == ["Action", "Narrative"]
    assert result.image_url == "https://cdn.example/art.webp"


def test_parse_steam_catalog() -> None:
    result = parse_steam_catalog(
        {
            "1920760": {
                "success": True,
                "data": {
                    "name": "StereoPaint",
                    "short_description": "Paint <b>in VR</b>.",
                    "developers": ["Millipede Software LLC"],
                    "publishers": ["Example Publisher"],
                    "genres": [{"description": "Design & Illustration"}],
                    "header_image": "https://steam.example/header.jpg",
                },
            }
        },
        "1920760",
    )
    assert result.name == "StereoPaint"
    assert result.description == "Paint in VR."
    assert result.developer == "Millipede Software LLC"
    assert result.publisher == "Example Publisher"
    assert result.genres == ["Design & Illustration"]
    assert result.store_url == "https://store.steampowered.com/app/1920760/"
    assert "library_600x900_2x.jpg" in result.artwork_urls["portrait"]


def test_generate_all_steam_artwork_sizes(tmp_path: Path) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    game = Game(
        "example", "Example VR", "123", "example", str(tmp_path), "game.exe", []
    )
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


def test_composite_never_crops_the_source_image() -> None:
    source = Image.new("RGB", (400, 800), "red")
    for y in range(390, 410):
        for x in range(400):
            source.putpixel((x, y), (0, 255, 0))

    result = _composite(source, (1920, 620))

    assert result.size == (1920, 620)
    assert result.getpixel((960, 310)) == (0, 255, 0)


def test_metadata_catalog_uses_validated_game_source(
    tmp_path: Path, monkeypatch
) -> None:
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    game = Game(
        "example",
        "Example",
        "123",
        "steam.app.custom",
        str(tmp_path),
        "game.exe",
        [],
        source="meta",
    )
    metadata = CatalogMetadata(
        "Example", "meta", "description", "developer", "", [], ""
    )
    calls = []
    monkeypatch.setattr(
        "riftlift.metadata.fetch_catalog_metadata",
        lambda app_id: calls.append(("meta", app_id)) or metadata,
    )
    monkeypatch.setattr(
        "riftlift.metadata.fetch_steam_catalog_metadata",
        lambda app_id: calls.append(("steam", app_id)) or metadata,
    )

    populate_game_metadata(paths, game)

    assert calls == [("meta", "123")]


def test_metadata_ignores_a_meta_games_steam_shortcut_id(
    tmp_path: Path, monkeypatch
) -> None:
    # A Meta/local game that has been synced to Steam ("Add to Steam") gets
    # a steam_app_id too, but that's Steam's own generated shortcut id, not
    # a real store id - it must never be used to fetch Meta's catalog.
    paths = Paths(
        tmp_path / "data",
        tmp_path / "cache",
        tmp_path / "config",
        tmp_path / "games",
        tmp_path / "prefix",
        tmp_path / "tools",
    )
    game = Game(
        "example",
        "Example",
        "123456789",
        "meta.example",
        str(tmp_path),
        "game.exe",
        [],
        source="meta",
        steam_app_id=3820161062,
    )
    metadata = CatalogMetadata(
        "Example", "meta", "description", "developer", "", [], ""
    )
    calls = []
    monkeypatch.setattr(
        "riftlift.metadata.fetch_catalog_metadata",
        lambda app_id: calls.append(app_id) or metadata,
    )

    populate_game_metadata(paths, game)

    assert calls == ["123456789"]
