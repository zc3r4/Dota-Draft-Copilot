from __future__ import annotations

from app.data import HeroProfile, ItemProfile
from app.recommend import (
    ItemEngine,
    NeutralEngine,
    RecommendationEngine,
    analyze_team,
    role_match,
    threat_counts,
)


def hero(
    hero_id: int,
    name: str,
    *,
    roles: set[str] | None = None,
    tags: set[str] | None = None,
    attr: str = "agi",
    pub_wr: float = 0.5,
    pub_pick: int = 5000,
) -> HeroProfile:
    return HeroProfile(
        id=hero_id,
        name=name,
        roles=frozenset(roles or set()),
        primary_attr=attr,
        attack_type="Melee",
        tags=frozenset(tags or set()),
        pub_wr=pub_wr,
        pub_pick=pub_pick,
    )


def item(
    item_id: int,
    name: str,
    *,
    cost: int = 1000,
    tier: int | None = None,
    neutral: bool = False,
    tags: set[str] | None = None,
    stats: dict[str, float] | None = None,
    component: bool = False,
    key: str = "",
) -> ItemProfile:
    return ItemProfile(
        id=item_id,
        key=key or name.lower().replace(" ", "_").replace("'", ""),
        name=name,
        cost=0 if neutral else cost,
        tier=tier,
        tags=frozenset(tags or set()),
        neutral=neutral,
        stats=stats or {},
        component=component,
    )


# ---------------------------------------------------------------------------
# Role filtering
# ---------------------------------------------------------------------------


def test_role_match_rules() -> None:
    dual = hero(1, "Windranger", roles={"Carry", "Support"})
    assert role_match(dual, "carry") is False
    assert role_match(dual, "support") is True
    assert role_match(dual, "hard_support") is False

    tank = hero(2, "Axe", roles={"Initiator", "Durable"})
    assert role_match(tank, "offlane") is True
    assert role_match(tank, "carry") is False
    assert role_match(tank, "mid") is False

    support = hero(3, "Crystal Maiden", roles={"Support"})
    assert role_match(support, "hard_support") is True
    assert role_match(support, "mid") is False
    assert role_match(support, "") is True


def test_analyze_team_reports_gaps() -> None:
    needs = analyze_team([hero(1, "Only Carry", roles={"Carry"}, tags={"physical"})])
    keys = {need.key for need in needs}
    assert "initiation" in keys
    assert "disable" in keys
    assert "support" in keys
    assert "carry" not in keys


def test_threat_counts_derives_disable_and_illusion() -> None:
    counts = threat_counts(
        [
            hero(1, "A", tags={"stun", "magic", "nuker"}),
            hero(2, "B", tags={"summon"}),
            hero(3, "C", tags={"silence"}),
        ]
    )
    assert counts["disable"] == 2
    assert counts["burst"] == 1
    assert counts["illusion"] == 1
    assert counts["summon"] == 1


# ---------------------------------------------------------------------------
# Counter picks
# ---------------------------------------------------------------------------


def matchup_fixture():
    profiles = {
        1: hero(1, "Axe", roles={"Initiator", "Durable"}, tags={"stun", "tanky"}),
        2: hero(2, "Zeus", roles={"Nuker", "Support"}, tags={"magic", "nuker"}),
        10: hero(10, "Counter", roles={"Carry"}, tags={"physical"}, pub_wr=0.55),
        11: hero(11, "Healer", roles={"Support"}, tags={"save", "heal"}, pub_wr=0.50),
        12: hero(12, "Ally", roles={"Carry"}, tags={"physical"}),
    }
    matchups = {
        1: {10: (0.40, 600), 11: (0.45, 600), 12: (0.50, 600)},
        2: {10: (0.42, 550), 11: (0.48, 550), 12: (0.50, 550)},
    }
    return profiles, matchups


def test_counter_recommendation_filters_role_and_picks() -> None:
    profiles, matchups = matchup_fixture()
    engine = RecommendationEngine(profiles, matchups, role="carry")
    result = engine.recommend([1, 2], [12])

    ids = [candidate.hero_id for candidate in result.candidates]
    assert ids == [10]  # support hero filtered, picked ally excluded
    best = result.candidates[0]
    assert best.advantage > 0
    assert best.lower_bound > 0
    assert best.confidence == "high"
    assert set(best.per_enemy) == {1, 2}
    assert best.role_match is True
    assert any("Counter" in reason or "против" in reason for reason in best.reasons)
    assert result.total_games == 2300


def test_counter_small_sample_has_low_confidence() -> None:
    profiles, _ = matchup_fixture()
    engine = RecommendationEngine(
        profiles, {1: {10: (0.45, 5), 11: (0.5, 5)}}, role=""
    )
    result = engine.recommend([1], [])
    by_id = {c.hero_id: c for c in result.candidates}
    assert by_id[10].confidence == "low"
    assert result.total_games == 10
    assert any("небольшая" in note or "надёжность" in note for note in result.notes)


