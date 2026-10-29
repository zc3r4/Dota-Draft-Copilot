from __future__ import annotations

import json
from pathlib import Path

from app.data import ItemProfile
from app.gamedata import (
    HERO_COMPLEXITY_PATH,
    NEUTRAL_DATA_PATH,
    apply_neutral_pool,
    load_hero_complexity,
    load_neutral_pool,
)
from app.steam import gsi_cfg_path, gsi_cfg_text, install_gsi, is_gsi_installed, read_patch_info


def item(key: str, *, neutral: bool, tier: int | None = None) -> ItemProfile:
    return ItemProfile(
        id=abs(hash(key)) % 100000,
        key=key,
        name=key,
        cost=0 if neutral else 500,
        tier=tier,
        tags=frozenset(),
        neutral=neutral,
    )


def test_neutral_pool_from_game_files() -> None:
    pool = load_neutral_pool()
    assert pool is not None
    assert pool.source
    assert pool.client_version
    assert set(pool.tiers) == {1, 2, 3, 4, 5}
    assert pool.times[1] == 0
    assert pool.times[2] == 900
    assert pool.times[3] == 1500
    assert pool.times[4] == 2100
    assert pool.times[5] == 3600
    assert len(pool.tiers[1]) == 12
    assert len(pool.tiers[5]) == 10
    assert sum(len(keys) for keys in pool.tiers.values()) == 49
    assert "grove_bow" not in pool.all_keys()


def test_missing_pool_returns_none(tmp_path: Path) -> None:
    assert load_neutral_pool(tmp_path / "nope.json") is None


def test_apply_pool_fixes_tiers_and_demotes_removed() -> None:
    pool = load_neutral_pool()
    assert pool is not None
    in_pool = next(iter(pool.tiers[3]))

    items = {
        1: item(in_pool, neutral=True, tier=1),
        2: item("grove_bow", neutral=True, tier=2),
        3: item("boots", neutral=False),
    }
    result = apply_neutral_pool(items, pool)

    assert result[1].tier == 3
    assert result[1].neutral is True
    assert result[2].neutral is False
    assert result[2].tier is None
    assert result[3].cost == 500 and result[3].neutral is False


def test_neutral_data_file_matches_loader() -> None:
    assert NEUTRAL_DATA_PATH.exists()
    pool = load_neutral_pool(NEUTRAL_DATA_PATH)
    assert pool is not None
    assert pool.tier_by_key.popitem()


def test_hero_complexity_from_game_files() -> None:
    complexity = load_hero_complexity()
    assert HERO_COMPLEXITY_PATH.exists()
    assert len(complexity) == 127
    assert set(complexity.values()) == {1, 2, 3}
    assert complexity["npc_dota_hero_antimage"] == 1
    assert complexity["npc_dota_hero_arc_warden"] == 3
    assert "npc_dota_hero_target_dummy" not in complexity


def test_hero_complexity_loader_ignores_invalid_values(tmp_path: Path) -> None:
    path = tmp_path / "complexity.json"
    path.write_text(
        json.dumps({"heroes": {"a": 1, "b": 9, "c": "bad", "d": 3}}),
        encoding="utf-8",
    )
    assert load_hero_complexity(path) == {"a": 1, "d": 3}
    assert load_hero_complexity(tmp_path / "missing.json") == {}


def test_install_gsi(tmp_path: Path) -> None:
    root = tmp_path / "dota 2 beta"
    path = install_gsi(root, 32123)
    assert path == gsi_cfg_path(root)
    assert path.exists()
    assert is_gsi_installed(root, 32123)
    assert not is_gsi_installed(root, 32124)
    assert not is_gsi_installed(None, 32123)
    text = gsi_cfg_text(40000)
    assert "127.0.0.1:40000" in text
    assert '"draft"' in text


def test_read_patch_info(tmp_path: Path) -> None:
    root = tmp_path / "dota"
    inf = root / "game" / "dota" / "steam.inf"
    inf.parent.mkdir(parents=True)
    inf.write_text(
        "ClientVersion=6966\r\nVersionDate=Oct 22 2026\r\nProductName=Dota 2\r\n",
        encoding="utf-8",
    )
    info = read_patch_info(root)
    assert info["ClientVersion"] == "6966"
    assert info["VersionDate"] == "Oct 22 2026"
    assert read_patch_info(None) == {}
