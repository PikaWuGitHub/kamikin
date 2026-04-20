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
from battle_engine import StatusEffect as BE_StatusEffect, MAX_LEVEL, STAT_MULT

from .models import PartyMember, BattleResult, Item, ItemType
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

    bc.level = member.level  # for display in battle UI and Resonance proration

    # ── Apply Resonance ───────────────────────────────────────────
    resonance = getattr(member, "resonance", None) or {}
    if resonance:
        # Combat stats (mgt/mag/grd/wil/swf): stored in resonance_bonus;
        # get_stat() prorates them by level automatically.
        bc.resonance_bonus = {
            k: resonance[k]
            for k in ("mgt", "mag", "grd", "wil", "swf")
            if resonance.get(k, 0)
        }

        # VIT (HP): prorated bonus added directly to max_hp
        vit_res = resonance.get("vit", 0)
        if vit_res:
            vit_add = round(vit_res * member.level / MAX_LEVEL)
            bc.max_hp += vit_add
            bc.current_hp = min(member.current_hp, bc.max_hp)

        # STA (MP): flat bonus (MP is not level-scaled).
        # bc.base is a deep copy — we bump base_sta so the engine's MP regen
        # cap (self.base.max_mp) matches the member's max_mp with resonance.
        sta_res = resonance.get("sta", 0)
        if sta_res:
            # base_sta × STAT_MULT = max_mp, so extra_base = sta_res // STAT_MULT
            # (integer division loses up to STAT_MULT-1 = 9 MP — acceptable)
            extra_base = sta_res // STAT_MULT
            if extra_base:
                bc.base.base_sta += extra_base
            # Also cap current_mp to the updated max
            bc.current_mp = min(member.current_mp, bc.base.max_mp)

    # Apply custom move slots if the player used the Move Tutor this run
    if member.custom_moves:
        move_lookup = _get_move_lookup(all_champions)
        custom = [move_lookup[n] for n in member.custom_moves if n in move_lookup]
        if custom:
            bc.base.moves = custom

    return bc


# ── Global move registry ─────────────────────────────────────────

_move_lookup_cache: Dict[str, object] | None = None


def _get_move_lookup(all_champions: Dict[str, Champion]) -> Dict[str, object]:
    """
    Build (and cache) a name→Move lookup table from the full champion roster.
    Used by the Move Tutor and custom_moves resolution.
    """
    global _move_lookup_cache
    if _move_lookup_cache is None:
        lookup: Dict[str, object] = {}
        for c in all_champions.values():
            for mv in c.moves:
                lookup[mv.name] = mv
        _move_lookup_cache = lookup
    return _move_lookup_cache


def read_back_results(
    party:   List[PartyMember],
    bcs:     List[BattleChampion],
) -> None:
    """Write battle-end HP/MP/faint state from BattleChampions back into PartyMembers."""
    for member, bc in zip(party, bcs):
        member.current_hp = bc.current_hp
        member.current_mp = bc.current_mp
        member.is_fainted = bc.is_fainted


# ── In-battle item application ────────────────────────────────────

