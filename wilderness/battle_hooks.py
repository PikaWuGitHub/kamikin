"""
wilderness/battle_hooks.py
==========================
Bridge between Wilderness Mode and the core battle engine.

Responsibilities
----------------
1. Convert PartyMembers → scaled BattleChampions (with correct current HP/MP)
2. Run the appropriate battle mode (1v1 or team)
3. Read back HP/MP/faint state and return a BattleResult
4. Keep HP persistent between wilderness battles

This is the only file that imports from battle_engine directly,
making it the single seam to swap or mock the engine in tests.
"""

from __future__ import annotations
import sys, os
from copy import deepcopy
from typing import List, Dict, TYPE_CHECKING

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from battle_engine import Champion, BattleChampion, Battle, load_champions

from .models import PartyMember, BattleResult
from .scaling import scale_champion, scaled_stat


# ── Champion cache ────────────────────────────────────────────────

_champion_cache: Dict[str, Champion] | None = None

def get_champion_roster(csv_path: str | None = None) -> Dict[str, Champion]:
    """Load (and cache) the champion roster."""
    global _champion_cache
    if _champion_cache is None:
        if csv_path is None:
            here = os.path.dirname(os.path.abspath(__file__))
            csv_path = os.path.join(
                here, "..",
                "02_Prototype and Builds", "Champions", "Kiboru_Champions_v3.csv"
            )
        _champion_cache = load_champions(csv_path)
    return _champion_cache


# ── Conversion helpers ────────────────────────────────────────────

def party_member_to_battle_champion(
    member: PartyMember,
    all_champions: Dict[str, Champion],
) -> BattleChampion:
    """
    Create a BattleChampion from a PartyMember.

    Stats are scaled to the member's level.
    Current HP/MP are preserved (wilderness HP persistence).
    """
    base = all_champions.get(member.champion_name.lower())
    if base is None:
        raise ValueError(f"Champion '{member.champion_name}' not found in roster.")

    scaled = scale_champion(base, member.level)
    bc     = BattleChampion(scaled)

    # Override max_hp with the correct formula: round(base_vit × STAT_MULT × level / 100).
    # scale_champion rounds base_vit first (int constraint), so bc.base.max_hp can differ
    # slightly from the true scaled value. Setting bc.max_hp explicitly keeps the battle
    # UI, heal/status calculations, and level-up logic all consistent.
    bc.max_hp = scaled_stat(base.base_vit, member.level)

    # Preserve persistent HP/MP — cap to new scaled max just in case
    bc.current_hp = min(member.current_hp, bc.max_hp)
    bc.current_mp = min(member.current_mp, bc.base.max_mp)
    if member.is_fainted:
        bc.is_fainted = True
        bc.current_hp = 0

    bc.level = member.level  # for display in battle UI

    return bc


def read_back_results(
    party:   List[PartyMember],
    bcs:     List[BattleChampion],
) -> None:
    """Write battle-end HP/MP/faint state from BattleChampions back into PartyMembers."""
    for member, bc in zip(party, bcs):
        member.current_hp = bc.current_hp
        member.current_mp = bc.current_mp
        member.is_fainted = bc.is_fainted


# ── Battle runners ────────────────────────────────────────────────

def run_wilderness_battle(
    party:           List[PartyMember],
    enemy_bcs:       List[BattleChampion],
    all_champions:   Dict[str, Champion],
    verbose:         bool = True,
) -> BattleResult:
    """
    Run a wilderness battle between the player's living party and enemy_bcs.

    Uses team battle if either side has >1 member, otherwise 1v1.
    HP/MP changes are written back to party members after the battle.

    Returns a BattleResult.
    """
    living = [m for m in party if not m.is_fainted]
    if not living:
        # Entire party already fainted — shouldn't happen but handle gracefully
        return BattleResult(
            player_won    = False,
            turns_taken   = 0,
            party_hp_after  = [m.current_hp for m in party],
            party_mp_after  = [m.current_mp for m in party],
            party_fainted   = [m.is_fainted for m in party],
        )

    # Build BattleChampion instances for living party members
    party_bcs: List[BattleChampion] = []
    bc_to_member: Dict[int, int] = {}  # id(bc) → party index

    for i, member in enumerate(party):
        bc = party_member_to_battle_champion(member, all_champions)
        party_bcs.append(bc)
        bc_to_member[id(bc)] = i

    # Choose battle mode
    use_team = len(living) > 1 or len(enemy_bcs) > 1

    # Dummy Champion objects required by Battle.__init__ (it deep-copies them)
    dummy_p = party_bcs[0].base
    dummy_e = enemy_bcs[0].base

    battle = Battle(dummy_p, dummy_e, verbose=verbose)
    # Fix HP-bar display bug: Battle.__init__ deep-copies dummy champions into
    # self.a / self.b at full HP.  Reassign them so show_state() sees the real
    # BattleChampion objects (with current, possibly-reduced HP/MP).
    battle.a = party_bcs[0]
    battle.b = enemy_bcs[0]

    if use_team:
        winner = battle.run_team_interactive(party_bcs, enemy_bcs)
        player_won = (winner == "Player")
    else:
        winner = battle.run_interactive(party_bcs[0], enemy_bcs[0])
        player_won = (winner == party_bcs[0].name)

    # Write HP/MP back to party members
    read_back_results(party, party_bcs)

    return BattleResult(
        player_won    = player_won,
        turns_taken   = battle.turn_num,
        party_hp_after  = [m.current_hp for m in party],
        party_mp_after  = [m.current_mp for m in party],
        party_fainted   = [m.is_fainted for m in party],
    )
