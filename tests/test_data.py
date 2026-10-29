from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.data import (
    HERO_TAG_KEYWORDS,
    DataError,
    JsonCache,
    OpenDota,
    _keyword_tags,
    build_hero_profiles,
    build_item_profiles,
    plural_ru,
)


def test_offline_without_cache_raises(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path, default_ttl=3600)
    api = OpenDota(cache, allow_network=False)
    with pytest.raises(DataError):
        api.heroes()


def test_offline_uses_stale_cache(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path, default_ttl=3600)
    cache.put("heroes", [{"id": 1, "localized_name": "Axe"}])
    # Age the entry so it is definitely stale.
    path = cache._path("heroes")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["ts"] = 0
    path.write_text(json.dumps(raw), encoding="utf-8")

    api = OpenDota(cache, allow_network=False)
    heroes = api.heroes()
    assert heroes[0]["localized_name"] == "Axe"
    assert "Офлайн-режим" in api.last_warning


def test_keyword_tags_use_word_boundaries() -> None:
    tags = _keyword_tags("Deals damage equal to current health.", HERO_TAG_KEYWORDS)
    assert "heal" not in tags

    tags = _keyword_tags("The effect fades after 3 seconds.", HERO_TAG_KEYWORDS)
    assert "invis" not in tags

    tags = _keyword_tags("Enemy creeps are dragged to Pudge.", HERO_TAG_KEYWORDS)
    assert "summon" not in tags

    tags = _keyword_tags("The unit is stunned and slowed.", HERO_TAG_KEYWORDS)
    assert {"stun", "slow"} <= tags


def test_russian_plural_forms() -> None:
    forms = ("матч", "матча", "матчей")
    assert plural_ru(1, *forms) == "матч"
    assert plural_ru(2, *forms) == "матча"
    assert plural_ru(4, *forms) == "матча"
    assert plural_ru(5, *forms) == "матчей"
    assert plural_ru(11, *forms) == "матчей"
    assert plural_ru(21, *forms) == "матч"
    assert plural_ru(22, *forms) == "матча"


def test_finished_items_are_not_components() -> None:
    raw = {
        "force_staff": {
            "id": 1,
            "dname": "Force Staff",
            "cost": 2200,
            "components": [],
            "created": True,
        },
        "hurricane_pike": {
            "id": 2,
            "dname": "Hurricane Pike",
            "cost": 4550,
            "components": ["force_staff"],
            "created": True,
        },
        "broadsword": {"id": 3, "dname": "Broadsword", "cost": 1000},
        "bfury": {"id": 4, "dname": "Battle Fury", "cost": 3900, "components": ["broadsword"]},
    }
    items = build_item_profiles(raw)
    assert items[1].component is False
    assert items[3].component is True


def test_physical_damage_mention_is_not_physical_immunity() -> None:
    raw = {
        "satanic": {
            "id": 20,
            "dname": "Satanic",
            "cost": 5100,
            "abilities": [
                {
                    "title": "Lifesteal",
                    "description": "Heals the attacker for a percentage of physical damage dealt.",
                }
            ],
        },
        "ghost": {
            "id": 21,
            "dname": "Ghost Scepter",
            "cost": 1500,
            "abilities": [
                {
                    "title": "Ghost Form",
                    "description": "Becoming immune to physical damage, but unable to attack.",
                }
            ],
        },
    }
    items = build_item_profiles(raw)
    assert "physical_immune" not in items[20].tags
    assert "physical_immune" in items[21].tags


def test_common_finished_items_are_not_hidden_as_components() -> None:
    raw = {
        "basher": {"id": 30, "dname": "Skull Basher", "cost": 2875},
        "abyssal_blade": {
            "id": 31,
            "dname": "Abyssal Blade",
            "cost": 6400,
            "components": ["basher"],
        },
        "diffusal_blade": {"id": 32, "dname": "Diffusal Blade", "cost": 2500},
        "disperser": {
            "id": 33,
            "dname": "Disperser",
            "cost": 5700,
            "components": ["diffusal_blade"],
        },
    }
    items = build_item_profiles(raw)
    assert items[30].component is False
    assert items[32].component is False


def test_magical_armor_is_not_physical_armor() -> None:
    raw = {
        "glimmer_cape": {
            "id": 10,
            "dname": "Glimmer Cape",
            "cost": 2150,
            "attrib": [
                {"key": "bonus_magical_armor", "value": 20},
                {"key": "barrier_block", "value": 375},
            ],
        }
    }
    glimmer = build_item_profiles(raw)[10]
    assert "armor" not in glimmer.tags
    assert "magic_resist" in glimmer.tags
    assert glimmer.stats.get("magic_resist") == 20


def test_hero_profiles_take_complexity_from_game_data() -> None:
    heroes = [
        {
            "id": 1,
            "name": "npc_dota_hero_axe",
            "localized_name": "Axe",
            "roles": ["Initiator", "Durable"],
            "primary_attr": "str",
            "attack_type": "Melee",
        }
    ]
    profiles = build_hero_profiles(
        heroes,
        [],
        {},
        {},
        {"npc_dota_hero_axe": 1},
    )
    assert profiles[1].complexity == 1
    assert profiles[1].name == "Axe"

    without_data = build_hero_profiles(heroes, [], {}, {})
    assert without_data[1].complexity == 0
