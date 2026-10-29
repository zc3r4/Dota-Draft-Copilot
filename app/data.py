from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import requests

from .config import CACHE_DIR, Config
from .steam import read_patch_info

OPEN_DOTA = "https://api.opendota.com/api"
DOTACONSTANTS_ITEMS = "https://raw.githubusercontent.com/odota/dotaconstants/master/build/items.json"
USER_AGENT = "DotaDraftCopilot/1.0 (+local desktop helper)"


class DataError(RuntimeError):
    pass


@dataclass
class CacheResult:
    data: Any
    age: float
    stale: bool = False


class JsonCache:
    def __init__(self, root: Path = CACHE_DIR, default_ttl: float = 12 * 3600) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
        self._lock = threading.Lock()

    @staticmethod
    def _safe(key: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", key)[:140]

    def _path(self, key: str) -> Path:
        return self.root / f"{self._safe(key)}.json"

    def get(self, key: str, ttl: float | None = None) -> CacheResult | None:
        path = self._path(key)
        try:
            with self._lock:
                raw = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - float(raw.get("ts") or 0)
        except (OSError, ValueError, TypeError):
            return None
        data = raw.get("data")
        max_age = self.default_ttl if ttl is None else ttl
        return CacheResult(data, age, stale=age > max_age)

    def put(self, key: str, data: Any) -> None:
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        with self._lock:
            try:
                tmp.write_text(json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
                tmp.replace(path)
            except OSError:
                pass


@dataclass(frozen=True)
class HeroProfile:
    id: int
    name: str
    roles: frozenset[str]
    primary_attr: str
    attack_type: str
    tags: frozenset[str]
    pub_wr: float
    pub_pick: int
    img: str = ""
    complexity: int = 0

    @property
    def is_support(self) -> bool:
        return "Support" in self.roles


@dataclass(frozen=True)
class ItemProfile:
    id: int
    key: str
    name: str
    cost: int
    tier: int | None
    tags: frozenset[str]
    neutral: bool
    img: str = ""
    stats: dict[str, float] = field(default_factory=dict)
    components: tuple[str, ...] = ()
    created: bool = False
    component: bool = False
    qual: str = ""
    description: str = ""


# ---------------------------------------------------------------------------
# Tag extraction
# ---------------------------------------------------------------------------

HERO_TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "stun": ("stun", "stuns", "stunned", "stunning", "bash", "bashes", "knockback", "taunt", "fear"),
    "root": ("root", "roots", "rooted", "ensnare", "ensnares"),
    "silence": ("silence", "silences", "silenced", "silencing"),
    "hex": ("hex", "hexes", "polymorph"),
    "break": ("break", "breaks", "disables passive"),
    "disarm": ("disarm", "disarms", "disarmed"),
    "invis": ("invisible", "invisibility"),
    "illusion": ("illusion", "illusions", "mirror image"),
    "summon": ("summon", "summons", "summoned", "summoning"),
    "heal": ("heal", "heals", "healed", "healing", "health regen", "restore health", "lifesteal", "regeneration"),
    "evasion": ("evasion", "miss chance"),
    "save": ("shield", "shields", "shielded", "barrier", "barriers", "save", "saves", "saved", "damage reduction", "protection"),
    "mobility": ("blink", "blinks", "teleport", "teleports", "dash", "dashes", "leap", "leaps", "leaping", "charge", "charges", "speed boost"),
    "aoe": ("area", "aoe", "radius", "nearby"),
    "global": ("global",),
    "anti_heal": ("heal reduction", "reduces healing", "reduces health regeneration"),
    "mana_burn": ("mana burn", "burns mana", "burn mana"),
    "true_sight": ("true sight", "reveal", "reveals", "revealed"),
    "spell_immunity": ("spell immune", "debuff immune", "magic immune"),
    "slow": ("slow", "slows", "slowed", "slowing"),
    "pull": ("pull", "pulls", "pulled", "pulling", "drag", "drags", "dragged"),
    "taunt": ("taunt", "taunts", "taunted"),
    "fear": ("fear", "fears", "feared"),
    "sleep": ("sleep", "sleeps", "sleeping", "slept"),
    "lifesteal": ("lifesteal",),
    "mana_regen": ("mana regen", "restore mana"),
    "attack_speed": ("attack speed",),
    "damage_amp": ("damage amplification", "amplifies damage", "increases damage taken"),
}

HERO_TAG_OVERRIDES: dict[str, set[str]] = {
    "Anti-Mage": {"mana_burn", "mobility", "anti_magic"},
    "Axe": {"taunt", "disable", "tanky"},
    "Bane": {"disable", "sleep", "save"},
    "Beastmaster": {"summon", "stun", "vision"},
    "Bloodseeker": {"anti_heal", "sustain", "mobility"},
    "Bristleback": {"tanky", "anti_physical", "slow"},
    "Broodmother": {"summon", "invis", "sustain"},
    "Chaos Knight": {"illusion", "stun", "tanky"},
    "Crystal Maiden": {"magic", "root", "mana_regen"},
    "Dazzle": {"save", "heal", "disable"},
    "Earthshaker": {"stun", "aoe", "initiation"},
    "Enigma": {"stun", "summon", "initiation"},
    "Faceless Void": {"stun", "aoe", "mobility"},
    "Huskar": {"heal", "magic_resist", "sustain"},
    "Invoker": {"magic", "aoe", "disable"},
    "Jakiro": {"magic", "aoe", "slow"},
    "Keeper of the Light": {"magic", "mana_regen", "heal"},
    "Legion Commander": {"disable", "sustain", "mobility"},
    "Lich": {"magic", "save", "aoe"},
    "Lifestealer": {"sustain", "lifesteal", "tanky"},
    "Luna": {"physical", "aoe", "mobility"},
    "Medusa": {"tanky", "mana", "aoe"},
    "Meepo": {"illusion", "mobility", "summon"},
    "Naga Siren": {"illusion", "save", "disable"},
    "Nature's Prophet": {"summon", "global", "push"},
    "Necrophos": {"heal", "anti_heal", "aoe"},
    "Oracle": {"save", "heal", "dispel"},
    "Phantom Assassin": {"evasion", "physical", "burst"},
    "Phantom Lancer": {"illusion", "mobility"},
    "Pudge": {"disable", "tanky", "hook"},
    "Rubick": {"magic", "disable"},
    "Sand King": {"stun", "aoe", "invis"},
    "Shadow Demon": {"illusion", "save", "disable"},
    "Silencer": {"silence", "global", "disable"},
    "Spectre": {"global", "tanky", "illusion"},
    "Spirit Breaker": {"stun", "global", "mobility"},
    "Sven": {"stun", "aoe", "physical"},
    "Terrorblade": {"illusion", "physical", "mobility"},
    "Tidehunter": {"stun", "aoe", "tanky"},
    "Timbersaw": {"tanky", "anti_physical", "mobility"},
    "Tinker": {"magic", "burst", "global"},
    "Tiny": {"stun", "burst", "physical"},
    "Treant Protector": {"invis", "save", "heal"},
    "Undying": {"heal", "tanky", "summon"},
    "Ursa": {"physical", "burst", "sustain"},
    "Venomancer": {"anti_heal", "slow", "aoe"},
    "Viper": {"anti_heal", "tanky", "slow"},
    "Warlock": {"heal", "summon", "aoe"},
    "Weaver": {"mobility", "sustain", "invis"},
    "Winter Wyvern": {"save", "disable", "aoe"},
    "Witch Doctor": {"heal", "stun", "magic"},
    "Wraith King": {"stun", "sustain", "summon"},
    "Zeus": {"magic", "true_sight", "global"},
}

ITEM_TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bkb": ("spell immune", "debuff immune", "magic immune"),
    "silence": ("silence", "silences", "silenced"),
    "root": ("root", "roots", "rooted"),
    "break": ("break", "breaks", "disables passive"),
    "anti_heal": ("heal reduction", "reduces healing", "reduces health regeneration", "healing"),
    "anti_evasion": ("true strike", "accuracy"),
    "detection": ("true sight", "reveal", "reveals", "detect", "detection"),
    "dispel": ("dispel", "dispels", "purge", "purges"),
    "save": ("barrier", "barriers", "shield", "shields", "save", "saves", "damage reduction", "damage block"),
    "anti_illusions": ("illusion", "illusions", "summon", "summons", "chain lightning", "cleave"),
    "anti_target": ("block", "blocks", "targeted", "reflect"),
    "mobility": ("blink", "teleport", "dash"),
    "sustain": ("lifesteal", "health regen", "regenerate", "regeneration"),
    "mana_regen": ("mana regen", "restore mana"),
    "spell_amp": ("spell damage", "spell amplify", "magic damage"),
    "attack_speed": ("attack speed",),
    "armor": ("armor",),
    "magic_resist": ("magic resistance", "magic resist"),
    "evasion": ("evasion", "miss chance"),
    "crit": ("critical", "crit", "crits"),
    "mana_burn": ("mana burn", "burn mana"),
    "aura": ("aura", "auras"),
    "summon": ("summon", "summons", "summoned"),
    "heal": ("heal", "heals", "healed", "healing", "restore health"),
    # Match the actual immunity wording, not every mention of "physical damage"
    # (Satanic, Basher, etc. would otherwise look like Ghost Scepter).
    "physical_immune": ("immune to physical", "physical immunity", "ethereal"),
    "status_resist": ("status resistance",),
    "spell_lifesteal": ("spell lifesteal",),
}

