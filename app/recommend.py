from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .data import (
    NON_BUILD_KEYS,
    HeroProfile,
    ItemProfile,
    display_signed,
    plural_ru,
    wilson_lower,
)

ROLE_TO_LABEL = {
    "carry": "керри",
    "mid": "мид",
    "offlane": "оффлейн",
    "support": "саппорт",
    "hard_support": "хард-саппорт",
}


@dataclass
class CounterCandidate:
    hero_id: int
    name: str
    score: float
    advantage: float
    lower_bound: float
    meta_wr: float
    confidence: str
    reasons: list[str]
    per_enemy: dict[int, tuple[float, int]] = field(default_factory=dict)
    roles: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    role_match: bool = False


@dataclass
class TeamNeed:
    key: str
    label: str
    predicate: Callable[[HeroProfile], bool]


@dataclass
class CounterResult:
    candidates: list[CounterCandidate]
    needs: list[str]
    total_games: int
    notes: list[str]


@dataclass
class ItemSuggestion:
    item_id: int
    name: str
    cost: int
    score: float
    reason: str
    phase: str
    neutral: bool = False
    tier: int | None = None


@dataclass
class BuildResult:
    start: list[ItemSuggestion] = field(default_factory=list)
    early: list[ItemSuggestion] = field(default_factory=list)
    mid: list[ItemSuggestion] = field(default_factory=list)
    late: list[ItemSuggestion] = field(default_factory=list)
    situational: list[ItemSuggestion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class NeutralSuggestion:
    item_id: int
    name: str
    tier: int
    score: float
    reason: str
    time_s: int
    available: bool | None


@dataclass
class NeutralResult:
    tiers: dict[int, list[NeutralSuggestion]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

THREAT_TAGS = (
    "magic",
    "physical",
    "pure",
    "heal",
    "invis",
    "illusion",
    "summon",
    "evasion",
    "silence",
    "root",
    "stun",
    "disable",
    "hex",
    "taunt",
    "fear",
    "sleep",
    "save",
    "mobility",
    "tanky",
    "nuker",
    "burst",
    "anti_heal",
    "mana_burn",
    "true_sight",
)

THREAT_LABELS = {
    "magic": "магического урона",
    "physical": "физического урона",
    "pure": "чистого урона",
    "heal": "лечения",
    "invis": "невидимости",
    "illusion": "иллюзий",
    "summon": "самонов",
    "evasion": "уклонения",
    "silence": "сайленса",
    "root": "рута",
    "disable": "контроля",
    "stun": "станов",
    "hex": "гекса",
    "taunt": "таунта",
    "fear": "страха",
    "sleep": "сна",
    "save": "спасения союзников",
    "mobility": "мобильности",
    "tanky": "танков",
    "nuker": "бурста",
    "anti_heal": "анти-хила",
    "mana_burn": "сжигания маны",
    "true_sight": "просвета",
}

THREAT_ITEMS: dict[str, list[tuple[str, float]]] = {
    "magic": [
        ("Black King Bar", 1.0),
        ("Pipe of Insight", 1.0),
        ("Mage Slayer", 0.75),
        ("Eternal Shroud", 0.65),
        ("Glimmer Cape", 0.55),
    ],
    "physical": [
        ("Ghost Scepter", 0.9),
        ("Crimson Guard", 1.0),
        ("Assault Cuirass", 0.8),
        ("Shiva's Guard", 0.8),
        ("Solar Crest", 0.65),
    ],
    "heal": [
        ("Spirit Vessel", 1.0),
        ("Shiva's Guard", 0.8),
        ("Eye of Skadi", 0.7),
    ],
    "invis": [
        ("Dust of Appearance", 1.0),
        ("Sentry Ward", 0.9),
        ("Gem of True Sight", 0.7),
        ("Silver Edge", 0.45),
    ],
    "illusion": [
        ("Mjollnir", 0.9),
        ("Gleipnir", 0.9),
        ("Radiance", 0.8),
        ("Battle Fury", 0.75),
        ("Shiva's Guard", 0.6),
    ],
    "summon": [
        ("Mjollnir", 0.8),
        ("Gleipnir", 0.8),
        ("Radiance", 0.7),
        ("Crimson Guard", 0.6),
    ],
    "evasion": [
        ("Monkey King Bar", 1.0),
        ("Bloodthorn", 0.8),
    ],
    "silence": [
        ("Black King Bar", 1.0),
        ("Eul's Scepter of Divinity", 0.7),
        ("Lotus Orb", 0.6),
        ("Manta Style", 0.6),
    ],
    "root": [
        ("Black King Bar", 0.8),
        ("Eul's Scepter of Divinity", 0.5),
        ("Manta Style", 0.5),
    ],
    "disable": [
        ("Black King Bar", 1.0),
        ("Linken's Sphere", 0.7),
        ("Aeon Disk", 0.8),
        ("Lotus Orb", 0.6),
    ],
    "burst": [
        ("Black King Bar", 0.8),
        ("Aeon Disk", 0.8),
        ("Linken's Sphere", 0.6),
    ],
    "mobility": [
        ("Rod of Atos", 0.8),
        ("Orchid Malevolence", 0.7),
        ("Bloodthorn", 0.7),
        ("Gleipnir", 0.8),
        ("Nullifier", 0.6),
    ],
    "save": [
        ("Nullifier", 0.8),
        ("Eye of Skadi", 0.6),
    ],
    "tanky": [
        ("Desolator", 0.7),
        ("Silver Edge", 0.7),
        ("Eye of Skadi", 0.6),
    ],
    "anti_heal": [
        ("Black King Bar", 0.4),
    ],
}


# Items that are almost exclusively bought by supports.  Cores can use some
# of them situationally, but recommending Pipe/Glimmer to an Anti-Mage makes the
# build look untrustworthy, so keep the shortlist role-appropriate.
SUPPORT_ONLY_KEYS = {
    "pipe",
    "glimmer_cape",
    "lotus_orb",
    "guardian_greaves",
    "holy_locket",
    "mekansm",
    "solar_crest",
    "spirit_vessel",
    "force_staff",
    "pavise",
    "arcane_boots",
    "tranquil_boots",
    "boots_of_bearing",
    "urn_of_shadows",
    "headdress",
    "buckler",
    "ring_of_basilius",
}

# Expensive right-click / initiator items that are not realistic hard-support
# purchases in a normal game.
CORE_ONLY_KEYS = {
    "mage_slayer",
    "eternal_shroud",
    "sphere",
    "gungir",
    "bloodthorn",
    "crimson_guard",
    "assault",
    "skadi",
    "shivas_guard",
    "monkey_king_bar",
    "nullifier",
    "silver_edge",
    "orchid",
    "abyssal_blade",
    "satanic",
    "butterfly",
    "bfury",
    "manta",
    "disperser",
    "harpoon",
    "echo_sabre",
    "sange_and_yasha",
    "radiance",
    "mjollnir",
    "maelstrom",
    "mask_of_madness",
    "diffusal_blade",
    "basher",
    "desolator",
    "greater_crit",
}


def item_fits_role(item: ItemProfile, role: str) -> bool:
    """Keep obvious support-only / core-only items out of the wrong role."""
    if not role:
        return True
    if role in ("carry", "mid") and item.key in SUPPORT_ONLY_KEYS:
        return False
    if role in ("support", "hard_support") and item.key in CORE_ONLY_KEYS:
        return False
    return True


def analyze_team(profiles: Iterable[HeroProfile]) -> list[TeamNeed]:
    profiles = list(profiles)
    needs: list[TeamNeed] = []

    def add(key: str, label: str, predicate: Callable[[HeroProfile], bool]) -> None:
        needs.append(TeamNeed(key, label, predicate))

    has = lambda predicate: any(predicate(p) for p in profiles)  # noqa: E731

    if not has(lambda p: "Initiator" in p.roles or "initiation" in p.tags):
        add("initiation", "инициацию", lambda p: "Initiator" in p.roles or "initiation" in p.tags)
    if not has(lambda p: "Disabler" in p.roles or "stun" in p.tags or "disable" in p.tags):
        add("disable", "контроль", lambda p: "Disabler" in p.roles or "stun" in p.tags or "disable" in p.tags)
    if not has(lambda p: "Durable" in p.roles or "tanky" in p.tags):
        add("frontline", "фронтлайн", lambda p: "Durable" in p.roles or "tanky" in p.tags)
    if not has(lambda p: "save" in p.tags):
        add("save", "спасение союзников", lambda p: "save" in p.tags)
    if not has(lambda p: "Support" in p.roles):
        add("support", "поддержку", lambda p: "Support" in p.roles)
    if not has(lambda p: "Carry" in p.roles):
        add("carry", "керри-урон", lambda p: "Carry" in p.roles)
    if not has(lambda p: "magic" in p.tags):
        add("magic", "магический урон", lambda p: "magic" in p.tags)
    if not has(lambda p: "physical" in p.tags):
        add("physical", "физический урон", lambda p: "physical" in p.tags)
    if not has(lambda p: "aoe" in p.tags):
        add("wave", "волновой клир", lambda p: "aoe" in p.tags)
    return needs


def role_match(profile: HeroProfile, role: str) -> bool:
    """Coarse role suitability used to keep the shortlist readable.

    Valve role tags overlap heavily (for example Windranger is both Carry and
    Support), so the rules below deliberately exclude the most common
    off-role noise instead of trying to model exact positions.
    """
    if not role:
        return True
    roles = profile.roles
    support = "Support" in roles
    carry = "Carry" in roles
    if role == "carry":
        return carry and not support
    if role == "mid":
        return carry or ("Nuker" in roles and not support)
    if role == "offlane":
        return ("Initiator" in roles or "Durable" in roles) and not support
    if role == "support":
        return support
    if role == "hard_support":
        return support and not carry
    return True


def threat_counts(profiles: Iterable[HeroProfile]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for profile in profiles:
        tags = set(profile.tags)
        if {"stun", "silence", "root", "hex", "taunt", "fear", "sleep"} & tags:
            tags.add("disable")
        if "nuker" in tags and ({"magic", "physical", "pure"} & tags):
            tags.add("burst")
        if "illusion" in tags or "summon" in tags:
            tags.add("illusion")
        for tag in THREAT_TAGS:
            if tag in tags:
                counts[tag] = counts.get(tag, 0) + 1
        if "illusion" in tags:
            counts["illusion"] = max(counts.get("illusion", 0), 1)
    return counts


def _smoothed(wins: float, games: int, prior: float = 60.0) -> float:
    return (wins + prior * 0.5) / (games + prior)


def _confidence(total_games: int, min_games: int) -> str:
    if total_games >= 700 and min_games >= 50:
        return "high"
    if total_games >= 250 and min_games >= 20:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Counter picks
# ---------------------------------------------------------------------------


class RecommendationEngine:
    def __init__(
        self,
        profiles: dict[int, HeroProfile],
        matchups_by_enemy: dict[int, dict[int, tuple[float, int]]],
        role: str = "",
        strict_role: bool = True,
    ) -> None:
        self.profiles = profiles
        self.matchups_by_enemy = matchups_by_enemy
        self.role = role
        self.strict_role = strict_role

    def recommend(
        self,
        enemy_ids: Iterable[int],
        ally_ids: Iterable[int],
    ) -> CounterResult:
        enemy_ids = [int(x) for x in enemy_ids if int(x) > 0]
        ally_ids = [int(x) for x in ally_ids if int(x) > 0]
        picked = set(enemy_ids) | set(ally_ids)
        ally_profiles = [self.profiles[i] for i in ally_ids if i in self.profiles]
        needs = analyze_team(ally_profiles)
        need_labels = [need.label for need in needs]
        candidates: list[CounterCandidate] = []
        total_games_all = 0

        for profile in self.profiles.values():
            if profile.id in picked:
                continue
            adv_values: list[float] = []
            lcb_values: list[float] = []
            weights: list[float] = []
            per_enemy: dict[int, tuple[float, int]] = {}
            games_for_confidence: list[int] = []

            for enemy_id in enemy_ids:
                stats = self.matchups_by_enemy.get(enemy_id) or {}
                entry = stats.get(profile.id)
                if not entry:
                    continue
                enemy_wr, games = entry
                if games <= 0:
                    continue
                wins_for_candidate = max(0.0, min(float(games), games - enemy_wr * games))
                wr = _smoothed(wins_for_candidate, games)
                adv = wr - 0.5
                lcb = wilson_lower(wins_for_candidate, games) - 0.5
                weight = math.sqrt(max(1, games))
                adv_values.append(adv)
                lcb_values.append(lcb)
                weights.append(weight)
                per_enemy[enemy_id] = (adv, games)
                games_for_confidence.append(games)

            total_weight = sum(weights)
            if total_weight > 0:
                advantage = sum(a * w for a, w in zip(adv_values, weights)) / total_weight
                lower = sum(l * w for l, w in zip(lcb_values, weights)) / total_weight
            else:
                advantage = 0.0
                lower = -0.02
            total_games = sum(games_for_confidence)
            total_games_all += total_games

            matched_needs = [need for need in needs if need.predicate(profile)]
            synergy_pp = 2.0 * (len(matched_needs) / len(needs)) if needs else 0.0
            meta_adv = profile.pub_wr - 0.5
            is_role_match = role_match(profile, self.role)
            if self.role and self.strict_role and not is_role_match:
                continue
            role_bonus = 0.7 if (self.role and is_role_match) else (-0.35 if self.role else 0.0)

            rank = (
                0.55 * (advantage * 100)
                + 0.25 * (lower * 100)
                + 0.20 * (meta_adv * 100)
                + synergy_pp
                + role_bonus
            )

            reasons: list[str] = []
            ranked_enemies = sorted(per_enemy.items(), key=lambda kv: kv[1][0], reverse=True)
            for enemy_id, (adv, games) in ranked_enemies[:2]:
                enemy = self.profiles.get(enemy_id)
                if enemy:
                    reasons.append(f"{display_signed(adv)} против {enemy.name}")
            for need in matched_needs[:2]:
                reasons.append(f"добирает {need.label}")
            if profile.pub_pick >= 1000:
                reasons.append(f"мета {profile.pub_wr * 100:.1f}%")

            candidates.append(
                CounterCandidate(
                    hero_id=profile.id,
                    name=profile.name,
                    score=rank,
                    advantage=advantage,
                    lower_bound=lower,
                    meta_wr=profile.pub_wr,
                    confidence=_confidence(total_games, min(games_for_confidence) if games_for_confidence else 0),
                    reasons=reasons[:4],
                    per_enemy=per_enemy,
                    roles=tuple(sorted(profile.roles)),
                    tags=tuple(sorted(profile.tags)),
                    role_match=is_role_match,
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        notes: list[str] = []
        if not enemy_ids:
            notes.append("Нет данных о вражеских героях — откалибруйте экран или внесите пики вручную.")
        elif len(enemy_ids) < 5:
            notes.append(f"Распознано врагов: {len(enemy_ids)}/5. Рекомендации уточнятся после остальных пиков.")
        if total_games_all < 200:
            notes.append("Публичная выборка OpenDota по парам героев небольшая; смотрите колонку «надёжность».")
        return CounterResult(candidates=candidates, needs=need_labels, total_games=total_games_all, notes=notes)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


class ItemEngine:
    def __init__(
        self,
        profiles: dict[int, HeroProfile],
        items: dict[int, ItemProfile],
    ) -> None:
        self.profiles = profiles
        self.items = items
        self.by_name: dict[str, ItemProfile] = {}
        for item in items.values():
            self.by_name[self._norm(item.name)] = item

    @staticmethod
    def _norm(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", name.lower())

    def _find(self, name: str) -> ItemProfile | None:
        return self.by_name.get(self._norm(name))

    def _phase_from_cost(self, cost: int) -> str:
        if cost <= 700:
            return "start"
        if cost <= 1800:
            return "early"
        if cost <= 3200:
            return "mid"
        return "late"

    def build(
        self,
        hero_id: int,
        enemy_ids: Iterable[int],
        popularity: dict[str, dict[str, int]],
        role: str = "",
    ) -> BuildResult:
        profile = self.profiles.get(hero_id)
        result = BuildResult()
        if not profile:
            result.notes.append("Не выбран свой герой — предметы появятся после пика.")
            return result

        enemies = [self.profiles[i] for i in enemy_ids if i in self.profiles]
        threats = threat_counts(enemies)
        threat_notes = self._core_threat_notes(threats)

        phase_keys = (
            ("start", "start_game_items"),
            ("early", "early_game_items"),
            ("mid", "mid_game_items"),
            ("late", "late_game_items"),
        )
        for phase, key in phase_keys:
            counts = (popularity or {}).get(key) or {}
            ranked: list[tuple[int, int]] = []
            for item_id, count in counts.items():
                try:
                    iid = int(item_id)
                    cnt = int(count)
                except (TypeError, ValueError):
                    continue
                item = self.items.get(iid)
                if not item or item.neutral or item.cost <= 0:
                    continue
                if not item.name or item.key.startswith("recipe_"):
                    continue
                if phase in ("mid", "late") and item.key in NON_BUILD_KEYS:
                    continue
                if not item_fits_role(item, role):
                    continue
                # OpenDota's "mid/late" buckets include component purchases.
                # Components are useful at the start, but a finished-item list
                # must not suggest Broadsword instead of Black King Bar.
                if item.component and phase in ("mid", "late"):
                    continue
                if item.component and phase == "early" and item.cost > 800:
                    continue
                # OpenDota buckets occasionally mix a luxury purchase into the
                # early game list (Battle Fury with one game). Keep the phase
                # readable with a cost envelope.
                if phase == "start" and item.cost > 900:
                    continue
                if phase == "early" and item.cost > 2500:
                    continue
                ranked.append((cnt, iid))
            ranked.sort(reverse=True)
            suggestions: list[ItemSuggestion] = []
            for count, iid in ranked[:8]:
                item = self.items[iid]
                reason = (
                    f"популярен на герое "
                    f"({count} {plural_ru(count, 'матч', 'матча', 'матчей')})"
                )
                if item.key == "black_king_bar":
                    # BKB grants spell immunity, not magic resistance; do not
                    # describe it as a resistance item.
                    extra = [threat_notes["bkb"]] if "bkb" in threat_notes else []
                else:
                    extra = [
                        label for tag, label in threat_notes.items() if tag in item.tags
                    ]
                if extra:
                    reason += "; " + ", ".join(extra[:2])
                suggestions.append(
                    ItemSuggestion(
                        item_id=item.id,
                        name=item.name,
                        cost=item.cost,
                        score=float(count),
                        reason=reason,
                        phase=phase,
                    )
                )
            setattr(result, phase, suggestions)

        if not result.start and not result.early:
            self._fallback_build(profile, result)

        seen = {s.item_id for phase in ("start", "early", "mid", "late") for s in getattr(result, phase)}
        situational: list[ItemSuggestion] = []
        for tag, count in threats.items():
            rules = THREAT_ITEMS.get(tag)
            if not rules:
                continue
            for name, weight in rules:
                item = self._find(name)
                if not item or item.neutral or item.id in seen:
                    continue
                if not item_fits_role(item, role):
                    continue
                score = weight * count
                if item.tags & {"save", "bkb"} and profile.is_support:
                    score += 0.2
                if item.cost > 3200 and profile.is_support:
                    score -= 0.35
                reason = f"против {THREAT_LABELS.get(tag, tag)}"
                if count > 1:
                    reason += (
                        f" ({count} {plural_ru(count, 'герой', 'героя', 'героев')})"
                    )
                situational.append(
                    ItemSuggestion(
                        item_id=item.id,
                        name=item.name,
                        cost=item.cost,
                        score=score,
                        reason=reason,
                        phase=self._phase_from_cost(item.cost),
                    )
                )
        # Merge duplicates, keeping the strongest reason.
        merged: dict[int, ItemSuggestion] = {}
        for suggestion in situational:
            old = merged.get(suggestion.item_id)
            if not old or suggestion.score > old.score:
                merged[suggestion.item_id] = suggestion
        result.situational = sorted(merged.values(), key=lambda s: s.score, reverse=True)[:10]
        if not result.situational:
            result.notes.append("Явных угроз для ситуативных предметов не найдено.")
        return result

    def _core_threat_notes(self, threats: dict[str, int]) -> dict[str, str]:
        notes: dict[str, str] = {}
        if threats.get("magic", 0) >= 2:
            notes["magic_resist"] = "добавляет магическое сопротивление"
            notes["bkb"] = "спасает от магии и контроля"
        if threats.get("physical", 0) >= 2:
            notes["armor"] = "добавляет броню"
            notes["evasion"] = "уклонение от атак"
            notes["physical_block"] = "блокирует физический урон"
        if threats.get("heal", 0) >= 1:
            notes["anti_heal"] = "режет лечение"
        if threats.get("invis", 0) >= 1:
            notes["detection"] = "раскрывает невидимость"
        if threats.get("illusion", 0) >= 1:
            notes["anti_illusions"] = "чистит иллюзии/самонов"
        if threats.get("evasion", 0) >= 1:
            notes["anti_evasion"] = "пробивает уклонение"
        if threats.get("disable", 0) >= 2:
            notes["bkb"] = "спасает от контроля"
            notes["dispel"] = "снимает дебаффы"
            notes["status_resist"] = "сопротивление контролю"
        return notes

    def _fallback_build(self, profile: HeroProfile, result: BuildResult) -> None:
        names = {
            "start": ["Tango", "Healing Salve", "Iron Branch", "Magic Stick"],
            "early": ["Boots of Speed", "Magic Wand", "Bracer"],
            "mid": ["Power Treads", "Black King Bar", "Blink Dagger"],
            "late": ["Black King Bar", "Aghanim's Scepter", "Shiva's Guard"],
        }
        if profile.is_support:
            names["start"] = ["Tango", "Healing Salve", "Observer Ward", "Sentry Ward", "Magic Stick"]
            names["early"] = ["Boots of Speed", "Magic Wand", "Wind Lace"]
            names["mid"] = ["Arcane Boots", "Force Staff", "Glimmer Cape"]
            names["late"] = ["Guardian Greaves", "Aghanim's Scepter", "Lotus Orb"]
        for phase, phase_names in names.items():
            suggestions: list[ItemSuggestion] = []
            for name in phase_names:
                item = self._find(name)
                if item:
                    suggestions.append(
                        ItemSuggestion(item.id, item.name, item.cost, 0.0, "базовый набор (нет статистики)", phase)
                    )
            setattr(result, phase, suggestions)
        result.notes.append("Для этого героя нет статистики предметов — показан базовый набор.")


# ---------------------------------------------------------------------------
# Neutral items
# ---------------------------------------------------------------------------


TAG_ROLE_AFFINITY: dict[str, dict[str, float]] = {
    "damage": {"carry": 0.8, "nuker": 0.25},
    "attack_speed": {"carry": 0.7},
    "crit": {"carry": 0.9},
    "lifesteal": {"carry": 0.5, "tank": 0.25},
    "health": {"tank": 0.5, "support": 0.2},
    "armor": {"tank": 0.5},
    "magic_resist": {"tank": 0.4},
    "mana_regen": {"support": 0.4, "nuker": 0.4},
    "save": {"support": 0.65},
    "heal": {"support": 0.45},
    "mobility": {"initiator": 0.5, "support": 0.2},
    "slow": {"initiator": 0.4},
    "stun": {"initiator": 0.5},
    "aura": {"support": 0.5},
    "farming": {"carry": 0.6},
    "spell_amp": {"nuker": 0.65},
    "anti_heal": {"support": 0.3, "nuker": 0.3},
    "detection": {"support": 0.4},
    "dispel": {"support": 0.4},
    "anti_evasion": {"carry": 0.5},
    "anti_illusions": {"carry": 0.4, "nuker": 0.4},
    "damage_block": {"tank": 0.4},
    "retaliation": {"tank": 0.4},
    "status_resist": {"tank": 0.3, "initiator": 0.2},
    "armor_reduction": {"carry": 0.5},
    "summon": {"carry": 0.3, "nuker": 0.2},
    "illusion": {"carry": 0.4},
    "economy": {"support": 0.2, "carry": 0.15},
    "utility": {"support": 0.2},
}

THREAT_TAG_BONUS: dict[str, tuple[float, str]] = {
    "detection": (2.4, "раскрывает невидимость"),
    "anti_heal": (2.2, "режет лечение"),
    "anti_evasion": (1.9, "пробивает уклонение"),
    "break": (1.8, "выключает пассивки"),
    "save": (1.4, "спасает от контроля"),
    "dispel": (1.1, "снимает дебаффы"),
    "anti_illusions": (1.1, "чистит иллюзии/самонов"),
    "status_resist": (1.0, "сопротивление контролю"),
}


class NeutralEngine:
    def __init__(self, profiles: dict[int, HeroProfile], items: dict[int, ItemProfile]) -> None:
        self.profiles = profiles
        self.items = items

    def recommend(
        self,
        hero_id: int,
        enemy_ids: Iterable[int],
        neutral_times: dict[str, int],
        clock: int | None = None,
    ) -> NeutralResult:
        profile = self.profiles.get(hero_id)
        result = NeutralResult()
        if not profile:
            result.notes.append("Не выбран свой герой — нейтральные предметы появятся после пика.")
            return result
        enemies = [self.profiles[i] for i in enemy_ids if i in self.profiles]
        threats = threat_counts(enemies)
        for tier in range(1, 6):
            time_s = int(neutral_times.get(str(tier), 0) or 0)
            candidates: list[NeutralSuggestion] = []
            for item in self.items.values():
                if not item.neutral or item.tier != tier:
                    continue
                if item.key.endswith("_token") or item.cost not in (0, None):
                    continue
                score, reasons = self._score(profile, item, threats)
                candidates.append(
                    NeutralSuggestion(
                        item_id=item.id,
                        name=item.name,
                        tier=tier,
                        score=score,
                        reason="; ".join(reasons[:2]) or "нейтральный предмет",
                        time_s=time_s,
                        available=(clock >= time_s) if clock is not None else None,
                    )
                )
            candidates.sort(key=lambda s: s.score, reverse=True)
            result.tiers[tier] = candidates[:3]
        if not any(result.tiers.values()):
            result.notes.append("В кэше нет нейтральных предметов с тирами — обновите данные.")
        return result

    @staticmethod
    def _profile_roles(profile: HeroProfile) -> set[str]:
        roles: set[str] = set()
        if "Carry" in profile.roles:
            roles.add("carry")
        if "Support" in profile.roles:
            roles.add("support")
        if "Nuker" in profile.roles:
            roles.add("nuker")
        if "Initiator" in profile.roles or "initiation" in profile.tags:
            roles.add("initiator")
        if "Durable" in profile.roles or "tanky" in profile.tags:
            roles.add("tank")
        return roles

    def _score(self, profile: HeroProfile, item: ItemProfile, threats: dict[str, int]) -> tuple[float, list[str]]:
        stats = item.stats
        tags = item.tags
        roles = self._profile_roles(profile)
        score = 0.05
        contributions: list[tuple[float, str]] = []

        def add(value: float, reason: str) -> None:
            nonlocal score
            if value <= 0:
                return
            score += value
            for index, (old_value, old_reason) in enumerate(contributions):
                if old_reason == reason:
                    contributions[index] = (old_value + value, reason)
                    return
            contributions.append((value, reason))

        # 1. Tags that fit the hero's jobs.
        for tag in tags:
            affinity = TAG_ROLE_AFFINITY.get(tag)
            if not affinity:
                continue
            fit = min(1.0, sum(affinity.get(role, 0.0) for role in roles))
            if fit > 0:
                add(fit * 0.85, f"подходит {profile.name}")

        # 2. Tags that answer the enemy draft.
        for tag, (value, reason) in THREAT_TAG_BONUS.items():
            if tag in tags and threats.get(tag, 0) >= 1:
                add(value * min(2, threats[tag]), reason)
        if "anti_heal" in tags and threats.get("heal", 0) >= 1:
            add(1.2, "анти-хил")
        if "aoe" in tags and threats.get("illusion", 0) >= 1:
            add(0.8, "урон по площади")
        if "magic_resist" in tags and threats.get("magic", 0) >= 2:
            add(0.6, "магический резист")
        if "armor" in tags and threats.get("physical", 0) >= 2:
            add(0.6, "броня")
        if "damage_block" in tags and threats.get("physical", 0) >= 2:
            add(0.6, "блок атак")

        # 3. Numeric stats (canonical keys from data.build_item_profiles).
        def stat(*keys: str) -> float:
            for key in keys:
                value = stats.get(key)
                if value:
                    return float(value)
            return 0.0

        armor = stat("armor")
        if armor:
            need = 1.0 if ("tank" in roles or threats.get("physical", 0) >= 2) else 0.45
            add(min(1.2, armor * 0.08 * need), "броня")
        magic_resist = stat("magic_resist")
        if magic_resist:
            need = 1.0 if threats.get("magic", 0) >= 2 else 0.4
            add(min(1.5, magic_resist * 0.028 * need), "магический резист")
        health = stat("health")
        if health:
            need = 1.0 if ("tank" in roles or "support" in roles or threats.get("burst", 0) >= 1) else 0.4
            add(min(1.4, health / 200 * need), "здоровье")
        mana_regen = stat("mana_regen")
        if mana_regen:
            need = 1.0 if ("support" in roles or "nuker" in roles or profile.primary_attr == "int") else 0.35
            add(min(1.2, mana_regen * 0.55 * need), "реген маны")
        attack_speed = stat("attack_speed")
        if attack_speed:
            need = 1.0 if ("carry" in roles or profile.primary_attr == "agi") else 0.35
            add(min(1.2, attack_speed / 30 * need), "скорость атаки")
        damage = stat("damage")
        if damage:
            need = 1.0 if "carry" in roles else 0.35
            add(min(1.2, damage / 45 * need), "урон")
        spell_amp = stat("spell_amp")
        if spell_amp:
            need = 1.0 if ("nuker" in roles or profile.primary_attr == "int") else 0.3
            add(min(1.2, spell_amp * 0.055 * need), "усиление заклинаний")
        evasion = stat("evasion")
        if evasion:
            need = 1.0 if threats.get("physical", 0) >= 1 else 0.35
            add(min(1.2, evasion * 0.025 * need), "уклонение")
        lifesteal = stat("lifesteal")
        if lifesteal:
            need = 1.0 if "carry" in roles else 0.3
            add(min(1.0, lifesteal * 0.04 * need), "вампиризм")
        move_speed = stat("move_speed")
        if move_speed:
            need = 1.0 if ("support" in roles or "initiator" in roles) else 0.4
            add(min(0.9, move_speed / 35 * need), "скорость передвижения")
        status_resist = stat("status_resist")
        if status_resist:
            need = 1.0 if threats.get("disable", 0) >= 1 else 0.25
            add(min(1.2, status_resist * 0.016 * need), "сопротивление контролю")
        damage_block = stat("damage_block")
        if damage_block:
            need = 1.0 if threats.get("physical", 0) >= 2 else 0.25
            add(min(1.1, damage_block / 50 * need), "блок урона")
        retaliation = stat("retaliation")
        if retaliation:
            need = 1.0 if threats.get("physical", 0) >= 2 else 0.25
            add(min(0.8, retaliation / 60 * need), "ответный урон")

        contributions.sort(key=lambda x: x[0], reverse=True)
        return score, [reason for _, reason in contributions]
