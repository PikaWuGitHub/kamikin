#!/usr/bin/env python3
"""
Kamikin Battle Engine  v0.1 — Prototype
=========================================
Core battle simulation for Kamikin (formerly Kiboru: Legends Rise).

HOW TO RUN:
    python battle_engine.py                         # Interactive 1v1 (you vs AI)
    python battle_engine.py --sim Kitzen Torusk     # Auto-simulate two champions
    python battle_engine.py --damage-test           # Verify the damage formula
    python battle_engine.py --list                  # List all champions

DESIGN NOTES:
    • Stats at max level = base_stat × 10
    • Damage = (BP/100) × ATK × (250/(250+DEF)) × STAB × TypeMult × Variance(0.85–1.0) × CritMult
    • Critical hits: 5% chance, 1.5× damage multiplier
    • Physical moves: MGT vs GRD  |  Special moves: MAG vs WIL
    • Speed (SWF) determines turn order; ties broken randomly
    • Stamina (MP) depletes as moves are used; moves with insufficient MP cannot be selected
    • MP Regen: 5% of max MP per turn (passively) or 50% if guarding
    • Actions each turn: Attack, Strike (always available), Switch, Guard
"""

import csv
import random
import sys
import os
import math
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Tuple
from copy import deepcopy

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

STAT_MULT         = 10       # base_stat × STAT_MULT = actual stat at max level
MAX_LEVEL         = 100      # level cap; K scales proportionally with attacker level
K_CONSTANT        = 250      # Defense scaling constant at MAX_LEVEL in damage formula
STAB_BONUS        = 1.5      # Same-type attack bonus multiplier
VARIANCE_MIN      = 0.85
VARIANCE_MAX      = 1.00
MP_REGEN_PASSIVE  = 0.05     # MP recovered passively each turn (5% of max MP)
MP_REGEN_GUARD    = 0.50     # MP recovered when Guarding (50% of max MP)
CRIT_CHANCE       = 1 / 20   # Critical hit: 1 in 20 (5%)
CRIT_MULTIPLIER   = 1.5      # Critical hit damage multiplier
GUARD_DMG_REDUCE  = 0.50     # Guard reduces incoming damage by 50%
VENOM_BASE        = 1 / 16   # Venom starts at 1/16 max HP, escalates
SCORCH_DAMAGE     = 1 / 8    # Scorch (burn) does 1/8 max HP per turn
FROSTBITE_DAMAGE  = 1 / 8    # Frostbite does 1/8 max HP per turn
BLUR_SELF_HIT_BP  = 40       # BP of confusion self-hit
MAX_STAGE         = 6
MIN_STAGE         = -6

# Stat stage multipliers (same as Pokémon's 2n/2d table)
STAGE_MULT: Dict[int, float] = {
    -6: 0.250, -5: 0.286, -4: 0.333, -3: 0.400, -2: 0.500, -1: 0.667,
     0: 1.000,
     1: 1.500,  2: 2.000,  3: 2.500,  4: 3.000,  5: 3.500,  6: 4.000,
}

# Accuracy stage multipliers
ACC_STAGE_MULT: Dict[int, float] = {
    -6: 0.333, -5: 0.375, -4: 0.429, -3: 0.500, -2: 0.600, -1: 0.750,
     0: 1.000,
     1: 1.333,  2: 1.500,  3: 1.750,  4: 2.000,  5: 2.333,  6: 2.667,
}

# ═══════════════════════════════════════════════════════════════
# TYPE CHART
# row = attacking type, col = defending type
# 0.0 = immune, 0.5 = resisted, 1.0 = neutral, 2.0 = super effective
# Source: "Type Charts - 2025.07.07.xlsx" Output sheet
# ═══════════════════════════════════════════════════════════════

ESSENCES = [
    "Inferno", "Aqua", "Flora", "Terra", "Wind", "Volt",
    "Frost", "Mind", "Spirit", "Cursed", "Bless",
    "Mythos", "Cyber", "Cosmic", "Neutral",
]

# TYPE_CHART[attacker][defender] = multiplier
TYPE_CHART: Dict[str, Dict[str, float]] = {
    "Inferno": {"Inferno":0.5,"Aqua":0.5,"Flora":2.0,"Terra":2.0,"Wind":0.5,"Volt":1.0,"Frost":2.0,"Mind":1.0,"Spirit":0.5,"Cursed":1.0,"Bless":2.0,"Mythos":1.0,"Cyber":2.0,"Cosmic":1.0,"Neutral":1.0},
    "Aqua":    {"Inferno":2.0,"Aqua":0.5,"Flora":0.5,"Terra":2.0,"Wind":1.0,"Volt":1.0,"Frost":0.5,"Mind":1.0,"Spirit":0.5,"Cursed":1.0,"Bless":1.0,"Mythos":1.0,"Cyber":2.0,"Cosmic":0.5,"Neutral":1.0},
    "Flora":   {"Inferno":0.5,"Aqua":2.0,"Flora":0.5,"Terra":2.0,"Wind":0.5,"Volt":1.0,"Frost":0.5,"Mind":1.0,"Spirit":1.0,"Cursed":1.0,"Bless":0.5,"Mythos":1.0,"Cyber":2.0,"Cosmic":1.0,"Neutral":1.0},
    "Terra":   {"Inferno":2.0,"Aqua":1.0,"Flora":0.5,"Terra":0.5,"Wind":0.0,"Volt":2.0,"Frost":0.5,"Mind":0.5,"Spirit":0.5,"Cursed":1.0,"Bless":1.0,"Mythos":1.0,"Cyber":1.0,"Cosmic":1.0,"Neutral":1.0},
    "Wind":    {"Inferno":2.0,"Aqua":1.0,"Flora":1.0,"Terra":0.0,"Wind":0.5,"Volt":1.0,"Frost":2.0,"Mind":1.0,"Spirit":2.0,"Cursed":1.0,"Bless":2.0,"Mythos":1.0,"Cyber":0.5,"Cosmic":1.0,"Neutral":1.0},
    "Volt":    {"Inferno":1.0,"Aqua":2.0,"Flora":1.0,"Terra":0.0,"Wind":2.0,"Volt":0.5,"Frost":0.5,"Mind":0.5,"Spirit":2.0,"Cursed":1.0,"Bless":2.0,"Mythos":1.0,"Cyber":2.0,"Cosmic":0.5,"Neutral":1.0},
    "Frost":   {"Inferno":0.5,"Aqua":0.5,"Flora":2.0,"Terra":0.5,"Wind":2.0,"Volt":0.5,"Frost":0.5,"Mind":1.0,"Spirit":1.0,"Cursed":1.0,"Bless":0.5,"Mythos":0.5,"Cyber":2.0,"Cosmic":0.5,"Neutral":1.0},
    "Mind":    {"Inferno":2.0,"Aqua":1.0,"Flora":1.0,"Terra":0.5,"Wind":2.0,"Volt":2.0,"Frost":1.0,"Mind":1.0,"Spirit":0.0,"Cursed":0.5,"Bless":2.0,"Mythos":1.0,"Cyber":0.5,"Cosmic":1.0,"Neutral":1.0},
    "Spirit":  {"Inferno":1.0,"Aqua":1.0,"Flora":0.5,"Terra":0.5,"Wind":1.0,"Volt":0.5,"Frost":1.0,"Mind":2.0,"Spirit":1.0,"Cursed":0.0,"Bless":0.5,"Mythos":1.0,"Cyber":1.0,"Cosmic":2.0,"Neutral":1.0},
    "Cursed":  {"Inferno":0.5,"Aqua":1.0,"Flora":1.0,"Terra":0.5,"Wind":1.0,"Volt":2.0,"Frost":1.0,"Mind":0.5,"Spirit":2.0,"Cursed":1.0,"Bless":0.0,"Mythos":1.0,"Cyber":2.0,"Cosmic":1.0,"Neutral":1.0},
    "Bless":   {"Inferno":2.0,"Aqua":1.0,"Flora":1.0,"Terra":1.0,"Wind":2.0,"Volt":0.5,"Frost":0.5,"Mind":0.0,"Spirit":0.5,"Cursed":2.0,"Bless":1.0,"Mythos":1.0,"Cyber":1.0,"Cosmic":0.5,"Neutral":1.0},
    "Mythos":  {"Inferno":1.0,"Aqua":1.0,"Flora":1.0,"Terra":1.0,"Wind":2.0,"Volt":1.0,"Frost":1.0,"Mind":2.0,"Spirit":1.0,"Cursed":1.0,"Bless":0.5,"Mythos":0.5,"Cyber":0.0,"Cosmic":0.5,"Neutral":1.0},
    "Cyber":   {"Inferno":1.0,"Aqua":0.5,"Flora":1.0,"Terra":1.0,"Wind":1.0,"Volt":1.0,"Frost":1.0,"Mind":2.0,"Spirit":1.0,"Cursed":0.5,"Bless":0.5,"Mythos":2.0,"Cyber":0.5,"Cosmic":2.0,"Neutral":1.0},
    "Cosmic":  {"Inferno":1.0,"Aqua":1.0,"Flora":1.0,"Terra":1.0,"Wind":2.0,"Volt":1.0,"Frost":0.5,"Mind":2.0,"Spirit":0.5,"Cursed":2.0,"Bless":1.0,"Mythos":0.5,"Cyber":0.0,"Cosmic":0.5,"Neutral":1.0},
    "Neutral": {"Inferno":1.0,"Aqua":1.0,"Flora":1.0,"Terra":1.0,"Wind":1.0,"Volt":1.0,"Frost":1.0,"Mind":1.0,"Spirit":1.0,"Cursed":1.0,"Bless":1.0,"Mythos":1.0,"Cyber":1.0,"Cosmic":1.0,"Neutral":1.0},
}

def get_type_multiplier(move_essence: str, defender_type1: str, defender_type2: Optional[str]) -> float:
    """Combined type multiplier for a move against a dual-typed defender."""
    mult = TYPE_CHART.get(move_essence, {}).get(defender_type1, 1.0)
    if defender_type2:
        mult *= TYPE_CHART.get(move_essence, {}).get(defender_type2, 1.0)
    return mult

def type_label(mult: float) -> str:
    if mult == 0.0:  return "immune!"
    if mult >= 4.0:  return "SUPER x4!"
    if mult >= 2.0:  return "super effective!"
    if mult <= 0.25: return "not very effective (x0.25)..."
    if mult < 1.0:   return "not very effective..."
    return ""

# ═══════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════

class MoveCategory(Enum):
    PHYSICAL = "physical"   # MGT vs GRD
    SPECIAL  = "special"    # MAG vs WIL
    STATUS   = "status"     # No direct damage

class StatusEffect(Enum):
    NONE      = "none"
    SCORCH    = "scorch"      # 1/8 HP/turn, -50% MGT
    FROSTBITE = "frostbite"   # 1/8 HP/turn, -50% MAG
    VENOM     = "venom"       # Escalating poison
    SHOCK     = "shock"       # -50% SWF, 25% skip turn
    SLEEP     = "sleep"       # 1–3 turns can't act
    BLUR      = "blur"        # 50% accuracy, may hurt self
    STUN      = "stun"        # Skip exactly one turn
    CORRUPTED = "corrupted"   # Random stat drop 1–3 turns

# ═══════════════════════════════════════════════════════════════
# MOVE DATACLASS
# ═══════════════════════════════════════════════════════════════

@dataclass
class Move:
    name:        str
    essence:     str
    category:    MoveCategory
    base_power:  int          # 0 for status moves
    accuracy:    float        # 1.0 = perfect accuracy
    mp_cost:     int
    priority:    int   = 0    # +1 moves go before speed order
    tier:        int   = 1    # 1=basic, 2=standard, 3=advanced, 4=signature
    # Effects
    effect_chance:    float          = 0.0
    inflict_status:   StatusEffect   = StatusEffect.NONE
    self_boost_stat:  str            = ""   # e.g. "mgt","mag","swf","grd","wil"
    self_boost_stages:int            = 0
    drop_stat:        str            = ""
    drop_stages:      int            = 0
    recoil_fraction:  float          = 0.0  # fraction of damage dealt as recoil
    heal_fraction:    float          = 0.0  # fraction of max HP healed after hit
    drain_fraction:   float          = 0.0  # fraction of damage dealt as healing
    always_crit:      bool           = False  # guaranteed critical hit
    no_stab:          bool           = False  # suppress STAB even on same-type champion
    description:      str            = ""

    def __str__(self):
        cat = {"physical": "Phys", "special": "Spec", "status": "Stat"}[self.category.value]
        if self.base_power:
            return f"{self.name:<22} [{self.essence:<7}] {cat}  BP:{self.base_power:>3}  Acc:{int(self.accuracy*100)}%  MP:{self.mp_cost}"
        else:
            return f"{self.name:<22} [{self.essence:<7}] {cat}  —status—       Acc:{int(self.accuracy*100)}%  MP:{self.mp_cost}"

# ═══════════════════════════════════════════════════════════════
# CHAMPION DATACLASS
# ═══════════════════════════════════════════════════════════════

@dataclass
class Champion:
    id:    int
    name:  str
    type1: str
    type2: Optional[str]
    # Base stats (× STAT_MULT = actual max-level value)
    base_vit: int   # HP
    base_sta: int   # MP / Stamina
    base_mgt: int   # Physical ATK
    base_mag: int   # Special ATK
    base_grd: int   # Physical DEF
    base_wil: int   # Magic DEF
    base_swf: int   # Speed
    role:     str   = ""
    niche:    str   = ""
    moves:    List[Move] = field(default_factory=list)

    # Computed actual stats
    @property
    def max_hp(self)  -> int: return self.base_vit * STAT_MULT
    @property
    def max_mp(self)  -> int: return self.base_sta * STAT_MULT
    @property
    def mgt(self)     -> int: return self.base_mgt * STAT_MULT
    @property
    def mag(self)     -> int: return self.base_mag * STAT_MULT
    @property
    def grd(self)     -> int: return self.base_grd * STAT_MULT
    @property
    def wil(self)     -> int: return self.base_wil * STAT_MULT
    @property
    def swf(self)     -> int: return self.base_swf * STAT_MULT

    def type_str(self) -> str:
        return f"{self.type1}" + (f"/{self.type2}" if self.type2 else "")

    def summary(self) -> str:
        t = self.type_str()
        return (f"  {self.name:<12} [{t:<14}]  HP:{self.max_hp:>4}  MP:{self.max_mp:>4}  "
                f"MGT:{self.mgt:>4}  MAG:{self.mag:>4}  GRD:{self.grd:>4}  WIL:{self.wil:>4}  SWF:{self.swf:>4}"
                f"\n    Role: {self.role}  |  {self.niche}")

# ═══════════════════════════════════════════════════════════════
# BATTLE CHAMPION — wraps Champion with mutable battle state
# ═══════════════════════════════════════════════════════════════

class BattleChampion:
    def __init__(self, champion: Champion):
        self.base          = champion
        self.max_hp        = champion.max_hp  # explicit attribute; wilderness layer may override
        self.current_hp    = self.max_hp
        self.current_mp    = champion.max_mp
        self.status        = StatusEffect.NONE
        self.status_turns  = 0   # sleep duration, stun flag, etc.
        self.venom_count   = 0   # escalating venom counter
        self.stages: Dict[str, int] = {
            "mgt": 0, "mag": 0, "grd": 0, "wil": 0,
            "swf": 0, "acc": 0, "eva": 0,
        }
        self.is_fainted    = False
        self.guarding      = False
        self.flinched      = False
        self.first_turn    = True  # for first-impression-style moves
        self.level: int | None = None  # set by wilderness layer; None in standalone battles
        # Resonance bonus — raw values (1-100 scale) for combat stats.
        # The wilderness layer sets these from PartyMember.resonance.
        # get_stat() prorates them by level so low-level champions get a
        # proportionally smaller bonus (mirrors the main scaling formula).
        # Keys: "mgt", "mag", "grd", "wil", "swf"
        # VIT and STA resonance are applied directly to max_hp / max_mp
        # by party_member_to_battle_champion() in battle_hooks.py.
        self.resonance_bonus: Dict[str, int] = {}

    # ── Properties ──────────────────────────────────────────────
    @property
    def name(self) -> str: return self.base.name

    @property
    def hp_pct(self) -> float:
        return self.current_hp / self.max_hp

    def get_stat(self, key: str) -> int:
        """Actual battle stat including Resonance bonus and stage modifiers."""
        base = getattr(self.base, key)   # e.g. self.base.mgt (already level-scaled)

        # Add Resonance contribution, prorated by this champion's current level.
        # Formula mirrors the main scaling: resonance × level / MAX_LEVEL
        if self.resonance_bonus:
            raw_res = self.resonance_bonus.get(key, 0)
            if raw_res:
                lv = self.level if self.level is not None else MAX_LEVEL
                base = base + round(raw_res * lv / MAX_LEVEL)

        mult = STAGE_MULT[self.stages.get(key, 0)]
        # Status penalties (flat multipliers, not stages)
        if key == "mgt" and self.status == StatusEffect.SCORCH:
            mult *= 0.5
        if key == "mag" and self.status == StatusEffect.FROSTBITE:
            mult *= 0.5
        if key == "swf" and self.status == StatusEffect.SHOCK:
            mult *= 0.5
        return max(1, int(base * mult))

    # ── Battle Actions ───────────────────────────────────────────
    def apply_status(self, status: StatusEffect) -> Tuple[bool, str]:
        """Try to inflict a status. Returns (success, message)."""
        if self.status != StatusEffect.NONE:
            return False, f"{self.name} already has {self.status.value}!"
        self.status = status
        if status == StatusEffect.SLEEP:
            self.status_turns = random.randint(1, 3)
            return True, f"{self.name} fell asleep!"
        if status == StatusEffect.STUN:
            self.status_turns = 1
            return True, f"{self.name} is stunned and will skip its next turn!"
        msgs = {
            StatusEffect.SCORCH:    f"{self.name} was scorched!",
            StatusEffect.FROSTBITE: f"{self.name} got frostbite!",
            StatusEffect.VENOM:     f"{self.name} was badly poisoned!",
            StatusEffect.SHOCK:     f"{self.name} is paralyzed with shock!",
            StatusEffect.BLUR:      f"{self.name} is confused!",
            StatusEffect.CORRUPTED: f"{self.name} is corrupted!",
        }
        if status == StatusEffect.CORRUPTED:
            self.status_turns = random.randint(1, 3)
        elif status == StatusEffect.BLUR:
            self.status_turns = random.randint(2, 5)   # Blur lasts 2–5 turns then clears
        else:
            self.status_turns = 0
        return True, msgs.get(status, f"{self.name} has a status!")

    def change_stage(self, stat: str, delta: int) -> str:
        old = self.stages[stat]
        new = max(MIN_STAGE, min(MAX_STAGE, old + delta))
        self.stages[stat] = new
        change = new - old
        if change == 0:
            direction = "higher" if delta > 0 else "lower"
            return f"  {self.name}'s {stat} won't go any {direction}!"
        word = "rose" if change > 0 else "fell"
        mag  = {1:"",2:" sharply",3:" drastically"}.get(abs(change)," drastically")
        return f"  {self.name}'s {stat}{mag} {word}!"

    def take_damage(self, amount: int) -> int:
        amount = max(1, amount)
        actual = min(self.current_hp, amount)
        self.current_hp -= actual
        if self.current_hp <= 0:
            self.current_hp = 0
            self.is_fainted = True
        return actual

    def heal(self, amount: int) -> int:
        actual = min(self.max_hp - self.current_hp, max(0, amount))
        self.current_hp += actual
        return actual

    def regen_mp(self, amount: int):
        self.current_mp = min(self.base.max_mp, self.current_mp + amount)

    # ── Display ──────────────────────────────────────────────────
    def hp_bar(self, width: int = 20) -> str:
        filled = round(self.hp_pct * width)
        color_char = "█" if self.hp_pct > 0.5 else ("▓" if self.hp_pct > 0.25 else "▒")
        return color_char * filled + "░" * (width - filled)

    def status_tag(self) -> str:
        tags = {
            StatusEffect.SCORCH:    "[BRN]",
            StatusEffect.FROSTBITE: "[FRZ]",
            StatusEffect.VENOM:     "[PSN]",
            StatusEffect.SHOCK:     "[PAR]",
            StatusEffect.SLEEP:     "[SLP]",
            StatusEffect.BLUR:      "[CNF]",
            StatusEffect.STUN:      "[STN]",
            StatusEffect.CORRUPTED: "[CRP]",
        }
        return tags.get(self.status, "")

    def display(self, label: str = "") -> str:
        tag   = self.status_tag()
        lv    = f" Lv{self.level}" if self.level is not None else ""
        return (f"{label}{self.name}{lv} [{self.base.type_str()}] {tag}\n"
                f"  HP [{self.hp_bar()}] {self.current_hp:>4}/{self.max_hp}  "
                f"MP {self.current_mp:>3}/{self.base.max_mp}")