ITEM_TAG_OVERRIDES: dict[str, set[str]] = {
    "Black King Bar": {"bkb", "magic_resist", "save"},
    "Pipe of Insight": {"magic_resist", "aura", "save"},
    "Mage Slayer": {"magic_resist", "anti_magic"},
    "Eternal Shroud": {"magic_resist", "mana_regen", "sustain"},
    "Glimmer Cape": {"save", "magic_resist", "invis"},
    "Ghost Scepter": {"physical_immune", "save"},
    "Crimson Guard": {"physical_block", "armor", "aura"},
    "Assault Cuirass": {"armor", "attack_speed", "aura"},
    "Shiva's Guard": {"armor", "anti_heal", "anti_illusions", "aoe"},
    "Spirit Vessel": {"anti_heal", "health"},
    "Eye of Skadi": {"anti_heal", "stats", "slow"},
    "Monkey King Bar": {"anti_evasion", "damage", "attack_speed"},
    "Bloodthorn": {"silence", "anti_evasion", "damage"},
    "Silver Edge": {"break", "invis", "damage"},
    "Nullifier": {"dispel", "anti_save", "damage"},
    "Aeon Disk": {"save", "status_resist", "health"},
    "Linken's Sphere": {"anti_target", "stats", "save"},
    "Lotus Orb": {"dispel", "anti_target", "armor", "save"},
    "Manta Style": {"dispel", "illusion", "mobility", "attack_speed"},
    "Eul's Scepter": {"dispel", "save", "mobility", "mana_regen"},
    "Scythe of Vyse": {"hex", "disable", "mana_regen"},
    "Orchid Malevolence": {"silence", "mana_regen", "damage"},
    "Rod of Atos": {"root", "health", "mana_regen"},
    "Gleipnir": {"root", "anti_illusions", "aoe", "damage"},
    "Mjollnir": {"anti_illusions", "attack_speed", "aoe"},
    "Radiance": {"anti_illusions", "evasion", "aoe", "damage"},
    "Battle Fury": {"anti_illusions", "cleave", "damage"},
    "Heaven's Halberd": {"disarm", "evasion", "health"},
    "Solar Crest": {"armor", "save", "mana_regen"},
    "Hurricane Pike": {"mobility", "save", "attack_range"},
    "Force Staff": {"mobility", "save", "intelligence"},
    "Blink Dagger": {"mobility", "initiation"},
    "Overwhelming Blink": {"mobility", "aoe", "strength"},
    "Arcane Blink": {"mobility", "mana_regen", "spell_amp"},
    "Swift Blink": {"mobility", "attack_speed", "evasion"},
    "Dust of Appearance": {"detection", "invis"},
    "Sentry Ward": {"detection", "vision"},
    "Gem of True Sight": {"detection", "vision"},
    "Smoke of Deceit": {"invis", "mobility"},
    "Refresher Orb": {"spell_amp", "mana_regen"},
    "Aghanim's Scepter": {"spell_amp", "stats"},
    "Aghanim's Shard": {"spell_amp"},
    "Guardian Greaves": {"heal", "armor", "mana_regen", "dispel"},
    "Mekansm": {"heal", "armor", "aura"},
    "Holy Locket": {"heal", "save"},
    "Urn of Shadows": {"anti_heal", "mana_regen"},
    "Vladmir's Offering": {"lifesteal", "armor", "aura"},
    "Drum of Endurance": {"attack_speed", "mobility", "aura"},
    "Boots of Travel": {"mobility", "attack_speed"},
    "Power Treads": {"attack_speed", "stats"},
    "Phase Boots": {"mobility", "armor", "damage"},
    "Arcane Boots": {"mana_regen", "mobility"},
    "Tranquil Boots": {"health", "armor", "mobility"},
    "Magic Wand": {"health", "mana_regen", "sustain"},
    "Bottle": {"health", "mana_regen", "sustain"},
    "Hand of Midas": {"attack_speed", "gold"},
    "Desolator": {"damage", "armor_reduction"},
    "Satanic": {"lifesteal", "sustain", "damage"},
    "Butterfly": {"evasion", "attack_speed", "damage"},
    "Heart of Tarrasque": {"health", "sustain", "tanky"},
    "Abyssal Blade": {"stun", "disable", "damage"},
    "Skull Basher": {"stun", "disable", "damage"},
    "Bloodseeker's Thirst": set(),
}

