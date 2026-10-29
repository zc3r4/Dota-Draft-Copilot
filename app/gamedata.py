"""Authoritative data extracted from the local Dota 2 game files.

Neutral-item tiers/unlock times and hero complexity ratings are imported from
the VPK.  Both change with patches more often than third-party tables are
refreshed, and a wrong tier or difficulty makes the recommendation filters
misleading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .config import DATA_DIR
from .data import ItemProfile

NEUTRAL_DATA_PATH = DATA_DIR / "neutral_items.json"
HERO_COMPLEXITY_PATH = DATA_DIR / "hero_complexity.json"


@dataclass(frozen=True)
class NeutralPool:
    times: dict[int, int]
    tiers: dict[int, tuple[str, ...]]
    source: str = ""
    client_version: str = ""
    version_date: str = ""

    @property
    def tier_by_key(self) -> dict[str, int]:
        return {key: tier for tier, keys in self.tiers.items() for key in keys}

    def all_keys(self) -> set[str]:
        return {key for keys in self.tiers.values() for key in keys}


def load_neutral_pool(path: Path = NEUTRAL_DATA_PATH) -> NeutralPool | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tiers: dict[int, tuple[str, ...]] = {}
    times: dict[int, int] = {}
    for tier_key, block in (raw.get("tiers") or {}).items():
        try:
            tier = int(tier_key)
        except (TypeError, ValueError):
            continue
        keys = tuple(str(k) for k in (block.get("items") or []))
        if not keys:
            continue
        tiers[tier] = keys
        try:
            times[tier] = int(block.get("start_time") or 0)
        except (TypeError, ValueError):
            times[tier] = 0
    if not tiers:
        return None
    return NeutralPool(
        times=times,
        tiers=tiers,
        source=str(raw.get("source") or ""),
        client_version=str(raw.get("client_version") or ""),
        version_date=str(raw.get("version_date") or ""),
    )


def load_hero_complexity(path: Path = HERO_COMPLEXITY_PATH) -> dict[str, int]:
    """Load Valve's 1-3 complexity ratings keyed by internal hero name."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    result: dict[str, int] = {}
    for name, value in (raw.get("heroes") or {}).items():
        try:
            rating = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= rating <= 3:
            result[str(name)] = rating
    return result


def apply_neutral_pool(items: dict[int, ItemProfile], pool: NeutralPool | None) -> dict[int, ItemProfile]:
    """Fit OpenDota neutral profiles to the pool from the game files.

    Items removed by Valve are demoted to regular items (``neutral=False``) so
    they can never be recommended as a neutral choice.
    """
    if pool is None:
        return items
    tier_by_key = pool.tier_by_key
    result: dict[int, ItemProfile] = {}
    for iid, item in items.items():
        if not item.neutral:
            result[iid] = item
            continue
        tier = tier_by_key.get(item.key)
        if tier is None:
            result[iid] = replace(item, neutral=False, tier=None)
        else:
            result[iid] = replace(item, tier=tier)
    return result