# ═══════════════════════════════════════════════════════════════
# DAMAGE CALCULATOR
# ═══════════════════════════════════════════════════════════════

def calc_damage(
    move:    Move,
    attacker: BattleChampion,
    defender: BattleChampion,
    apply_variance: bool = True,
) -> Tuple[int, float, float, str]:
    """
    Returns (damage, type_mult, variance, hit_description)

    Formula:
        damage = (BP/100) × ATK × (K/(K+DEF)) × STAB × TypeMult × Variance

    Physical: ATK = attacker.mgt,  DEF = defender.grd
    Special:  ATK = attacker.mag,  DEF = defender.wil

    K scales with the attacker's level so defense provides consistent mitigation
    at all levels (K_CONSTANT is the value at level 100):
        K = round(K_CONSTANT × attacker.level / MAX_LEVEL)
    When attacker.level is None (standalone battles outside wilderness), K_CONSTANT
    is used directly (equivalent to level-100 behaviour).
    """
    if move.category == MoveCategory.STATUS or move.base_power == 0:
        return 0, 1.0, 1.0, ""

    # Choose stat axis
    if move.category == MoveCategory.PHYSICAL:
        atk = attacker.get_stat("mgt")
        dfn = defender.get_stat("grd")
    else:
        atk = attacker.get_stat("mag")
        dfn = defender.get_stat("wil")

    # Guard reduction (defender guarded last turn)
    if defender.guarding:
        dfn = int(dfn * (1 + GUARD_DMG_REDUCE))  # effectively raises defence

    # Type multiplier
    type_mult = get_type_multiplier(
        move.essence, defender.base.type1, defender.base.type2
    )
    if type_mult == 0.0:
        return 0, 0.0, 1.0, f"  It had no effect on {defender.name}!"

    # STAB — suppressed when move.no_stab is set (e.g. the always-available Strike)
    stab = 1.0
    if not move.no_stab and (
        move.essence == attacker.base.type1 or
        move.essence == attacker.base.type2
    ):
        stab = STAB_BONUS

    # Variance
    variance = random.uniform(VARIANCE_MIN, VARIANCE_MAX) if apply_variance else 1.0

    # Critical hit — always_crit moves guarantee it; others roll 5%
    crit_mult = 1.0
    is_crit = False
    if move.always_crit or (apply_variance and random.random() < CRIT_CHANCE):
        crit_mult = CRIT_MULTIPLIER
        is_crit = True

    # K scales with attacker level; full K_CONSTANT at level 100 (or when level unknown)
    if attacker.level is not None:
        k = max(1, round(K_CONSTANT * attacker.level / MAX_LEVEL))
    else:
        k = K_CONSTANT

    # Core formula
    raw = (move.base_power / 100) * atk * (k / (k + dfn))
    damage = int(raw * stab * type_mult * variance * crit_mult)
    damage = max(1, damage)

    # Assemble hit description
    parts = []
    tl = type_label(type_mult)
    if tl:
        parts.append(tl)
    if stab > 1.0:
        parts.append("STAB!")
    if is_crit:
        parts.append("CRITICAL HIT!")
    hit_desc = "  " + "  ".join(parts) if parts else ""

    return damage, type_mult, variance, hit_desc

# ═══════════════════════════════════════════════════════════════
# MOVE DATABASE
# Each type has: 1 physical, 1 special, 1 strong move, 1 status,
#                1 self-boost, 1 priority, 1 drain/heal
# ═══════════════════════════════════════════════════════════════

MP_COST_MULTIPLIER = 10  # All move costs × 10

def _m(name, ess, cat, bp, acc, mp, t=2, pri=0, eff_ch=0.0,
       infl=StatusEffect.NONE, s_boost="", s_stages=0,
       drop="", drop_s=0, recoil=0.0, heal=0.0, drain=0.0,
       crit=False, desc="") -> Move:
    """
    Shorthand move constructor.
    t    = tier: 1=basic, 2=standard, 3=advanced, 4=signature
    crit = always_crit: guaranteed critical hit on every use
    """
    return Move(
        name=name, essence=ess, category=cat, base_power=bp,
        accuracy=acc, mp_cost=mp * MP_COST_MULTIPLIER, priority=pri,
        tier=t,
        effect_chance=eff_ch, inflict_status=infl,
        self_boost_stat=s_boost, self_boost_stages=s_stages,
        drop_stat=drop, drop_stages=drop_s,
        recoil_fraction=recoil, heal_fraction=heal,
        drain_fraction=drain, always_crit=crit, description=desc,
    )

P, S, X = MoveCategory.PHYSICAL, MoveCategory.SPECIAL, MoveCategory.STATUS

MOVE_DB: Dict[str, Move] = {}

# ═══════════════════════════════════════════════════════════════
# MOVE DATABASE
# ═══════════════════════════════════════════════════════════════
#
# Tier guide:
#   1 = Basic     (levels  1–15)  low BP, simple/no effects
#   2 = Standard  (levels 16–35)  moderate BP, one clear effect
#   3 = Advanced  (levels 36–60)  high BP or compound effects
#   4 = Signature (levels 61+  )  max power with meaningful trade-off
#
# Each type has 10 moves spanning all four tiers.
# Pool layout per type (indices 0-9):
#   0  T1 physical       1  T1 special        2  T1 status
#   3  T2 physical       4  T2 special        5  T2 status/utility
#   6  T3 physical/spec  7  T3 drain/support  8  T4 signature atk
#   9  T4 signature util
# ═══════════════════════════════════════════════════════════════