# Tags used by the neutral-item engine.  Game descriptions are intentionally
# sparse (many neutral items only show an active ability), so the hand-written
# map is the reliable part; keyword parsing and numeric stats are added on top.
NEUTRAL_TAG_OVERRIDES: dict[str, set[str]] = {
    # Tier 1
    "ash_legion_shield": {"save", "damage_block", "aura"},
    "chipped_vest": {"damage_block", "damage", "retaliation", "tanky"},
    "dagger_of_ristul": {"damage", "sustain"},
    "dormant_curio": {"utility", "economy"},
    "duelist_gloves": {"attack_speed", "damage"},
    "foragers_kit": {"utility", "economy", "sustain"},
    "kobold_cup": {"mobility", "aura", "utility"},
    "occult_bracelet": {"mana_regen", "sustain"},
    "polliwog_charm": {"heal", "sustain", "mobility"},
    "possessed_mask": {"lifesteal", "sustain", "damage"},
    "stonefeather_satchel": {"mobility", "armor", "utility"},
    "weighted_dice": {"damage", "economy"},
    # Tier 2
    "crippling_crossbow": {"slow", "anti_heal", "damage"},
    "defiant_shell": {"damage_block", "retaliation", "tanky"},
    "essence_ring": {"health", "sustain", "tanky"},
    "mana_draught": {"mana_regen", "sustain"},
    "medallion_of_courage": {"armor", "armor_reduction"},
    "poor_mans_shield": {"damage_block", "tanky"},
    "searing_signet": {"magic", "damage", "nuker"},
    "seeds_of_serenity": {"heal", "aura", "sustain"},
    "pogo_stick": {"mobility", "escape"},
    # Tier 3
    "cloak_of_flames": {"aoe", "damage", "anti_illusions", "farming"},
    "gunpowder_gauntlets": {"damage", "magic", "aoe", "burst"},
    "jidi_pollen_bag": {"anti_heal", "aoe", "damage"},
    "partisans_brand": {"spell_amp", "nuker"},
    "psychic_headband": {"disable", "mobility", "utility"},
    "serrated_shiv": {"anti_evasion", "damage", "physical"},
    "spellslinger": {"mana_regen", "nuker"},
    "stormcrafter": {"damage", "slow", "magic", "aoe"},
    "unrelenting_eye": {"status_resist", "tanky"},
    # Tier 4
    "conjurers_catalyst": {"spell_amp", "nuker", "aoe"},
    "dandelion_amulet": {"magic_resist", "damage_block", "save"},
    "enchanters_bauble": {"utility", "economy"},
    "flayers_bota": {"damage", "attack_speed", "carry"},
    "giant_maul": {"crit", "damage", "slow", "carry"},
    "idol_of_screeauk": {"evasion", "status_resist", "mobility", "save"},
    "metamorphic_mandible": {"magic_resist", "mobility", "escape"},
    "prophets_pendulum": {"damage_block", "tanky", "save"},
    "rattlecage": {"damage", "slow", "aoe", "anti_illusions"},
    # Tier 5
    "demonicon": {"summon", "push", "damage", "mana_burn"},
    "dezun_bloodrite": {"spell_amp", "aoe", "nuker"},
    "divine_regalia": {"damage", "carry", "spell_amp"},
    "fallen_sky": {"stun", "aoe", "initiation", "magic"},
    "harmonizer": {"spell_amp", "mana_regen", "nuker"},
    "minotaur_horn": {"magic_resist", "save", "dispel", "status_resist"},
    "riftshadow_prism": {"illusion", "damage", "carry", "utility"},
    "spider_legs": {"mobility", "escape", "initiation"},
    "desolator_2": {"armor_reduction", "damage", "physical", "carry"},
    "heavy_blade": {"dispel", "damage", "anti_illusions", "mana_burn"},
}

