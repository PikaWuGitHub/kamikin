"""
wilderness/move_tutor.py
========================
The Wandering Sage — Move Tutor system for Wilderness Mode.

Each champion has a personal "learn list" of moves they can unlock via
permanent meta-currency (meta.perm_currency).  The learn list is generated
deterministically from the global move pool, seeded by the champion name,
so it's stable across sessions without requiring hand-curated data.

Unlock model
------------
• A move is LOCKED until the player spends perm_currency to unlock it.
• Unlocked moves are stored permanently in meta.unlocked_moves[champion_name].
• A move cannot be equipped unless the active champion is at or above its
  learn_level threshold.
• Once unlocked AND the level requirement is met, the player can equip the
  move by replacing one of the champion's current four move slots.
• Custom moves are stored in PartyMember.custom_moves and persist for the
  current run.  They are re-applied each battle via battle_hooks.

Cost formula
------------
  cost = max(TUTOR_MIN_COST, learn_level × TUTOR_COST_PER_LEVEL)
  Tune TUTOR_COST_PER_LEVEL and TUTOR_MIN_COST in config.py.

Move pool
---------
All unique moves across the champion roster form the global pool.
Each champion gets NUM_TUTOR_MOVES moves drawn from the pool (excluding
their default moves), sorted by base_power and assigned ascending
learn_level tiers so stronger moves cost more and unlock later.

Design note
-----------
The learn list is a placeholder — future content work will replace it with
hand-curated per-champion signature moves.  The scaffold is intentionally
flexible: once curated data exists, swap get_learn_list() without touching
the UI or unlock mechanics.
"""

from __future__ import annotations
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import RunState, MetaState

from .config import (
    TUTOR_COST_PER_LEVEL, TUTOR_MIN_COST, NUM_TUTOR_MOVES,
    TUTOR_LEARN_LEVELS,
)


# ═══════════════════════════════════════════════════════════════
# DATA
# ═══════════════════════════════════════════════════════════════

@dataclass
class TutorMove:
    """A move entry in a champion's learn list."""
    move:        object   # battle_engine.Move (typed loosely to avoid circular import)
    learn_level: int      # minimum champion level required to equip
    cost:        int      # permanent currency cost to unlock


# ═══════════════════════════════════════════════════════════════
# LEARN LIST GENERATION
# ═══════════════════════════════════════════════════════════════

def _tutor_cost(learn_level: int) -> int:
    return max(TUTOR_MIN_COST, learn_level * TUTOR_COST_PER_LEVEL)


def get_learn_list(champion, all_champions: dict) -> List[TutorMove]:
    """
    Generate a stable, per-champion learn list.

    Algorithm
    ---------
    1. Collect every unique move from the global roster.
    2. Exclude the champion's own default moves.
    3. Sort the pool by name for a stable base order.
    4. Shuffle deterministically using the champion name as seed.
    5. Take the first NUM_TUTOR_MOVES candidates.
    6. Sort those candidates by base_power (ascending) and assign learn levels
       from TUTOR_LEARN_LEVELS so weaker moves are accessible earlier.
    """
    # Build global move pool
    all_moves: Dict[str, object] = {}
    for c in all_champions.values():
        for mv in c.moves:
            all_moves[mv.name] = mv

    existing_names = {mv.name for mv in champion.moves}
    candidates = sorted(
        [mv for mv in all_moves.values() if mv.name not in existing_names],
        key=lambda mv: mv.name,
    )

    # Deterministic per-champion shuffle
    rng = random.Random(abs(hash(champion.name.lower())) % (2 ** 32))
    rng.shuffle(candidates)

    selected = candidates[:NUM_TUTOR_MOVES]
    # Sort by base_power so learn levels map naturally to move strength
    selected.sort(key=lambda mv: (mv.base_power, mv.name))

    result = []
    for i, mv in enumerate(selected):
        ll   = TUTOR_LEARN_LEVELS[min(i, len(TUTOR_LEARN_LEVELS) - 1)]
        cost = _tutor_cost(ll)
        result.append(TutorMove(move=mv, learn_level=ll, cost=cost))

    return result


# ═══════════════════════════════════════════════════════════════
# MOVE TUTOR UI
# ═══════════════════════════════════════════════════════════════

def _section(text: str):
    print(f"\n  ── {text} {'─' * max(0, 50 - len(text))}")


def _choose_int(prompt: str, lo: int, hi: int) -> int:
    while True:
        raw = input(f"  {prompt} [{lo}-{hi}]: ").strip()
        try:
            val = int(raw)
            if lo <= val <= hi:
                return val
        except ValueError:
            pass
        print(f"  Enter a number between {lo} and {hi}.")


def _show_learn_list(
    learn_list: List[TutorMove],
    champion_level: int,
    unlocked_names: set,
):
    """Print the learn list for a champion with lock/unlock status."""
    print()
    print(f"  {'#':<3} {'Move':<20} {'Type':<10} {'Cat':<9} "
          f"{'BP':>4}  {'Lv Req':>6}  {'Cost':>5}  Status")
    print("  " + "─" * 78)
    for i, tm in enumerate(learn_list, 1):
        mv      = tm.move
        unlocked = mv.name in unlocked_names
        can_use  = champion_level >= tm.learn_level
        bp_str   = str(mv.base_power) if mv.base_power > 0 else "—"
        cat_str  = mv.category.value[:8]

        if unlocked:
            status = "✓ UNLOCKED" + ("  (can equip)" if can_use else f"  (need Lv{tm.learn_level})")
        else:
            status = f"🔒  {tm.cost}𝕮  (need Lv{tm.learn_level})"

        print(f"  [{i:<2}] {mv.name:<20} {mv.essence:<10} {cat_str:<9} "
              f"{bp_str:>4}  {tm.learn_level:>6}  {tm.cost:>5}  {status}")