_moves_raw = [

    # ══════════════════════════════════════════════════════════
    # INFERNO
    # ══════════════════════════════════════════════════════════
    # T1 — basic
    _m("Cinder Jab",      "Inferno", P, 45, 1.00,  5, t=1, desc="A weak but reliable fire-coated punch."),
    _m("Ash Bolt",         "Inferno", S, 45, 1.00,  5, t=1, desc="Fires a small bolt of smouldering ash."),
    _m("Ember Sting",      "Inferno", X,  0, 1.00,  4, t=1, eff_ch=0.60, infl=StatusEffect.SCORCH,
                                                                desc="Small chance to inflict Scorch."),
    # T2 — standard
    _m("Flame Strike",    "Inferno", P, 90, 1.00, 14, t=2, desc="Fierce flaming blow."),
    _m("Ember Blast",     "Inferno", S, 90, 1.00, 14, t=2, desc="Magical fire burst."),
    _m("Will-O-Scorch",   "Inferno", X,  0, 0.85, 10, t=2, eff_ch=1.0, infl=StatusEffect.SCORCH,
                                                                desc="Reliably inflicts Scorch (burn)."),
    # T3 — advanced
    _m("Inferno Crash",   "Inferno", P,120, 1.00, 24, t=3, recoil=1/3, desc="Devastating — recoils 1/3 damage dealt."),
    _m("Flame Drain",     "Inferno", S, 80, 1.00, 20, t=3, drain=0.50, desc="Siphons life — heals 50% of damage."),
    # T4 — signature
    _m("Overheat",        "Inferno", S,135, 0.90, 30, t=4, eff_ch=1.0, s_boost="mag", s_stages=-2,
                                                                desc="Nuclear fire — drops own MAG by 2."),
    _m("Searing Boost",   "Inferno", X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="mag", s_stages=2,
                                                                desc="Sharply raises own MAG by 2 stages."),

    # ══════════════════════════════════════════════════════════
    # AQUA
    # ══════════════════════════════════════════════════════════
    _m("Splash Strike",   "Aqua",    P, 45, 1.00,  5, t=1, desc="A modest water-coated hit."),
    _m("Drizzle Pulse",   "Aqua",    S, 45, 1.00,  5, t=1, desc="Sends a ripple of pressurised water."),
    _m("Aqua Veil",       "Aqua",    X,  0, 1.00,  5, t=1, eff_ch=1.0, s_boost="grd", s_stages=1,
                                                                desc="Raises own GRD by 1 — defensive posture."),
    _m("Tidal Strike",    "Aqua",    P, 90, 1.00, 14, t=2, desc="A powerful wave-propelled blow."),
    _m("Hydro Pulse",     "Aqua",    S, 90, 1.00, 14, t=2, desc="Focused jet of magical water."),
    _m("Water Shiv",      "Aqua",    P, 45, 1.00,  7, t=2, pri=1, desc="Priority strike — fast water blade."),
    _m("Torrent Crash",   "Aqua",    P,120, 1.00, 24, t=3, recoil=1/3, desc="Devastating surge — recoils 1/3 damage."),
    _m("Tidal Drain",     "Aqua",    S, 80, 1.00, 20, t=3, drain=0.50, desc="Drowning pull — heals 50% of damage."),
    _m("Geyser Blast",    "Aqua",    S,120, 0.90, 28, t=4, drop="swf", drop_s=1,
                                                                desc="Erupts from beneath — drops target SWF by 1."),
    _m("Water Cleanse",   "Aqua",    X,  0, 1.00, 12, t=4, heal=0.50, desc="Purifying wave — heals 50% max HP."),

    # ══════════════════════════════════════════════════════════
    # FLORA
    # ══════════════════════════════════════════════════════════
    _m("Thorn Poke",      "Flora",   P, 45, 1.00,  5, t=1, desc="A basic jab with a barbed tendril."),
    _m("Pollen Drift",    "Flora",   S, 45, 1.00,  5, t=1, desc="Sends a cloud of charged pollen."),
    _m("Spore Dusting",   "Flora",   X,  0, 1.00,  4, t=1, eff_ch=0.30, infl=StatusEffect.VENOM,
                                                                desc="30% chance to inflict Venom."),
    _m("Vine Whip",       "Flora",   P, 90, 1.00, 14, t=2, desc="Lashes with a coiling vine."),
    _m("Petal Storm",     "Flora",   S, 90, 1.00, 14, t=2, desc="Razor petals slice the target."),
    _m("Leech Seed",      "Flora",   X,  0, 1.00, 10, t=2, eff_ch=1.0, infl=StatusEffect.VENOM,
                                                                desc="Plants a seed that drains HP each turn."),
    _m("Root Crush",      "Flora",   P,120, 1.00, 24, t=3, recoil=1/3, desc="Roots erupt violently — recoils 1/3 damage."),
    _m("Giga Drain",      "Flora",   S, 80, 1.00, 20, t=3, drain=0.50, desc="Drinks vitality — heals 50% of damage."),
    _m("Bloom Burst",     "Flora",   S,125, 0.90, 28, t=4, drop="wil", drop_s=1,
                                                                desc="Explosive bloom — drops target WIL by 1."),
    _m("Regen Spores",    "Flora",   X,  0, 1.00, 12, t=4, heal=0.50, desc="Restorative spores — heals 50% max HP."),

    # ══════════════════════════════════════════════════════════
    # TERRA
    # ══════════════════════════════════════════════════════════
    _m("Gravel Toss",     "Terra",   P, 45, 1.00,  5, t=1, desc="Flings small stones at the target."),
    _m("Dust Wave",       "Terra",   S, 40, 0.95,  5, t=1, desc="A low-accuracy cloud of sharpened dust."),
    _m("Stone Harden",    "Terra",   X,  0, 1.00,  5, t=1, eff_ch=1.0, s_boost="grd", s_stages=1,
                                                                desc="Hardens skin — raises own GRD by 1."),
    _m("Rock Slam",       "Terra",   P, 90, 1.00, 14, t=2, desc="Drives a boulder into the target."),
    _m("Earthen Pulse",   "Terra",   S, 90, 1.00, 14, t=2, desc="Seismic energy ripples outward."),
    _m("Rock Shard",      "Terra",   P, 45, 1.00,  7, t=2, pri=1, desc="Priority — launches a sharp rock fragment."),
    _m("Tectonic Crash",  "Terra",   P,120, 0.90, 24, t=3, recoil=1/3, desc="Earth-splitting blow — recoils 1/3 damage."),
    _m("Seismic Drain",   "Terra",   P, 80, 1.00, 20, t=3, drain=0.50, desc="Quake-powered drain — heals 50% of damage."),
    _m("Quake Burst",     "Terra",   S,125, 0.90, 28, t=4, drop="grd", drop_s=1,
                                                                desc="Shockwave breaks defences — drops target GRD by 1."),
    _m("Bedrock Stance",  "Terra",   X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="grd", s_stages=2,
                                                                desc="Immovable — sharply raises own GRD by 2."),

    # ══════════════════════════════════════════════════════════
    # WIND
    # ══════════════════════════════════════════════════════════
    _m("Gust Clip",       "Wind",    P, 45, 1.00,  5, t=1, desc="A glancing blow on the wind."),
    _m("Air Burst",       "Wind",    S, 40, 0.95,  5, t=1, desc="Fires a small pocket of compressed air."),
    _m("Breeze Veil",     "Wind",    X,  0, 1.00,  4, t=1, eff_ch=1.0, s_boost="swf", s_stages=1,
                                                                desc="A favourable gust — raises own SWF by 1."),
    _m("Gale Slash",      "Wind",    P, 90, 1.00, 14, t=2, desc="Cuts through the target with wind pressure."),
    _m("Cyclone Burst",   "Wind",    S, 90, 1.00, 14, t=2, desc="Spinning funnel of magical air."),
    _m("Gust Rush",       "Wind",    P, 45, 1.00,  7, t=2, pri=1, desc="Priority — rides the wind for a fast strike."),
    _m("Hurricane Strike","Wind",    P,120, 0.85, 24, t=3, recoil=1/3, desc="Reckless tempest blow — recoils 1/3 damage."),
    _m("Whirlwind Drain", "Wind",    S, 80, 1.00, 20, t=3, drain=0.50, desc="Vortex siphons energy — heals 50% of damage."),
    _m("Twister Blast",   "Wind",    S,125, 0.85, 28, t=4, drop="swf", drop_s=1,
                                                                desc="Devastating twister — drops target SWF by 1."),
    _m("Tailwind",        "Wind",    X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="swf", s_stages=2,
                                                                desc="Perfect tailwind — sharply raises own SWF by 2."),

    # ══════════════════════════════════════════════════════════
    # VOLT
    # ══════════════════════════════════════════════════════════
    _m("Static Tap",      "Volt",    P, 45, 1.00,  5, t=1, eff_ch=0.10, infl=StatusEffect.SHOCK,
                                                                desc="A light electric touch — 10% Shock."),
    _m("Charge Pulse",    "Volt",    S, 45, 1.00,  5, t=1, desc="Releases a small built-up charge."),
    _m("Thunder Wave",    "Volt",    X,  0, 0.90,  6, t=1, eff_ch=1.0, infl=StatusEffect.SHOCK,
                                                                desc="Paralyses the target with a static burst."),
    _m("Thunder Fang",    "Volt",    P, 90, 1.00, 14, t=2, eff_ch=0.15, infl=StatusEffect.SHOCK,
                                                                desc="Electric bite — 15% chance to Shock."),
    _m("Volt Beam",       "Volt",    S, 90, 1.00, 14, t=2, desc="Focused beam of raw electricity."),
    _m("Spark Rush",      "Volt",    P, 45, 1.00,  7, t=2, pri=1, desc="Priority — crackles forward in a flash."),
    _m("Thunderclap",     "Volt",    P,120, 0.90, 24, t=3, recoil=1/3, desc="Explosive thunder hit — recoils 1/3 damage."),
    _m("Volt Drain",      "Volt",    S, 80, 1.00, 20, t=3, drain=0.50, desc="Channels energy back — heals 50% of damage."),
    _m("Lightning Surge", "Volt",    S,130, 0.85, 28, t=4, drop="wil", drop_s=1,
                                                                desc="Overwhelming current — drops target WIL by 1."),
    _m("Overcharge",      "Volt",    X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="mgt", s_stages=2,
                                                                desc="Overloads circuits — sharply raises own MGT by 2."),

    # ══════════════════════════════════════════════════════════
    # FROST
    # ══════════════════════════════════════════════════════════
    _m("Cold Snap",       "Frost",   P, 45, 1.00,  5, t=1, desc="A chilling, numbing blow."),
    _m("Frost Mote",      "Frost",   S, 45, 1.00,  5, t=1, desc="Fires a tiny shard of magically frozen air."),
    _m("Chill Haze",      "Frost",   X,  0, 0.90,  4, t=1, eff_ch=0.30, infl=StatusEffect.FROSTBITE,
                                                                desc="30% chance to inflict Frostbite."),
    _m("Ice Fang",        "Frost",   P, 90, 1.00, 14, t=2, eff_ch=0.15, infl=StatusEffect.FROSTBITE,
                                                                desc="Freezing bite — 15% Frostbite."),
    _m("Blizzard Beam",   "Frost",   S, 90, 1.00, 14, t=2, desc="A concentrated column of blizzard energy."),
    _m("Ice Shard",       "Frost",   P, 45, 1.00,  7, t=2, pri=1, desc="Priority — hurls a razor-edged ice shard."),
    _m("Glacial Crash",   "Frost",   P,120, 1.00, 24, t=3, recoil=1/3, desc="Glacier impact — recoils 1/3 damage."),
    _m("Frozen Drain",    "Frost",   S, 80, 1.00, 20, t=3, drain=0.50, desc="Drains warmth — heals 50% of damage."),
    _m("Absolute Zero",   "Frost",   S,135, 0.85, 30, t=4, drop="swf", drop_s=1,
                                                                desc="Total freeze — drops target SWF by 1."),
    _m("Snow Cloak",      "Frost",   X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="wil", s_stages=2,
                                                                desc="Blanketing snow — sharply raises own WIL by 2."),

    # ══════════════════════════════════════════════════════════
    # MIND
    # ══════════════════════════════════════════════════════════
    _m("Mind Flick",      "Mind",    P, 45, 1.00,  5, t=1, desc="A brief psychic prod."),
    _m("Thought Nudge",   "Mind",    S, 45, 1.00,  5, t=1, desc="Sends a weak telepathic shockwave."),
    _m("Lull",            "Mind",    X,  0, 0.95,  4, t=1, eff_ch=0.30, infl=StatusEffect.BLUR,
                                                                desc="30% chance to inflict Blur (confusion)."),
    _m("Psionic Strike",  "Mind",    P, 90, 1.00, 14, t=2, desc="A focused psychic lance."),
    _m("Mind Blast",      "Mind",    S, 90, 1.00, 14, t=2, desc="Explosive telepathic discharge."),
    _m("Mental Edge",     "Mind",    P, 45, 1.00,  7, t=2, pri=1, desc="Priority — razor-sharp psionic slice."),
    _m("Psycho Crash",    "Mind",    P,120, 1.00, 24, t=3, recoil=1/3, desc="All-or-nothing mental slam — recoils 1/3."),
    _m("Focus Drain",     "Mind",    S, 80, 1.00, 20, t=3, drain=0.50, desc="Steals focus — heals 50% of damage."),
    _m("Thought Cannon",  "Mind",    S,125, 0.90, 28, t=4, drop="wil", drop_s=1,
                                                                desc="Fires a concentrated mind-beam — drops target WIL."),
    _m("Calm Mind",       "Mind",    X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="mag", s_stages=2,
                                                                desc="Perfect clarity — sharply raises own MAG by 2."),

    # ══════════════════════════════════════════════════════════
    # SPIRIT
    # ══════════════════════════════════════════════════════════
    _m("Wisp Touch",      "Spirit",  P, 45, 1.00,  5, t=1, desc="A ghostly grazing strike."),
    _m("Pale Beam",       "Spirit",  S, 45, 1.00,  5, t=1, desc="A dim, chilling spectral ray."),
    _m("Haunt",           "Spirit",  X,  0, 0.90,  4, t=1, eff_ch=0.25, infl=StatusEffect.BLUR,
                                                                desc="25% chance to inflict Blur with dread."),
    _m("Soul Strike",     "Spirit",  P, 90, 1.00, 14, t=2, desc="A direct hit to the target's spirit."),
    _m("Specter Blast",   "Spirit",  S, 90, 1.00, 14, t=2, desc="A burst of spectral energy."),
    _m("Ghost Rush",      "Spirit",  P, 45, 1.00,  7, t=2, pri=1, desc="Priority — passes through defences."),
    _m("Phantom Crash",   "Spirit",  P,120, 1.00, 24, t=3, recoil=1/3, desc="Ethereal slam — recoils 1/3 damage."),
    _m("Soul Drain",      "Spirit",  S, 80, 1.00, 20, t=3, drain=0.50, desc="Consumes the soul — heals 50% of damage."),
    _m("Sleep Shroud",    "Spirit",  X,  0, 0.80, 15, t=4, eff_ch=1.0, infl=StatusEffect.SLEEP,
                                                                desc="Puts the target into a deep sleep."),
    _m("Spirit Ward",     "Spirit",  X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="wil", s_stages=2,
                                                                desc="Spectral barrier — sharply raises own WIL by 2."),

    # ══════════════════════════════════════════════════════════
    # CURSED
    # ══════════════════════════════════════════════════════════
    _m("Taint Scratch",   "Cursed",  P, 45, 1.00,  5, t=1, desc="A corroding swipe that weakens foes."),
    _m("Blight Bolt",     "Cursed",  S, 45, 1.00,  5, t=1, desc="Fires a tiny bolt of corrupting energy."),
    _m("Toxic Hex",       "Cursed",  X,  0, 0.90,  5, t=1, eff_ch=1.0, infl=StatusEffect.VENOM,
                                                                desc="Curses the target with a lingering venom."),
    _m("Decay Slash",     "Cursed",  P, 90, 1.00, 14, t=2, desc="A rotting slash that eats through armour."),
    _m("Curse Bolt",      "Cursed",  S, 90, 1.00, 14, t=2, desc="Bolts of hexed energy pierce the target."),
    _m("Shadow Rush",     "Cursed",  P, 45, 1.00,  7, t=2, pri=1, desc="Priority — darts from shadow."),
    _m("Corruption Strike","Cursed", P,120, 1.00, 24, t=3, recoil=1/3, desc="Devastating corruption — recoils 1/3."),
    _m("Life Leech",      "Cursed",  S, 80, 1.00, 20, t=3, drain=0.50, desc="Parasitic hex — heals 50% of damage."),
    _m("Plague Burst",    "Cursed",  S,125, 0.90, 28, t=4, drop="grd", drop_s=1,
                                                                desc="Disease eruption — drops target GRD by 1."),
    _m("Dark Pact",       "Cursed",  X,  0, 1.00, 14, t=4, eff_ch=1.0, s_boost="mgt", s_stages=3,
                                                                desc="Forbidden oath — raises MGT +3 at great cost."),

    # ══════════════════════════════════════════════════════════
    # BLESS
    # ══════════════════════════════════════════════════════════
    _m("Holy Tap",        "Bless",   P, 45, 1.00,  5, t=1, desc="A gentle consecrated strike."),
    _m("Glimmer Shot",    "Bless",   S, 45, 1.00,  5, t=1, desc="A small burst of divine light."),
    _m("Mend",            "Bless",   X,  0, 1.00,  5, t=1, heal=0.20, desc="Modest self-heal — restores 20% max HP."),
    _m("Sacred Strike",   "Bless",   P, 90, 1.00, 14, t=2, desc="A powerful blessed blow."),
    _m("Holy Beam",       "Bless",   S, 90, 1.00, 14, t=2, desc="A purifying ray of holy energy."),
    _m("Light Rush",      "Bless",   P, 45, 1.00,  7, t=2, pri=1, desc="Priority — divine light propels the strike."),
    _m("Radiant Crash",   "Bless",   P,120, 1.00, 24, t=3, recoil=1/3, desc="Blinding radiance — recoils 1/3 damage."),
    _m("Radiant Drain",   "Bless",   S, 80, 1.00, 20, t=3, drain=0.50, desc="Holy siphon — heals 50% of damage."),
    _m("Purge Blast",     "Bless",   S,125, 0.90, 28, t=4, drop="wil", drop_s=1,
                                                                desc="Purifying force — drops target WIL by 1."),
    _m("Blessed Rest",    "Bless",   X,  0, 1.00, 12, t=4, heal=0.50,
                                                                desc="Consecrated rest — heals 50% max HP."),

    # ══════════════════════════════════════════════════════════
    # MYTHOS
    # ══════════════════════════════════════════════════════════
    _m("Rune Tap",        "Mythos",  P, 45, 1.00,  5, t=1, desc="Etches a minor rune into the target."),
    _m("Legend Whisper",  "Mythos",  S, 45, 1.00,  5, t=1, desc="A faint echo of legendary power."),
    _m("Elder Mark",      "Mythos",  X,  0, 0.90,  4, t=1, eff_ch=0.25, infl=StatusEffect.CORRUPTED,
                                                                desc="25% chance to inflict Corrupted."),
    _m("Ancient Claw",    "Mythos",  P, 90, 1.00, 14, t=2, desc="Strikes with the force of myth."),
    _m("Legend Pulse",    "Mythos",  S, 90, 1.00, 14, t=2, desc="A pulse of legendary energy."),
    _m("Myth Rush",       "Mythos",  P, 45, 1.00,  7, t=2, pri=1, desc="Priority — speed of ancient legend."),
    _m("Rune Crash",      "Mythos",  P,120, 1.00, 24, t=3, recoil=1/3, desc="Shatters runes for massive force — recoils 1/3."),
    _m("Rune Drain",      "Mythos",  S, 80, 1.00, 20, t=3, drain=0.50, desc="Absorbs runic power — heals 50% of damage."),
    _m("Myth Cannon",     "Mythos",  S,130, 0.85, 28, t=4, desc="Channelled legend — an unstoppable beam."),
    _m("Arcane Rite",     "Mythos",  X,  0, 1.00, 14, t=4, eff_ch=1.0, s_boost="mag", s_stages=2,
                                                                desc="Ancient ritual — sharply raises own MAG by 2."),

    # ══════════════════════════════════════════════════════════
    # CYBER
    # ══════════════════════════════════════════════════════════
    _m("Pixel Punch",     "Cyber",   P, 45, 1.00,  5, t=1, desc="A fast, mechanised jab."),
    _m("Signal Burst",    "Cyber",   S, 45, 1.00,  5, t=1, desc="Fires a burst of disruptive data."),
    _m("Circuit Shock",   "Cyber",   X,  0, 0.90,  5, t=1, eff_ch=1.0, infl=StatusEffect.SHOCK,
                                                                desc="Overloads the target's circuits — Shock."),
    _m("Data Strike",     "Cyber",   P, 90, 1.00, 14, t=2, desc="Executes a high-speed physical protocol."),
    _m("Laser Burst",     "Cyber",   S, 90, 1.00, 14, t=2, desc="Fires a tight, focused laser."),
    _m("Packet Rush",     "Cyber",   P, 45, 1.00,  7, t=2, pri=1, desc="Priority — data packets delivered instantly."),
    _m("System Crash",    "Cyber",   P,120, 1.00, 24, t=3, recoil=1/3, desc="Catastrophic system failure — recoils 1/3."),
    _m("Data Drain",      "Cyber",   S, 80, 1.00, 20, t=3, drain=0.50, desc="Siphons data life-force — heals 50% damage."),
    _m("Overload Beam",   "Cyber",   S,125, 0.90, 28, t=4, drop="grd", drop_s=1,
                                                                desc="Peak power emission — drops target GRD by 1."),
    _m("System Boost",    "Cyber",   X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="swf", s_stages=2,
                                                                desc="Overclocked — sharply raises own SWF by 2."),

    # ══════════════════════════════════════════════════════════
    # COSMIC
    # ══════════════════════════════════════════════════════════
    _m("Starfall Poke",   "Cosmic",  P, 45, 1.00,  5, t=1, desc="A tiny meteorite impact."),
    _m("Nebula Wisp",     "Cosmic",  S, 45, 1.00,  5, t=1, desc="A faint trace of cosmic radiation."),
    _m("Void Stun",       "Cosmic",  X,  0, 0.90,  5, t=1, eff_ch=0.30, infl=StatusEffect.STUN,
                                                                desc="30% chance to inflict Stun from the void."),
    _m("Void Strike",     "Cosmic",  P, 90, 1.00, 14, t=2, desc="Channels void energy into a heavy blow."),
    _m("Star Beam",       "Cosmic",  S, 90, 1.00, 14, t=2, desc="Focused stellar radiation."),
    _m("Warp Rush",       "Cosmic",  P, 45, 1.00,  7, t=2, pri=1, desc="Priority — bends space for instant strike."),
    _m("Singularity",     "Cosmic",  P,120, 1.00, 24, t=3, recoil=1/3, desc="Collapses space — recoils 1/3 damage."),
    _m("Star Drain",      "Cosmic",  S, 80, 1.00, 20, t=3, drain=0.50, desc="Stellar absorption — heals 50% of damage."),
    _m("Nova Burst",      "Cosmic",  S,135, 0.85, 30, t=4, drop="mag", drop_s=1,
                                                                desc="Stellar death-flash — drops target MAG by 1."),
    _m("Cosmic Veil",     "Cosmic",  X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="wil", s_stages=2,
                                                                desc="Dimensional shield — sharply raises own WIL by 2."),

    # ══════════════════════════════════════════════════════════
    # NEUTRAL
    # ══════════════════════════════════════════════════════════
    _m("Brawl",           "Neutral", P, 45, 1.00,  5, t=1, desc="A basic, untrained punch."),
    _m("Force Ripple",    "Neutral", S, 45, 1.00,  5, t=1, desc="A simple burst of raw force."),
    _m("Rattle",          "Neutral", X,  0, 1.00,  4, t=1, eff_ch=1.0, drop="grd", drop_s=1,
                                                                desc="Intimidates target — drops their GRD by 1."),
    _m("Quick Strike",    "Neutral", P, 90, 1.00, 14, t=2, desc="A clean, reliable strike."),
    _m("Force Pulse",     "Neutral", S, 90, 1.00, 14, t=2, desc="An efficient pulse of raw energy."),
    _m("Swift Strike",    "Neutral", P, 45, 1.00,  7, t=2, pri=1, desc="Priority — speed of pure instinct."),
    _m("Crash Down",      "Neutral", P,120, 1.00, 24, t=3, recoil=1/3, desc="Overhead slam — recoils 1/3 damage."),
    _m("Neutral Drain",   "Neutral", S, 80, 1.00, 20, t=3, drain=0.50, desc="Pure life-steal — heals 50% of damage."),
    _m("Null Wave",       "Neutral", S,115, 0.90, 28, t=4, drop="mgt", drop_s=1,
                                                                desc="Cancels all momentum — drops target MGT by 1."),
    _m("Adaptive Stance", "Neutral", X,  0, 1.00, 12, t=4, eff_ch=1.0, s_boost="mgt", s_stages=2,
                                                                desc="Adapts to any enemy — sharply raises own MGT by 2."),

    # ════════════════════════════════════════════════════════════════════════
    # CROSS-TYPE BROWSE MOVES
    # Sanctum-only: appear in CROSS_LEARNSET, never auto-assigned by POOL.
    # Low-to-mid BP — accessible coverage, diverse mechanics.
    # ════════════════════════════════════════════════════════════════════════

    # ── Inferno cross-type ───────────────────────────────────────────────
    _m("Cinder Rush",      "Inferno", P, 35, 1.00,  5, t=1, pri=1,
                                                    desc="Priority fire jab — quick scorching strike."),
    _m("Sacred Flame",     "Inferno", S, 65, 1.00, 10, t=2, eff_ch=0.10, infl=StatusEffect.SCORCH,
                                                    desc="Hallowed fire — 10% chance to Scorch."),
    _m("Immolate",         "Inferno", S,100, 0.90, 18, t=3, eff_ch=1.0, infl=StatusEffect.SCORCH,
                                                    desc="Engulfs in flame — guarantees Scorch."),

    # ── Aqua cross-type ──────────────────────────────────────────────────
    _m("Bubble",           "Aqua",    S, 35, 1.00,  4, t=1, eff_ch=0.20, drop="swf", drop_s=1,
                                                    desc="Tiny bubbles — 20% chance to reduce target SWF."),
    _m("Water Pulse",      "Aqua",    S, 60, 1.00,  9, t=2, eff_ch=0.20, infl=StatusEffect.BLUR,
                                                    desc="Resonating water wave — 20% chance to Blur."),
    _m("Surf",             "Aqua",    S, 90, 1.00, 15, t=2, desc="A surging wave of water."),

    # ── Flora cross-type ─────────────────────────────────────────────────
    _m("Absorb",           "Flora",   S, 40, 1.00,  5, t=1, drain=0.50,
                                                    desc="Sips vitality — heals 50% of damage dealt."),
    _m("Nature Bond",      "Flora",   X,  0, 1.00,  4, t=1, eff_ch=1.0, s_boost="wil", s_stages=1,
                                                    desc="Roots to nature — raises own WIL by 1."),
    _m("Thorn Volley",     "Flora",   P, 65, 1.00, 10, t=2, desc="Rapid barrage of hardened thorns."),
    _m("Overgrow",         "Flora",   X,  0, 1.00, 14, t=3, eff_ch=1.0, s_boost="mag", s_stages=2,
                                                    desc="Wild growth surge — sharply raises own MAG by 2."),

    # ── Terra cross-type ─────────────────────────────────────────────────
    _m("Earth Shard",      "Terra",   P, 50, 0.95,  6, t=1, desc="A jagged stone fragment hurled at speed."),
    _m("Iron Defense",     "Terra",   X,  0, 1.00,  9, t=2, eff_ch=1.0, s_boost="grd", s_stages=2,
                                                    desc="Hardens to iron — sharply raises own GRD by 2."),
    _m("Landslide",        "Terra",   P, 95, 0.90, 18, t=3, drop="swf", drop_s=1,
                                                    desc="Avalanche drive — drops target SWF by 1."),

    # ── Wind cross-type ──────────────────────────────────────────────────
    _m("Aerial Ace",       "Wind",    P, 55, 1.00,  7, t=1, desc="Swift aerial strike — never misses."),
    _m("Razor Wind",       "Wind",    S, 70, 1.00, 11, t=2, desc="Slicing gale of condensed wind."),
    _m("Feather Dance",    "Wind",    X,  0, 0.95, 10, t=2, eff_ch=1.0, drop="mgt", drop_s=2,
                                                    desc="Drifting feathers dull the foe — drops target MGT by 2."),
    _m("Gale Force",       "Wind",    S, 95, 0.90, 18, t=3, desc="Focused hurricane — devastating wind power."),

    # ── Volt cross-type ──────────────────────────────────────────────────
    _m("Numb Touch",       "Volt",    X,  0, 0.90,  5, t=1, eff_ch=1.0, infl=StatusEffect.SHOCK,
                                                    desc="A numbing touch — guarantees Shock."),
    _m("Galvanic Edge",    "Volt",    P, 60, 1.00,  9, t=2, eff_ch=0.20, infl=StatusEffect.SHOCK,
                                                    desc="Charged blade — 20% chance to Shock."),
    _m("Discharge",        "Volt",    S, 80, 1.00, 13, t=2, eff_ch=0.20, infl=StatusEffect.SHOCK,
                                                    desc="Burst of static — 20% chance to Shock."),
    _m("Plasma Surge",     "Volt",    S,100, 0.90, 20, t=3, drop="wil", drop_s=1,
                                                    desc="Plasma overload — drops target WIL by 1."),

    # ── Frost cross-type ─────────────────────────────────────────────────
    _m("Hail Shard",       "Frost",   P, 50, 1.00,  6, t=1, eff_ch=0.20, infl=StatusEffect.FROSTBITE,
                                                    desc="Sharp ice chunk — 20% chance to Frostbite."),
    _m("Chilling Aura",    "Frost",   X,  0, 1.00,  5, t=1, eff_ch=1.0, drop="swf", drop_s=1,
                                                    desc="Freezing aura — drops target SWF by 1."),
    _m("Ice Beam",         "Frost",   S, 90, 1.00, 14, t=2, eff_ch=0.15, infl=StatusEffect.FROSTBITE,
                                                    desc="Focused ice ray — 15% chance to Frostbite."),
    _m("Frozen Core",      "Frost",   X,  0, 1.00, 16, t=3, eff_ch=1.0, s_boost="wil", s_stages=3,
                                                    desc="Crystalline focus — drastically raises own WIL by 3."),

    # ── Mind cross-type ──────────────────────────────────────────────────
    _m("Confusion",        "Mind",    S, 55, 1.00,  7, t=1, eff_ch=0.20, infl=StatusEffect.BLUR,
                                                    desc="Psychic distortion — 20% chance to Blur."),
    _m("Psych Up",         "Mind",    X,  0, 1.00,  5, t=1, eff_ch=1.0, s_boost="mag", s_stages=1,
                                                    desc="Mental focus — raises own MAG by 1."),
    _m("Extrasensory",     "Mind",    S, 80, 1.00, 13, t=2, eff_ch=0.10, infl=StatusEffect.STUN,
                                                    desc="Sixth sense strike — 10% chance to Stun."),
    _m("Future Sight",     "Mind",    S,100, 1.00, 18, t=3, desc="Delayed psychic blow of great power."),

    # ── Spirit cross-type ────────────────────────────────────────────────
    _m("Hex",              "Spirit",  S, 55, 1.00,  7, t=1, desc="A simple curse flung at the target."),
    _m("Curse Touch",      "Spirit",  X,  0, 1.00,  5, t=1, eff_ch=1.0, drop="grd", drop_s=1,
                                                    desc="Cursed contact — drops target GRD by 1."),
    _m("Phantom Force",    "Spirit",  P, 90, 1.00, 14, t=2, desc="Phases through and strikes from within."),
    _m("Night Shade",      "Spirit",  S, 70, 1.00, 11, t=2, desc="Reliable spectral energy beam."),

    # ── Cursed cross-type ────────────────────────────────────────────────
    _m("Sap Life",         "Cursed",  S, 40, 1.00,  5, t=1, drain=0.50,
                                                    desc="Parasitic hex — heals 50% of damage dealt."),
    _m("Toxic Fang",       "Cursed",  P, 55, 1.00,  7, t=1, eff_ch=0.30, infl=StatusEffect.VENOM,
                                                    desc="Venom-coated bite — 30% chance to inflict Venom."),
    _m("Shadow Ball",      "Cursed",  S, 80, 1.00, 13, t=2, eff_ch=0.10, drop="wil", drop_s=1,
                                                    desc="Shadowy sphere — 10% chance to drop target WIL."),

    # ── Bless cross-type ─────────────────────────────────────────────────
    _m("Holy Light",       "Bless",   S, 60, 1.00,  8, t=1, eff_ch=0.10, infl=StatusEffect.STUN,
                                                    desc="Divine flash — 10% chance to Stun."),
    _m("Recover",          "Bless",   X,  0, 1.00, 10, t=2, heal=0.50,
                                                    desc="Healing blessing — restores 50% max HP."),
    _m("Aura Beam",        "Bless",   S, 85, 1.00, 14, t=2, eff_ch=1.0, drop="mag", drop_s=1,
                                                    desc="Holy aura blast — drops target MAG by 1."),

    # ── Mythos cross-type ────────────────────────────────────────────────
    _m("Ancient Power",    "Mythos",  S, 60, 1.00,  8, t=1, eff_ch=0.10, s_boost="mgt", s_stages=1,
                                                    desc="Primordial force — 10% chance to raise own MGT."),
    _m("Dragon Pulse",     "Mythos",  S, 85, 1.00, 14, t=2, desc="A pulse of raw legendary energy."),
    _m("Fate's Edge",      "Mythos",  P, 75, 1.00, 12, t=2, crit=True,
                                                    desc="Destiny-guided strike — always lands a critical hit."),
    _m("Rune Seal",        "Mythos",  X,  0, 1.00, 11, t=2, eff_ch=1.0, drop="wil", drop_s=2,
                                                    desc="Ancient seal — sharply drops target WIL by 2."),

    # ── Cyber cross-type ─────────────────────────────────────────────────
    _m("Hack",             "Cyber",   X,  0, 0.90,  5, t=1, eff_ch=1.0, drop="wil", drop_s=1,
                                                    desc="System intrusion — drops target WIL by 1."),
    _m("Zap Burst",        "Cyber",   S, 55, 1.00,  7, t=1, eff_ch=0.20, infl=StatusEffect.SHOCK,
                                                    desc="Data discharge — 20% chance to Shock."),
    _m("Beam Protocol",    "Cyber",   S, 80, 1.00, 13, t=2, desc="Standard-issue high-power laser."),
    _m("Logic Bomb",       "Cyber",   S, 90, 0.90, 15, t=2, eff_ch=0.20, infl=StatusEffect.STUN,
                                                    desc="Corrupted payload — 20% chance to Stun."),

    # ── Cosmic cross-type ────────────────────────────────────────────────
    _m("Lunar Beam",       "Cosmic",  S, 60, 1.00,  8, t=1, desc="A sliver of moonlight, focused."),
    _m("Gravity Pull",     "Cosmic",  X,  0, 1.00,  6, t=1, eff_ch=1.0, drop="swf", drop_s=2,
                                                    desc="Gravity crush — sharply drops target SWF by 2."),
    _m("Starlight",        "Cosmic",  S, 80, 1.00, 13, t=2, eff_ch=1.0, s_boost="wil", s_stages=1,
                                                    desc="Stellar glow — deals damage and raises own WIL."),
    _m("Black Hole",       "Cosmic",  X,  0, 0.90, 16, t=3, eff_ch=1.0, drop="swf", drop_s=3,
                                                    desc="Singularity — drastically drops target SWF by 3."),

    # ── Neutral cross-type ───────────────────────────────────────────────
    _m("Endure",           "Neutral", X,  0, 1.00,  4, t=1, eff_ch=1.0, s_boost="grd", s_stages=1,
                                                    desc="Braces for impact — raises own GRD by 1."),
    _m("Body Slam",        "Neutral", P, 75, 1.00, 11, t=2, eff_ch=0.30, infl=StatusEffect.SHOCK,
                                                    desc="Full body impact — 30% chance to Shock."),
    _m("Vital Strike",     "Neutral", P, 70, 1.00, 12, t=2, crit=True,
                                                    desc="Strikes a vital point — always a critical hit."),
    _m("Hyper Voice",      "Neutral", S, 85, 1.00, 14, t=2, desc="Overwhelming sonic wave of raw power."),
    _m("Bulk Up",          "Neutral", X,  0, 1.00, 10, t=2, eff_ch=1.0, s_boost="mgt", s_stages=2,
                                                    desc="Builds raw power — sharply raises own MGT by 2."),
    _m("Taunt",            "Neutral", X,  0, 1.00, 10, t=2, eff_ch=1.0, drop="mag", drop_s=2,
                                                    desc="Infuriates the target — sharply drops their MAG by 2."),
    _m("Seismic Slam",     "Neutral", P,100, 0.90, 20, t=3, desc="Earthshaking full-force slam."),
    _m("Adrenaline Rush",  "Neutral", X,  0, 1.00, 16, t=3, eff_ch=1.0, s_boost="swf", s_stages=3,
                                                    desc="Pure adrenaline — drastically raises own SWF by 3."),

    # ════════════════════════════════════════════════════════════════════════
    # FATE SEAL EXCLUSIVE MOVES
    # Hidden gacha pool — high power, unique trade-offs, champion-specific.
    # Never in POOL; only accessible via FATE_SEAL_POOL draw.
    # ════════════════════════════════════════════════════════════════════════

    # ── Inferno Fate Seal ────────────────────────────────────────────────
    _m("Blaze of Glory",   "Inferno", S,150, 0.90, 35, t=4, drop="mag", drop_s=3,
                                                    desc="Nuclear fire — drops own MAG by 3 stages after use."),
    _m("Flare Dance",      "Inferno", P,100, 0.95, 22, t=3, crit=True,
                                                    desc="Flame-wreathed assault — always strikes critically."),
    _m("Burning Judgment", "Bless",   S,110, 1.00, 25, t=4, eff_ch=1.0, infl=StatusEffect.SCORCH,
                                                    desc="Sacred fire verdict — guarantees Scorch."),

    # ── Aqua Fate Seal ───────────────────────────────────────────────────
    _m("Tidal Wave",       "Aqua",    S,140, 0.85, 32, t=4, recoil=0.25,
                                                    desc="Catastrophic surge — recoils 1/4 damage dealt."),
    _m("Whirlpool Prison", "Aqua",    X,  0, 0.85, 18, t=3, eff_ch=1.0, infl=StatusEffect.STUN,
                                                    desc="Traps the foe in a drowning vortex — guarantees Stun."),

    # ── Flora Fate Seal ──────────────────────────────────────────────────
    _m("Bloom Apocalypse", "Flora",   S,140, 0.85, 32, t=4, recoil=0.25,
                                                    desc="Explosive pollen detonation — recoils 1/4 damage."),
    _m("Spore Nightmare",  "Flora",   X,  0, 0.85, 18, t=3, eff_ch=1.0, infl=StatusEffect.SLEEP,
                                                    desc="Hallucinogenic spores — guarantees Sleep."),

    # ── Terra Fate Seal ──────────────────────────────────────────────────
    _m("Continental Drift","Terra",   P,140, 0.85, 32, t=4, drop="swf", drop_s=2,
                                                    desc="Tectonic force — drops own SWF by 2 after impact."),
    _m("Meteor Crash",     "Terra",   P,130, 0.90, 28, t=4, crit=True,
                                                    desc="Falling meteor strike — always a critical hit."),
    _m("Ancient Guardian", "Terra",   X,  0, 1.00, 20, t=4, eff_ch=1.0, s_boost="grd", s_stages=3,
                                                    desc="Primordial defence — drastically raises own GRD by 3."),

    # ── Wind Fate Seal ───────────────────────────────────────────────────
    _m("Tempest Wrath",    "Wind",    S,140, 0.80, 32, t=4, recoil=1/3,
                                                    desc="Catastrophic storm — recoils 1/3 damage dealt."),
    _m("Sky Rend",         "Wind",    P,120, 0.95, 26, t=4, crit=True,
                                                    desc="Tears the sky — always strikes critically."),

    # ── Volt Fate Seal ───────────────────────────────────────────────────
    _m("Megavolt",         "Volt",    S,140, 0.85, 32, t=4, eff_ch=0.50, infl=StatusEffect.SHOCK,
                                                    desc="Maximum discharge — 50% chance to Shock."),
    _m("Judgment Bolt",    "Volt",    S,110, 0.95, 24, t=3, crit=True,
                                                    desc="Divine lightning — always lands a critical hit."),

    # ── Frost Fate Seal ──────────────────────────────────────────────────
    _m("Blizzard Cataclysm","Frost",  S,140, 0.80, 32, t=4, eff_ch=0.30, infl=StatusEffect.FROSTBITE,
                                                    desc="End-of-winter storm — 30% chance to Frostbite."),
    _m("Permafrost",       "Frost",   P,120, 0.95, 26, t=4, crit=True,
                                                    desc="Absolute cold — always lands a critical hit."),
    _m("Deep Freeze",      "Frost",   X,  0, 0.80, 22, t=4, eff_ch=1.0, infl=StatusEffect.FROSTBITE,
                                                    desc="Total temperature collapse — guarantees Frostbite."),

    # ── Mind Fate Seal ───────────────────────────────────────────────────
    _m("Psychic Tempest",  "Mind",    S,140, 0.85, 32, t=4, recoil=0.25,
                                                    desc="Mind-shattering storm — recoils 1/4 damage dealt."),
    _m("Mind Break",       "Mind",    X,  0, 0.90, 18, t=3, eff_ch=1.0, drop="wil", drop_s=3,
                                                    desc="Shatters mental defences — drastically drops target WIL."),

    # ── Spirit Fate Seal ─────────────────────────────────────────────────
    _m("Soul Eater",       "Spirit",  S,100, 1.00, 22, t=4, drain=0.75,
                                                    desc="Consumes the soul — heals 75% of damage dealt."),
    _m("Phantom Pulse",    "Spirit",  S, 30, 1.00,  8, t=2, crit=True,
                                                    desc="Faint ghost echo — low power but always critical."),
    _m("Last Rites",       "Spirit",  X,  0, 0.90, 22, t=4, eff_ch=1.0, infl=StatusEffect.SLEEP,
                                                    desc="Final benediction — guarantees Sleep."),

    # ── Cursed Fate Seal ─────────────────────────────────────────────────
    _m("Necrotic Blast",   "Cursed",  S,130, 0.90, 28, t=4, eff_ch=1.0, infl=StatusEffect.VENOM,
                                                    desc="Rotting explosion — guarantees Venom."),
    _m("Cursed Seal",      "Cursed",  X,  0, 0.85, 20, t=4, eff_ch=1.0, drop="wil", drop_s=3,
                                                    desc="Binding hex — drastically drops target WIL by 3."),

    # ── Bless Fate Seal ──────────────────────────────────────────────────
    _m("Divine Smite",     "Bless",   P,120, 1.00, 26, t=4, crit=True,
                                                    desc="God's own blow — always strikes critically."),
    _m("Heavenly Judgment","Bless",   S,130, 0.90, 28, t=4, drop="mag", drop_s=2,
                                                    desc="Celestial verdict — sharply drops target MAG by 2."),
    _m("Holy Restoration", "Bless",   X,  0, 1.00, 20, t=4, heal=0.75,
                                                    desc="Divine grace — restores 75% of max HP."),

    # ── Mythos Fate Seal ─────────────────────────────────────────────────
    _m("Legend's End",     "Mythos",  S,150, 0.85, 35, t=4, drop="mag", drop_s=2,
                                                    desc="The final legend — drops own MAG by 2 after use."),
    _m("Elder Wrath",      "Mythos",  P,140, 0.85, 32, t=4, crit=True,
                                                    desc="Ancient fury — always lands a critical hit."),
    _m("Runic Catastrophe","Mythos",  S,130, 0.90, 28, t=4, eff_ch=1.0, infl=StatusEffect.CORRUPTED,
                                                    desc="Cataclysmic rune — guarantees Corrupted status."),
    _m("Runic Fury",       "Mythos",  P,110, 0.95, 24, t=3, crit=True,
                                                    desc="Runic power focus — always lands a critical hit."),

    # ── Cyber Fate Seal ──────────────────────────────────────────────────
    _m("Omega Protocol",   "Cyber",   S,140, 0.90, 32, t=4, drop="wil", drop_s=2,
                                                    desc="Final directive — drops own WIL by 2 after firing."),
    _m("Zero-Day",         "Cyber",   X,  0, 1.00, 20, t=4, eff_ch=1.0, drop="wil", drop_s=3,
                                                    desc="Exploits every weakness — drastically drops target WIL."),
    _m("Meltdown",         "Cyber",   P,130, 1.00, 28, t=4, recoil=1/3, crit=True,
                                                    desc="System meltdown — always crits but recoils 1/3 damage."),

    # ── Cosmic Fate Seal ─────────────────────────────────────────────────
    _m("Big Bang",         "Cosmic",  S,150, 0.80, 35, t=4, recoil=1/3,
                                                    desc="Universal detonation — recoils 1/3 damage dealt."),
    _m("Event Horizon",    "Cosmic",  X,  0, 0.90, 22, t=4, eff_ch=1.0, infl=StatusEffect.STUN,
                                                    desc="No escape — guarantees Stun from the void."),
    _m("Void Collapse",    "Cosmic",  S,130, 1.00, 28, t=4, drain=0.25,
                                                    desc="Collapses space to steal energy — heals 25% of damage."),

    # ── Neutral Fate Seal ────────────────────────────────────────────────
    _m("Wrath Surge",      "Neutral", P,150, 1.00, 35, t=4, recoil=0.50,
                                                    desc="Pure unhinged power — recoils 1/2 damage dealt."),
    _m("Timeless Roar",    "Neutral", X,  0, 1.00, 20, t=4, eff_ch=1.0, drop="grd", drop_s=3,
                                                    desc="Primal roar — drastically drops target GRD by 3."),
]

