"""
wilderness/models.py
====================
Core data classes for Wilderness Mode.

Separation of concerns:
  RunState   — everything that changes within a single run (lost on death)
  MetaState  — permanent progression that persists across all runs
  Map / Node — the procedurally generated stage graph for one run
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Set, Tuple


# ═══════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════

class NodeType(Enum):
    BATTLE = "battle"
    ELITE  = "elite"
    SHOP   = "shop"    # future
    EVENT  = "event"   # future


class RewardType(Enum):
    HEAL     = "heal"
    CURRENCY = "currency"
    ITEM     = "item"


class ItemType(Enum):
    HEAL_SMALL   = "heal_small"    # restore % HP to one mon
    HEAL_FULL    = "heal_full"     # full HP + cleanse status
    MP_RESTORE   = "mp_restore"    # restore MP to one mon
    REVIVE       = "revive"        # revive a fainted mon with 50% HP
    RARE_EQUIP   = "rare_equip"    # placeholder for equipment system


# ═══════════════════════════════════════════════════════════════
# MAP
# ═══════════════════════════════════════════════════════════════

@dataclass
class Realm:
    """A biome that determines which enemy types appear."""
    name: str            # e.g. "Inferno" or "Inferno/Flora" for Bridgeland
    primary: str         # primary essence type
    secondary: Optional[str] = None   # set for Bridgelands

    @property
    def is_bridgeland(self) -> bool:
        return self.secondary is not None

    def __str__(self) -> str:
        if self.secondary:
            return f"{self.primary}/{self.secondary} Bridgeland"
        return f"{self.primary} Realm"


@dataclass
class MapNode:
    """A single node on the run map."""
    node_id:   int
    node_type: NodeType
    stage:     int          # which stage this node belongs to
    realm:     Realm
    # Children are set during map generation; roots have no parent.
    children:  List[int] = field(default_factory=list)   # node_ids

    def label(self) -> str:
        icon = {"battle": "⚔", "elite": "💀", "shop": "🏪", "event": "?"}
        return f"[{icon.get(self.node_type.value,'?')} {self.node_type.value.title()} | {self.realm}]"


@dataclass
class RunMap:
    """
    The full node graph for a run.
    nodes     — all MapNodes keyed by node_id
    current   — the id of the node the player is currently at
    stage     — current stage number (1-indexed)
    """
    nodes:   Dict[int, MapNode]
    current: int
    stage:   int = 1

    def current_node(self) -> MapNode:
        return self.nodes[self.current]

    def next_choices(self) -> List[MapNode]:
        """Return the child nodes the player can move to."""
        return [self.nodes[nid] for nid in self.nodes[self.current].children]


# ═══════════════════════════════════════════════════════════════
# PARTY
# ═══════════════════════════════════════════════════════════════

@dataclass
class PartyMember:
    """
    A champion in the player's active party during a run.
    HP and MP persist between battles — no auto-heal.
    """
    champion_name: str        # key into the champion roster
    level:         int
    current_hp:    int
    max_hp:        int
    current_mp:    int
    max_mp:        int
    is_fainted:    bool = False
    is_shiny:      bool = False
    # Status effects reset between battles for simplicity; extend if desired
    held_item:     Optional[str] = None  # ItemType.value placeholder

    @property
    def hp_pct(self) -> float:
        return self.current_hp / self.max_hp if self.max_hp else 0.0

    def heal(self, amount: int):
        self.current_hp = min(self.max_hp, self.current_hp + amount)
        if self.current_hp > 0:
            self.is_fainted = False

    def revive(self, hp_fraction: float = 0.50):
        self.is_fainted = False
        self.current_hp = max(1, int(self.max_hp * hp_fraction))

    def summary(self) -> str:
        status = "✗ FAINTED" if self.is_fainted else f"{self.current_hp}/{self.max_hp} HP"
        shiny  = " ✨" if self.is_shiny else ""
        return f"{self.champion_name}{shiny} Lv{self.level}  [{status}]"


# ═══════════════════════════════════════════════════════════════
# ITEMS
# ═══════════════════════════════════════════════════════════════

@dataclass
class Item:
    item_type:   ItemType
    name:        str
    description: str
    # For equipment (future): slot, stat bonuses, etc.

    def __str__(self) -> str:
        return f"{self.name}: {self.description}"


# ═══════════════════════════════════════════════════════════════
# REWARDS
# ═══════════════════════════════════════════════════════════════

@dataclass
class RewardOption:
    reward_type: RewardType
    label:       str           # display string shown to player
    heal_amount: int = 0       # HP to restore (HEAL rewards)
    currency:    int = 0       # gold gained (CURRENCY rewards)
    item:        Optional[Item] = None  # item granted (ITEM rewards)


# ═══════════════════════════════════════════════════════════════
# BATTLE RESULT
# ═══════════════════════════════════════════════════════════════

@dataclass
class BattleResult:
    """Returned by battle_hooks after every combat."""
    player_won:       bool
    turns_taken:      int
    # HP remaining for each party slot after battle (index-matched to party list)
    party_hp_after:   List[int]
    party_mp_after:   List[int]
    party_fainted:    List[bool]
    # Currency bonus from this fight (base; rewards system may add more)
    currency_earned:  int = 0


# ═══════════════════════════════════════════════════════════════
# RUN STATE  (lives only for one run)
# ═══════════════════════════════════════════════════════════════

@dataclass
class RunState:
    party:       List[PartyMember]
    inventory:   List[Item] = field(default_factory=list)
    currency:    int = 0
    stage:       int = 1
    run_map:     Optional[RunMap] = None
    run_over:    bool = False
    stages_won:  int = 0   # how many stages cleared (for end-of-run summary)

    # ── Convenience ──────────────────────────────────────────────
    @property
    def living_party(self) -> List[PartyMember]:
        return [m for m in self.party if not m.is_fainted]

    @property
    def highest_level(self) -> int:
        if not self.party:
            return 1
        return max(m.level for m in self.party)

    @property
    def party_full(self) -> bool:
        return len(self.party) >= 6   # config.PARTY_MAX_SIZE

    def is_defeated(self) -> bool:
        return all(m.is_fainted for m in self.party)

    def apply_battle_result(self, result: BattleResult):
        """Write battle HP/MP/faint state back into party members."""
        for i, member in enumerate(self.party):
            if i < len(result.party_hp_after):
                member.current_hp = result.party_hp_after[i]
                member.current_mp = result.party_mp_after[i]
                member.is_fainted = result.party_fainted[i]


# ═══════════════════════════════════════════════════════════════
# META STATE  (persists across runs)
# ═══════════════════════════════════════════════════════════════

@dataclass
class MetaState:
    """
    Permanent progression that survives run death.

    unlocked_champions — set of champion names the player can start runs with
    pc_bonuses         — champion_name → count of duplicate deposits (IV system placeholder)
    total_runs         — lifetime run counter
    best_stage         — furthest stage ever reached
    """
    unlocked_champions: Set[str]          = field(default_factory=set)
    pc_bonuses:         Dict[str, int]    = field(default_factory=dict)
    total_runs:         int               = 0
    best_stage:         int               = 0

    def deposit_to_pc(self, champion_name: str) -> str:
        """
        Deposit a champion to the PC.
        First deposit → unlock for future runs.
        Subsequent deposits → increment IV bonus counter.
        Returns a message describing what happened.
        """
        name = champion_name
        if name not in self.unlocked_champions:
            self.unlocked_champions.add(name)
            return f"{name} is now permanently unlocked for future runs!"
        else:
            self.pc_bonuses[name] = self.pc_bonuses.get(name, 0) + 1
            count = self.pc_bonuses[name]
            return f"{name} deposited (duplicate #{count}) — IV bonus stored."

    def is_unlocked(self, champion_name: str) -> bool:
        return champion_name in self.unlocked_champions

    def get_iv_bonus(self, champion_name: str) -> int:
        return self.pc_bonuses.get(champion_name, 0)