# Raw item attribute -> canonical stat key.  OpenDota/dotaconstants changed the
# spelling of several attributes over the years; normalising here keeps the
# scoring code readable and patch-tolerant.
STAT_CANON: dict[str, str] = {
    "bonus_armor": "armor",
    "armor": "armor",
    "ally_armor": "armor",
    "enemy_armor": "armor_reduction",
    "armor_decrease": "armor_reduction",
    "corruption_armor": "armor_reduction",
    "bonus_magic_resistance": "magic_resist",
    "bonus_magic_resist": "magic_resist",
    "magic_resist": "magic_resist",
    "magic_block": "magic_resist",
    "bonus_magical_armor": "magic_resist",
    "magical_armor": "magic_resist",
    "bonus_health": "health",
    "health_bonus": "health",
    "health_gain": "health",
    "health": "health",
    "mana_regen": "mana_regen",
    "aura_health_regen": "health_regen",
    "health_regen": "health_regen",
    "bonus_attack_speed": "attack_speed",
    "attack_speed": "attack_speed",
    "bonus_damage": "damage",
    "base_damage": "damage",
    "bonus_max_damage": "damage",
    "damage": "damage",
    "hp_dmg": "damage",
    "hp_damage": "damage",
    "bonus_movespeed": "move_speed",
    "bonus_movement_speed_active": "move_speed",
    "movement_speed": "move_speed",
    "movespeed_pct": "move_speed",
    "movespeed_slow": "slow",
    "slow": "slow",
    "slow_pct": "slow",
    "evasion": "evasion",
    "lifesteal": "lifesteal",
    "spell_amp": "spell_amp",
    "status_resistance": "status_resist",
    "status_resist": "status_resist",
    "slow_resist": "status_resist",
    "max_slow_res": "status_resist",
    "knockback_resistance": "status_resist",
    "damage_block": "damage_block",
    "damage_block_melee": "damage_block",
    "damage_block_ranged": "damage_block",
    "block_amount": "damage_block",
    "damage_return_hero": "retaliation",
    "mana_restore": "mana_regen",
    "mana_restore_pct": "mana_regen",
    "mana_pct": "mana_regen",
    "heal_reduce": "anti_heal",
    "stun_duration": "stun",
    "crit_multiplier": "crit",
}