# ─────────────────────────────────────────────────────────────────
# Populate the move dictionary
# ─────────────────────────────────────────────────────────────────
for _mv in _moves_raw:
    MOVE_DB[_mv.name] = _mv

# Always-available basic attack — separate from the four move slots, like Guard.
# BP 30, Neutral type, no STAB regardless of attacker type, no MP cost.
STRIKE = Move(
    name="Strike", essence="Neutral", category=MoveCategory.PHYSICAL,
    base_power=30, accuracy=1.0, mp_cost=0, tier=1,
    no_stab=True,
    description="A basic physical blow. Neutral damage — always available.",
)

# ─────────────────────────────────────────────────────────────────
# TIERED MOVE POOLS
# Pool layout per type (10 moves, indices 0-9):
#   0  T1 physical   1  T1 special    2  T1 status/utility
#   3  T2 physical   4  T2 special    5  T2 status/utility
#   6  T3 physical   7  T3 drain      8  T4 signature atk
#   9  T4 signature util
# ─────────────────────────────────────────────────────────────────

POOL: Dict[str, List[str]] = {
    "Inferno": ["Cinder Jab","Ash Bolt","Ember Sting",
                "Flame Strike","Ember Blast","Will-O-Scorch",
                "Inferno Crash","Flame Drain","Overheat","Searing Boost"],
    "Aqua":    ["Splash Strike","Drizzle Pulse","Aqua Veil",
                "Tidal Strike","Hydro Pulse","Water Shiv",
                "Torrent Crash","Tidal Drain","Geyser Blast","Water Cleanse"],
    "Flora":   ["Thorn Poke","Pollen Drift","Spore Dusting",
                "Vine Whip","Petal Storm","Leech Seed",
                "Root Crush","Giga Drain","Bloom Burst","Regen Spores"],
    "Terra":   ["Gravel Toss","Dust Wave","Stone Harden",
                "Rock Slam","Earthen Pulse","Rock Shard",
                "Tectonic Crash","Seismic Drain","Quake Burst","Bedrock Stance"],
    "Wind":    ["Gust Clip","Air Burst","Breeze Veil",
                "Gale Slash","Cyclone Burst","Gust Rush",
                "Hurricane Strike","Whirlwind Drain","Twister Blast","Tailwind"],
    "Volt":    ["Static Tap","Charge Pulse","Thunder Wave",
                "Thunder Fang","Volt Beam","Spark Rush",
                "Thunderclap","Volt Drain","Lightning Surge","Overcharge"],
    "Frost":   ["Cold Snap","Frost Mote","Chill Haze",
                "Ice Fang","Blizzard Beam","Ice Shard",
                "Glacial Crash","Frozen Drain","Absolute Zero","Snow Cloak"],
    "Mind":    ["Mind Flick","Thought Nudge","Lull",
                "Psionic Strike","Mind Blast","Mental Edge",
                "Psycho Crash","Focus Drain","Thought Cannon","Calm Mind"],
    "Spirit":  ["Wisp Touch","Pale Beam","Haunt",
                "Soul Strike","Specter Blast","Ghost Rush",
                "Phantom Crash","Soul Drain","Sleep Shroud","Spirit Ward"],
    "Cursed":  ["Taint Scratch","Blight Bolt","Toxic Hex",
                "Decay Slash","Curse Bolt","Shadow Rush",
                "Corruption Strike","Life Leech","Plague Burst","Dark Pact"],
    "Bless":   ["Holy Tap","Glimmer Shot","Mend",
                "Sacred Strike","Holy Beam","Light Rush",
                "Radiant Crash","Radiant Drain","Purge Blast","Blessed Rest"],
    "Mythos":  ["Rune Tap","Legend Whisper","Elder Mark",
                "Ancient Claw","Legend Pulse","Myth Rush",
                "Rune Crash","Rune Drain","Myth Cannon","Arcane Rite"],
    "Cyber":   ["Pixel Punch","Signal Burst","Circuit Shock",
                "Data Strike","Laser Burst","Packet Rush",
                "System Crash","Data Drain","Overload Beam","System Boost"],
    "Cosmic":  ["Starfall Poke","Nebula Wisp","Void Stun",
                "Void Strike","Star Beam","Warp Rush",
                "Singularity","Star Drain","Nova Burst","Cosmic Veil"],
    "Neutral": ["Brawl","Force Ripple","Rattle",
                "Quick Strike","Force Pulse","Swift Strike",
                "Crash Down","Neutral Drain","Null Wave","Adaptive Stance"],
}

# ═══════════════════════════════════════════════════════════════════════
# CROSS_LEARNSET
# Moves each champion can browse and unlock at the Sanctum (cross-type
# coverage + unique utilities).  auto_moveset() never reads this — these
# are 100% Sanctum-gated.
#
# Design rules per champion:
#   • 8–12 moves spanning 2–4 non-native essences
#   • Always include 1–2 T1 moves for early-run accessibility
#   • At most 1–2 T4 moves from other type pools (aspirational)
#   • Neutral moves are broadly available to everyone
# ═══════════════════════════════════════════════════════════════════════