def test_counter_without_enemies_explains() -> None:
    profiles, _ = matchup_fixture()
    result = RecommendationEngine(profiles, {}).recommend([], [])
    assert result.candidates
    assert any("Нет данных" in note for note in result.notes)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


def item_fixture():
    profiles = {
        1: hero(1, "Axe", tags={"stun", "magic", "nuker"}),
        2: hero(2, "Zeus", tags={"magic", "nuker"}),
        10: hero(10, "Juggernaut", roles={"Carry"}, tags={"physical"}),
    }
    items = {
        100: item(100, "Tango", cost=90),
        101: item(101, "Broadsword", cost=1000, component=True),
        102: item(102, "Black King Bar", cost=4050, tags={"bkb", "magic_resist"}),
        103: item(103, "Pipe of Insight", cost=3725, tags={"magic_resist"}, key="pipe"),
        104: item(104, "Dust of Appearance", cost=80, tags={"detection"}),
        105: item(105, "Mage Slayer", cost=3100, tags={"magic_resist"}, key="mage_slayer"),
    }
    return profiles, items


def test_components_hidden_from_mid_and_late() -> None:
    profiles, items = item_fixture()
    popularity = {
        "mid_game_items": {"101": 500, "102": 400},
        "late_game_items": {"101": 450, "102": 350},
    }
    build = ItemEngine(profiles, items).build(10, [1, 2], popularity, role="carry")
    mid_ids = {s.item_id for s in build.mid}
    late_ids = {s.item_id for s in build.late}
    assert 101 not in mid_ids
    assert 101 not in late_ids
    assert 102 in mid_ids and 102 in late_ids


def test_situational_items_answer_threats() -> None:
    profiles, items = item_fixture()
    build = ItemEngine(profiles, items).build(10, [1, 2], {}, role="carry")
    assert build.mid  # fallback build kicks in
    situational_names = {s.name for s in build.situational}
    # Pipe is a support item and must not be offered to a carry.
    assert "Pipe of Insight" not in situational_names
    assert "Mage Slayer" in situational_names
    assert any("магического урона" in s.reason for s in build.situational)


def test_support_role_gets_support_items() -> None:
    profiles, items = item_fixture()
    build = ItemEngine(profiles, items).build(10, [1, 2], {}, role="support")
    situational_names = {s.name for s in build.situational}
    assert "Pipe of Insight" in situational_names


def test_core_only_items_excluded_for_support() -> None:
    profiles, items = item_fixture()
    build = ItemEngine(profiles, items).build(10, [1, 2], {}, role="hard_support")
    assert "Mage Slayer" not in {s.name for s in build.situational}


def test_fallback_build_uses_real_items() -> None:
    profiles, items = item_fixture()
    build = ItemEngine(profiles, items).build(10, [], {}, role="")
    assert all(s.item_id in items for s in build.start + build.early + build.mid + build.late)
    assert build.notes


# ---------------------------------------------------------------------------
# Neutral items
# ---------------------------------------------------------------------------


def neutral_fixture():
    profiles = {10: hero(10, "Juggernaut", roles={"Carry"}, tags={"physical"})}
    items = {
        200: item(200, "Tier One", neutral=True, tier=1, stats={"health": 100}, tags={"health"}),
        201: item(201, "Tier Two", neutral=True, tier=2, stats={"damage": 20}, tags={"damage"}),
        202: item(202, "Tier Three A", neutral=True, tier=3, stats={"armor": 8}, tags={"armor"}),
        203: item(203, "Tier Three B", neutral=True, tier=3, stats={"attack_speed": 25}, tags={"attack_speed"}),
        204: item(204, "Not Neutral", neutral=False, tier=3, stats={"damage": 99}),
        205: item(205, "Shop Token", neutral=True, tier=2, key="shop_token"),
    }
    return profiles, items


def test_neutral_tiers_and_availability() -> None:
    profiles, items = neutral_fixture()
    times = {"1": 0, "2": 900, "3": 1500, "4": 2100, "5": 3600}
    result = NeutralEngine(profiles, items).recommend(10, [], times, clock=1000)

    assert [s.item_id for s in result.tiers[1]] == [200]
    assert [s.item_id for s in result.tiers[2]] == [201]  # token skipped
    assert result.tiers[1][0].available is True
    assert result.tiers[2][0].available is True
    assert result.tiers[3][0].available is False
    assert len(result.tiers[3]) == 2
    assert 204 not in {s.item_id for tier in result.tiers.values() for s in tier}
    assert result.tiers[4] == [] and result.tiers[5] == []


def test_neutral_unknown_clock_marks_availability_unknown() -> None:
    profiles, items = neutral_fixture()
    result = NeutralEngine(profiles, items).recommend(
        10, [], {"1": 0, "2": 900, "3": 1500, "4": 2100, "5": 3600}, clock=None
    )
    assert result.tiers[1][0].available is None
