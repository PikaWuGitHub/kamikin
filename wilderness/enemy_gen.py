"""
wilderness/enemy_gen.py
=======================
Procedural enemy generation for Wilderness Mode encounters.

Enemies are selected from the full champion roster filtered by
the current Realm's type identity. Level is sampled from the
range defined by the player's highest-level party member.

Elite encounters add more enemies per stage (ELITE_BASE + stage).

Design note
-----------
This module is intentionally simple. Realm filtering is the primary
content lever; future work can layer modifiers (buffs, boss mechanics,
difficulty tiers) without changing the interface.
"""

from __future__ import annotations
import random
import sys
import os
from copy import deepcopy
from typing import List, Dict, Optional, TYPE_CHECKING

# Allow importing from parent directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from battle_engine import Champion, BattleChampion
from .config import (
    ENEMY_LEVEL_OFFSET_MIN, ENEMY_LEVEL_OFFSET_MAX,
    ELITE_BASE_ENEMY_COUNT,
)
from .models import Realm
from .scaling import scale_champion, enemy_level_range


# ── Realm filtering ──────────────────────────────────────────────

def champions_for_realm(
    all_champions: Dict[str, Champion],
    realm: Realm,
) -> List[Champion]:
    """
    Return champions whose type(s) match the realm.

    Single realm  → champions with type1 or type2 == realm.primary
    Bridgeland    → champions whose types include either realm essence
                    (union approach for more variety)
    """
    pool: List[Champion] = []
    for c in all_champions.values():
        types = {c.type1}
        if c.type2:
            types.add(c.type2)

        if realm.is_bridgeland:
            if realm.primary in types or (realm.secondary and realm.secondary in types):
                pool.append(c)
        else:
            if realm.primary in types:
                pool.append(c)

    # Fallback: if realm filtering yields nothing (e.g. sparse type coverage)
    # use the full roster to keep the game playable.
    if not pool:
        pool = list(all_champions.values())

    return pool


# ── Enemy team generation ────────────────────────────────────────

def _sample_level(lo: int, hi: int) -> int:
    """Uniform sample from [lo, hi]."""
    return random.randint(lo, hi)


def generate_enemy_team(
    all_champions: Dict[str, Champion],
    realm: Realm,
    highest_player_level: int,
    team_size: int = 1,
    level_offset_min: int = ENEMY_LEVEL_OFFSET_MIN,
    level_offset_max: int = ENEMY_LEVEL_OFFSET_MAX,
) -> List[BattleChampion]:
    """
    Build a team of `team_size` enemy BattleChampions scaled to the
    appropriate level range.

    Returns ready-to-battle BattleChampion instances.
    """
    lo, hi  = enemy_level_range(highest_player_level, level_offset_min, level_offset_max)
    pool    = champions_for_realm(all_champions, realm)
    enemies: List[BattleChampion] = []

    for _ in range(team_size):
        champ   = deepcopy(random.choice(pool))
        level   = _sample_level(lo, hi)
        scaled  = scale_champion(champ, level)
        bc      = BattleChampion(scaled)
        bc.level = level  # for display in battle UI
        enemies.append(bc)

    return enemies


def generate_normal_encounter(
    all_champions: Dict[str, Champion],
    realm: Realm,
    highest_player_level: int,
) -> List[BattleChampion]:
    """Single-enemy wild encounter for normal battle nodes."""
    return generate_enemy_team(
        all_champions, realm, highest_player_level, team_size=1
    )


def generate_elite_encounter(
    all_champions: Dict[str, Champion],
    realm: Realm,
    highest_player_level: int,
    stage_number: int,
) -> List[BattleChampion]:
    """
    Elite encounter: ELITE_BASE + stage_number enemies.

    Stage 1 → 2 enemies
    Stage 2 → 3 enemies
    ...

    The design note in config.py flags this as a future pivot point:
    large stages may need a cap or modifier system instead of
    pure enemy count scaling.
    """
    count = ELITE_BASE_ENEMY_COUNT + stage_number
    return generate_enemy_team(
        all_champions, realm, highest_player_level,
        team_size=count,
        # Elite enemies are slightly stronger within range
        level_offset_min=ENEMY_LEVEL_OFFSET_MIN + 1,
        level_offset_max=ENEMY_LEVEL_OFFSET_MAX + 1,
    )


def generate_recruit_candidate(
    all_champions: Dict[str, Champion],
    realm: Realm,
    highest_player_level: int,
    shiny_chance: float = 1 / 4000,
) -> tuple[Champion, int, bool]:
    """
    Generate the champion offered for recruitment after an elite battle.

    Returns (champion, level, is_shiny).
    The caller (run_manager) decides whether the player accepts it.
    """
    lo, hi  = enemy_level_range(highest_player_level)
    pool    = champions_for_realm(all_champions, realm)
    champ   = deepcopy(random.choice(pool))
    level   = _sample_level(lo, hi)
    is_shiny = random.random() < shiny_chance
    return champ, level, is_shiny