CROSS_LEARNSET: Dict[str, List[str]] = {
    # ── Inferno champions ────────────────────────────────────────────────
    "Solaire":  ["Psych Up","Confusion","Extrasensory","Future Sight","Calm Mind",
                 "Aerial Ace","Razor Wind","Holy Light","Recover","Vital Strike","Hyper Voice"],
    "Kitzen":   ["Aerial Ace","Cinder Rush","Galvanic Edge","Numb Touch","Body Slam",
                 "Vital Strike","Gust Rush","Discharge","Extrasensory","Endure","Adrenaline Rush"],
    "Ignovar":  ["Calm Mind","Arcane Rite","Tailwind","Bulk Up","Psych Up","Overgrow",
                 "Iron Defense","Recover","Frozen Core","Hyper Voice","Vital Strike"],
    "Pyrrin":   ["Calm Mind","Arcane Rite","Extrasensory","Future Sight","Phantom Force",
                 "Shadow Ball","Vital Strike","Bulk Up","Recover","Hyper Voice"],
    "Scaithe":  ["Vital Strike","Body Slam","Aerial Ace","Phantom Force","Toxic Fang",
                 "Sap Life","Galvanic Edge","Hail Shard","Ancient Power","Seismic Slam"],
    "Soltren":  ["Calm Mind","Extrasensory","Future Sight","Ice Beam","Phantom Force",
                 "Dragon Pulse","Overgrow","Recover","Hyper Voice","Psych Up"],
    "Fernace":  ["Psych Up","Calm Mind","Extrasensory","Tidal Drain","Water Pulse",
                 "Phantom Force","Vital Strike","Ice Beam","Dragon Pulse","Recover","Hyper Voice"],

    # ── Aqua champions ───────────────────────────────────────────────────
    "Finyu":    ["Chilling Aura","Gravity Pull","Iron Defense","Rune Seal","Taunt",
                 "Curse Touch","Nature Bond","Numb Touch","Recover","Endure","Bulk Up"],
    "Otanei":   ["Recover","Regen Spores","Blessed Rest","Nature Bond","Iron Defense",
                 "Frozen Core","Snow Cloak","Spirit Ward","Endure","Psych Up","Calm Mind"],
    "Eurgeist": ["Iron Defense","Recover","Frozen Core","Snow Cloak","Spirit Ward",
                 "Blessed Rest","Rune Seal","Taunt","Endure","Nature Bond","Feather Dance"],
    "Narviu":   ["Iron Defense","Recover","Blessed Rest","Nature Bond","Spirit Ward",
                 "Feather Dance","Rune Seal","Taunt","Endure","Bulk Up","Frozen Core"],

    # ── Flora champions ──────────────────────────────────────────────────
    "Mokoro":   ["Recover","Blessed Rest","Water Cleanse","Spirit Ward","Snow Cloak",
                 "Iron Defense","Feather Dance","Frozen Core","Endure","Nature Bond","Psych Up"],
    "Gravanel": ["Vital Strike","Body Slam","Seismic Slam","Phantom Force","Aerial Ace",
                 "Earth Shard","Ancient Power","Extrasensory","Discharge","Landslide"],
    "Lychbloom":["Recover","Blessed Rest","Iron Defense","Frozen Core","Snow Cloak",
                 "Spirit Ward","Feather Dance","Rune Seal","Taunt","Endure","Nature Bond"],
    "Rootmaw":  ["Iron Defense","Bedrock Stance","Endure","Recover","Taunt",
                 "Nature Bond","Spirit Ward","Feather Dance","Rune Seal","Bulk Up"],
    "Mylaren":  ["Calm Mind","Extrasensory","Arcane Rite","Ice Beam","Phantom Force",
                 "Discharge","Vital Strike","Shadow Ball","Dragon Pulse","Recover"],
    "Trevolt":  ["Extrasensory","Future Sight","Phantom Force","Ice Beam","Surf",
                 "Aerial Ace","Vital Strike","Body Slam","Recover","Hyper Voice"],

    # ── Terra champions ──────────────────────────────────────────────────
    "Brunhoka": ["Vital Strike","Body Slam","Seismic Slam","Aerial Ace","Phantom Force",
                 "Galvanic Edge","Ancient Power","Extrasensory","Discharge","Bulk Up"],
    "Torusk":   ["Iron Defense","Endure","Bulk Up","Recover","Taunt",
                 "Rune Seal","Feather Dance","Spirit Ward","Nature Bond","Numb Touch"],
    "Vollox":   ["Numb Touch","Discharge","Galvanic Edge","Iron Defense","Body Slam",
                 "Aerial Ace","Vital Strike","Seismic Slam","Endure","Bulk Up"],
    "Rokhara":  ["Vital Strike","Seismic Slam","Body Slam","Ancient Power","Aerial Ace",
                 "Extrasensory","Phantom Force","Discharge","Galvanic Edge","Bulk Up"],
    "Gravyrn":  ["Iron Defense","Endure","Taunt","Feather Dance","Spirit Ward",
                 "Recover","Nature Bond","Frozen Core","Rune Seal","Snow Cloak"],

    # ── Wind champions ───────────────────────────────────────────────────
    "Elyuri":   ["Recover","Blessed Rest","Regen Spores","Feather Dance","Nature Bond",
                 "Spirit Ward","Iron Defense","Psych Up","Endure","Taunt","Snow Cloak"],
    "Galeva":   ["Vital Strike","Body Slam","Numb Touch","Galvanic Edge","Discharge",
                 "Lunar Beam","Starlight","Aerial Ace","Hyper Voice","Endure"],
    "Sorin":    ["Aerial Ace","Numb Touch","Galvanic Edge","Cinder Rush","Vital Strike",
                 "Body Slam","Hail Shard","Extrasensory","Confusion","Adrenaline Rush"],
    "Skirra":   ["Numb Touch","Discharge","Galvanic Edge","Lunar Beam","Starlight",
                 "Recover","Vital Strike","Hyper Voice","Endure","Feather Dance"],
    "Miravi":   ["Extrasensory","Future Sight","Phantom Force","Soul Drain","Hex",
                 "Vital Strike","Hyper Voice","Endure","Aerial Ace","Recover"],
    "Skaiya":   ["Recover","Blessed Rest","Holy Light","Feather Dance","Nature Bond",
                 "Spirit Ward","Iron Defense","Regen Spores","Endure","Psych Up","Snow Cloak"],

    # ── Volt champions ───────────────────────────────────────────────────
    "Thryxa":   ["Aerial Ace","Razor Wind","Cinder Rush","Vital Strike","Body Slam",
                 "Zap Burst","Extrasensory","Adrenaline Rush","Endure","Hail Shard"],
    "Axerra":   ["Extrasensory","Confusion","Taunt","Shadow Ball","Phantom Force",
                 "Iron Defense","Vital Strike","Hyper Voice","Endure","Body Slam"],
    "Synkra":   ["Extrasensory","Future Sight","Calm Mind","Confusion","Shadow Ball",
                 "Ice Beam","Dragon Pulse","Hyper Voice","Vital Strike","Recover"],
    "Zintrel":  ["Aerial Ace","Hail Shard","Chilling Aura","Ice Beam","Body Slam",
                 "Vital Strike","Extrasensory","Confusion","Endure","Adrenaline Rush"],
    "Trevolt":  ["Absorb","Nature Bond","Giga Drain","Surf","Water Pulse",
                 "Extrasensory","Vital Strike","Hyper Voice","Recover","Endure"],
    "Zintrel":  ["Aerial Ace","Hail Shard","Chilling Aura","Ice Beam","Body Slam",
                 "Vital Strike","Extrasensory","Confusion","Endure","Adrenaline Rush"],

    # ── Frost champions ──────────────────────────────────────────────────
    "Friselle": ["Lunar Beam","Starlight","Gravity Pull","Water Pulse","Surf",
                 "Extrasensory","Future Sight","Calm Mind","Recover","Hyper Voice"],
    "Glacyn":   ["Lunar Beam","Starlight","Surf","Water Pulse","Confusion",
                 "Extrasensory","Calm Mind","Recover","Vital Strike","Hyper Voice"],
    "Nyoroa":   ["Water Pulse","Surf","Starlight","Recover","Taunt","Feather Dance",
                 "Gravity Pull","Endure","Vital Strike","Hyper Voice","Extrasensory"],
    "Frisela":  ["Recover","Blessed Rest","Regen Spores","Spirit Ward","Nature Bond",
                 "Lunar Beam","Starlight","Endure","Psych Up","Feather Dance"],
    "Narviu":   ["Recover","Spirit Ward","Nature Bond","Endure","Feather Dance",
                 "Rune Seal","Taunt","Iron Defense","Bulk Up","Frozen Core"],
    "Zerine":   ["Numb Touch","Discharge","Galvanic Edge","Aerial Ace","Body Slam",
                 "Vital Strike","Extrasensory","Confusion","Adrenaline Rush","Hail Shard"],

    # ── Mind champions ───────────────────────────────────────────────────
    "Noema":    ["Soul Drain","Hex","Phantom Force","Lunar Beam","Starlight","Calm Mind",
                 "Recover","Vital Strike","Hyper Voice","Taunt","Dragon Pulse"],
    "Sombrae":  ["Hex","Soul Drain","Phantom Force","Night Shade","Curse Touch",
                 "Extrasensory","Vital Strike","Taunt","Shadow Ball","Endure"],
    "Synkra":   ["Discharge","Galvanic Edge","Numb Touch","Hex","Soul Drain",
                 "Phantom Force","Recover","Vital Strike","Hyper Voice","Taunt"],

    # ── Spirit champions ─────────────────────────────────────────────────
    "Mourin":   ["Recover","Blessed Rest","Holy Light","Regen Spores","Nature Bond",
                 "Extrasensory","Psych Up","Spirit Ward","Taunt","Endure"],
    "Quenara":  ["Recover","Blessed Rest","Regen Spores","Holy Light","Spirit Ward",
                 "Nature Bond","Feather Dance","Endure","Psych Up","Frozen Core"],
    "Myrabyte": ["Hack","Zap Burst","Logic Bomb","Beam Protocol","Extrasensory",
                 "Confusion","Vital Strike","Taunt","Hyper Voice","Endure"],
    "Miravi":   ["Extrasensory","Future Sight","Phantom Force","Vital Strike","Razor Wind",
                 "Aerial Ace","Hyper Voice","Endure","Recover","Feather Dance"],

    # ── Cursed champions ─────────────────────────────────────────────────
    "Crynith":  ["Sap Life","Hex","Night Shade","Phantom Force","Gravity Pull","Black Hole",
                 "Cinder Rush","Sacred Flame","Vital Strike","Taunt","Hyper Voice"],
    "Somrel":   ["Hex","Night Shade","Phantom Force","Soul Drain","Gravity Pull",
                 "Lunar Beam","Cinder Rush","Vital Strike","Taunt","Shadow Ball"],
    "Noxtar":   ["Lunar Beam","Starlight","Gravity Pull","Black Hole","Soul Drain",
                 "Phantom Force","Hex","Vital Strike","Hyper Voice","Dragon Pulse"],
    "Lumira":   ["Recover","Blessed Rest","Holy Light","Hex","Sap Life","Soul Drain",
                 "Spirit Ward","Endure","Nature Bond","Psych Up","Feather Dance"],

    # ── Bless champions ──────────────────────────────────────────────────
    "Caelira":  ["Lunar Beam","Starlight","Psych Up","Extrasensory","Aerial Ace",
                 "Razor Wind","Vital Strike","Hyper Voice","Endure","Feather Dance"],
    "Elarin":   ["Lunar Beam","Starlight","Psych Up","Extrasensory","Future Sight",
                 "Feather Dance","Vital Strike","Hyper Voice","Recover","Endure"],
    "Pandana":  ["Lunar Beam","Starlight","Feather Dance","Nature Bond","Spirit Ward",
                 "Extrasensory","Psych Up","Frozen Core","Endure","Snow Cloak"],
    "Turtaura": ["Hack","Logic Bomb","Zap Burst","Extrasensory","Psych Up","Recover",
                 "Vital Strike","Endure","Feather Dance","Nature Bond"],
    "Skaiya":   ["Feather Dance","Aerial Ace","Razor Wind","Lunar Beam","Starlight",
                 "Psych Up","Endure","Nature Bond","Extrasensory","Recover"],
    "Frisela":  ["Lunar Beam","Starlight","Feather Dance","Nature Bond","Psych Up",
                 "Recover","Endure","Snow Cloak","Spirit Ward","Frozen Core"],

    # ── Mythos champions ─────────────────────────────────────────────────
    "Eldrune":  ["Extrasensory","Future Sight","Calm Mind","Hex","Soul Drain",
                 "Phantom Force","Shadow Ball","Vital Strike","Hyper Voice","Recover"],
    "Galivor":  ["Calm Mind","Extrasensory","Future Sight","Hex","Soul Drain",
                 "Shadow Ball","Recover","Vital Strike","Taunt","Dragon Pulse"],
    "Rokhara":  ["Vital Strike","Seismic Slam","Body Slam","Ancient Power","Extrasensory",
                 "Landslide","Earth Shard","Discharge","Galvanic Edge","Bulk Up"],

    # ── Cyber champions ──────────────────────────────────────────────────
    "Kyntra":   ["Iron Defense","Endure","Bulk Up","Recover","Taunt",
                 "Numb Touch","Rune Seal","Feather Dance","Spirit Ward","Nature Bond"],
    "Sonari":   ["Iron Defense","Endure","Recover","Taunt","Rune Seal",
                 "Spirit Ward","Feather Dance","Numb Touch","Nature Bond","Bulk Up"],
    "Hexel":    ["Hex","Sap Life","Toxic Fang","Night Shade","Shadow Ball",
                 "Extrasensory","Vital Strike","Taunt","Hyper Voice","Endure"],
    "Myrabyte": ["Hex","Soul Drain","Night Shade","Extrasensory","Psych Up",
                 "Calm Mind","Vital Strike","Hyper Voice","Taunt","Endure"],
    "Neorift":  ["Lunar Beam","Starlight","Extrasensory","Future Sight","Dragon Pulse",
                 "Vital Strike","Hyper Voice","Calm Mind","Recover","Taunt"],
    "Turtaura": ["Recover","Blessed Rest","Holy Light","Nature Bond","Feather Dance",
                 "Endure","Psych Up","Spirit Ward","Extrasensory","Snow Cloak"],

    # ── Cosmic champions ─────────────────────────────────────────────────
    "Mirellon": ["Extrasensory","Future Sight","Calm Mind","Dragon Pulse","Hex",
                 "Soul Drain","Phantom Force","Vital Strike","Hyper Voice","Recover"],
    "Neorift":  ["Hack","Logic Bomb","Extrasensory","Future Sight","Calm Mind",
                 "Dragon Pulse","Vital Strike","Hyper Voice","Taunt","Recover"],
    "Thalassa": ["Lunar Beam","Starlight","Dragon Pulse","Extrasensory","Future Sight",
                 "Soul Drain","Hex","Vital Strike","Hyper Voice","Calm Mind"],
    "Noxtar":   ["Lunar Beam","Starlight","Black Hole","Dragon Pulse","Extrasensory",
                 "Soul Drain","Hex","Vital Strike","Hyper Voice","Calm Mind"],

    # ── Neutral champions ────────────────────────────────────────────────
    "Mimari":   ["Confusion","Extrasensory","Hex","Sap Life","Aerial Ace","Hail Shard",
                 "Ancient Power","Cinder Rush","Holy Light","Earth Shard","Lunar Beam",
                 "Bubble","Absorb","Toxic Fang","Zap Burst","Nature Bond"],
    "Orrikai":  ["Iron Defense","Endure","Bulk Up","Recover","Taunt","Feather Dance",
                 "Rune Seal","Spirit Ward","Nature Bond","Extrasensory","Psych Up",
                 "Numb Touch","Chilling Aura","Gravity Pull","Curse Touch","Holy Light"],
}

# ═══════════════════════════════════════════════════════════════════════
# FATE_SEAL_POOL
# Hidden gacha pool per champion — drawn randomly at the Sanctum.
# Exclusively fate-seal-exclusive moves + powerful cross-type picks.
# ═══════════════════════════════════════════════════════════════════════