def _apply_item_to_bc(item: "Item", bc: BattleChampion) -> str:
    """
    Apply a wilderness item directly to a BattleChampion during battle.
    Mirrors apply_item() in items.py but operates on BattleChampion fields.
    """
    if item.item_type in (ItemType.HEAL_LOW, ItemType.HEAL_SMALL):
        fraction = 0.25 if item.item_type == ItemType.HEAL_LOW else 0.40
        amount   = max(1, int(bc.max_hp * fraction))
        old_hp   = bc.current_hp
        bc.current_hp = min(bc.max_hp, bc.current_hp + amount)
        return f"{bc.name} recovered {bc.current_hp - old_hp} HP."

    elif item.item_type == ItemType.HEAL_MED:
        amount = max(1, int(bc.max_hp * 0.50))
        old_hp = bc.current_hp
        bc.current_hp = min(bc.max_hp, bc.current_hp + amount)
        return f"{bc.name} recovered {bc.current_hp - old_hp} HP."

    elif item.item_type == ItemType.HEAL_HIGH:
        amount = max(1, int(bc.max_hp * 0.75))
        old_hp = bc.current_hp
        bc.current_hp = min(bc.max_hp, bc.current_hp + amount)
        return f"{bc.name} recovered {bc.current_hp - old_hp} HP."

    elif item.item_type in (ItemType.HEAL_MAX, ItemType.HEAL_FULL):
        bc.current_hp   = bc.max_hp
        bc.status       = BE_StatusEffect.NONE
        bc.status_turns = 0
        return f"{bc.name} was fully healed and status cleared!"

    elif item.item_type == ItemType.HEAL_STATUS:
        bc.status       = BE_StatusEffect.NONE
        bc.status_turns = 0
        return f"{bc.name}'s status conditions were cleansed."

    elif item.item_type == ItemType.MP_RESTORE:
        bc.current_mp = bc.base.max_mp
        return f"{bc.name}'s MP was fully restored."

    elif item.item_type == ItemType.REVIVE:
        if not bc.is_fainted:
            return f"{bc.name} isn't fainted — revive wasted!"
        bc.is_fainted = False
        bc.current_hp = max(1, int(bc.max_hp * 0.50))
        return f"{bc.name} was revived with {bc.current_hp} HP!"

    elif item.item_type == ItemType.RARE_EQUIP:
        return f"[{item.name}] equipped to {bc.name} (passive system pending)."

    return f"Used {item.name} on {bc.name}."


def _make_battle_item_callback(inventory: list):
    """
    Returns a callback for in-battle item use.
    Signature: callback(active_bc, team_bcs) -> bool
    Modifies inventory in-place when an item is consumed.
    """
    def callback(active_bc: BattleChampion, team_bcs: list) -> bool:
        if not inventory:
            print("  Your bag is empty!")
            return False

        print("\n  Your items:")
        for i, item in enumerate(inventory, 1):
            print(f"  [{i}] {item}")
        print("  [0] Cancel")

        while True:
            raw = input("  Item > ").strip()
            if raw == "0" or raw == "":
                return False
            try:
                idx = int(raw) - 1
                if not (0 <= idx < len(inventory)):
                    raise ValueError
            except ValueError:
                print("  Invalid — enter an item number or 0 to cancel.")
                continue

            item = inventory[idx]

            # Determine valid targets
            if item.item_type == ItemType.REVIVE:
                targets = [bc for bc in team_bcs if bc.is_fainted]
                if not targets:
                    print("  No fainted monsters to revive.")
                    continue
            else:
                targets = [bc for bc in team_bcs if not bc.is_fainted]
                if not targets:
                    print("  No available targets.")
                    continue

            if len(targets) == 1:
                target = targets[0]
            else:
                print("  Target:")
                for j, bc in enumerate(targets, 1):
                    pct = int(bc.current_hp / bc.max_hp * 100) if bc.max_hp else 0
                    print(f"  [{j}] {bc.name} — {bc.current_hp}/{bc.max_hp} HP ({pct}%)")
                while True:
                    t_raw = input("  Target > ").strip()
                    try:
                        t_idx = int(t_raw) - 1
                        if 0 <= t_idx < len(targets):
                            target = targets[t_idx]
                            break
                    except ValueError:
                        pass
                    print("  Invalid target.")

            msg = _apply_item_to_bc(item, target)
            print(f"  {msg}")
            inventory.pop(idx)
            return True

    return callback


# ── Battle runners ────────────────────────────────────────────────

def run_wilderness_battle(
    party:           List[PartyMember],
    enemy_bcs:       List[BattleChampion],
    all_champions:   Dict[str, Champion],
    verbose:         bool = True,
    inventory:       list = None,
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

    item_cb = _make_battle_item_callback(inventory) if inventory is not None else None
    battle = Battle(dummy_p, dummy_e, verbose=verbose, item_callback=item_cb)
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
