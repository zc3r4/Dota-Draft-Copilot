from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CACHE_DIR, Config
from app.data import (
    JsonCache,
    OpenDota,
    build_hero_profiles,
    build_item_profiles,
    matchup_index,
)
from app.gamedata import (
    apply_neutral_pool,
    load_hero_complexity,
    load_neutral_pool,
)
from app.recommend import ItemEngine, NeutralEngine, RecommendationEngine
from app.steam import find_dota_root, read_patch_info


def main() -> None:
    cfg = Config()
    cache = JsonCache(CACHE_DIR, cfg.data_ttl_hours * 3600)
    api = OpenDota(cache, cfg)

    print("patch:", read_patch_info(find_dota_root()))
    heroes = api.heroes()
    stats = api.hero_stats()
    abilities = api.abilities()
    hero_abilities = api.hero_abilities()
    items_raw = api.items()
    complexity = load_hero_complexity()
    profiles = build_hero_profiles(
        heroes, stats, hero_abilities, abilities, complexity
    )
    items = build_item_profiles(items_raw)
    pool = load_neutral_pool()
    items = apply_neutral_pool(items, pool)
    print(f"heroes={len(profiles)} items={len(items)} neutrals={sum(1 for i in items.values() if i.neutral)}")
    distribution = {rating: sum(1 for value in complexity.values() if value == rating) for rating in (1, 2, 3)}
    print(f"complexity: {len(complexity)} heroes, {distribution}")
    print("tiers:", {t: sum(1 for i in items.values() if i.neutral and i.tier == t) for t in range(1, 6)})
    if pool:
        print("neutral pool:", pool.source, pool.client_version, pool.times)

    names = {p.name: p.id for p in profiles.values()}
    enemy_names = ["Axe", "Pudge", "Zeus", "Riki", "Necrophos"]
    ally_names = ["Juggernaut", "Crystal Maiden", "Axe"][:2] + ["Windranger"]
    enemy_ids = [names[n] for n in enemy_names if n in names]
    ally_ids = [names[n] for n in ally_names if n in names]
    print("enemy ids:", list(zip(enemy_names, enemy_ids)))
    print("ally ids:", list(zip(ally_names, ally_ids)))

    matchups = {}
    for eid in enemy_ids:
        rows = api.matchups(eid)
        matchups[eid] = matchup_index(rows)
        print("matchup", profiles[eid].name, "opponents", len(matchups[eid]))

    engine = RecommendationEngine(profiles, matchups, role="carry")
    result = engine.recommend(enemy_ids, ally_ids)
    print("\nNEEDS:", result.needs, "NOTES:", result.notes)
    for c in result.candidates[:12]:
        print(
            f"{c.score:5.2f}  {c.name:20s} adv={c.advantage*100:+5.1f}% "
            f"lcb={c.lower_bound*100:+5.1f}% meta={c.meta_wr*100:5.1f}% {c.confidence:6s} | "
            + "; ".join(c.reasons)
        )

    my_hero = names.get("Juggernaut")
    if my_hero:
        pop = api.item_popularity(my_hero)
        ib = ItemEngine(profiles, items).build(my_hero, enemy_ids, pop, role="carry")
        for phase in ("start", "early", "mid", "late"):
            print(f"\n{phase.upper()}:")
            for s in getattr(ib, phase):
                print(f"  {s.name:28s} {s.cost:5d}  {s.reason}")
        print("\nSITUATIONAL:")
        for s in ib.situational:
            print(f"  {s.name:28s} {s.cost:5d}  {s.reason}")

        nr = NeutralEngine(profiles, items).recommend(my_hero, enemy_ids, cfg.neutral_times, clock=0)
        for tier, rows in nr.tiers.items():
            print(f"\nNEUTRAL T{tier} (from {nr.tiers[tier][0].time_s if rows else '?'}s):")
            for s in rows:
                print(f"  {s.name:30s} {s.score:5.2f}  {s.reason}")


if __name__ == "__main__":
    main()
