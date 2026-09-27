import re
from pathlib import Path


def validate_dll_overrides(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("DLL overrides must be text")
    for rule in value.split(";"):
        if not rule.strip():
            continue
        names, separator, order = rule.partition("=")
        if (
            not separator
            or any(
                re.fullmatch(r"\*?[a-zA-Z0-9_.-]+|\*", name.strip()) is None
                for name in names.split(",")
            )
            or order.replace(" ", "").lower() not in {"", "n", "b", "n,b", "b,n"}
        ):
            raise ValueError("Use DLL overrides such as version=n,b;winhttp=n,b")


def _dll_name(value: str) -> str:
    name = value.strip().casefold().removesuffix(".dll")
    return name if name == "*" else name.lstrip("*")


def apply_dll_overrides(environment: dict[str, str], overrides: str) -> None:
    """Let saved per-game choices override inherited rules, including groups."""
    validate_dll_overrides(overrides)
    rules = [rule.strip() for rule in overrides.split(";") if rule.strip()]
    if not rules:
        return
    chosen = {
        _dll_name(name) for rule in rules for name in rule.partition("=")[0].split(",")
    }
    retained = []
    for rule in environment.get("WINEDLLOVERRIDES", "").split(";"):
        names, separator, order = rule.partition("=")
        remaining = [name for name in names.split(",") if _dll_name(name) not in chosen]
        if separator and remaining and "*" not in chosen:
            retained.append(f"{','.join(remaining)}={order}")
    environment["WINEDLLOVERRIDES"] = ";".join([*retained, *rules])


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
                # Wine accepts both grouped names and *name overrides.
                explicit.add(_dll_name(name))
    if "*" in explicit:
        return
    additions = [f"{name}=n,b" for name in sorted(proxies - explicit)]
    if additions:
        environment["WINEDLLOVERRIDES"] = ";".join(
            [existing.rstrip(";"), *additions] if existing else additions
        )
