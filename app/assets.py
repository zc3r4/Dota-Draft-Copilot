from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

import requests

from .config import CACHE_DIR
from .data import ItemProfile

STEAM_CDN = "https://cdn.cloudflare.steamstatic.com"
USER_AGENT = "DotaDraftCopilot/1.0 (+local desktop helper)"
ITEM_DIR = CACHE_DIR / "items"


def item_icon_path(item: ItemProfile) -> Path:
    return ITEM_DIR / f"{item.key}.png"


def ensure_item_icons(
    items: Iterable[ItemProfile],
    progress: Callable[[int, int], None] | None = None,
    allow_download: bool = True,
) -> None:
    ITEM_DIR.mkdir(parents=True, exist_ok=True)
    todo: list[tuple[str, Path, str]] = []
    total = 0
    done = 0
    for item in items:
        if not item.img or item.key.startswith("recipe_"):
            continue
        total += 1
        path = item_icon_path(item)
        if path.exists() and path.stat().st_size > 300:
            done += 1
            continue
        if not allow_download:
            continue
        url = STEAM_CDN + item.img.split("?")[0]
        todo.append((item.key, path, url))
    if progress:
        progress(min(done, total), total)
    if not todo:
        return

    def fetch(entry: tuple[str, Path, str]) -> bool:
        _key, path, url = entry
        try:
            response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            if len(response.content) < 300:
                return False
            path.write_bytes(response.content)
            return True
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(fetch, entry) for entry in todo]
        for future in as_completed(futures):
            done += 1
            future.result()
            if progress and done % 8 == 0:
                progress(min(done, total), total)
    if progress:
        progress(min(done, total), total)