# Items that look like components in dotaconstants but are legitimate
# mid/late-game purchases in their own right.  dotaconstants marks every item
# that is used by another recipe as a component, which would otherwise hide
# finished items such as Aghanim's Scepter, Force Staff or Orchid.
STANDALONE_COMPONENTS = {
    "blink",
    "boots",
    "magic_stick",
    "wind_lace",
    "dust",
    "smoke_of_deceit",
    "arcane_boots",
    "tranquil_boots",
    "travel_boots",
    "force_staff",
    "echo_sabre",
    "vanguard",
    "helm_of_the_dominator",
    "mekansm",
    "pavise",
    "rod_of_atos",
    "orchid",
    "ultimate_scepter",
    # Other finished items that dotaconstants also lists as a component of an
    # upgrade.  They are routinely bought and used on their own.
    "ghost",
    "ancient_janggo",
    "veil_of_discord",
    "dragon_lance",
    "diffusal_blade",
    "cyclone",
    "phylactery",
    "witch_blade",
    "basher",
    "maelstrom",
    "invis_sword",
    "lesser_crit",
    "vladmir",
}

# Consumables/one-shot upgrades that should not appear as build items even if
# OpenDota's popularity buckets contain a few purchases.
NON_BUILD_KEYS = {
    "ultimate_scepter_2",  # Aghanim's Blessing
    "aegis",
    "cheese",
    "refresher_shard",
    "tome_of_knowledge",
}


