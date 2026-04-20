"""
wilderness/move_tutor.py
========================
The Wandering Sage — in-run move equip system.

What changed from the old design
---------------------------------
Previously the Sage both UNLOCKED moves (spending perm_currency) and
EQUIPPED them.  That responsibility has been split:

  Sanctum (main menu, town.py) — permanently unlocks moves with 𝕮
  Wandering Sage (in-run)      — equips already-unlocked moves with 💰

This creates a clean two-step economy:
  1. Earn 𝕮 from runs → spend at the Sanctum between runs
  2. During a run → visit the Sage to equip what you've unlocked

Cost to equip
-------------
  SAGE_EQUIP_BASE_COST × move.tier  (paid in run-time gold)
    Tier 1:  10 💰
    Tier 2:  25 💰
    Tier 3:  50 💰
    Tier 4: 100 💰

Level gating
------------
A move cannot be equipped if the champion is below SAGE_TIER_LEVEL_REQ[tier].
(Prevents equipping Tier-4 moves on a level-5 champion even if it's unlocked.)

Dataclass
---------
TutorMove is kept for any code that still imports it, but is no longer
used internally — the Sage builds its list from meta.unlocked_moves directly.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import RunState, MetaState

log = logging.getLogger(__name__)

from .config import SAGE_EQUIP_BASE_COST, SAGE_TIER_LEVEL_REQ


# ═══════════════════════════════════════════════════════════════
# LEGACY DATACLASS (kept for back-compat)
# ═══════════════════════════════════════════════════════════════

@dataclass
class TutorMove:
    """A move entry in a champion's learn list (legacy; see Sanctum for new usage)."""
    move:        object
    learn_level: int
    cost:        int


# ═══════════════════════════════════════════════════════════════
# SAGE INVENTORY — what the Sage can equip for this champion
# ═══════════════════════════════════════════════════════════════

def _sage_inventory(
    champ_name:      str,
    champion_level:  int,
    meta:            "MetaState",
    all_champions:   dict,
) -> List[dict]:
    """
    Build the list of moves the Sage can equip for this champion.

    Rules
    -----
    • Only moves in meta.unlocked_moves[champ_name] are shown.
    • Moves whose tier level-requirement exceeds champion_level are shown
      but marked as unavailable (so the player sees what's coming).
    • Returns a list of dicts: {move, tier, cost, can_equip}
    """
    from battle_engine import MOVE_DB

    unlocked_names = meta.unlocked_moves.get(champ_name, set())
    if not unlocked_names:
        return []

    entries = []
    for name in sorted(unlocked_names):
        mv = MOVE_DB.get(name)
        if mv is None:
            continue
        tier      = mv.tier
        cost      = SAGE_EQUIP_BASE_COST * tier
        level_req = SAGE_TIER_LEVEL_REQ.get(tier, 1)
        can_equip = champion_level >= level_req
        entries.append({
            "move":      mv,
            "tier":      tier,
            "cost":      cost,
            "level_req": level_req,
            "can_equip": can_equip,
        })

    # Sort by tier then name
    entries.sort(key=lambda e: (e["tier"], e["move"].name))
    return entries


# ═══════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════

_TIER_STARS = {1: "★☆☆☆", 2: "★★☆☆", 3: "★★★☆", 4: "★★★★"}


def _section(text: str):
    print(f"\n  ── {text} {'─' * max(0, 50 - len(text))}")