def _show_current_moves(champion, member):
    """Print the champion's active moveset."""
    moves = member.custom_moves or []  # names of custom moves if any

    # We show the moves that would actually be used in battle.
    # If custom_moves is set, those names replace the defaults.
    if moves:
        print(f"\n  Current moves (custom):")
        for i, name in enumerate(moves, 1):
            print(f"  [{i}] {name}")
    else:
        print(f"\n  Current moves (default):")
        for i, mv in enumerate(champion.moves or [], 1):
            bp_str = str(mv.base_power) if mv.base_power > 0 else "—"
            print(f"  [{i}] {mv.name}  ({mv.essence}, BP {bp_str})")


def _equip_move(
    tm: TutorMove,
    member,
    champion,
):
    """
    Let the player choose which move slot to replace with tm.move.
    Updates member.custom_moves in-place.
    """
    # Build the current effective move list (names)
    if member.custom_moves:
        current_names = list(member.custom_moves)
    else:
        current_names = [mv.name for mv in (champion.moves or [])]

    if not current_names:
        print("  This champion has no moves to replace.")
        return

    print(f"\n  Replace which move with {tm.move.name}?")
    for i, name in enumerate(current_names, 1):
        print(f"  [{i}] {name}")
    print("  [0] Cancel")

    while True:
        raw = input("  Slot > ").strip()
        if raw == "0" or raw == "":
            return
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(current_names):
                old_name = current_names[idx]
                current_names[idx] = tm.move.name
                member.custom_moves = current_names
                print(f"  {old_name} → {tm.move.name}  ✓")
                return
        except ValueError:
            pass
        print("  Invalid slot.")


def run_move_tutor(
    run:           "RunState",
    meta:          "MetaState",
    all_champions: dict,
):
    """
    Full Move Tutor (Wandering Sage) interaction.

    Flow
    ----
    1. Show perm_currency balance.
    2. Player selects a champion from the party.
    3. Show the champion's learn list with unlock / level status.
    4. Player can: unlock a move, equip an unlocked move, or exit.
    """
    print("\n" + "═" * 60)
    print("  ✦  WANDERING SAGE  —  Move Tutor")
    print(f"  Permanent currency: {meta.perm_currency} 𝕮")
    print("═" * 60)

    # ── Select champion ───────────────────────────────────────────
    print("\n  Which champion would you like to train?")
    for i, member in enumerate(run.party, 1):
        fainted = " ✗ FAINTED" if member.is_fainted else ""
        print(f"  [{i}] {member.champion_name} Lv{member.level}{fainted}")
    print("  [0] Leave")
    print()

    raw = input("  > ").strip()
    if raw == "0" or raw == "":
        return
    try:
        p_idx = int(raw) - 1
        if not (0 <= p_idx < len(run.party)):
            raise ValueError
    except ValueError:
        print("  Invalid choice.")
        return

    member   = run.party[p_idx]
    champ_lc = member.champion_name.lower()
    champion = all_champions.get(champ_lc)
    if champion is None:
        print(f"  Champion data not found for {member.champion_name}.")
        return

    learn_list     = get_learn_list(champion, all_champions)
    unlocked_names = meta.unlocked_moves.get(member.champion_name, set())

    # ── Main tutor loop ───────────────────────────────────────────
    while True:
        _section(f"Wandering Sage — {member.champion_name} Lv{member.level}")
        print(f"  Permanent currency: {meta.perm_currency} 𝕮")
        _show_learn_list(learn_list, member.level, unlocked_names)
        _show_current_moves(champion, member)

        print()
        print("  [#]   Select a move to unlock or equip")
        print("  [0]   Leave the Wandering Sage")
        print()

        raw = input("  > ").strip()
        if raw == "0" or raw == "":
            break

        try:
            mv_idx = int(raw) - 1
            if not (0 <= mv_idx < len(learn_list)):
                raise ValueError
        except ValueError:
            print("  Enter a move number or 0 to leave.")
            continue

        tm       = learn_list[mv_idx]
        mv       = tm.move
        unlocked = mv.name in unlocked_names
        can_equip = member.level >= tm.learn_level

        if not unlocked:
            # Offer to unlock
            print(f"\n  {mv.name}  ({mv.essence}, BP {mv.base_power or '—'})")
            print(f"  Cost:      {tm.cost} 𝕮")
            print(f"  Level req: {tm.learn_level}  (you are Lv{member.level})")
            if meta.perm_currency < tm.cost:
                print(f"  ✗ Not enough currency "
                      f"(need {tm.cost}, have {meta.perm_currency}).")
            else:
                confirm = input(f"  Unlock for {tm.cost} 𝕮? [y/N] ").strip().lower()
                if confirm == "y":
                    meta.perm_currency -= tm.cost
                    if member.champion_name not in meta.unlocked_moves:
                        meta.unlocked_moves[member.champion_name] = set()
                    meta.unlocked_moves[member.champion_name].add(mv.name)
                    unlocked_names = meta.unlocked_moves[member.champion_name]
                    print(f"  ✓ {mv.name} unlocked for {member.champion_name}!")
                    if not can_equip:
                        print(f"  (Reach Lv{tm.learn_level} to equip it.)")

        else:
            # Already unlocked — offer to equip
            print(f"\n  {mv.name}  —  already unlocked")
            if not can_equip:
                print(f"  ✗ Requires Lv{tm.learn_level} "
                      f"(you are Lv{member.level}).")
            else:
                _equip_move(tm, member, champion)

    print("\n  The Wandering Sage nods. Safe travels.")