def _text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_text_of(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_text_of(v) for v in value.values())
    return ""


def _keyword_tags(text: str, mapping: dict[str, tuple[str, ...]]) -> set[str]:
    """Match whole words only.

    Substring matching used to turn "health" into the "heal" tag and
    "fades" into "invis", which polluted threat detection for many heroes.
    """
    low = text.lower()
    tags: set[str] = set()
    for tag, words in mapping.items():
        for word in words:
            pattern = r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])"
            if re.search(pattern, low):
                tags.add(tag)
                break
    return tags


def _ability_text(ability: dict) -> str:
    # Lore is flavour text and produced false positives ("fades", "summoned
    # from the depths"), so only gameplay-facing fields are used for tags.
    parts = [
        ability.get("desc"),
        ability.get("dname"),
        ability.get("behavior"),
        ability.get("target_type"),
    ]
    return _text_of(parts)


def build_hero_profiles(
    heroes: Iterable[dict],
    stats: Iterable[dict],
    hero_abilities: dict[str, Any],
    abilities: dict[str, Any],
    complexity_by_name: dict[str, int] | None = None,
) -> dict[int, HeroProfile]:
    stats_by_id = {int(row.get("id") or 0): row for row in stats if row.get("id")}
    complexity_by_name = complexity_by_name or {}
    profiles: dict[int, HeroProfile] = {}
    for hero in heroes:
        try:
            hid = int(hero.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if hid <= 0:
            continue
        name = str(hero.get("localized_name") or hero.get("name") or hid)
        roles = frozenset(str(r) for r in (hero.get("roles") or []))
        tags: set[str] = set()
        ability_block = hero_abilities.get(hero.get("name") or "") or {}
        for ability_name in ability_block.get("abilities") or []:
            if not isinstance(ability_name, str):
                continue
            ability = abilities.get(ability_name) or {}
            text = _ability_text(ability)
            if not text:
                continue
            tags |= _keyword_tags(text, HERO_TAG_KEYWORDS)
            dmg = str(ability.get("dmg_type") or "").lower()
            if "magical" in dmg:
                tags.add("magic")
            elif "physical" in dmg:
                tags.add("physical")
            elif "pure" in dmg:
                tags.add("pure")

        if "Disabler" in roles:
            tags.add("disable")
        if "Initiator" in roles:
            tags.add("initiation")
        if "Durable" in roles:
            tags.add("tanky")
        if "Nuker" in roles:
            tags.add("nuker")
        if "Support" in roles:
            tags.add("support")
        if "Carry" in roles:
            tags.add("carry")
        if "Escape" in roles:
            tags.add("mobility")
        if "Pusher" in roles:
            tags.add("push")
        tags |= HERO_TAG_OVERRIDES.get(name, set())

        row = stats_by_id.get(hid, {})
        pub_pick = int(row.get("pub_pick") or 0)
        pub_win = int(row.get("pub_win") or 0)
        prior = 2000.0
        pub_wr = (pub_win + prior * 0.5) / (pub_pick + prior) if pub_pick >= 0 else 0.5

        profiles[hid] = HeroProfile(
            id=hid,
            name=name,
            roles=roles,
            primary_attr=str(hero.get("primary_attr") or ""),
            attack_type=str(hero.get("attack_type") or ""),
            tags=frozenset(tags),
            pub_wr=pub_wr,
            pub_pick=pub_pick,
            img=str(row.get("img") or hero.get("img") or ""),
            complexity=int(complexity_by_name.get(str(hero.get("name") or ""), 0) or 0),
        )
    return profiles


def _numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def build_item_profiles(items_raw: dict[str, Any]) -> dict[int, ItemProfile]:
    items_raw = items_raw or {}
    component_keys: set[str] = set()
    for raw in items_raw.values():
        if not isinstance(raw, dict):
            continue
        for part in raw.get("components") or []:
            if isinstance(part, str):
                component_keys.add(part)

    profiles: dict[int, ItemProfile] = {}
    for key, raw in items_raw.items():
        if not isinstance(raw, dict):
            continue
        try:
            iid = int(raw.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if iid <= 0:
            continue
        name = str(raw.get("dname") or key)
        if key.startswith("recipe_"):
            continue
        try:
            cost = int(raw.get("cost") or 0)
        except (TypeError, ValueError):
            cost = 0
        tier_value = raw.get("tier")
        try:
            tier = int(tier_value) if tier_value not in (None, "") else None
        except (TypeError, ValueError):
            tier = None
        neutral = tier is not None and 1 <= tier <= 5 and "token" not in key and "enhancement" not in key

        abilities = [a for a in (raw.get("abilities") or []) if isinstance(a, dict)]
        texts: list[str] = []
        for ability in abilities:
            texts.append(_text_of([ability.get("title"), ability.get("description")]))
        texts.append(_text_of(raw.get("notes")))
        texts.append(_text_of(raw.get("hint")))
        for attrib in raw.get("attrib") or []:
            if isinstance(attrib, dict):
                texts.append(_text_of(attrib.get("key")))
                texts.append(_text_of(attrib.get("display")))
        text = " ".join(texts)
        # "magical armor" is a magic barrier in Dota's item data, not physical
        # armor.  Strip it before keyword matching so Glimmer/barrier items do
        # not get a false "armor" tag.
        for token in ("bonus_magical_armor", "magical_armor", "magic_armor"):
            text = text.replace(token, " ")
        tags = _keyword_tags(text, ITEM_TAG_KEYWORDS)
        stats: dict[str, float] = {}

        for attrib in raw.get("attrib") or []:
            if not isinstance(attrib, dict):
                continue
            attr_key = str(attrib.get("key") or "").lower()
            value = _numeric(attrib.get("value"))
            if value is not None and attr_key:
                stats[attr_key] = value
                canonical = STAT_CANON.get(attr_key)
                if canonical:
                    stats[canonical] = max(stats.get(canonical, 0.0), value)
            if "magical_armor" in attr_key or "magic_armor" in attr_key:
                # Glimmer Cape's "bonus_magical_armor" is a magic damage
                # barrier, not physical armor.
                tags.add("magic_resist")
            elif "armor" in attr_key:
                tags.add("armor")
            if "magic_resistance" in attr_key or "magic_resist" in attr_key:
                tags.add("magic_resist")
            if "evasion" in attr_key:
                tags.add("evasion")
            if "lifesteal" in attr_key:
                tags.add("lifesteal")
            if "mana_regen" in attr_key or "mana_restore" in attr_key:
                tags.add("mana_regen")
            if "attack_speed" in attr_key:
                tags.add("attack_speed")
            if "spell_amp" in attr_key:
                tags.add("spell_amp")
            if "health_regen" in attr_key:
                tags.add("sustain")
            if "status_resistance" in attr_key or "slow_resist" in attr_key:
                tags.add("status_resist")
            if "damage_block" in attr_key or "damage_reduction" in attr_key:
                tags.add("physical_block")
                tags.add("damage_block")
            if "armor" in attr_key and ("decrease" in attr_key or "corruption" in attr_key or "enemy" in attr_key):
                tags.add("armor_reduction")
            if "crit" in attr_key:
                tags.add("crit")
            if "health" in attr_key:
                tags.add("health")
            if "slow" in attr_key:
                tags.add("slow")
        tags |= ITEM_TAG_OVERRIDES.get(name, set())
        tags |= NEUTRAL_TAG_OVERRIDES.get(key, set())

        components = tuple(str(c) for c in (raw.get("components") or []) if isinstance(c, str))
        created = bool(raw.get("created"))
        # "created" is also true for intermediate assemblies such as Yasha,
        # Sange and Perseverance.  If an item is consumed by another item, it
        # belongs in a component list, not in a finished mid/late build.
        is_component = key in component_keys and key not in STANDALONE_COMPONENTS
        if is_component:
            tags.add("component")

        profiles[iid] = ItemProfile(
            id=iid,
            key=key,
            name=name,
            cost=cost,
            tier=tier,
            tags=frozenset(tags),
            neutral=neutral,
            img=str(raw.get("img") or ""),
            stats=stats,
            components=components,
            created=created,
            component=is_component,
            qual=str(raw.get("qual") or ""),
            description=" ".join(t for t in texts if t).strip(),
        )
    return profiles


# ---------------------------------------------------------------------------
# OpenDota client
# ---------------------------------------------------------------------------


class OpenDota:
    def __init__(
        self,
        cache: JsonCache,
        config: Config | None = None,
        allow_network: bool = True,
    ) -> None:
        self.cache = cache
        self.config = config or Config()
        self.allow_network = allow_network
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self._lock = threading.Lock()
        self._last_request = 0.0
        self.last_warning = ""
        self.last_error = ""

    def _request_json(self, url: str, cache_key: str, ttl: float) -> Any:
        fresh = self.cache.get(cache_key, ttl)
        if fresh and not fresh.stale:
            return fresh.data
        if not self.allow_network:
            if fresh is not None:
                self.last_warning = (
                    f"Офлайн-режим: кэш {cache_key} устарел на {fresh.age / 3600:.1f} ч"
                )
                return fresh.data
            raise DataError(
                f"Офлайн-режим, а кэш {cache_key} пуст. Запустите приложение с интернетом."
            )

        with self._lock:
            wait = 1.05 - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                response = self.session.get(url, timeout=self.config.request_timeout)
                if response.status_code == 429:
                    time.sleep(5.0)
                    response = self.session.get(url, timeout=self.config.request_timeout)
                response.raise_for_status()
                data = response.json()
                self.cache.put(cache_key, data)
                self.last_error = ""
                return data
            except Exception as exc:
                self.last_error = str(exc)
                stale = self.cache.get(cache_key, ttl)
                if stale is not None:
                    self.last_warning = f"Нет сети, использую кэш: {cache_key} ({stale.age / 3600:.1f} ч)"
                    return stale.data
                raise DataError(f"Запрос {url} не удался: {exc}") from exc

    def heroes(self) -> list[dict]:
        return self._request_json(f"{OPEN_DOTA}/heroes", "heroes", 24 * 3600)

    def hero_stats(self) -> list[dict]:
        return self._request_json(f"{OPEN_DOTA}/heroStats", "hero_stats", 6 * 3600)

    def hero_abilities(self) -> dict[str, Any]:
        return self._request_json(f"{OPEN_DOTA}/constants/hero_abilities", "hero_abilities", 24 * 3600)

    def abilities(self) -> dict[str, Any]:
        return self._request_json(f"{OPEN_DOTA}/constants/abilities", "abilities", 24 * 3600)

    def matchups(self, hero_id: int) -> list[dict]:
        return self._request_json(
            f"{OPEN_DOTA}/heroes/{hero_id}/matchups",
            f"matchups_{hero_id}",
            12 * 3600,
        )

    def item_popularity(self, hero_id: int) -> dict[str, dict[str, int]]:
        return self._request_json(
            f"{OPEN_DOTA}/heroes/{hero_id}/itemPopularity",
            f"item_popularity_{hero_id}",
            12 * 3600,
        )

    def items(self) -> dict[str, Any]:
        try:
            return self._request_json(DOTACONSTANTS_ITEMS, "items_constants", 24 * 3600)
        except DataError:
            return self._request_json(f"{OPEN_DOTA}/constants/items", "items_constants_od", 24 * 3600)

    def patch_info(self, dota_root: Path | None) -> dict[str, str]:
        return read_patch_info(dota_root)

    def cache_age(self, key: str) -> float | None:
        result = self.cache.get(key, 10**12)
        return result.age if result else None


def matchup_index(rows: Iterable[dict]) -> dict[int, tuple[float, int]]:
    """Map opponent hero id -> (opponent winrate vs this hero, games)."""
    index: dict[int, tuple[float, int]] = {}
    for row in rows or []:
        try:
            opp = int(row.get("hero_id") or 0)
            games = int(row.get("games_played") or 0)
            wins = int(row.get("wins") or 0)
        except (TypeError, ValueError):
            continue
        if opp > 0 and games > 0:
            index[opp] = (wins / games, games)
    return index


def display_signed(value: float, digits: int = 1) -> str:
    return f"{value * 100:+.{digits}f}%"


def plural_ru(count: int, one: str, few: str, many: str) -> str:
    """Return the Russian noun form for a count (1 матч, 2 матча, 5 матчей)."""
    number = abs(int(count))
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def wilson_lower(wins: int, games: int, z: float = 1.64) -> float:
    if games <= 0:
        return 0.5
    p = wins / games
    denom = 1 + z * z / games
    centre = p + z * z / (2 * games)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * games)) / games)
    return (centre - spread) / denom
