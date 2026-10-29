"""Import authoritative game data from the local Dota 2 VPK.

Currently imported:

* neutral item tiers and unlock times from ``scripts/npc/neutral_items.txt``;
* hero complexity (1-3) from ``scripts/npc/heroes/npc_dota_hero_*.txt``.

Run with the venv Python (the optional ``vpk`` package is required):

    .venv\\Scripts\\python.exe tools\\import_game_data.py

The generated JSON files are committed to the project, so the application
itself does not need the VPK or the ``vpk`` package at runtime.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR  # noqa: E402
from app.steam import find_dota_root, read_patch_info  # noqa: E402

OUT_PATH = DATA_DIR / "neutral_items.json"
HERO_COMPLEXITY_PATH = DATA_DIR / "hero_complexity.json"

TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def strip_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        # Valve files use // comments; the data we need never contains //.
        line = line.split("//", 1)[0]
        lines.append(line)
    return "\n".join(lines)


def parse_kv(text: str) -> dict[str, Any]:
    """Parse a Valve KeyValues document into nested dicts."""
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(strip_comments(text)):
        tokens.append(match.group(1) if match.group(1) is not None else match.group(2))

    pos = 0

    def parse_block() -> dict[str, Any]:
        nonlocal pos
        result: dict[str, Any] = {}
        while pos < len(tokens):
            token = tokens[pos]
            pos += 1
            if token == "}":
                break
            if token == "{":
                continue
            key = token
            if pos >= len(tokens):
                result[key] = ""
                break
            nxt = tokens[pos]
            if nxt == "{":
                pos += 1
                result[key] = parse_block()
            else:
                pos += 1
                result[key] = nxt
        return result

    root: dict[str, Any] = {}
    while pos < len(tokens):
        if tokens[pos] == "{":
            pos += 1
            continue
        key = tokens[pos]
        pos += 1
        if pos < len(tokens) and tokens[pos] == "{":
            pos += 1
            root[key] = parse_block()
        elif pos < len(tokens):
            root[key] = tokens[pos]
            pos += 1
    return root


def parse_time(value: str) -> int:
    match = re.match(r"\s*(\d+):(\d+)", str(value))
    if not match:
        return 0
    return int(match.group(1)) * 60 + int(match.group(2))


def read_hero_complexities(package: Any) -> dict[str, int]:
    """Read Valve's 1-3 complexity rating for every playable hero.

    Hero definitions are split into ``scripts/npc/heroes/npc_dota_hero_*.txt``
    in the current client.  ``target_dummy`` has no complexity and is skipped.
    """
    complexities: dict[str, int] = {}
    for name in package:
        if not name.startswith("scripts/npc/heroes/npc_dota_hero_") or not name.endswith(".txt"):
            continue
        text = package.get_file(name).read().decode("utf-8-sig", errors="replace")
        match = re.search(r'"Complexity"\s+"([^"]+)"', text, re.IGNORECASE)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if 1 <= value <= 3:
            complexities[Path(name).stem] = value
    return dict(sorted(complexities.items()))


def main() -> None:
    root = find_dota_root()
    if not root:
        raise SystemExit("Dota 2 not found; pass no arguments and start Steam once.")
    vpk_path = root / "game" / "dota" / "pak01_dir.vpk"
    if not vpk_path.exists():
        raise SystemExit(f"VPK not found: {vpk_path}")

    try:
        import vpk  # type: ignore
    except ImportError:
        raise SystemExit("Install the optional 'vpk' package: .venv\\Scripts\\pip install vpk")

    package = vpk.VPK(str(vpk_path))
    try:
        raw = package.get_file("scripts/npc/neutral_items.txt").read()
    except Exception as exc:
        raise SystemExit(f"Cannot read neutral_items.txt from VPK: {exc}") from exc

    text = raw.decode("utf-8-sig", errors="replace")
    parsed = parse_kv(text).get("neutral_items") or {}
    tiers_raw = parsed.get("neutral_tiers") or {}

    tiers: dict[str, dict[str, Any]] = {}
    for tier_key in ("1", "2", "3", "4", "5"):
        block = tiers_raw.get(tier_key) or {}
        items = block.get("items") or {}
        keys = [
            key[5:] if key.startswith("item_") else key
            for key in items
            if key.startswith("item_") and "enhancement" not in key and "token" not in key
        ]
        tiers[tier_key] = {
            "start_time": parse_time(block.get("start_time", "0:00")),
            "items": sorted(keys),
        }

    patch = read_patch_info(root)
    payload = {
        "source": "scripts/npc/neutral_items.txt",
        "vpk": "game/dota/pak01_dir.vpk",
        "client_version": patch.get("ClientVersion", ""),
        "version_date": patch.get("VersionDate", ""),
        "tiers": tiers,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    for tier, block in tiers.items():
        print(f"  T{tier}: {len(block['items'])} items from {block['start_time']}s")

    heroes = read_hero_complexities(package)
    hero_payload = {
        "source": "scripts/npc/heroes/npc_dota_hero_*.txt (Complexity)",
        "vpk": "game/dota/pak01_dir.vpk",
        "client_version": patch.get("ClientVersion", ""),
        "version_date": patch.get("VersionDate", ""),
        "heroes": heroes,
    }
    HERO_COMPLEXITY_PATH.write_text(
        json.dumps(hero_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {HERO_COMPLEXITY_PATH} ({len(heroes)} heroes)")


if __name__ == "__main__":
    main()