FATE_SEAL_POOL: Dict[str, List[str]] = {
    # ── Inferno ──────────────────────────────────────────────────────────
    "Solaire":  ["Blaze of Glory","Flare Dance","Burning Judgment","Psychic Tempest",
                 "Soul Eater","Divine Smite","Wrath Surge","Legend's End"],
    "Kitzen":   ["Flare Dance","Judgment Bolt","Sky Rend","Megavolt","Wrath Surge",
                 "Adrenaline Rush","Tempest Wrath","Meteor Crash"],
    "Ignovar":  ["Blaze of Glory","Flare Dance","Burning Judgment","Psychic Tempest",
                 "Elder Wrath","Legend's End","Wrath Surge","Ancient Guardian"],
    "Pyrrin":   ["Blaze of Glory","Flare Dance","Soul Eater","Psychic Tempest",
                 "Runic Catastrophe","Necrotic Blast","Wrath Surge","Legend's End"],
    "Scaithe":  ["Flare Dance","Necrotic Blast","Sky Rend","Meteor Crash","Wrath Surge",
                 "Meltdown","Elder Wrath","Continental Drift"],
    "Soltren":  ["Blaze of Glory","Megavolt","Judgment Bolt","Burning Judgment",
                 "Psychic Tempest","Wrath Surge","Legend's End","Runic Fury"],
    "Fernace":  ["Blaze of Glory","Flare Dance","Bloom Apocalypse","Burning Judgment",
                 "Tidal Wave","Psychic Tempest","Wrath Surge","Legend's End"],

    # ── Aqua ─────────────────────────────────────────────────────────────
    "Finyu":    ["Tidal Wave","Whirlpool Prison","Soul Eater","Blizzard Cataclysm",
                 "Psychic Tempest","Event Horizon","Wrath Surge","Legend's End"],
    "Otanei":   ["Tidal Wave","Holy Restoration","Soul Eater","Deep Freeze",
                 "Bloom Apocalypse","Timeless Roar","Wrath Surge","Last Rites"],
    "Eurgeist": ["Tidal Wave","Whirlpool Prison","Last Rites","Deep Freeze",
                 "Psychic Tempest","Timeless Roar","Wrath Surge","Ancient Guardian"],
    "Narviu":   ["Tidal Wave","Deep Freeze","Blizzard Cataclysm","Holy Restoration",
                 "Last Rites","Timeless Roar","Wrath Surge","Ancient Guardian"],

    # ── Flora ─────────────────────────────────────────────────────────────
    "Mokoro":   ["Bloom Apocalypse","Spore Nightmare","Holy Restoration","Soul Eater",
                 "Tidal Wave","Last Rites","Wrath Surge","Legend's End"],
    "Gravanel": ["Bloom Apocalypse","Meteor Crash","Elder Wrath","Sky Rend","Wrath Surge",
                 "Continental Drift","Necrotic Blast","Meltdown"],
    "Lychbloom":["Bloom Apocalypse","Spore Nightmare","Soul Eater","Last Rites",
                 "Holy Restoration","Timeless Roar","Wrath Surge","Legend's End"],
    "Rootmaw":  ["Bloom Apocalypse","Ancient Guardian","Continental Drift","Meteor Crash",
                 "Timeless Roar","Deep Freeze","Wrath Surge","Spore Nightmare"],
    "Mylaren":  ["Bloom Apocalypse","Runic Catastrophe","Psychic Tempest","Elder Wrath",
                 "Necrotic Blast","Soul Eater","Wrath Surge","Legend's End"],
    "Trevolt":  ["Bloom Apocalypse","Megavolt","Judgment Bolt","Tidal Wave",
                 "Psychic Tempest","Wrath Surge","Legend's End","Soul Eater"],

    # ── Terra ─────────────────────────────────────────────────────────────
    "Brunhoka": ["Continental Drift","Meteor Crash","Ancient Guardian","Elder Wrath",
                 "Wrath Surge","Timeless Roar","Meltdown","Sky Rend"],
    "Torusk":   ["Continental Drift","Ancient Guardian","Timeless Roar","Deep Freeze",
                 "Holy Restoration","Last Rites","Wrath Surge","Tidal Wave"],
    "Vollox":   ["Continental Drift","Megavolt","Judgment Bolt","Meteor Crash",
                 "Elder Wrath","Wrath Surge","Timeless Roar","Meltdown"],
    "Rokhara":  ["Continental Drift","Meteor Crash","Elder Wrath","Runic Fury",
                 "Wrath Surge","Timeless Roar","Ancient Guardian","Sky Rend"],
    "Gravyrn":  ["Ancient Guardian","Continental Drift","Timeless Roar","Tidal Wave",
                 "Holy Restoration","Last Rites","Deep Freeze","Wrath Surge"],

    # ── Wind ─────────────────────────────────────────────────────────────
    "Elyuri":   ["Tempest Wrath","Sky Rend","Holy Restoration","Spore Nightmare",
                 "Soul Eater","Last Rites","Timeless Roar","Wrath Surge"],
    "Galeva":   ["Tempest Wrath","Sky Rend","Megavolt","Judgment Bolt","Wrath Surge",
                 "Big Bang","Adrenaline Rush","Meltdown"],
    "Sorin":    ["Tempest Wrath","Sky Rend","Flare Dance","Judgment Bolt","Wrath Surge",
                 "Adrenaline Rush","Meteor Crash","Meltdown"],
    "Skirra":   ["Tempest Wrath","Sky Rend","Big Bang","Event Horizon","Wrath Surge",
                 "Adrenaline Rush","Soul Eater","Judgment Bolt"],
    "Miravi":   ["Tempest Wrath","Soul Eater","Psychic Tempest","Last Rites",
                 "Runic Catastrophe","Wrath Surge","Legend's End","Mind Break"],
    "Skaiya":   ["Tempest Wrath","Holy Restoration","Soul Eater","Last Rites",
                 "Burning Judgment","Timeless Roar","Wrath Surge","Spore Nightmare"],

    # ── Volt ─────────────────────────────────────────────────────────────
    "Thryxa":   ["Megavolt","Judgment Bolt","Tempest Wrath","Sky Rend","Wrath Surge",
                 "Adrenaline Rush","Meltdown","Flare Dance"],
    "Axerra":   ["Megavolt","Judgment Bolt","Omega Protocol","Zero-Day","Psychic Tempest",
                 "Wrath Surge","Timeless Roar","Meltdown"],
    "Synkra":   ["Megavolt","Judgment Bolt","Omega Protocol","Psychic Tempest",
                 "Mind Break","Wrath Surge","Legend's End","Runic Catastrophe"],
    "Zintrel":  ["Megavolt","Judgment Bolt","Blizzard Cataclysm","Permafrost",
                 "Adrenaline Rush","Wrath Surge","Tempest Wrath","Meltdown"],

    # ── Frost ─────────────────────────────────────────────────────────────
    "Friselle": ["Blizzard Cataclysm","Permafrost","Deep Freeze","Tidal Wave",
                 "Psychic Tempest","Big Bang","Wrath Surge","Soul Eater"],
    "Glacyn":   ["Blizzard Cataclysm","Permafrost","Deep Freeze","Tidal Wave",
                 "Psychic Tempest","Event Horizon","Wrath Surge","Legend's End"],
    "Nyoroa":   ["Blizzard Cataclysm","Permafrost","Tidal Wave","Event Horizon",
                 "Psychic Tempest","Soul Eater","Wrath Surge","Timeless Roar"],
    "Frisela":  ["Blizzard Cataclysm","Deep Freeze","Holy Restoration","Last Rites",
                 "Soul Eater","Timeless Roar","Wrath Surge","Spore Nightmare"],
    "Zerine":   ["Blizzard Cataclysm","Permafrost","Megavolt","Judgment Bolt",
                 "Flare Dance","Adrenaline Rush","Wrath Surge","Meltdown"],

    # ── Mind ─────────────────────────────────────────────────────────────
    "Noema":    ["Psychic Tempest","Mind Break","Soul Eater","Last Rites",
                 "Runic Catastrophe","Big Bang","Wrath Surge","Legend's End"],
    "Sombrae":  ["Psychic Tempest","Mind Break","Soul Eater","Necrotic Blast",
                 "Runic Catastrophe","Cursed Seal","Wrath Surge","Legend's End"],
    "Synkra":   ["Psychic Tempest","Judgment Bolt","Mind Break","Megavolt",
                 "Omega Protocol","Wrath Surge","Legend's End","Runic Catastrophe"],

    # ── Spirit ────────────────────────────────────────────────────────────
    "Mourin":   ["Soul Eater","Last Rites","Holy Restoration","Phantom Pulse",
                 "Spore Nightmare","Timeless Roar","Wrath Surge","Psychic Tempest"],
    "Quenara":  ["Soul Eater","Last Rites","Holy Restoration","Phantom Pulse",
                 "Burning Judgment","Timeless Roar","Wrath Surge","Deep Freeze"],
    "Myrabyte": ["Soul Eater","Phantom Pulse","Omega Protocol","Zero-Day",
                 "Meltdown","Cursed Seal","Wrath Surge","Psychic Tempest"],
    "Miravi":   ["Soul Eater","Phantom Pulse","Last Rites","Tempest Wrath",
                 "Psychic Tempest","Runic Catastrophe","Wrath Surge","Mind Break"],

    # ── Cursed ────────────────────────────────────────────────────────────
    "Crynith":  ["Necrotic Blast","Cursed Seal","Blaze of Glory","Big Bang",
                 "Soul Eater","Wrath Surge","Timeless Roar","Runic Catastrophe"],
    "Somrel":   ["Necrotic Blast","Cursed Seal","Soul Eater","Psychic Tempest",
                 "Big Bang","Wrath Surge","Legend's End","Mind Break"],
    "Noxtar":   ["Necrotic Blast","Cursed Seal","Big Bang","Event Horizon",
                 "Void Collapse","Soul Eater","Wrath Surge","Legend's End"],
    "Lumira":   ["Necrotic Blast","Cursed Seal","Holy Restoration","Soul Eater",
                 "Last Rites","Burning Judgment","Wrath Surge","Timeless Roar"],

    # ── Bless ─────────────────────────────────────────────────────────────
    "Caelira":  ["Divine Smite","Heavenly Judgment","Holy Restoration","Burning Judgment",
                 "Sky Rend","Psychic Tempest","Wrath Surge","Timeless Roar"],
    "Elarin":   ["Divine Smite","Heavenly Judgment","Holy Restoration","Burning Judgment",
                 "Psychic Tempest","Soul Eater","Wrath Surge","Legend's End"],
    "Pandana":  ["Divine Smite","Holy Restoration","Last Rites","Soul Eater",
                 "Spore Nightmare","Timeless Roar","Wrath Surge","Deep Freeze"],
    "Turtaura": ["Divine Smite","Holy Restoration","Burning Judgment","Omega Protocol",
                 "Zero-Day","Wrath Surge","Timeless Roar","Psychic Tempest"],
    "Frisela":  ["Divine Smite","Holy Restoration","Burning Judgment","Deep Freeze",
                 "Last Rites","Soul Eater","Wrath Surge","Timeless Roar"],

    # ── Mythos ────────────────────────────────────────────────────────────
    "Eldrune":  ["Legend's End","Elder Wrath","Runic Catastrophe","Runic Fury",
                 "Psychic Tempest","Soul Eater","Wrath Surge","Big Bang"],
    "Galivor":  ["Legend's End","Elder Wrath","Runic Catastrophe","Runic Fury",
                 "Psychic Tempest","Mind Break","Wrath Surge","Soul Eater"],
    "Rokhara":  ["Elder Wrath","Runic Fury","Continental Drift","Meteor Crash",
                 "Wrath Surge","Timeless Roar","Ancient Guardian","Meltdown"],

    # ── Cyber ─────────────────────────────────────────────────────────────
    "Kyntra":   ["Omega Protocol","Zero-Day","Meltdown","Continental Drift",
                 "Ancient Guardian","Timeless Roar","Wrath Surge","Deep Freeze"],
    "Sonari":   ["Omega Protocol","Zero-Day","Ancient Guardian","Timeless Roar",
                 "Holy Restoration","Deep Freeze","Wrath Surge","Last Rites"],
    "Hexel":    ["Omega Protocol","Zero-Day","Necrotic Blast","Cursed Seal",
                 "Soul Eater","Meltdown","Wrath Surge","Psychic Tempest"],
    "Myrabyte": ["Omega Protocol","Zero-Day","Soul Eater","Phantom Pulse",
                 "Meltdown","Cursed Seal","Wrath Surge","Psychic Tempest"],
    "Neorift":  ["Omega Protocol","Big Bang","Event Horizon","Void Collapse",
                 "Psychic Tempest","Wrath Surge","Legend's End","Mind Break"],

    # ── Cosmic ────────────────────────────────────────────────────────────
    "Mirellon": ["Big Bang","Event Horizon","Void Collapse","Psychic Tempest",
                 "Soul Eater","Legend's End","Wrath Surge","Runic Catastrophe"],
    "Thalassa": ["Big Bang","Event Horizon","Void Collapse","Psychic Tempest",
                 "Mind Break","Legend's End","Wrath Surge","Elder Wrath"],
    "Noxtar":   ["Big Bang","Event Horizon","Void Collapse","Necrotic Blast",
                 "Cursed Seal","Legend's End","Wrath Surge","Soul Eater"],

    # ── Neutral ───────────────────────────────────────────────────────────
    "Mimari":   ["Wrath Surge","Timeless Roar","Soul Eater","Big Bang","Flare Dance",
                 "Judgment Bolt","Meteor Crash","Divine Smite","Runic Catastrophe","Sky Rend"],
    "Orrikai":  ["Wrath Surge","Timeless Roar","Ancient Guardian","Holy Restoration",
                 "Last Rites","Deep Freeze","Tidal Wave","Blizzard Cataclysm","Continental Drift","Soul Eater"],
}

# ─────────────────────────────────────────────────────────────────
# TIER THRESHOLDS
# Returns the max move tier available at a given champion level.
# ─────────────────────────────────────────────────────────────────

def level_to_max_tier(level: int) -> int:
    """
    Maps a champion level to the highest move tier they can use.
      Level  1-15  → Tier 1
      Level 16-35  → Tier 2
      Level 36-60  → Tier 3
      Level  61+   → Tier 4
    """
    if level < 16:  return 1
    if level < 36:  return 2
    if level < 61:  return 3
    return 4

# ─────────────────────────────────────────────────────────────────
# ROLE CATEGORIES
# ─────────────────────────────────────────────────────────────────
PHYSICAL_ROLES = {"Fast Attacker","Bruiser","Revenge Killer"}
SPECIAL_ROLES  = {"Special Sweeper","Burst Caster","Burst Nuker","Setup Sweeper","Tempo Swinger"}
SUPPORT_ROLES  = {"Support","Cleric","Utility","Wall","Utility Support","Utility Pivot"}
MIXED_ROLES    = {"Mixed Sweeper","Generalist","Disruptor","Zone Setter","Hazard Setter","Pivot"}


def auto_moveset(champion: "Champion", level: int = 5) -> List[Move]:
    """
    Assign 4 level-appropriate moves based on typing, role, and level tier.

    Early-game champions start with only Tier-1 basics.  Moves naturally
    improve as the champion levels up through a run (and as players unlock
    higher-tier moves at the Sanctum).

    Pool indices (per type, 10 entries):
      0  T1 phys    1  T1 spec    2  T1 status
      3  T2 phys    4  T2 spec    5  T2 status/priority
      6  T3 phys    7  T3 drain   8  T4 signature atk
      9  T4 signature util
    """
    max_tier = level_to_max_tier(level)

    pool1: List[str] = POOL.get(champion.type1, POOL["Neutral"])
    pool2: List[str] = POOL.get(champion.type2, POOL["Neutral"]) if champion.type2 else []

    # Running set of already-selected move names — ensures no duplicates.
    _selected: set = set()
    _result:   List[Move] = []

    def pick_one(pool: List[str], prefer_indices: List[int]) -> bool:
        """
        Pick exactly one new move from `pool`, trying `prefer_indices` in
        order (highest-tier preference first), then falling back to the best
        unseen tier-eligible move in the pool.

        Returns True if a move was added, False otherwise.
        """
        # Try preferred indices first
        for i in prefer_indices:
            if i < len(pool):
                name = pool[i]
                mv   = MOVE_DB.get(name)
                if mv and mv.tier <= max_tier and name not in _selected:
                    _selected.add(name)
                    _result.append(mv)
                    return True
        # Fall back: best (highest tier, then highest BP) unseen eligible move
        fallback: Optional[Move] = None
        for name in pool:
            mv = MOVE_DB.get(name)
            if mv and mv.tier <= max_tier and name not in _selected:
                if (fallback is None or
                        mv.tier > fallback.tier or
                        (mv.tier == fallback.tier and mv.base_power > fallback.base_power)):
                    fallback = mv
        if fallback:
            _selected.add(fallback.name)
            _result.append(fallback)
            return True
        return False

    def fill_to_four(*extra_pools: List[str]):
        """
        Pad _result to 4 moves using pool1 first, then any extra pools,
        picking the best unseen tier-eligible move each time.
        """
        all_pools = [pool1] + list(extra_pools)
        for _ in range(4 - len(_result)):
            for p in all_pools:
                best: Optional[Move] = None
                for name in p:
                    mv = MOVE_DB.get(name)
                    if mv and mv.tier <= max_tier and name not in _selected:
                        if (best is None or
                                mv.tier > best.tier or
                                (mv.tier == best.tier and mv.base_power > best.base_power)):
                            best = mv
                if best:
                    _selected.add(best.name)
                    _result.append(best)
                    break

    role = champion.role

    if role in PHYSICAL_ROLES:
        # Physical → strong phys, second phys, priority, drain/status
        pick_one(pool1, [6, 3, 0])   # best physical (T3 > T2 > T1)
        pick_one(pool1, [3, 0])      # second physical
        pick_one(pool1, [5, 2])      # priority (T2) or status (T1)
        pick_one(pool1, [7, 9, 2])   # drain (T3) > sig util (T4) > status (T1)

    elif role in SPECIAL_ROLES:
        # Special → setup util, signature atk, spec, drain/status
        pick_one(pool1, [9, 4, 1])   # sig util (T4) > spec (T2) > spec (T1)
        pick_one(pool1, [8, 4, 1])   # sig atk (T4) > spec (T2) > spec (T1)
        pick_one(pool1, [4, 1])      # spec
        pick_one(pool1, [7, 2, 5])   # drain (T3) > status (T1) > status/prio (T2)

    elif role in SUPPORT_ROLES:
        # Support → best heal/drain, secondary drain, support/spec, status
        pick_one(pool1, [9, 7, 2])   # sig heal (T4) > drain (T3) > status (T1)
        pick_one(pool1, [7, 9, 4])   # drain > sig util > spec
        if pool2:
            pick_one(pool2, [9, 7])  # type2 heal/drain
            pick_one(pool2, [4, 1])  # type2 spec
        else:
            pick_one(pool1, [4, 1])  # spec
            pick_one(pool1, [5, 2])  # status

    elif role in MIXED_ROLES:
        # Mixed → physical, special, status/utility, type2 spec or drain
        pick_one(pool1, [6, 3, 0])   # physical
        pick_one(pool1, [8, 4, 1])   # special
        pick_one(pool1, [5, 2])      # status/utility
        if pool2:
            pick_one(pool2, [8, 4, 1])  # type2 special
        else:
            pick_one(pool1, [7, 6])  # drain > heavy phys

    else:
        # Default: phys, spec, status, drain
        pick_one(pool1, [6, 3, 0])
        pick_one(pool1, [8, 4, 1])
        pick_one(pool1, [5, 2])
        pick_one(pool1, [7, 9])

    # Fill remaining slots to 4 (handles low-tier situations with few options)
    if len(_result) < 4:
        fill_to_four(pool2)

    return _result[:4]

# ═══════════════════════════════════════════════════════════════
# CHAMPION DATABASE — loaded from CSV
# ═══════════════════════════════════════════════════════════════

def _fill_missing_stat(name: str, role: str, vit: int, sta: int, mgt: int, mag: int, grd: int, wil: int, swf: int) -> tuple:
    """Intelligently fill missing stats (value=70) based on role and archetype."""
    # Define role patterns for intelligent defaults
    support_roles = {"Support", "Cleric", "Utility Support", "Utility", "Utility Pivot"}
    tank_roles = {"Tank", "Wall", "Hazard Setter", "Zone Setter"}
    physical_roles = {"Fast Attacker", "Bruiser", "Revenge Killer"}
    special_roles = {"Special Sweeper", "Burst Caster", "Burst Nuker"}

    # Fill missing stats (70 is the placeholder)
    if sta == 70 and role in tank_roles:
        sta = 87  # Tanks need good MP for defensive moves
    elif sta == 70:
        sta = 80  # Default decent MP

    if swf == 70 and role in support_roles:
        swf = 85  # Supports should act before enemies
    elif swf == 70 and role in tank_roles:
        swf = 75  # Tanks are slower
    elif swf == 70:
        swf = 82  # Default

    if grd == 70 and role in special_roles:
        grd = 80  # Special attackers are fragile but need some def
    elif grd == 70 and role in physical_roles:
        grd = 78  # Physical attackers also fragile
    elif grd == 70 and role in support_roles:
        grd = 80  # Supports need moderate defense
    elif grd == 70:
        grd = 82  # Default

    if wil == 70 and role in special_roles:
        wil = 75  # Special attackers have lower magic def
    elif wil == 70 and role in support_roles:
        wil = 90  # Supports resist magic
    elif wil == 70:
        wil = 82  # Default

    if mag == 70 and role in tank_roles:
        mag = 70  # Tanks don't need magic (OK to keep low)
    elif mag == 70 and role in physical_roles:
        mag = 75  # Physical attackers have lower magic
    elif mag == 70:
        mag = 82  # Default

    return vit, sta, mgt, mag, grd, wil, swf


def load_champions(csv_path: str) -> Dict[str, Champion]:
    """Load champions from the Kiboru_Champions_v3.csv file."""
    champions: Dict[str, Champion] = {}

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                cid  = int(row["# "].strip()) if row.get("# ","").strip().isdigit() else None
                name = row.get("Name","").strip()
                if not name or not cid:
                    continue
                t1 = row.get("Typing 1","").strip() or "Neutral"
                t2 = row.get("Typing 2","").strip() or None
                # Normalize "Aqua/Frost" style entries in type2 field
                if t2 and "/" in t2:
                    t2 = t2.split("/")[1].strip()

                def safe_int(val, default=70):
                    try:    return int(float(val)) if val and str(val).strip() else default
                    except: return default

                vit = safe_int(row.get("HP"))
                sta = safe_int(row.get("MP"))
                mgt = safe_int(row.get("STR"))
                mag = safe_int(row.get("INT"))
                grd = safe_int(row.get("DEF"))
                wil = safe_int(row.get("M.DEF"))
                swf = safe_int(row.get("DEX"))
                role = row.get("Role","").strip()

                # Fill missing stats intelligently
                vit, sta, mgt, mag, grd, wil, swf = _fill_missing_stat(name, role, vit, sta, mgt, mag, grd, wil, swf)

                c = Champion(
                    id=cid, name=name, type1=t1, type2=t2,
                    base_vit = vit,
                    base_sta = sta,
                    base_mgt = mgt,
                    base_mag = mag,
                    base_grd = grd,
                    base_wil = wil,
                    base_swf = swf,
                    role     = role,
                    niche    = row.get("Battle Niche","").strip(),
                )
                c.moves = auto_moveset(c)
                champions[name.lower()] = c
            except Exception:
                continue

    return champions


def _default_csv_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "02_Prototype and Builds", "Champions", "Kiboru_Champions_v3.csv")


# ═══════════════════════════════════════════════════════════════
# BATTLE ENGINE
# ═══════════════════════════════════════════════════════════════

