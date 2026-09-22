from pathlib import Path


def configure_mod_loaders(environment: dict[str, str], executable: Path) -> None:
    """Enable installed proxy loaders for this launch without editing the prefix."""
    try:
        entries = {path.name.casefold(): path for path in executable.parent.iterdir()}
    except OSError:
        return

    proxies: set[str] = set()
    for folder, names in (
        ("melonloader", ("version", "winhttp", "winmm")),
        ("bepinex", ("winhttp",)),
    ):
        loader = entries.get(folder)
        if loader is not None and loader.is_dir():
            proxies.update(
                name
                for name in names
                if (dll := entries.get(f"{name}.dll")) is not None and dll.is_file()
            )
    if not proxies:
        return

    existing = environment.get("WINEDLLOVERRIDES", "")
    explicit: set[str] = set()
    for rule in existing.split(";"):
        names, separator, _order = rule.partition("=")
        if separator:
            for name in names.split(","):
                name = name.strip().casefold().removesuffix(".dll")
                # Wine accepts both grouped names and *name overrides.
                explicit.add(name if name == "*" else name.lstrip("*"))
    if "*" in explicit:
        return
    additions = [f"{name}=n,b" for name in sorted(proxies - explicit)]
    if additions:
        environment["WINEDLLOVERRIDES"] = ";".join(
            [existing.rstrip(";"), *additions] if existing else additions
        )
