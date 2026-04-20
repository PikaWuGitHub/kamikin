"""
wilderness/scaling.py
=====================
Level-scaling utilities.

Formula (standard stats)
------------------------
  scaledStat = round((maxStat + resonance) * level / MAX_LEVEL)

where maxStat = base_stat × STAT_MULT.
  • At Lv100 with resonance=0: stat = base_stat × STAT_MULT  (unchanged)
  • At Lv100 with resonance=100: stat = base_stat × STAT_MULT + 100
  • At Lv50  with resonance=100: stat = (base_stat × STAT_MULT + 100) × 0.5

Resonance keys: "vit", "sta", "mgt", "mag", "grd", "wil", "swf" (each 1–100).
STA (MP) resonance is added FLAT (not prorated) because MP is full at all levels.

MP intentionally NOT level-scaled — monsters keep full MP at all levels.
"""

from __future__ import annotations
from copy import deepcopy
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from battle_engine import Champion, STAT_MULT
except ImportError:
    Champion = None   # type: ignore
    STAT_MULT = 10

from .config import MAX_LEVEL


# ── Core formula ─────────────────────────────────────────────────

def scaled_stat(base_stat: int, level: int, resonance: int = 0) -> int:
    """
    scaledStat = round((maxStat + resonance) * level / MAX_LEVEL)
    where maxStat = base_stat × STAT_MULT.

    Examples (STAT_MULT=10, MAX_LEVEL=100, resonance=0):
      base_vit=100, level=100 → 1000
      base_vit=100, level=5   → 50

    With resonance=50, level=100:
      base_vit=100 → 1050  (+50 bonus at max level)
    With resonance=50, level=50:
      base_vit=100 → 525   (both base and resonance prorated equally)
    """
    max_stat = base_stat * STAT_MULT
    return max(1, round((max_stat + resonance) * level / MAX_LEVEL))


# ── Champion scaling ──────────────────────────────────────────────

def scale_champion(champion: "Champion", level: int) -> "Champion":
    """
    Return a deep-copied Champion whose base_* stats are adjusted so that
    the engine's computed stats (base_* × STAT_MULT) match the level formula.

    MP (base_sta) is NOT scaled — full MP at all levels.

    The returned Champion's .max_hp, .mgt, etc. will be the correctly
    scaled values for use in BattleChampion.
    """
    c = deepcopy(champion)

    # Scale each combat stat: set base_x = round(original_base_x * level / MAX_LEVEL)
    # Then BattleChampion.max_hp = base_vit × STAT_MULT = round(max_hp × level / MAX_LEVEL) ✓
    c.base_vit = max(1, round(champion.base_vit * level / MAX_LEVEL))
    # MP stays at full — do NOT scale base_sta
    # c.base_sta unchanged
    c.base_mgt = max(1, round(champion.base_mgt * level / MAX_LEVEL))
    c.base_mag = max(1, round(champion.base_mag * level / MAX_LEVEL))
    c.base_grd = max(1, round(champion.base_grd * level / MAX_LEVEL))
    c.base_wil = max(1, round(champion.base_wil * level / MAX_LEVEL))
    c.base_swf = max(1, round(champion.base_swf * level / MAX_LEVEL))

    return c


# ── Convenience helpers ───────────────────────────────────────────

def party_member_scaled_stats(champion: "Champion", level: int,
                              resonance: dict = None) -> dict:
    """
    Return a dict of actual stat values for a champion at `level`.
    Used when creating PartyMember instances and for level-up recalculation.

    resonance   — optional dict keyed by stat name ("vit", "sta", "mgt", …),
                  values 1–100.  Each stat's resonance is folded into the
                  level-scaling formula.  STA (MP) resonance is added flat
                  because MP is not level-scaled.

    max_mp uses the full unscaled value (not prorated by level).
    """
    res = resonance or {}
    return {
        "max_hp": scaled_stat(champion.base_vit, level, res.get("vit", 0)),
        # MP full at all levels; STA resonance added flat (not prorated):
        "max_mp": champion.base_sta * STAT_MULT + res.get("sta", 0),
        "mgt":    scaled_stat(champion.base_mgt, level, res.get("mgt", 0)),
        "mag":    scaled_stat(champion.base_mag, level, res.get("mag", 0)),
        "grd":    scaled_stat(champion.base_grd, level, res.get("grd", 0)),
        "wil":    scaled_stat(champion.base_wil, level, res.get("wil", 0)),
        "swf":    scaled_stat(champion.base_swf, level, res.get("swf", 0)),
    }


def enemy_level_range(highest_player_level: int,
                      offset_min: int = -4,
                      offset_max: int = -2):
    """
    Return the (min_level, max_level) tuple for wild enemies.
    Uses the *highest* party level (not average) for deterministic scaling.
    Result is clamped so minimum level is ≥ 1.
    """
    lo = max(1, highest_player_level + offset_min)
    hi = max(1, highest_player_level + offset_max)
    return lo, hi


# ── EXP / Level-up ───────────────────────────────────────────────

def apply_level_up(member, champion: "Champion") -> str:
    """
    Increment a PartyMember's level by 1 and recalculate its stats.

    HP is adjusted proportionally (not healed) so leveling up doesn't
    grant free HP. MP stays at max.

    Resonance is preserved — the member's current resonance dict is passed
    into party_member_scaled_stats so the bonus correctly re-prorates at
    the new level.

    Returns a human-readable level-up message.

    PLACEHOLDER: a real EXP curve can replace the +1 logic in run_manager.
    The recalculation here is correct and does not need to change.
    """
    old_max_hp  = member.max_hp
    member.level = min(member.level + 1, MAX_LEVEL)
    resonance   = getattr(member, "resonance", None) or {}
    new_stats   = party_member_scaled_stats(champion, member.level, resonance)

    # Proportional HP carry-over
    if old_max_hp > 0 and not member.is_fainted:
        ratio           = member.current_hp / old_max_hp
        member.current_hp = max(1, round(new_stats["max_hp"] * ratio))
    elif member.is_fainted:
        member.current_hp = 0

    member.max_hp  = new_stats["max_hp"]
    member.max_mp  = new_stats["max_mp"]   # unchanged (no MP scaling)
    member.current_mp = min(member.current_mp, member.max_mp)

    gain = member.max_hp - old_max_hp
    return (f"  {member.champion_name} grew to Lv{member.level}! "
            f"Max HP {old_max_hp} → {member.max_hp} (+{gain})")

    # ── PLACEHOLDER: move unlock hook ──────────────────────────────
    # When move progression is implemented, call something like:
    #   check_move_unlocks(member, champion, member.level)
    # here before returning.