class Battle:
    """
    Manages a 1v1 battle between two BattleChampion instances.
    Tracks the full log and returns a winner.
    """
    def __init__(self,
                 champion_a: Champion,
                 champion_b: Champion,
                 verbose: bool = True,
                 item_callback = None):
        self.a = BattleChampion(deepcopy(champion_a))
        self.b = BattleChampion(deepcopy(champion_b))
        self.verbose  = verbose
        self.turn_num = 0
        self.log: List[str] = []
        self.fate_break_used = False  # Fate Break can be used once per battle
        self.last_turn_state = None  # (saved_a, saved_b, move_p, move_ai, first, second, mv1, mv2)
        # Optional callable(active_bc, team_bcs) -> bool for wilderness item use
        self.item_callback = item_callback
        # Set by _team_player_choose_action when player pre-confirms a switch target;
        # consumed by run_team_interactive to avoid a second prompt.
        self._pending_switch_target = None

    def _print(self, msg: str = ""):
        if self.verbose:
            print(msg)
        self.log.append(msg)

    def _banner(self, text: str):
        self._print(f"\n{'─'*60}")
        self._print(f"  {text}")
        self._print(f"{'─'*60}")

    def show_state(self):
        self._print()
        self._print(self.a.display("  ◈ "))
        self._print(self.b.display("  ◇ "))
        self._print()

    def _save_state(self) -> Tuple[BattleChampion, BattleChampion]:
        """Save a deep copy of both champions' current state for Fate Break."""
        return deepcopy(self.a), deepcopy(self.b)

    def _restore_inplace(self, target: BattleChampion, source: BattleChampion):
        """Copy all battle-state fields from source into target without replacing the reference.
        This keeps local player/ai variables valid after a Fate Break restore."""
        target.current_hp   = source.current_hp
        target.current_mp   = source.current_mp
        target.status       = source.status
        target.status_turns = source.status_turns
        target.venom_count  = source.venom_count
        target.stages       = dict(source.stages)
        target.is_fainted   = source.is_fainted
        target.guarding     = source.guarding
        target.flinched     = source.flinched
        target.first_turn   = source.first_turn

    def _save_team_state(self,
                         player_team: List[BattleChampion],
                         ai_team:     List[BattleChampion],
                         p_active:    BattleChampion,
                         a_active:    BattleChampion,
                         ) -> tuple:
        """Deep-copy both teams for Fate Break. Returns (saved_p, saved_a, p_idx, a_idx)."""
        return (
            [deepcopy(m) for m in player_team],
            [deepcopy(m) for m in ai_team],
            player_team.index(p_active),
            ai_team.index(a_active),
        )

    def _restore_team_inplace(self,
                               player_team: List[BattleChampion],
                               ai_team:     List[BattleChampion],
                               saved_p:     List[BattleChampion],
                               saved_a:     List[BattleChampion],
                               ) -> None:
        """Restore every team member in-place from saved copies."""
        for target, source in zip(player_team, saved_p):
            self._restore_inplace(target, source)
        for target, source in zip(ai_team, saved_a):
            self._restore_inplace(target, source)

    # ── Action resolution ────────────────────────────────────────

    def resolve_move(self, attacker: BattleChampion,
                     defender: BattleChampion,
                     move: Move) -> bool:
        """Attempt to execute a move. Returns True if it connected."""
        self._print(f"  {attacker.name} uses {move.name}!")

        # Deduct MP cost (Strike and Guard have mp_cost=0 and always succeed)
        actual_move = move
        if move.mp_cost > 0:
            if attacker.current_mp < move.mp_cost:
                # Defensive fallback — player UI and AI should prevent reaching here
                log.warning(
                    "%s tried %s but has %d/%d MP — defaulting to Strike",
                    attacker.name, move.name, attacker.current_mp, move.mp_cost,
                )
                actual_move = STRIKE
            else:
                attacker.current_mp -= move.mp_cost

        # Accuracy check
        eff_acc = actual_move.accuracy
        acc_mult = ACC_STAGE_MULT[attacker.stages.get("acc", 0)]
        eva_mult = ACC_STAGE_MULT[-defender.stages.get("eva", 0)]
        eff_acc  = min(1.0, eff_acc * acc_mult * eva_mult)

        # Status-based accuracy penalty
        if attacker.status == StatusEffect.BLUR:
            eff_acc *= 0.5
            # Also might hurt self
            if random.random() < 0.33:
                self._print(f"  {attacker.name} hurt itself in its confusion!")
                self_dmg, _, _, _ = calc_damage(
                    Move("Confusion", "Neutral", MoveCategory.PHYSICAL,
                         BLUR_SELF_HIT_BP, 1.0, 0),
                    attacker, attacker, apply_variance=True
                )
                attacker.take_damage(self_dmg)
                self._print(f"  Took {self_dmg} damage!")
                return False

        if random.random() > eff_acc:
            self._print(f"  {attacker.name}'s attack missed!")
            return False

        # STATUS MOVE branch
        if actual_move.category == MoveCategory.STATUS or actual_move.base_power == 0:
            self._apply_effect(actual_move, attacker, defender, dmg_dealt=0)
            return True

        # DAMAGE calculation
        dmg, type_mult, variance, hit_desc = calc_damage(actual_move, attacker, defender)

        if type_mult == 0.0:
            self._print(hit_desc)
            return False

        # Guard reduction
        if defender.guarding:
            orig = dmg
            dmg  = int(dmg * (1 - GUARD_DMG_REDUCE))
            self._print(f"  {defender.name} is guarding! ({orig} → {dmg} dmg)")

        actual_dmg = defender.take_damage(dmg)
        self._print(f"  Dealt {actual_dmg} damage  [{variance:.2f} roll]{hit_desc}")
        log.debug(
            "%s → %s | %s | dmg=%d | type=×%.1f%s | %s HP: %d/%d",
            attacker.name, defender.name, move.name,
            actual_dmg, type_mult,
            "  CRIT" if "CRITICAL HIT" in hit_desc else "",
            defender.name, defender.current_hp, defender.max_hp,
        )

        # Drain healing
        if actual_move.drain_fraction > 0:
            healed = attacker.heal(int(actual_dmg * actual_move.drain_fraction))
            self._print(f"  {attacker.name} drained {healed} HP!")

        # Recoil
        if actual_move.recoil_fraction > 0:
            recoil = max(1, int(actual_dmg * actual_move.recoil_fraction))
            attacker.take_damage(recoil)
            self._print(f"  {attacker.name} took {recoil} recoil damage!")

        # Apply effects
        self._apply_effect(actual_move, attacker, defender, actual_dmg)

        return True

    def _apply_effect(self, move: Move, attacker: BattleChampion,
                      defender: BattleChampion, dmg_dealt: int):
        """Apply secondary effects of a move."""
        if move.effect_chance <= 0:
            return
        if random.random() > move.effect_chance:
            return

        # Status infliction
        if move.inflict_status != StatusEffect.NONE:
            ok, msg = defender.apply_status(move.inflict_status)
            if ok:
                self._print(f"  {msg}")

        # Self-boost
        if move.self_boost_stat:
            msg = attacker.change_stage(move.self_boost_stat, move.self_boost_stages)
            self._print(msg)

        # Target stat drop
        if move.drop_stat:
            msg = defender.change_stage(move.drop_stat, -abs(move.drop_stages))
            self._print(msg)

        # Heal (percentage of max HP)
        if move.heal_fraction > 0:
            healed = attacker.heal(int(attacker.max_hp * move.heal_fraction))
            self._print(f"  {attacker.name} restored {healed} HP!")

    def _process_end_of_turn(self, bc: BattleChampion):
        """End-of-turn status effects and MP regen."""
        if bc.is_fainted:
            return

        # Passive MP regeneration (percentage-based)
        regen_pct = MP_REGEN_GUARD if bc.guarding else MP_REGEN_PASSIVE
        regen_amount = max(1, int(bc.base.max_mp * regen_pct))
        bc.regen_mp(regen_amount)
        bc.guarding  = False
        bc.flinched  = False
        bc.first_turn = False

        # Status damage
        if bc.status == StatusEffect.SCORCH:
            dmg = max(1, int(bc.max_hp * SCORCH_DAMAGE))
            bc.take_damage(dmg)
            self._print(f"  {bc.name} is scorched! (-{dmg} HP)")

        elif bc.status == StatusEffect.FROSTBITE:
            dmg = max(1, int(bc.max_hp * FROSTBITE_DAMAGE))
            bc.take_damage(dmg)
            self._print(f"  {bc.name} suffers frostbite! (-{dmg} HP)")

        elif bc.status == StatusEffect.VENOM:
            bc.venom_count += 1
            fraction = min(bc.venom_count, 9) * VENOM_BASE
            dmg = max(1, int(bc.max_hp * fraction))
            bc.take_damage(dmg)
            self._print(f"  {bc.name} is badly poisoned! (-{dmg} HP, stage {bc.venom_count})")

        elif bc.status == StatusEffect.CORRUPTED:
            if bc.status_turns > 0:
                stat = random.choice(["mgt","mag","grd","wil","swf"])
                msg = bc.change_stage(stat, -1)
                self._print(f"  {bc.name} is corrupted! {msg}")
                bc.status_turns -= 1
                if bc.status_turns == 0:
                    bc.status = StatusEffect.NONE
                    self._print(f"  {bc.name} shook off the corruption!")

        elif bc.status == StatusEffect.BLUR:
            bc.status_turns -= 1
            if bc.status_turns <= 0:
                bc.status = StatusEffect.NONE
                bc.status_turns = 0
                self._print(f"  {bc.name} snapped out of confusion!")

    def _turn_order(self, bc_a: BattleChampion, action_a: str,
                    bc_b: BattleChampion, action_b: str,
                    move_a: Optional[Move], move_b: Optional[Move]):
        """
        Returns (first, second, first_move, second_move).
        Priority moves > speed order > random tie-break.
        Switches always go before attacks.
        Guard is instant.
        """
        pri_a = move_a.priority if move_a else (2 if action_a == "switch" else 0)
        pri_b = move_b.priority if move_b else (2 if action_b == "switch" else 0)

        if pri_a != pri_b:
            if pri_a > pri_b:
                return bc_a, bc_b, move_a, move_b, action_a, action_b
            else:
                return bc_b, bc_a, move_b, move_a, action_b, action_a
        else:
            spd_a = bc_a.get_stat("swf")
            spd_b = bc_b.get_stat("swf")
            if spd_a != spd_b:
                if spd_a > spd_b:
                    return bc_a, bc_b, move_a, move_b, action_a, action_b
                else:
                    return bc_b, bc_a, move_b, move_a, action_b, action_a
            else:
                # Tie → random
                if random.random() < 0.5:
                    return bc_a, bc_b, move_a, move_b, action_a, action_b
                else:
                    return bc_b, bc_a, move_b, move_a, action_b, action_a

    def _can_act(self, bc: BattleChampion) -> Tuple[bool, str]:
        """Returns (can_act, reason_if_not). Checks sleep/stun/shock skip."""
        if bc.status == StatusEffect.SLEEP:
            if bc.status_turns > 0:
                bc.status_turns -= 1
                if bc.status_turns == 0:
                    bc.status = StatusEffect.NONE
                    return True, ""  # Woke up this turn
                return False, f"{bc.name} is fast asleep!"
        if bc.status == StatusEffect.STUN:
            bc.status = StatusEffect.NONE
            bc.status_turns = 0
            return False, f"{bc.name} is stunned and loses its turn!"
        if bc.status == StatusEffect.SHOCK:
            if random.random() < 0.25:
                return False, f"{bc.name} is fully paralyzed and can't move!"
        return True, ""

    def execute_action(self, actor: BattleChampion, target: BattleChampion,
                       action: str, move: Optional[Move]):
        """Execute one actor's action."""
        if action == "guard":
            actor.guarding = True
            self._print(f"  {actor.name} takes a defensive stance! (MP regen +{MP_REGEN_GUARD}/turn, -50% dmg)")
            return

        if action == "strike":
            can, reason = self._can_act(actor)
            if not can:
                self._print(f"  {reason}")
                return
            self.resolve_move(actor, target, STRIKE)
            return

        if action == "attack" and move:
            can, reason = self._can_act(actor)
            if not can:
                self._print(f"  {reason}")
                return
            self.resolve_move(actor, target, move)

    # ── Public battle runner ──────────────────────────────────────

    def run_auto(self, max_turns: int = 50) -> Optional[str]:
        """Fully automated simulation — AI picks moves greedily."""
        log.info("Battle start (auto): %s vs %s", self.a.name, self.b.name)
        self._banner(f"⚔  {self.a.name} vs {self.b.name}  ⚔")
        self.show_state()

        for turn in range(1, max_turns + 1):
            self._banner(f"TURN {turn}")
            self.turn_num = turn

            # AI: pick the move that maximises expected damage (considering type)
            move_a = self._ai_pick_move(self.a, self.b)
            move_b = self._ai_pick_move(self.b, self.a)

            first, second, mv1, mv2, act1, act2 = self._turn_order(
                self.a, "attack", self.b, "attack", move_a, move_b
            )

            self.execute_action(first, second, "attack", mv1)
            if second.is_fainted:
                self._print(f"\n  ✦ {second.name} fainted!")
                break

            self.execute_action(second, first, "attack", mv2)
            if first.is_fainted:
                self._print(f"\n  ✦ {first.name} fainted!")
                break

            # End of turn
            self._print()
            self._process_end_of_turn(self.a)
            self._process_end_of_turn(self.b)

            if self.a.is_fainted or self.b.is_fainted:
                break

            self.show_state()

        # Determine winner
        if self.a.is_fainted and not self.b.is_fainted:
            winner = self.b.name
        elif self.b.is_fainted and not self.a.is_fainted:
            winner = self.a.name
        elif self.a.current_hp > self.b.current_hp:
            winner = self.a.name
        elif self.b.current_hp > self.a.current_hp:
            winner = self.b.name
        else:
            winner = "Draw"

        log.info("Battle end (auto): winner=%s after %d turns", winner, self.turn_num)
        self._banner(f"🏆  BATTLE OVER — {winner} wins after {self.turn_num} turns!")
        self._print(f"  {self.a.name}: {self.a.current_hp}/{self.a.max_hp} HP")
        self._print(f"  {self.b.name}: {self.b.current_hp}/{self.b.max_hp} HP")
        return winner

    def _ai_pick_move(self, attacker: BattleChampion,
                      defender: BattleChampion) -> Move:
        """Greedy AI: picks the move with highest expected damage (or heals when low)."""
        moves = attacker.base.moves or []

        # Affordable moves only — no MP means no move, fall back to Strike
        affordable = [mv for mv in moves if mv.mp_cost == 0 or attacker.current_mp >= mv.mp_cost]

        if not affordable:
            # All moves cost more MP than the AI has — use Strike (no MP cost)
            return STRIKE

        # Heal if below 25% HP and has a heal move
        if attacker.hp_pct < 0.25:
            for mv in affordable:
                if mv.heal_fraction > 0:
                    return mv

        best_mv, best_val = affordable[0], -1
        for mv in affordable:
            if mv.base_power > 0:
                tm   = get_type_multiplier(mv.essence, defender.base.type1, defender.base.type2)
                stab = STAB_BONUS if (mv.essence in (attacker.base.type1, attacker.base.type2) and not mv.no_stab) else 1.0
                val  = mv.base_power * tm * stab
                if val > best_val:
                    best_val, best_mv = val, mv
            elif mv.inflict_status != StatusEffect.NONE and defender.status == StatusEffect.NONE:
                if best_val < 50:
                    best_val, best_mv = 50, mv

        return best_mv

    def run_interactive(self, player: BattleChampion,
                        ai: BattleChampion,
                        max_turns: int = 50) -> Optional[str]:
        """Interactive player-vs-AI battle."""
        lv_p = f" Lv{player.level}" if player.level is not None else ""
        lv_a = f" Lv{ai.level}" if ai.level is not None else ""
        log.info("Battle start: %s%s (HP %d) vs %s%s (HP %d)",
                 player.name, lv_p, player.current_hp,
                 ai.name,     lv_a, ai.current_hp)
        self._banner(f"⚔  {player.name} (YOU) vs {ai.name} (AI)  ⚔")
        self.show_state()

        for turn in range(1, max_turns + 1):
            self._banner(f"TURN {turn}")
            self.turn_num = turn

            # Snapshot state now so it can be saved as last_turn_state after moves are chosen
            pre_turn = self._save_state()

            # Player chooses (Fate Break option appears here if available)
            move_p = self._player_choose_action(player, ai)

            if move_p.name == "Fate Break":
                # ── Fate Break: restore and replay the previous turn ──────────
                saved_a, saved_b, fb_move_p, fb_move_ai = self.last_turn_state
                self.fate_break_used = True

                # Restore in-place so the player/ai references remain valid
                self._restore_inplace(player, saved_a)
                self._restore_inplace(ai, saved_b)

                log.info("Fate Break used by %s at turn %d", player.name, turn)
                self._print(f"\n  ✦ {player.name} activates Fate Break!")
                self._print(f"  Rewinding turn {turn - 1} — replaying with new RNG...\n")
                self._banner(f"TURN {turn - 1}  [FATE BREAK REPLAY]")

                first, second, mv1, mv2, _, _ = self._turn_order(
                    player, "attack", ai, "attack", fb_move_p, fb_move_ai
                )
                self.execute_action(first, second, "attack", mv1)
                if second.is_fainted:
                    self._print(f"\n  ✦ {second.name} fainted!")
                    break
                self.execute_action(second, first, "attack", mv2)
                if first.is_fainted:
                    self._print(f"\n  ✦ {first.name} fainted!")
                    break
                self._print()
                self._process_end_of_turn(player)
                self._process_end_of_turn(ai)
                if player.is_fainted or ai.is_fainted:
                    break
                self.show_state()

                # ── Now continue with the current turn ────────────────────────
                self._banner(f"TURN {turn}  (continuing after Fate Break)")
                move_p = self._player_choose_action(player, ai)

            # AI picks and last_turn_state is updated for both normal and post-Fate-Break turns
            move_ai = self._ai_pick_move(ai, player)
            self.last_turn_state = (pre_turn[0], pre_turn[1], move_p, move_ai)

            # Execute current turn
            p_action = "strike" if move_p.name == "Strike" else "attack"
            first, second, mv1, mv2, act1, act2 = self._turn_order(
                player, p_action, ai, "attack", move_p, move_ai
            )
            self.execute_action(first, second, act1, mv1)
            if second.is_fainted:
                self._print(f"\n  ✦ {second.name} fainted!")
                break
            self.execute_action(second, first, act2, mv2)
            if first.is_fainted:
                self._print(f"\n  ✦ {first.name} fainted!")
                break

            self._print()
            self._process_end_of_turn(player)
            self._process_end_of_turn(ai)

            if player.is_fainted or ai.is_fainted:
                break

            self.show_state()

        if player.is_fainted:    winner = ai.name
        elif ai.is_fainted:      winner = player.name
        elif player.current_hp > ai.current_hp: winner = player.name
        elif ai.current_hp > player.current_hp: winner = ai.name
        else: winner = "Draw"

        log.info("Battle end: winner=%s after %d turns | %s HP %d/%d | %s HP %d/%d",
                 winner, self.turn_num,
                 player.name, player.current_hp, player.max_hp,
                 ai.name,     ai.current_hp,     ai.max_hp)
        self._banner(f"🏆  BATTLE OVER — {winner} wins after {self.turn_num} turns!")
        input("\n  Press Enter to exit...")
        return winner

    def _player_choose_action(self, player: BattleChampion,
                              ai: BattleChampion) -> Move:
        """CLI prompt for move selection."""
        print(f"\n  Choose a move for {player.name}:")
        moves = player.base.moves or []
        for i, mv in enumerate(moves, 1):
            mp_ok = "✓" if player.current_mp >= mv.mp_cost else "✗"
            tm = get_type_multiplier(mv.essence, ai.base.type1, ai.base.type2)
            tm_str = {0.0:"◼IMMUNE",2.0:"▲ SE",4.0:"▲▲x4"}.get(tm,"")
            if 0 < tm < 1.0:
                tm_str = "▼ NVE"
            print(f"  [{i}] {mv}  {mp_ok} {tm_str}")

        print(f"  [b] Strike  (BP 30, always available, no type bonus)")
        print(f"  [g] Guard   (+{MP_REGEN_GUARD} MP regen, -50% incoming dmg)")
        fate_break_available = self.last_turn_state is not None and not self.fate_break_used
        if fate_break_available:
            print(f"  [f] Fate Break  (replay last turn with new RNG — once per battle)")
        if self.item_callback is not None:
            print(f"  [i] Item  (use an item from your bag)")

        while True:
            choice = input("  > ").strip().lower()
            if choice == "b":
                return Move("Strike","Neutral",MoveCategory.STATUS,0,1.0,0,description="Strike action")
            if choice == "g":
                player.guarding = True
                print(f"  {player.name} guards!")
                return Move("Guard","Neutral",MoveCategory.STATUS,0,1.0,0,description="Guard action")
            if choice == "f" and fate_break_available:
                return Move("Fate Break","Neutral",MoveCategory.STATUS,0,1.0,0,description="Fate Break")
            if choice == "i" and self.item_callback is not None:
                used = self.item_callback(player, [player])
                if used:
                    return Move("Item","Neutral",MoveCategory.STATUS,0,1.0,0,description="Item used")
                continue  # player cancelled — re-prompt
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(moves):
                    mv = moves[idx]
                    if mv.mp_cost > 0 and player.current_mp < mv.mp_cost:
                        print(f"  Not enough MP for {mv.name} (need {mv.mp_cost}, have {player.current_mp}). Use [b] Strike or [g] Guard.")
                        continue
                    return mv
            except ValueError:
                pass
            print("  Invalid choice — enter a number, 'b', 'g', or 'i'.")

    # ── 6v6 Team Battle helpers ──────────────────────────────────

    def _show_team_header(self,
                          player_team: List["BattleChampion"],
                          ai_team: List["BattleChampion"],
                          p_active: "BattleChampion",
                          a_active: "BattleChampion"):
        """Print a compact team health bar above the active matchup."""
        def slot(bc, active):
            if bc.is_fainted:
                tag = "✗"
            else:
                pct = bc.current_hp / bc.max_hp
                tag = f"{int(pct*100):>3}%"
            marker = "►" if bc is active else " "
            return f"{marker}{bc.name[:7]:<7} {tag}"

        self._print()
        self._print("  PLAYER  " + "  │  ".join(slot(m, p_active) for m in player_team))
        self._print("  AI      " + "  │  ".join(slot(m, a_active) for m in ai_team))
        self._print()
        self._print(p_active.display("  ◈ "))
        self._print(a_active.display("  ◇ "))
        self._print()

    def _team_player_choose_action(self,
                                   active: "BattleChampion",
                                   opponent: "BattleChampion",
                                   player_team: List["BattleChampion"]) -> Move:
        """Move menu for team battle — includes Switch and Fate Break options."""
        print(f"\n  Choose a move for {active.name}:")
        moves = active.base.moves or []
        for i, mv in enumerate(moves, 1):
            mp_ok = "✓" if active.current_mp >= mv.mp_cost else "✗"
            tm = get_type_multiplier(mv.essence, opponent.base.type1, opponent.base.type2)
            tm_str = {0.0: "◼IMMUNE", 2.0: "▲ SE", 4.0: "▲▲x4"}.get(tm, "")
            if 0 < tm < 1.0:
                tm_str = "▼ NVE"
            print(f"  [{i}] {mv}  {mp_ok} {tm_str}")

        print(f"  [b] Strike  (BP 30, always available, no type bonus)")
        print(f"  [g] Guard   (+{MP_REGEN_GUARD} MP regen, -50% incoming dmg)")

        bench = [m for m in player_team if m is not active and not m.is_fainted]
        if bench:
            print(f"  [s] Switch  (use your turn to swap in a different monster)")

        fate_break_available = self.last_turn_state is not None and not self.fate_break_used
        if fate_break_available:
            print(f"  [f] Fate Break  (replay last turn with new RNG — once per battle)")
        if self.item_callback is not None:
            print(f"  [i] Item  (use an item from your bag)")

        while True:
            choice = input("  > ").strip().lower()
            if choice == "b":
                return Move("Strike", "Neutral", MoveCategory.STATUS, 0, 1.0, 0, description="Strike action")
            if choice == "g":
                active.guarding = True
                print(f"  {active.name} guards!")
                return Move("Guard", "Neutral", MoveCategory.STATUS, 0, 1.0, 0, description="Guard action")
            if choice == "s" and bench:
                # Pre-resolve switch target here so player can cancel before committing the turn
                target = self._player_pick_switch_target(player_team, active)
                if target is None:
                    # Player cancelled — redraw action menu
                    print(f"\n  Choose a move for {active.name}:")
                    for i, mv in enumerate(moves, 1):
                        mp_ok = "✓" if active.current_mp >= mv.mp_cost else "✗"
                        tm = get_type_multiplier(mv.essence, opponent.base.type1, opponent.base.type2)
                        tm_str = {0.0: "◼IMMUNE", 2.0: "▲ SE", 4.0: "▲▲x4"}.get(tm, "")
                        if 0 < tm < 1.0:
                            tm_str = "▼ NVE"
                        print(f"  [{i}] {mv}  {mp_ok} {tm_str}")
                    print(f"  [b] Strike  (BP 30, always available, no type bonus)")
                    print(f"  [g] Guard   (+{MP_REGEN_GUARD} MP regen, -50% incoming dmg)")
                    if bench:
                        print(f"  [s] Switch  (use your turn to swap in a different monster)")
                    if fate_break_available:
                        print(f"  [f] Fate Break  (replay last turn with new RNG — once per battle)")
                    if self.item_callback is not None:
                        print(f"  [i] Item  (use an item from your bag)")
                    continue
                # Confirmed — store target so run_team_interactive doesn't prompt again
                self._pending_switch_target = target
                return Move("Switch", "Neutral", MoveCategory.STATUS, 0, 1.0, 0, description="Switch action")
            if choice == "f" and fate_break_available:
                return Move("Fate Break", "Neutral", MoveCategory.STATUS, 0, 1.0, 0, description="Fate Break")
            if choice == "i" and self.item_callback is not None:
                used = self.item_callback(active, player_team)
                if used:
                    return Move("Item", "Neutral", MoveCategory.STATUS, 0, 1.0, 0, description="Item used")
                continue  # player cancelled — re-prompt
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(moves):
                    mv = moves[idx]
                    if mv.mp_cost > 0 and active.current_mp < mv.mp_cost:
                        print(f"  Not enough MP for {mv.name} (need {mv.mp_cost}, have {active.current_mp}). Use [b] Strike or [g] Guard.")
                        continue
                    return mv
            except ValueError:
                pass
            print("  Invalid choice — enter a number, 'b', 'g', 's', or 'f'.")

    def _player_pick_switch_target(self,
                                   player_team: List["BattleChampion"],
                                   current: "BattleChampion",
                                   forced: bool = False):
        """
        Let the player choose which bench mon to switch in.

        Voluntary switch: shows [0] Go back — returns None if the player cancels.
        Forced switch:    no cancel option — loops until a valid pick is made.
        """
        bench = [m for m in player_team if m is not current and not m.is_fainted]
        if not bench:
            return current  # No valid switch targets (shouldn't happen if caller checks)

        prompt = "  Forced switch — " if forced else "  Switch to — "
        print(prompt + "choose a monster:")
        for i, m in enumerate(bench, 1):
            pct = int(m.current_hp / m.max_hp * 100)
            type_str = m.base.type1 + (f"/{m.base.type2}" if m.base.type2 else "")
            print(f"  [{i}] {m.name} ({type_str}) — {m.current_hp}/{m.max_hp} HP ({pct}%)")
        if not forced:
            print(f"  [0] Go back")

        while True:
            choice = input("  > ").strip()
            if not forced and choice == "0":
                print("  Switch cancelled.")
                return None
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(bench):
                    chosen = bench[idx]
                    print(f"  Go, {chosen.name}!")
                    return chosen
            except ValueError:
                pass
            print("  Invalid choice — enter a number.")

    def run_team_interactive(self,
                             player_team: List["BattleChampion"],
                             ai_team: List["BattleChampion"],
                             max_turns: int = 300) -> Optional[str]:
        """6v6 interactive team battle. Player picks moves; AI plays greedily."""
        log.info("Team battle start: player[%s] vs ai[%s]",
                 ", ".join(f"{m.name} Lv{m.level}" if m.level else m.name for m in player_team),
                 ", ".join(f"{m.name} Lv{m.level}" if m.level else m.name for m in ai_team))
        self._banner("⚔  6v6 TEAM BATTLE  ⚔")

        # ── Battle start: pick first living champion as lead ───────
        p_living_start = [m for m in player_team if not m.is_fainted]
        a_living_start = [m for m in ai_team     if not m.is_fainted]
        if not p_living_start or not a_living_start:
            # Degenerate case: one side is already fully fainted
            p_alive = sum(1 for m in player_team if not m.is_fainted)
            a_alive = sum(1 for m in ai_team     if not m.is_fainted)
            return "Player" if p_alive > a_alive else "Opponent"

        p_active = player_team[0] if not player_team[0].is_fainted else p_living_start[0]
        a_active = ai_team[0]     if not ai_team[0].is_fainted     else a_living_start[0]

        # If the pre-designated lead was fainted, force the player to pick
        if player_team[0].is_fainted:
            self._print("\n  Your lead is fainted — choose your starting champion:")
            p_active = self._player_pick_switch_target(player_team, player_team[0], forced=True)

        self._print(f"  {p_active.name} vs {a_active.name} — begin!\n")

        turn = 0
        while True:
            # ── End condition ───────────────────────────────────────
            p_remaining = [m for m in player_team if not m.is_fainted]
            a_remaining = [m for m in ai_team   if not m.is_fainted]
            if not p_remaining or not a_remaining:
                break

            turn += 1
            self.turn_num = turn
            self._banner(f"TURN {turn}")

            # ── Guard: if p_active somehow entered the turn fainted,
            #    force a switch before doing anything else.
            if p_active.is_fainted:
                p_remaining = [m for m in player_team if not m.is_fainted]
                if not p_remaining:
                    break
                p_active = self._player_pick_switch_target(player_team, p_active, forced=True)

            self._show_team_header(player_team, ai_team, p_active, a_active)

            # ── Save state for Fate Break (before player chooses) ───
            pre_p, pre_a, pre_p_idx, pre_a_idx = self._save_team_state(
                player_team, ai_team, p_active, a_active
            )

            # ── Action selection ────────────────────────────────────
            move_p = self._team_player_choose_action(p_active, a_active, player_team)

            if move_p.name == "Fate Break":
                # Restore and replay last turn, then ask for current-turn move
                fb_pre_p, fb_pre_a, fb_p_idx, fb_a_idx, fb_move_p, fb_move_ai = self.last_turn_state
                self.fate_break_used = True
                self._restore_team_inplace(player_team, ai_team, fb_pre_p, fb_pre_a)
                p_active = player_team[fb_p_idx]
                a_active = ai_team[fb_a_idx]

                log.info("Fate Break used (team battle) at turn %d", turn)
                self._print(f"\n  ✦ Fate Break activated! Replaying last turn with new RNG...")
                self._show_team_header(player_team, ai_team, p_active, a_active)

                # Replay — same moves, new RNG (skip Switch/Guard/Item replays for simplicity)
                if fb_move_p.name not in ("Switch", "Guard", "Fate Break", "Item"):
                    fb_first, fb_second, fb_mv1, fb_mv2, _, _ = self._turn_order(
                        p_active, "attack", a_active, "attack", fb_move_p, fb_move_ai
                    )
                    self.execute_action(fb_first, fb_second, "attack", fb_mv1)
                    if not fb_second.is_fainted:
                        self.execute_action(fb_second, fb_first, "attack", fb_mv2)
                    if fb_first.is_fainted:
                        self._print(f"\n  ✦ {fb_first.name} fainted!")
                    if fb_second.is_fainted:
                        self._print(f"\n  ✦ {fb_second.name} fainted!")
                    # Handle forced switches from replay faints
                    if p_active.is_fainted:
                        p_rem = [m for m in player_team if not m.is_fainted]
                        if p_rem:
                            p_active = self._player_pick_switch_target(player_team, p_active, forced=True)
                    if a_active.is_fainted:
                        a_rem = [m for m in ai_team if not m.is_fainted]
                        if a_rem:
                            a_active = a_rem[0]
                            self._print(f"  Opponent sends out {a_active.name}!")

                self._banner(f"TURN {turn}  (continuing after Fate Break)")
                self._show_team_header(player_team, ai_team, p_active, a_active)
                move_p = self._team_player_choose_action(p_active, a_active, player_team)

            move_ai = self._ai_pick_move(a_active, p_active)

            # Record this turn's state for future Fate Break
            self.last_turn_state = (pre_p, pre_a, pre_p_idx, pre_a_idx, move_p, move_ai)

            # ── Voluntary switch (player) ───────────────────────────
            if move_p.name == "Switch":
                # Target was already chosen (and confirmed) inside _team_player_choose_action
                p_active = self._pending_switch_target or self._player_pick_switch_target(player_team, p_active, forced=True)
                self._pending_switch_target = None
                # AI still attacks the new active mon
                self.execute_action(a_active, p_active, "attack", move_ai)
                if p_active.is_fainted:
                    self._print(f"\n  ✦ {p_active.name} fainted!")
            else:
                # ── Normal turn execution (attack or strike) ────────
                p_action = "strike" if move_p.name == "Strike" else "attack"
                first, second, mv1, mv2, act1, act2 = self._turn_order(
                    p_active, p_action, a_active, "attack", move_p, move_ai
                )
                self.execute_action(first, second, act1, mv1)
                if not second.is_fainted:
                    self.execute_action(second, first, act2, mv2)
                else:
                    self._print(f"\n  ✦ {second.name} fainted!")

                if first.is_fainted:
                    self._print(f"\n  ✦ {first.name} fainted!")

            # ── End-of-turn effects ─────────────────────────────────
            self._print()
            self._process_end_of_turn(p_active)
            self._process_end_of_turn(a_active)

            # ── Forced switches after faints ────────────────────────
            if p_active.is_fainted:
                p_remaining = [m for m in player_team if not m.is_fainted]
                if not p_remaining:
                    break
                p_active = self._player_pick_switch_target(player_team, p_active, forced=True)

            if a_active.is_fainted:
                a_remaining = [m for m in ai_team if not m.is_fainted]
                if not a_remaining:
                    break
                a_active = a_remaining[0]
                self._print(f"  Opponent sends out {a_active.name}!")

        # ── Determine winner ────────────────────────────────────────
        p_alive = sum(1 for m in player_team if not m.is_fainted)
        a_alive = sum(1 for m in ai_team   if not m.is_fainted)
        if p_alive > a_alive:
            winner = "Player"
        elif a_alive > p_alive:
            winner = "Opponent"
        else:
            winner = "Draw"

        log.info("Team battle end: winner=%s | player %d alive | opponent %d alive | turns=%d",
                 winner, p_alive, a_alive, self.turn_num)
        self._banner(f"🏆  BATTLE OVER — {winner} wins!  "
                     f"(Player: {p_alive} left  |  Opponent: {a_alive} left)")
        input("\n  Press Enter to exit...")
        return winner

