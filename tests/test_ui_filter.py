from __future__ import annotations

from app.data import HeroProfile
from app.recommend import CounterCandidate, ItemSuggestion, NeutralSuggestion
from app.ui import ItemList, filter_counter_candidates, filter_proposed_counters


def profile(
    hero_id: int,
    name: str,
    *,
    attr: str,
    attack: str,
    complexity: int,
) -> HeroProfile:
    return HeroProfile(
        id=hero_id,
        name=name,
        roles=frozenset(),
        primary_attr=attr,
        attack_type=attack,
        tags=frozenset(),
        pub_wr=0.5,
        pub_pick=1000,
        complexity=complexity,
    )


def candidate(hero_id: int, name: str, score: float) -> CounterCandidate:
    return CounterCandidate(
        hero_id=hero_id,
        name=name,
        score=score,
        advantage=0.02,
        lower_bound=0.01,
        meta_wr=0.5,
        confidence="medium",
        reasons=[],
    )


def sample() -> tuple[list[CounterCandidate], dict[int, HeroProfile]]:
    profiles = {
        1: profile(1, "Axe", attr="str", attack="Melee", complexity=1),
        2: profile(2, "Bane", attr="int", attack="Ranged", complexity=2),
        3: profile(3, "Arc Warden", attr="agi", attack="Ranged", complexity=3),
        4: profile(4, "Clockwerk", attr="str", attack="Melee", complexity=0),
    }
    candidates = [
        candidate(1, "Axe", 9.0),
        candidate(2, "Bane", 7.0),
        candidate(3, "Arc Warden", 8.0),
        candidate(4, "Clockwerk", 6.0),
    ]
    return candidates, profiles


def names(items: list[CounterCandidate]) -> list[str]:
    return [item.name for item in items]


def test_default_sort_is_by_recommendation() -> None:
    candidates, profiles = sample()
    result = filter_counter_candidates(candidates, profiles)
    assert names(result) == ["Axe", "Arc Warden", "Bane", "Clockwerk"]


def test_search_attribute_and_attack_filters() -> None:
    candidates, profiles = sample()
    assert names(filter_counter_candidates(candidates, profiles, query="arc")) == [
        "Arc Warden"
    ]
    assert names(filter_counter_candidates(candidates, profiles, attribute="str")) == [
        "Axe",
        "Clockwerk",
    ]
    assert names(
        filter_counter_candidates(candidates, profiles, attack_type="Ranged")
    ) == ["Arc Warden", "Bane"]


def test_complexity_filter_and_sorting() -> None:
    candidates, profiles = sample()
    assert names(filter_counter_candidates(candidates, profiles, complexity=3)) == [
        "Arc Warden"
    ]
    assert names(
        filter_counter_candidates(candidates, profiles, sort_mode="complexity_asc")
    ) == ["Axe", "Bane", "Arc Warden", "Clockwerk"]
    assert names(
        filter_counter_candidates(candidates, profiles, sort_mode="complexity_desc")
    ) == ["Arc Warden", "Bane", "Axe", "Clockwerk"]


def test_name_sort_is_case_insensitive() -> None:
    candidates, profiles = sample()
    assert names(
        filter_counter_candidates(candidates, profiles, sort_mode="name")
    ) == ["Arc Warden", "Axe", "Bane", "Clockwerk"]


def test_filters_do_not_pull_in_heroes_outside_the_shortlist() -> None:
    candidates, profiles = sample()
    # Clockwerk is fourth; with a top-2 shortlist he must not be surfaced.
    assert names(
        filter_proposed_counters(candidates, profiles, limit=2, query="Clockwerk")
    ) == []
    assert names(
        filter_proposed_counters(candidates, profiles, limit=2, attribute="str")
    ) == ["Axe"]
    assert names(
        filter_proposed_counters(candidates, profiles, limit=2, complexity=3)
    ) == ["Arc Warden"]


def test_sorting_only_reorders_the_shortlist() -> None:
    candidates, profiles = sample()
    assert names(
        filter_proposed_counters(candidates, profiles, limit=2, sort_mode="name")
    ) == ["Arc Warden", "Axe"]
    assert names(
        filter_proposed_counters(
            candidates, profiles, limit=3, sort_mode="complexity_desc"
        )
    ) == ["Arc Warden", "Bane", "Axe"]


def test_item_tooltip_has_name_cost_and_reason_once() -> None:
    suggestion = ItemSuggestion(
        item_id=1,
        name="Blink Dagger",
        cost=2250,
        score=1.0,
        reason="Мобильность и инициация.",
        phase="start",
    )
    tooltip = ItemList._tooltip(suggestion, "", "")
    assert tooltip.splitlines()[0] == "Blink Dagger"
    assert tooltip.count("Blink Dagger") == 1
    assert "Стоимость: 2 250g" in tooltip
    assert "Мобильность и инициация." in tooltip


def test_neutral_tooltip_shows_tier_and_availability() -> None:
    suggestion = NeutralSuggestion(
        item_id=2,
        name="Grove Bow",
        tier=2,
        score=1.0,
        reason="Дальний бой и магический урон.",
        time_s=900,
        available=True,
    )
    tooltip = ItemList._tooltip(suggestion, "доступно", "Появляется на 15:00")
    assert tooltip.count("Grove Bow") == 1
    assert "Нейтральный предмет · T2" in tooltip
    assert "Стоимость" not in tooltip
    assert "доступно" in tooltip
    assert "Появляется на 15:00" in tooltip