def _idx_to_letter(i: int) -> str:
    """0-based index → label letter: 0→A, 1→B, … 25→Z, 26→AA …"""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if i < 26:
        return letters[i]
    return letters[i // 26 - 1] + letters[i % 26]


def _letter_to_idx(s: str) -> int:
    """Reverse of _idx_to_letter. Returns -1 on bad input."""
    s = s.upper().strip()
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if len(s) == 1 and s in letters:
        return letters.index(s)
    if len(s) == 2 and s[0] in letters and s[1] in letters:
        return (letters.index(s[0]) + 1) * 26 + letters.index(s[1])
    return -1


def _show_sage_inventory(
    entries: List[dict],
    run_currency: int,
):
    """Print the Sage's available moves for equipping (lettered A, B, C…)."""
    if not entries:
        print("\n  No unlocked moves available for this champion.")
        print("  Visit the Sanctum between runs to unlock moves.")
        return

    print()
    print(f"       {'Move':<22} {'Type':<10} {'Cat':<9} "
          f"{'BP':>4}  {'Tier':<10}  {'Cost':>6}  Status")
    print("  " + "─" * 82)

    for i, entry in enumerate(entries):
        label     = _idx_to_letter(i)
        mv        = entry["move"]
        cost      = entry["cost"]
        can_equip = entry["can_equip"]
        lv_req    = entry["level_req"]
        bp_str    = str(mv.base_power) if mv.base_power > 0 else "—"
        cat_str   = mv.category.value[:8]
        tier_str  = _TIER_STARS.get(entry["tier"], "")

        if not can_equip:
            status = f"  Need Lv{lv_req}"
        elif run_currency < cost:
            status = f"  ✗ need {cost} 💰"
        else:
            status = f"  {cost} 💰  ← equip"

        print(f"  ({label})  {mv.name:<22} {mv.essence:<10} {cat_str:<9} "
              f"{bp_str:>4}  {tier_str:<10}  {cost:>5}💰  {status}")
    print()


def _show_current_moves(champion, member):
    """Print the champion's currently active moveset."""
    custom = member.custom_moves or []
    if custom:
        print(f"\n  {member.champion_name}'s moves (custom):")
        for i, name in enumerate(custom, 1):
            print(f"  [{i}] {name}")
    else:
        print(f"\n  {member.champion_name}'s moves (default):")
        for i, mv in enumerate(champion.moves or [], 1):
            bp_str = str(mv.base_power) if mv.base_power > 0 else "—"
            print(f"  [{i}] {mv.name}  ({mv.essence}, BP {bp_str})")


def _equip_move(entry: dict, member, champion):
    """
    Prompt the player to choose which move slot to replace.
    Returns the gold cost if equipped, or 0 if cancelled.
    """
    mv   = entry["move"]
    cost = entry["cost"]

    # Build current effective movelist (names)
    if member.custom_moves:
        current_names = list(member.custom_moves)
    else:
        current_names = [m.name for m in (champion.moves or [])]

    if not current_names:
        print("  This champion has no move slots.")
        return 0

    print(f"\n  Equip {mv.name}  ({mv.essence}, "
          f"{'BP ' + str(mv.base_power) if mv.base_power else 'Status'})  "
          f"for {cost} 💰")
    print("  Replace which slot?")
    for i, name in enumerate(current_names, 1):
        print(f"  [{i}] {name}")
    print("  [0] Cancel")

    while True:
        raw = input("  Slot > ").strip()
        if raw == "0" or raw == "":
            return 0
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(current_names):
                old = current_names[idx]
                current_names[idx] = mv.name
                member.custom_moves = current_names
                print(f"  {old} → {mv.name}  ✓")
                return cost
        except ValueError:
            pass
        print("  Invalid slot.")


# ═══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def run_move_tutor(
    run:           "RunState",
    meta:          "MetaState",
    all_champions: dict,
):
    """
    Full Wandering Sage interaction (in-run equip system).

    Flow
    ----
    1. Show run currency balance.
    2. Player selects a champion from the party.
    3. Show all moves unlocked (via Sanctum) for that champion.
    4. Player pays gold to equip an unlocked move into a slot.
    """
    print()
    print("  ══════════════════════════════════════════════════════════════")
    print("  ✦  WANDERING SAGE  —  Move Equip")
    print("  ══════════════════════════════════════════════════════════════")
    print("  Equip moves you've unlocked at the Sanctum.")
    print(f"  Run currency: {run.currency} 💰")
    print()

    # ── Select champion ───────────────────────────────────────────
    print("  Which champion would you like to train?")
    for i, member in enumerate(run.party, 1):
        fainted = " ✗ FAINTED" if member.is_fainted else ""
        unlocked_count = len(meta.unlocked_moves.get(member.champion_name, set()))
        print(f"  [{i}] {member.champion_name:<18} Lv{member.level:<3}  "
              f"{unlocked_count} move(s) unlocked{fainted}")
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

    # ── Main equip loop ───────────────────────────────────────────
    while True:
        _section(f"Wandering Sage — {member.champion_name} Lv{member.level}")
        print(f"  Run currency: {run.currency} 💰")

        entries = _sage_inventory(
            member.champion_name, member.level, meta, all_champions
        )

        _show_sage_inventory(entries, run.currency)
        _show_current_moves(champion, member)

        if not entries:
            # Nothing to show — bail gracefully
            print()
            input("  [Enter to leave]")
            break

        letter_range = ", ".join(
            _idx_to_letter(i) for i in range(min(len(entries), 5))
        ) + ("…" if len(entries) > 5 else "")
        print()
        print(f"  [A/B/C…]  Type a letter to equip that move  ({letter_range})")
        print("  [0]       Leave the Sage")
        print()

        raw = input("  > ").strip()
        if raw == "0" or raw == "":
            break

        mv_idx = _letter_to_idx(raw)
        if not (0 <= mv_idx < len(entries)):
            print("  Enter a letter (A, B, C…) to select a move, or 0 to leave.")
            continue

        entry = entries[mv_idx]

        if not entry["can_equip"]:
            print(f"\n  {entry['move'].name} requires "
                  f"Lv{entry['level_req']} (you are Lv{member.level}).")
            input("  [Enter to continue]")
            continue

        if run.currency < entry["cost"]:
            print(f"\n  Not enough gold "
                  f"(need {entry['cost']} 💰, have {run.currency} 💰).")
            input("  [Enter to continue]")
            continue

        cost_paid = _equip_move(entry, member, champion)
        if cost_paid > 0:
            run.currency -= cost_paid
            print(f"  Gold remaining: {run.currency} 💰")
            log.info("Sage equip: %s gained %s (cost %d 💰, remaining %d)",
                     member.champion_name, entry["move"].name,
                     cost_paid, run.currency)

    print("\n  The Wandering Sage nods.  May your moves serve you well.")
