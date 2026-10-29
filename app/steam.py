from __future__ import annotations

import os
import re
import winreg
from pathlib import Path

DOTA_DIR_NAME = "dota 2 beta"
GSI_CFG_NAME = "gamestate_integration_draftcopilot.cfg"


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
            if steam_path:
                roots.append(Path(steam_path))
    except OSError:
        pass

    common = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ]
    for drive in "CDEFGH":
        common.append(Path(f"{drive}:\\Steam"))
        common.append(Path(f"{drive}:\\SteamLibrary"))
    for path in common:
        if path.exists():
            roots.append(path)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _library_folders(steam_root: Path) -> list[Path]:
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    folders: list[Path] = []
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return folders
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        raw = match.group(1).replace("\\\\", "\\")
        path = Path(raw)
        if path.exists():
            folders.append(path)
    return folders


def find_dota_root() -> Path | None:
    """Locate the Dota 2 install directory (the one containing game/dota)."""
    candidates: list[Path] = []
    for steam in _steam_roots():
        candidates.append(steam / "steamapps" / "common" / DOTA_DIR_NAME)
        for library in _library_folders(steam):
            candidates.append(library / "steamapps" / "common" / DOTA_DIR_NAME)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if (candidate / "game" / "dota" / "steam.inf").exists():
            return candidate
    return None


def read_patch_info(dota_root: Path | None) -> dict[str, str]:
    if not dota_root:
        return {}
    path = dota_root / "game" / "dota" / "steam.inf"
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    info: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            info[key.strip()] = value.strip()
    return info


def gsi_cfg_text(port: int) -> str:
    return f'''"Dota 2 Integration Configuration"
{{
    "uri"           "http://127.0.0.1:{port}/"
    "timeout"       "5.0"
    "buffer"        "0.1"
    "throttle"      "0.5"
    "heartbeat"     "10.0"
    "data"
    {{
        "provider"      "1"
        "map"           "1"
        "player"        "1"
        "hero"          "1"
        "abilities"     "1"
        "items"         "1"
        "draft"         "1"
        "wearables"     "0"
    }}
}}
'''


def gsi_cfg_path(dota_root: Path) -> Path:
    return dota_root / "game" / "dota" / "cfg" / "gamestate_integration" / GSI_CFG_NAME


def install_gsi(dota_root: Path, port: int) -> Path:
    path = gsi_cfg_path(dota_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(gsi_cfg_text(port), encoding="utf-8")
    return path


def is_gsi_installed(dota_root: Path | None, port: int) -> bool:
    if not dota_root:
        return False
    path = gsi_cfg_path(dota_root)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return f"127.0.0.1:{port}" in text