# ═══════════════════════════════════════════════════════════════
# DAMAGE TEST
# ═══════════════════════════════════════════════════════════════

def run_damage_test():
    """Verify the damage formula with known values from the GDD."""
    print("\n" + "═"*60)
    print("  DAMAGE FORMULA TEST")
    print("  Formula: (BP/100) × ATK × (K/(K+DEF)) × STAB × Type × Var")
    print(f"  K = {K_CONSTANT}")
    print("═"*60)

    # Example from GDD: BP=110, ATK=1000, DEF=500 → base = 110/100 * 1000 * (250/750)
    bp, atk, dfn = 110, 1000, 500
    base = (bp / 100) * atk * (K_CONSTANT / (K_CONSTANT + dfn))
    print(f"\n  GDD Example:")
    print(f"  BP={bp}, ATK={atk}, DEF={dfn}")
    print(f"  Base = ({bp}/100) × {atk} × ({K_CONSTANT}/{K_CONSTANT+dfn})")
    print(f"       = {bp/100:.2f} × {atk} × {K_CONSTANT/(K_CONSTANT+dfn):.4f}")
    print(f"       = {base:.2f}  (before STAB/Type/Variance)")
    print(f"  With STAB (1.5×): {base*1.5:.2f}")
    print(f"  With Type 2× :    {base*2.0:.2f}")
    print(f"  Variance range:   {base*VARIANCE_MIN:.1f} – {base*VARIANCE_MAX:.1f}")

    # Kitzen (fast physical) vs Torusk (terra tank)
    champions = load_champions(_default_csv_path())
    kitzen = champions.get("kitzen")
    torusk = champions.get("torusk")
    if kitzen and torusk:
        bk = BattleChampion(kitzen)
        bt = BattleChampion(torusk)
        print(f"\n  Live Test: Kitzen's Flame Strike vs Torusk")
        print(f"  Kitzen MGT = {bk.base.mgt}  (base {kitzen.base_mgt} × {STAT_MULT})")
        print(f"  Torusk GRD = {bt.base.grd}  (base {torusk.base_grd} × {STAT_MULT})")
        move = MOVE_DB["Flame Strike"]
        dmg, tm, var, desc = calc_damage(move, bk, bt, apply_variance=False)
        print(f"  Flame Strike (Inferno Phys BP={move.base_power}) vs Torusk (Terra/None)")
        print(f"  Type mult  = {tm}× (Inferno vs Terra = {TYPE_CHART['Inferno']['Terra']}×)")
        stab = STAB_BONUS if move.essence == kitzen.type1 else 1.0
        print(f"  STAB       = {stab}×  (Inferno attacker, Inferno move)")
        raw = (move.base_power/100) * bk.base.mgt * (K_CONSTANT/(K_CONSTANT+bt.base.grd))
        print(f"  Raw base   = ({move.base_power}/100)×{bk.base.mgt}×({K_CONSTANT}/{K_CONSTANT+bt.base.grd:.0f})")
        print(f"             = {raw:.2f}")
        print(f"  Final (var=1.0) = {int(raw * stab * tm)}")
        print(f"  Returned damage = {dmg}")

    print("\n" + "═"*60)


# ═══════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]

    # Set up logging when running battle_engine.py standalone
    try:
        from logger import setup_logging
        setup_logging(debug="--debug" in args)
    except ImportError:
        logging.basicConfig(level=logging.DEBUG)

    champions = load_champions(_default_csv_path())
    print(f"  Loaded {len(champions)} champions.")

    if "--list" in args:
        print("\n  CHAMPION ROSTER")
        print("  " + "─"*100)
        for c in sorted(champions.values(), key=lambda x: x.id):
            print(c.summary())
        return

    if "--damage-test" in args:
        run_damage_test()
        return

    if "--sim" in args:
        # python battle_engine.py --sim Kitzen Torusk
        idx = args.index("--sim")
        name_a = args[idx+1].lower() if idx+1 < len(args) else None
        name_b = args[idx+2].lower() if idx+2 < len(args) else None

        ca = champions.get(name_a) if name_a else None
        cb = champions.get(name_b) if name_b else None

        if not ca:
            print(f"  Champion '{name_a}' not found. Use --list to see all.")
            return
        if not cb:
            print(f"  Champion '{name_b}' not found. Use --list to see all.")
            return

        battle = Battle(ca, cb, verbose=True)
        battle.run_auto()
        return

    # ── Interactive mode ──────────────────────────────────────────
    print("\n" + "═"*145)
    print("  KAMIKIN BATTLE ENGINE — Interactive Mode")
    print("═"*145)

    # Mode selection
    print("\n  Select battle mode:")
    print("  [1] 1v1")
    print("  [2] 6v6 Team Battle")
    while True:
        mode = input("  > ").strip()
        if mode in ("1", "2"):
            break
        print("  Enter 1 or 2.")

    # Champion roster display
    print("\n  Available Champions:")
    print("  " + "─"*143)
    print(f"  {'#':<3} {'Name':<15} {'Type':<15} {'HP':<4} {'MP':<4} {'MGT':<4} {'MAG':<4} {'GRD':<4} {'WIL':<4} {'SWF':<4} {'Role':<18}")
    print("  " + "─"*143)

    names = sorted(c.name for c in champions.values())
    for i, n in enumerate(names, 1):
        c = champions[n.lower()]
        type_str = c.type1 + (f"/{c.type2}" if c.type2 else "")
        role = c.role[:17] if c.role else "—"
        print(f"  {i:<3} {n:<15} {type_str:<15} {c.base_vit:<4} {c.base_sta:<4} {c.base_mgt:<4} {c.base_mag:<4} {c.base_grd:<4} {c.base_wil:<4} {c.base_swf:<4} {role:<18}")

    def pick_champion(prompt: str, exclude: list = []) -> "Champion":
        exclude_names = {c.name.lower() for c in exclude}
        while True:
            raw = input(f"  {prompt}: ").strip().lower()
            try:
                idx = int(raw) - 1
                champ = champions[names[idx].lower()]
            except (ValueError, IndexError, KeyError):
                champ = champions.get(raw)
            if not champ:
                print("  Not found — try again.")
            elif champ.name.lower() in exclude_names:
                print(f"  {champ.name} is already on your team — pick a different one.")
            else:
                return champ

    if mode == "1":
        # ── 1v1 ─────────────────────────────────────────────────
        print()
        c_player = pick_champion("Choose YOUR champion (name or #)")
        c_ai     = pick_champion("Choose OPPONENT champion (name or #)")

        battle = Battle(c_player, c_ai, verbose=True)
        battle.run_interactive(battle.a, battle.b)

    else:
        # ── 6v6 Team Battle ──────────────────────────────────────
        print("\n  Pick your 6 monsters (name or #, one at a time):")
        player_champs: List["Champion"] = []
        for slot in range(1, 7):
            c = pick_champion(f"  Slot {slot}/6", exclude=player_champs)
            player_champs.append(c)
            print(f"  ✓ {c.name} added.")

        # AI gets 6 random monsters
        ai_champs = random.sample(list(champions.values()), 6)
        print("\n  Opponent's team:")
        for c in ai_champs:
            type_str = c.type1 + (f"/{c.type2}" if c.type2 else "")
            print(f"    • {c.name} [{type_str}]")

        # Build BattleChampion lists
        # Use a dummy Battle just to borrow the engine methods
        battle = Battle(player_champs[0], ai_champs[0], verbose=True)
        player_team = [BattleChampion(deepcopy(c)) for c in player_champs]
        ai_team     = [BattleChampion(deepcopy(c)) for c in ai_champs]

        battle.run_team_interactive(player_team, ai_team)


if __name__ == "__main__":
    main()
