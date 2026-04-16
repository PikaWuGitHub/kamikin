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
    • Stamina (MP) depletes as moves are used; Struggle is the fallback (no MP cost)
    • MP Regen: 5% of max MP per turn (passively) or 50% if guarding
    • Actions each turn: Attack, Switch, Guard (recover MP faster, reduce damage by 25%)
"""

import csv
import random
import sys
import os
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Tuple
from copy import deepcopy

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

    # ── Properties ──────────────────────────────────────────────
    @property
    def name(self) -> str: return self.base.name

    @property
    def hp_pct(self) -> float:
        return self.current_hp / self.max_hp

    def get_stat(self, key: str) -> int:
        """Actual battle stat including stage modifiers."""
        base = getattr(self.base, key)   # e.g. self.base.mgt
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
        self.status_turns = random.randint(1, 3) if status == StatusEffect.CORRUPTED else 0
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

    # STAB
    stab = STAB_BONUS if (
        move.essence == attacker.base.type1 or
        move.essence == attacker.base.type2
    ) else 1.0

    # Variance
    variance = random.uniform(VARIANCE_MIN, VARIANCE_MAX) if apply_variance else 1.0

    # Critical hit (5% chance, 1.5× damage)
    crit_mult = 1.0
    is_crit = False
    if apply_variance and random.random() < CRIT_CHANCE:
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

def _m(name, ess, cat, bp, acc, mp, pri=0, eff_ch=0.0,
       infl=StatusEffect.NONE, s_boost="", s_stages=0,
       drop="", drop_s=0, recoil=0.0, heal=0.0, drain=0.0, desc="") -> Move:
    return Move(
        name=name, essence=ess, category=cat, base_power=bp,
        accuracy=acc, mp_cost=mp * MP_COST_MULTIPLIER, priority=pri,
        effect_chance=eff_ch, inflict_status=infl,
        self_boost_stat=s_boost, self_boost_stages=s_stages,
        drop_stat=drop, drop_stages=drop_s,
        recoil_fraction=recoil, heal_fraction=heal,
        drain_fraction=drain, description=desc,
    )

P, S, X = MoveCategory.PHYSICAL, MoveCategory.SPECIAL, MoveCategory.STATUS

MOVE_DB: Dict[str, Move] = {}

_moves_raw = [
    # ── INFERNO ──────────────────────────────────────────────────
    _m("Flame Strike",    "Inferno", P, 95, 1.00, 15, desc="Fierce flaming blow."),
    _m("Ember Blast",     "Inferno", S, 95, 1.00, 15, desc="Magical fire burst."),
    _m("Inferno Crash",   "Inferno", P,120, 1.00, 25, recoil=1/3, desc="Devastating—recoils 1/3 damage."),
    _m("Overheat",        "Inferno", S,130, 0.90, 30, eff_ch=1.0, s_boost="mag", s_stages=-2, desc="Burns all—drops own MAG by 2."),
    _m("Will-O-Scorch",   "Inferno", X,  0, 0.85, 10, eff_ch=1.0, infl=StatusEffect.SCORCH, desc="Inflicts Scorch (burn)."),
    _m("Heat Rush",       "Inferno", P, 40, 1.00,  8, pri=1, desc="Priority +1 fire strike."),
    _m("Flame Drain",     "Inferno", S, 75, 1.00, 20, drain=0.50, desc="Heals 50% of damage dealt."),
    _m("Searing Boost",   "Inferno", X,  0, 1.00, 12, eff_ch=1.0, s_boost="mag", s_stages=2, desc="Sharply raises own MAG."),
    # ── AQUA ─────────────────────────────────────────────────────
    _m("Tidal Strike",    "Aqua",    P, 95, 1.00, 15),
    _m("Hydro Pulse",     "Aqua",    S, 95, 1.00, 15),
    _m("Torrent Crash",   "Aqua",    P,120, 1.00, 25, recoil=1/3),
    _m("Geyser Blast",    "Aqua",    S,110, 0.90, 25),
    _m("Aqua Veil",       "Aqua",    X,  0, 1.00, 10, eff_ch=1.0, s_boost="grd", s_stages=1),
    _m("Water Shiv",      "Aqua",    P, 15, 1.00,  5, pri=1, desc="Priority multi-hit concept (hits once here)."),
    _m("Tidal Drain",     "Aqua",    S, 75, 1.00, 20, drain=0.50),
    _m("Water Cleanse",   "Aqua",    X,  0, 1.00, 10, heal=0.50, desc="Heals 50% HP."),
    # ── FLORA ────────────────────────────────────────────────────
    _m("Vine Whip",       "Flora",   P, 95, 1.00, 15),
    _m("Petal Storm",     "Flora",   S, 95, 1.00, 15),
    _m("Root Crush",      "Flora",   P,120, 1.00, 25, recoil=1/3),
    _m("Bloom Burst",     "Flora",   S,110, 0.90, 25),
    _m("Leech Seed",      "Flora",   X,  0, 1.00, 10, eff_ch=1.0, infl=StatusEffect.VENOM, desc="Seeds target—drains HP (venom proxy)."),
    _m("Quick Thorn",     "Flora",   P, 40, 1.00,  8, pri=1),
    _m("Giga Drain",      "Flora",   S, 75, 1.00, 20, drain=0.50),
    _m("Regen Spores",    "Flora",   X,  0, 1.00, 10, heal=0.50),
    # ── TERRA ────────────────────────────────────────────────────
    _m("Rock Slam",       "Terra",   P, 95, 1.00, 15),
    _m("Earthen Pulse",   "Terra",   S, 95, 1.00, 15),
    _m("Tectonic Crash",  "Terra",   P,120, 0.90, 25, recoil=1/3),
    _m("Quake Burst",     "Terra",   S,110, 0.90, 25),
    _m("Stone Harden",    "Terra",   X,  0, 1.00, 12, eff_ch=1.0, s_boost="grd", s_stages=2),
    _m("Rock Shard",      "Terra",   P, 40, 1.00,  8, pri=1),
    _m("Seismic Drain",   "Terra",   P, 75, 1.00, 20, drain=0.50),
    _m("Bedrock Stance",  "Terra",   X,  0, 1.00, 10, eff_ch=1.0, s_boost="wil", s_stages=1),
    # ── WIND ─────────────────────────────────────────────────────
    _m("Gale Slash",      "Wind",    P, 95, 1.00, 15),
    _m("Cyclone Burst",   "Wind",    S, 95, 1.00, 15),
    _m("Hurricane Strike","Wind",    P,120, 0.85, 25, recoil=1/3),
    _m("Twister Blast",   "Wind",    S,110, 0.85, 25),
    _m("Shock Wave",      "Wind",    X,  0, 0.90, 10, eff_ch=1.0, infl=StatusEffect.SHOCK),
    _m("Gust Rush",       "Wind",    P, 40, 1.00,  8, pri=1),
    _m("Whirlwind Drain", "Wind",    S, 75, 1.00, 20, drain=0.50),
    _m("Tailwind",        "Wind",    X,  0, 1.00, 12, eff_ch=1.0, s_boost="swf", s_stages=2),
    # ── VOLT ─────────────────────────────────────────────────────
    _m("Thunder Fang",    "Volt",    P, 95, 1.00, 15, eff_ch=0.10, infl=StatusEffect.SHOCK),
    _m("Volt Beam",       "Volt",    S, 95, 1.00, 15),
    _m("Thunderclap",     "Volt",    P,120, 0.90, 25, recoil=1/3),
    _m("Lightning Surge", "Volt",    S,110, 0.85, 25),
    _m("Thunder Wave",    "Volt",    X,  0, 0.90, 10, eff_ch=1.0, infl=StatusEffect.SHOCK),
    _m("Spark Rush",      "Volt",    P, 40, 1.00,  8, pri=1),
    _m("Volt Drain",      "Volt",    S, 75, 1.00, 20, drain=0.50),
    _m("Overcharge",      "Volt",    X,  0, 1.00, 12, eff_ch=1.0, s_boost="mgt", s_stages=2),
    # ── FROST ────────────────────────────────────────────────────
    _m("Ice Fang",        "Frost",   P, 95, 1.00, 15, eff_ch=0.10, infl=StatusEffect.FROSTBITE),
    _m("Blizzard Beam",   "Frost",   S, 95, 1.00, 15),
    _m("Glacial Crash",   "Frost",   P,120, 1.00, 25, recoil=1/3),
    _m("Absolute Zero",   "Frost",   S,130, 0.85, 30, drop="swf", drop_s=1),
    _m("Frost Bite",      "Frost",   X,  0, 0.85, 10, eff_ch=1.0, infl=StatusEffect.FROSTBITE),
    _m("Ice Shard",       "Frost",   P, 40, 1.00,  8, pri=1),
    _m("Frozen Drain",    "Frost",   S, 75, 1.00, 20, drain=0.50),
    _m("Snow Cloak",      "Frost",   X,  0, 1.00, 10, eff_ch=1.0, s_boost="wil", s_stages=1),
    # ── MIND ─────────────────────────────────────────────────────
    _m("Psionic Strike",  "Mind",    P, 95, 1.00, 15),
    _m("Mind Blast",      "Mind",    S, 95, 1.00, 15),
    _m("Psycho Crash",    "Mind",    P,120, 1.00, 25, recoil=1/3),
    _m("Thought Cannon",  "Mind",    S,110, 0.90, 25),
    _m("Confuse Ray",     "Mind",    X,  0, 0.90, 10, eff_ch=1.0, infl=StatusEffect.BLUR),
    _m("Mental Edge",     "Mind",    P, 40, 1.00,  8, pri=1),
    _m("Focus Drain",     "Mind",    S, 75, 1.00, 20, drain=0.50),
    _m("Calm Mind",       "Mind",    X,  0, 1.00, 12, eff_ch=1.0, s_boost="mag", s_stages=2, desc="Raises MAG by 2 stages."),
    # ── SPIRIT ───────────────────────────────────────────────────
    _m("Soul Strike",     "Spirit",  P, 95, 1.00, 15),
    _m("Specter Blast",   "Spirit",  S, 95, 1.00, 15),
    _m("Phantom Crash",   "Spirit",  P,120, 1.00, 25, recoil=1/3),
    _m("Haunting Wave",   "Spirit",  S,110, 0.90, 25),
    _m("Sleep Shroud",    "Spirit",  X,  0, 0.80, 15, eff_ch=1.0, infl=StatusEffect.SLEEP),
    _m("Ghost Rush",      "Spirit",  P, 40, 1.00,  8, pri=1),
    _m("Soul Drain",      "Spirit",  S, 75, 1.00, 20, drain=0.50),
    _m("Spirit Ward",     "Spirit",  X,  0, 1.00, 12, eff_ch=1.0, s_boost="wil", s_stages=2),
    # ── CURSED ───────────────────────────────────────────────────
    _m("Decay Slash",     "Cursed",  P, 95, 1.00, 15),
    _m("Curse Bolt",      "Cursed",  S, 95, 1.00, 15),
    _m("Corruption Strike","Cursed", P,120, 1.00, 25, recoil=1/3),
    _m("Plague Burst",    "Cursed",  S,110, 0.90, 25),
    _m("Toxic Hex",       "Cursed",  X,  0, 0.90, 10, eff_ch=1.0, infl=StatusEffect.VENOM),
    _m("Shadow Rush",     "Cursed",  P, 40, 1.00,  8, pri=1),
    _m("Life Leech",      "Cursed",  S, 75, 1.00, 20, drain=0.50),
    _m("Dark Pact",       "Cursed",  X,  0, 1.00, 15, eff_ch=1.0, s_boost="mgt", s_stages=3, desc="Raises MGT +3 but costs extra HP concept."),
    # ── BLESS ────────────────────────────────────────────────────
    _m("Sacred Strike",   "Bless",   P, 95, 1.00, 15),
    _m("Holy Beam",       "Bless",   S, 95, 1.00, 15),
    _m("Radiant Crash",   "Bless",   P,120, 1.00, 25, recoil=1/3),
    _m("Purge Blast",     "Bless",   S,110, 0.90, 25),
    _m("Blessed Rest",    "Bless",   X,  0, 1.00, 10, heal=0.50, desc="Heals 50% HP."),
    _m("Light Rush",      "Bless",   P, 40, 1.00,  8, pri=1),
    _m("Radiant Drain",   "Bless",   S, 75, 1.00, 20, drain=0.50),
    _m("Aura Shield",     "Bless",   X,  0, 1.00, 12, eff_ch=1.0, s_boost="wil", s_stages=2),
    # ── MYTHOS ───────────────────────────────────────────────────
    _m("Ancient Claw",    "Mythos",  P, 95, 1.00, 15),
    _m("Legend Pulse",    "Mythos",  S, 95, 1.00, 15),
    _m("Rune Crash",      "Mythos",  P,120, 1.00, 25, recoil=1/3),
    _m("Myth Cannon",     "Mythos",  S,120, 0.85, 30),
    _m("Elder Curse",     "Mythos",  X,  0, 0.85, 12, eff_ch=1.0, infl=StatusEffect.CORRUPTED),
    _m("Myth Rush",       "Mythos",  P, 40, 1.00,  8, pri=1),
    _m("Rune Drain",      "Mythos",  S, 75, 1.00, 20, drain=0.50),
    _m("Arcane Rite",     "Mythos",  X,  0, 1.00, 15, eff_ch=1.0, s_boost="mag", s_stages=2),
    # ── CYBER ────────────────────────────────────────────────────
    _m("Data Strike",     "Cyber",   P, 95, 1.00, 15),
    _m("Laser Burst",     "Cyber",   S, 95, 1.00, 15),
    _m("System Crash",    "Cyber",   P,120, 1.00, 25, recoil=1/3),
    _m("Overload Beam",   "Cyber",   S,110, 0.90, 25),
    _m("Circuit Shock",   "Cyber",   X,  0, 0.90, 10, eff_ch=1.0, infl=StatusEffect.SHOCK),
    _m("Packet Rush",     "Cyber",   P, 40, 1.00,  8, pri=1),
    _m("Data Drain",      "Cyber",   S, 75, 1.00, 20, drain=0.50),
    _m("System Boost",    "Cyber",   X,  0, 1.00, 12, eff_ch=1.0, s_boost="swf", s_stages=2),
    # ── COSMIC ───────────────────────────────────────────────────
    _m("Void Strike",     "Cosmic",  P, 95, 1.00, 15),
    _m("Star Beam",       "Cosmic",  S, 95, 1.00, 15),
    _m("Singularity",     "Cosmic",  P,120, 1.00, 25, recoil=1/3),
    _m("Nova Burst",      "Cosmic",  S,130, 0.85, 30, drop="mag", drop_s=-1),
    _m("Void Stun",       "Cosmic",  X,  0, 1.00, 12, eff_ch=1.0, infl=StatusEffect.STUN),
    _m("Warp Rush",       "Cosmic",  P, 40, 1.00,  8, pri=1),
    _m("Star Drain",      "Cosmic",  S, 75, 1.00, 20, drain=0.50),
    _m("Cosmic Veil",     "Cosmic",  X,  0, 1.00, 10, eff_ch=1.0, s_boost="wil", s_stages=1),
    # ── NEUTRAL ──────────────────────────────────────────────────
    _m("Quick Strike",    "Neutral", P, 95, 1.00, 15),
    _m("Force Pulse",     "Neutral", S, 95, 1.00, 15),
    _m("Crash Down",      "Neutral", P,120, 1.00, 25, recoil=1/3),
    _m("Null Wave",       "Neutral", S,110, 0.90, 25),
    _m("Memento",         "Neutral", X,  0, 1.00, 10, eff_ch=1.0, drop="mgt", drop_s=2, desc="Drops target MGT by 2."),
    _m("Swift Strike",    "Neutral", P, 40, 1.00,  8, pri=1),
    _m("Neutral Drain",   "Neutral", S, 75, 1.00, 20, drain=0.50),
    _m("Adaptive Stance", "Neutral", X,  0, 1.00, 12, eff_ch=1.0, s_boost="mgt", s_stages=1, desc="Raises MGT +1."),
]

# Populate the dictionary
for _mv in _moves_raw:
    MOVE_DB[_mv.name] = _mv

# Struggle — used when MP is fully depleted
STRUGGLE = Move(
    name="Struggle", essence="Neutral", category=MoveCategory.PHYSICAL,
    base_power=50, accuracy=1.0, mp_cost=0, recoil_fraction=0.0,
    description="Recoils 25% max HP. Used when out of MP.",
)

# ─────────────────────────────────────────────────────────────────
# Move pools by type (name references into MOVE_DB)
# ─────────────────────────────────────────────────────────────────
TYPE_MOVE_POOL: Dict[str, Dict[str, str]] = {
    t: {
        "phys":   f"{t.split('/')[0]} {p}" if f"{t} {p}" not in MOVE_DB else f"{t} {p}",
        "spec":   None, "strong": None, "priority": None,
        "status": None, "boost":  None, "drain":    None, "heal": None,
    }
    for t in ESSENCES for p in [""]
}

# Simpler: directly map type → list of move names to pick from
POOL: Dict[str, List[str]] = {
    "Inferno": ["Flame Strike","Ember Blast","Inferno Crash","Overheat","Will-O-Scorch","Heat Rush","Flame Drain","Searing Boost"],
    "Aqua":    ["Tidal Strike","Hydro Pulse","Torrent Crash","Geyser Blast","Aqua Veil","Water Shiv","Tidal Drain","Water Cleanse"],
    "Flora":   ["Vine Whip","Petal Storm","Root Crush","Bloom Burst","Leech Seed","Quick Thorn","Giga Drain","Regen Spores"],
    "Terra":   ["Rock Slam","Earthen Pulse","Tectonic Crash","Quake Burst","Stone Harden","Rock Shard","Seismic Drain","Bedrock Stance"],
    "Wind":    ["Gale Slash","Cyclone Burst","Hurricane Strike","Twister Blast","Shock Wave","Gust Rush","Whirlwind Drain","Tailwind"],
    "Volt":    ["Thunder Fang","Volt Beam","Thunderclap","Lightning Surge","Thunder Wave","Spark Rush","Volt Drain","Overcharge"],
    "Frost":   ["Ice Fang","Blizzard Beam","Glacial Crash","Absolute Zero","Frost Bite","Ice Shard","Frozen Drain","Snow Cloak"],
    "Mind":    ["Psionic Strike","Mind Blast","Psycho Crash","Thought Cannon","Confuse Ray","Mental Edge","Focus Drain","Calm Mind"],
    "Spirit":  ["Soul Strike","Specter Blast","Phantom Crash","Haunting Wave","Sleep Shroud","Ghost Rush","Soul Drain","Spirit Ward"],
    "Cursed":  ["Decay Slash","Curse Bolt","Corruption Strike","Plague Burst","Toxic Hex","Shadow Rush","Life Leech","Dark Pact"],
    "Bless":   ["Sacred Strike","Holy Beam","Radiant Crash","Purge Blast","Blessed Rest","Light Rush","Radiant Drain","Aura Shield"],
    "Mythos":  ["Ancient Claw","Legend Pulse","Rune Crash","Myth Cannon","Elder Curse","Myth Rush","Rune Drain","Arcane Rite"],
    "Cyber":   ["Data Strike","Laser Burst","System Crash","Overload Beam","Circuit Shock","Packet Rush","Data Drain","System Boost"],
    "Cosmic":  ["Void Strike","Star Beam","Singularity","Nova Burst","Void Stun","Warp Rush","Star Drain","Cosmic Veil"],
    "Neutral": ["Quick Strike","Force Pulse","Crash Down","Null Wave","Memento","Swift Strike","Neutral Drain","Adaptive Stance"],
}

PHYSICAL_ROLES = {"Fast Attacker","Bruiser","Revenge Killer"}
SPECIAL_ROLES  = {"Special Sweeper","Burst Caster","Burst Nuker","Setup Sweeper","Tempo Swinger"}
SUPPORT_ROLES  = {"Support","Cleric","Utility","Wall","Utility Support","Utility Pivot"}
MIXED_ROLES    = {"Mixed Sweeper","Generalist","Disruptor","Zone Setter","Hazard Setter","Pivot"}

def auto_moveset(champion: Champion) -> List[Move]:
    """Assign 4 moves to a champion based on typing and role."""
    pools = [POOL.get(champion.type1, POOL["Neutral"])]
    if champion.type2:
        pools.append(POOL.get(champion.type2, POOL["Neutral"]))

    role = champion.role

    def pick(pool, indices):
        moves = []
        for i in indices:
            if i < len(pool):
                mn = pool[i]
                if mn in MOVE_DB:
                    moves.append(MOVE_DB[mn])
        return moves

    # Indices in POOL list: 0=phys, 1=spec, 2=strong_phys, 3=strong_spec,
    #                        4=status/debuff, 5=priority, 6=drain, 7=heal/boost

    if role in PHYSICAL_ROLES:
        # 2 physical, 1 priority from type1, 1 status or heal
        mv = pick(pools[0], [0, 2, 5])           # phys, strong, priority
        mv += pick(pools[0], [4])                 # status
    elif role in SPECIAL_ROLES:
        mv = pick(pools[0], [1, 3, 7])            # spec, strong spec, boost
        mv += pick(pools[0], [4])                 # status
    elif role in SUPPORT_ROLES:
        mv = pick(pools[0], [7, 4])               # heal/boost, status
        if len(pools) > 1:
            mv += pick(pools[1], [7, 6])          # secondary heal, drain
        else:
            mv += pick(pools[0], [6, 1])          # drain, spec
    elif role in MIXED_ROLES:
        mv = pick(pools[0], [0, 1, 4])            # phys, spec, status
        if len(pools) > 1:
            mv += pick(pools[1], [1])             # spec from type2
        else:
            mv += pick(pools[0], [2])             # strong phys
    else:
        # Default: phys, spec, strong, status
        mv = pick(pools[0], [0, 1, 2, 4])

    # Fill to 4 if needed
    while len(mv) < 4:
        idx = len(mv) % len(pools[0])
        mn = pools[0][idx]
        if mn in MOVE_DB and MOVE_DB[mn] not in mv:
            mv.append(MOVE_DB[mn])
        else:
            break  # avoid infinite loop

    return mv[:4]

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
                 verbose: bool = True):
        self.a = BattleChampion(deepcopy(champion_a))
        self.b = BattleChampion(deepcopy(champion_b))
        self.verbose  = verbose
        self.turn_num = 0
        self.log: List[str] = []
        self.fate_break_used = False  # Fate Break can be used once per battle
        self.last_turn_state = None  # (saved_a, saved_b, move_p, move_ai, first, second, mv1, mv2)

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

        # Check if enough MP (or use Struggle)
        actual_move = move
        if move.mp_cost > 0 and attacker.current_mp < move.mp_cost:
            self._print(f"  (Not enough MP — {attacker.name} uses Struggle!)")
            actual_move = STRUGGLE
        elif move.mp_cost > 0:
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

        # Drain healing
        if actual_move.drain_fraction > 0:
            healed = attacker.heal(int(actual_dmg * actual_move.drain_fraction))
            self._print(f"  {attacker.name} drained {healed} HP!")

        # Recoil
        if actual_move.recoil_fraction > 0:
            recoil = max(1, int(actual_dmg * actual_move.recoil_fraction))
            attacker.take_damage(recoil)
            self._print(f"  {attacker.name} took {recoil} recoil damage!")
        elif actual_move == STRUGGLE:
            recoil = max(1, int(attacker.max_hp * 0.25))
            attacker.take_damage(recoil)
            self._print(f"  {attacker.name} took {recoil} recoil damage (25% max HP)!")

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
            self._print(f"  {actor.name} takes a defensive stance! (MP regen +{MP_REGEN_GUARD}/turn, -25% dmg)")
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

        self._banner(f"🏆  BATTLE OVER — {winner} wins after {self.turn_num} turns!")
        self._print(f"  {self.a.name}: {self.a.current_hp}/{self.a.max_hp} HP")
        self._print(f"  {self.b.name}: {self.b.current_hp}/{self.b.max_hp} HP")
        return winner

    def _ai_pick_move(self, attacker: BattleChampion,
                      defender: BattleChampion) -> Move:
        """Greedy AI: picks the move with highest expected damage (or heals when low)."""
        moves = attacker.base.moves or [STRUGGLE]

        # Heal if below 25% HP and has a heal move
        if attacker.hp_pct < 0.25:
            for mv in moves:
                if mv.heal_fraction > 0:
                    return mv

        best_mv, best_val = moves[0], -1
        for mv in moves:
            if mv.mp_cost > attacker.current_mp and mv.mp_cost > 0:
                continue  # Skip if no MP (Struggle handled in resolve_move)
            if mv.base_power > 0:
                tm   = get_type_multiplier(mv.essence, defender.base.type1, defender.base.type2)
                stab = STAB_BONUS if mv.essence in (attacker.base.type1, attacker.base.type2) else 1.0
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
            first, second, mv1, mv2, _, _ = self._turn_order(
                player, "attack", ai, "attack", move_p, move_ai
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

        if player.is_fainted:    winner = ai.name
        elif ai.is_fainted:      winner = player.name
        elif player.current_hp > ai.current_hp: winner = player.name
        elif ai.current_hp > player.current_hp: winner = ai.name
        else: winner = "Draw"

        self._banner(f"🏆  BATTLE OVER — {winner} wins after {self.turn_num} turns!")
        input("\n  Press Enter to exit...")
        return winner

    def _player_choose_action(self, player: BattleChampion,
                              ai: BattleChampion) -> Move:
        """CLI prompt for move selection."""
        print(f"\n  Choose a move for {player.name}:")
        moves = player.base.moves or [STRUGGLE]
        for i, mv in enumerate(moves, 1):
            mp_ok = "✓" if player.current_mp >= mv.mp_cost else "✗"
            tm = get_type_multiplier(mv.essence, ai.base.type1, ai.base.type2)
            tm_str = {0.0:"◼IMMUNE",2.0:"▲ SE",4.0:"▲▲x4"}.get(tm,"")
            if 0 < tm < 1.0:
                tm_str = "▼ NVE"
            print(f"  [{i}] {mv}  {mp_ok} {tm_str}")

        print(f"  [g] Guard  (+{MP_REGEN_GUARD} MP, -50% incoming dmg)")
        fate_break_available = self.last_turn_state is not None and not self.fate_break_used
        if fate_break_available:
            print(f"  [f] Fate Break  (replay last turn with new RNG — once per battle)")

        while True:
            choice = input("  > ").strip().lower()
            if choice == "g":
                player.guarding = True
                print(f"  {player.name} guards!")
                # Return a dummy guard move — handled via action type
                return Move("Guard","Neutral",MoveCategory.STATUS,0,1.0,0,description="Guard action")
            if choice == "f" and fate_break_available:
                return Move("Fate Break","Neutral",MoveCategory.STATUS,0,1.0,0,description="Fate Break")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(moves):
                    return moves[idx]
            except ValueError:
                pass
            print("  Invalid choice — enter a number or 'g'.")

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
        moves = active.base.moves or [STRUGGLE]
        for i, mv in enumerate(moves, 1):
            mp_ok = "✓" if active.current_mp >= mv.mp_cost else "✗"
            tm = get_type_multiplier(mv.essence, opponent.base.type1, opponent.base.type2)
            tm_str = {0.0: "◼IMMUNE", 2.0: "▲ SE", 4.0: "▲▲x4"}.get(tm, "")
            if 0 < tm < 1.0:
                tm_str = "▼ NVE"
            print(f"  [{i}] {mv}  {mp_ok} {tm_str}")

        print(f"  [g] Guard  (+{MP_REGEN_GUARD} MP, -50% incoming dmg)")

        bench = [m for m in player_team if m is not active and not m.is_fainted]
        if bench:
            print(f"  [s] Switch  (use your turn to swap in a different monster)")

        fate_break_available = self.last_turn_state is not None and not self.fate_break_used
        if fate_break_available:
            print(f"  [f] Fate Break  (replay last turn with new RNG — once per battle)")

        while True:
            choice = input("  > ").strip().lower()
            if choice == "g":
                active.guarding = True
                print(f"  {active.name} guards!")
                return Move("Guard", "Neutral", MoveCategory.STATUS, 0, 1.0, 0, description="Guard action")
            if choice == "s" and bench:
                return Move("Switch", "Neutral", MoveCategory.STATUS, 0, 1.0, 0, description="Switch action")
            if choice == "f" and fate_break_available:
                return Move("Fate Break", "Neutral", MoveCategory.STATUS, 0, 1.0, 0, description="Fate Break")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(moves):
                    return moves[idx]
            except ValueError:
                pass
            print("  Invalid choice — enter a number, 'g', 's', or 'f'.")

    def _player_pick_switch_target(self,
                                   player_team: List["BattleChampion"],
                                   current: "BattleChampion",
                                   forced: bool = False) -> "BattleChampion":
        """Let the player choose which bench mon to switch in."""
        bench = [m for m in player_team if m is not current and not m.is_fainted]
        if not bench:
            return current  # No valid switch targets (shouldn't happen if caller checks)

        prompt = "  Forced switch — " if forced else "  Switch to — "
        print(prompt + "choose a monster:")
        for i, m in enumerate(bench, 1):
            pct = int(m.current_hp / m.max_hp * 100)
            type_str = m.base.type1 + (f"/{m.base.type2}" if m.base.type2 else "")
            print(f"  [{i}] {m.name} ({type_str}) — {m.current_hp}/{m.max_hp} HP ({pct}%)")

        while True:
            choice = input("  > ").strip()
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
        self._banner("⚔  6v6 TEAM BATTLE  ⚔")

        p_active = player_team[0]
        a_active = ai_team[0]
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

                self._print(f"\n  ✦ Fate Break activated! Replaying last turn with new RNG...")
                self._show_team_header(player_team, ai_team, p_active, a_active)

                # Replay — same moves, new RNG (skip Switch/Guard replays for simplicity)
                if fb_move_p.name not in ("Switch", "Guard", "Fate Break"):
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
                p_active = self._player_pick_switch_target(player_team, p_active)
                # AI still attacks the new active mon
                self.execute_action(a_active, p_active, "attack", move_ai)
                if p_active.is_fainted:
                    self._print(f"\n  ✦ {p_active.name} fainted!")
            else:
                # ── Normal turn execution ───────────────────────────
                first, second, mv1, mv2, _, _ = self._turn_order(
                    p_active, "attack", a_active, "attack", move_p, move_ai
                )
                self.execute_action(first, second, "attack", mv1)
                if not second.is_fainted:
                    self.execute_action(second, first, "attack", mv2)
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
