"""
wilderness/town.py
==================
The Sanctum — persistent meta-progression screen accessed from the main menu.

What it does
------------
After earning permanent currency (𝕮) from runs, the player visits the Sanctum
to permanently unlock moves for specific champions.  Unlocking a move here
makes it available to the Wandering Sage during future runs — the Sage then
lets the player equip those unlocked moves in exchange for run-time gold.

Flow
----
  Main menu → [S] Visit Sanctum
  Sanctum
    1. Pick a champion from your unlocked roster (meta.unlocked_champions)
    2. Browse that champion's type-pool learn list (cross-type moves included)
    3. Spend 𝕮 to permanently unlock a move
       — OR —
    4. Use the Fate Seal (gacha): spend 60 𝕮 to randomly draw one move from
       a hidden champion-specific pool; pity guarantees a new move every 3 draws.

Unlock tiers (browse list)
--------------------------
  Cost = SANCTUM_BASE_COST × move.tier
    Tier 1 Basic      →  5 𝕮   (any level)
    Tier 2 Standard   → 15 𝕮   (Lv 16+)
    Tier 3 Advanced   → 30 𝕮   (Lv 36+)
    Tier 4 Signature  → 60 𝕮   (Lv 61+)

Fate Seal
---------
  Cost:       60 𝕮 per draw
  Pity:       every 3rd draw guaranteed to give a move you don't have yet
  Duplicate:  refunds 15 𝕮 instead of re-adding the same move
  Pool:       FATE_SEAL_POOL[champion_name] in battle_engine.py
"""

from __future__ import annotations
import logging
import random
from typing import TYPE_CHECKING, Dict, List, Optional, Set

if TYPE_CHECKING:
    from .models import MetaState

log = logging.getLogger(__name__)

from .config import (
    SANCTUM_BASE_COST, SANCTUM_TIER_LEVEL_REQ, SANCTUM_MAX_LEARN_LIST,
    FATE_SEAL_COST, FATE_SEAL_PITY_THRESHOLD, FATE_SEAL_DUPE_REFUND,
)

# Resonance stat display order and labels
_RES_STAT_LABELS = [
    ("vit", "VIT"), ("sta", "STA"), ("mgt", "MGT"), ("mag", "MAG"),
    ("grd", "GRD"), ("wil", "WIL"), ("swf", "SWF"),
]
_RES_MAX = 100   # hard cap per stat


# ═══════════════════════════════════════════════════════════════
# LEARN LIST — what a champion can unlock via the browse screen
# ═══════════════════════════════════════════════════════════════

def sanctum_learn_list(champion, all_champions: dict = None) -> list:
    """
    Build the Sanctum browse learn list for a champion.

    Two sections:
      1. Type-pool moves  — from POOL[champion.type1] (and type2 if dual-typed),
                            excluding moves already in the champion's default T1 set.
      2. Cross-type moves — from CROSS_LEARNSET[champion.name], deduplicated against
                            section 1.

    Returns a list of dicts with keys: move, tier, cost, level_req, cross_type
    (cross_type=True flags moves from section 2 for display differentiation).

    Sorted within each section by tier then name.  Total capped at
    SANCTUM_MAX_LEARN_LIST per section (cross-type cap = same).
    """
    from battle_engine import POOL, MOVE_DB, CROSS_LEARNSET

    # ── Section 1: type-pool moves ────────────────────────────────
    pool_names: List[str] = list(POOL.get(champion.type1, []))
    if champion.type2:
        for name in POOL.get(champion.type2, []):
            if name not in pool_names:
                pool_names.append(name)

    default_names = {mv.name for mv in (champion.moves or [])}

    type_entries = []
    seen = set()
    for name in pool_names:
        mv = MOVE_DB.get(name)
        if mv is None or mv.name in default_names:
            continue
        cost      = SANCTUM_BASE_COST * mv.tier
        level_req = SANCTUM_TIER_LEVEL_REQ.get(mv.tier, 1)
        type_entries.append({
            "move":       mv,
            "tier":       mv.tier,
            "cost":       cost,
            "level_req":  level_req,
            "cross_type": False,
        })
        seen.add(name)

    type_entries.sort(key=lambda e: (e["tier"], e["move"].name))
    type_entries = type_entries[:SANCTUM_MAX_LEARN_LIST]

    # ── Section 2: cross-type moves ───────────────────────────────
    cross_names: List[str] = CROSS_LEARNSET.get(champion.name, [])
    cross_entries = []
    for name in cross_names:
        if name in seen or name in default_names:
            continue
        mv = MOVE_DB.get(name)
        if mv is None:
            continue
        cost      = SANCTUM_BASE_COST * mv.tier
        level_req = SANCTUM_TIER_LEVEL_REQ.get(mv.tier, 1)
        cross_entries.append({
            "move":       mv,
            "tier":       mv.tier,
            "cost":       cost,
            "level_req":  level_req,
            "cross_type": True,
        })
        seen.add(name)

    cross_entries.sort(key=lambda e: (e["tier"], e["move"].name))
    cross_entries = cross_entries[:SANCTUM_MAX_LEARN_LIST]

    return type_entries + cross_entries


# ═══════════════════════════════════════════════════════════════
# FATE SEAL — gacha draw logic
# ═══════════════════════════════════════════════════════════════

def _fate_seal_draw(
    champ_name: str,
    meta: "MetaState",
    save_fn,
) -> None:
    """
    Perform one Fate Seal draw for champ_name.

    Pity system
    -----------
    draws_so_far = meta.fate_seal_draws.get(champ_name, 0)
    If (draws_so_far + 1) % PITY_THRESHOLD == 0, the draw is guaranteed
    to pick a move the player does NOT already have.

    Duplicate handling
    ------------------
    If the drawn move is already in fate_seal_unlocked[champ_name],
    the player receives a partial refund of FATE_SEAL_DUPE_REFUND 𝕮.

    Cost is deducted BEFORE we reveal the result (caller already confirmed).
    """
    from battle_engine import FATE_SEAL_POOL, MOVE_DB

    pool: List[str] = FATE_SEAL_POOL.get(champ_name, [])
    if not pool:
        print(f"\n  No Fate Seal pool defined for {champ_name}.")
        return

    already_have: Set[str] = meta.fate_seal_unlocked.get(champ_name, set())

    # Increment draw counter BEFORE deciding (counts this draw for pity math)
    draws_so_far = meta.fate_seal_draws.get(champ_name, 0)
    draws_so_far += 1
    meta.fate_seal_draws[champ_name] = draws_so_far

    is_pity = (draws_so_far % FATE_SEAL_PITY_THRESHOLD == 0)

    if is_pity:
        # Guaranteed new move if any remain
        new_options = [n for n in pool if n not in already_have]
        if new_options:
            drawn_name = random.choice(new_options)
        else:
            # Entire pool collected — pity becomes a duplicate draw
            drawn_name = random.choice(pool)
    else:
        drawn_name = random.choice(pool)

    mv = MOVE_DB.get(drawn_name)
    if mv is None:
        print(f"\n  [Error] Move '{drawn_name}' not found in MOVE_DB.")
        return

    is_duplicate = drawn_name in already_have

    # ── Reveal animation ──────────────────────────────────────────
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    if is_pity:
        print("  ║  ✦ PITY DRAW — Guaranteed New Move!                    ║")
    else:
        print("  ║  ✦ FATE SEAL — The seal reveals...                     ║")
    print("  ╠══════════════════════════════════════════════════════════╣")

    bp_str   = str(mv.base_power) if mv.base_power > 0 else "—"
    cat_str  = mv.category.value
    tier_str = _TIER_STARS.get(mv.tier, "")
    crit_tag = "  [Always Critical!]" if getattr(mv, "always_crit", False) else ""

    print(f"  ║                                                          ║")
    print(f"  ║    {mv.name:<54}║")
    print(f"  ║    {mv.essence:<14} {cat_str:<12} BP: {bp_str:<8} {tier_str:<8} ║")
    if mv.description:
        desc = mv.description[:54]
        print(f"  ║    {desc:<54}║")
    if crit_tag:
        print(f"  ║    {crit_tag:<54}║")
    print(f"  ║                                                          ║")

    if is_duplicate:
        print(f"  ║  Already learned!  Refunding {FATE_SEAL_DUPE_REFUND} 𝕮...            ║")
        print(f"  ╚══════════════════════════════════════════════════════════╝")
        meta.perm_currency += FATE_SEAL_DUPE_REFUND
        print(f"\n  Duplicate — {FATE_SEAL_DUPE_REFUND} 𝕮 returned. Balance: {meta.perm_currency} 𝕮")
    else:
        print(f"  ╚══════════════════════════════════════════════════════════╝")
        if champ_name not in meta.fate_seal_unlocked:
            meta.fate_seal_unlocked[champ_name] = set()
        meta.fate_seal_unlocked[champ_name].add(drawn_name)
        # Also add to regular unlocked_moves so the Sage can equip it
        if champ_name not in meta.unlocked_moves:
            meta.unlocked_moves[champ_name] = set()
        meta.unlocked_moves[champ_name].add(drawn_name)
        print(f"\n  ✦ {mv.name} added to {champ_name}'s arsenal!")

    # Show next pity milestone
    next_pity = FATE_SEAL_PITY_THRESHOLD - (draws_so_far % FATE_SEAL_PITY_THRESHOLD)
    if next_pity == FATE_SEAL_PITY_THRESHOLD:
        next_pity = 0
    if next_pity > 0:
        print(f"  Pity: {next_pity} draw(s) until guaranteed new move.")
    else:
        print(f"  Pity counter reset.")

    save_fn()
    log.info(
        "Fate Seal draw: %s → %s  (draw#%d, pity=%s, dupe=%s)",
        champ_name, drawn_name, draws_so_far, is_pity, is_duplicate,
    )


# ═══════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════

_TIER_LABEL = {1: "Basic", 2: "Standard", 3: "Advanced", 4: "Signature"}
_TIER_STARS = {1: "★☆☆☆", 2: "★★☆☆", 3: "★★★☆", 4: "★★★★"}


def _section(text: str):
    print(f"\n  ── {text} {'─' * max(0, 50 - len(text))}")


def _show_balance(meta: "MetaState"):
    print(f"  Sanctum currency: {meta.perm_currency} 𝕮")


def _print_learn_list(
    entries: list,
    unlocked_names: set,
    fate_seal_names: set,
    meta: "MetaState",
):
    """
    Display the browse learn list with two sections:
      • Type Pool  (cross_type=False)
      • Cross-Type (cross_type=True)
    """
    type_entries  = [e for e in entries if not e["cross_type"]]
    cross_entries = [e for e in entries if e["cross_type"]]

    def _print_section(section_entries, offset=0):
        print()
        print(f"  {'#':<3} {'Move':<22} {'Type':<10} {'Cat':<9} "
              f"{'BP':>4}  {'Tier':<10}  {'Lv':>4}  {'Cost':>6}  Status")
        print("  " + "─" * 86)
        for i, entry in enumerate(section_entries, 1 + offset):
            mv       = entry["move"]
            tier     = entry["tier"]
            cost     = entry["cost"]
            lv_req   = entry["level_req"]
            unlocked = mv.name in unlocked_names or mv.name in fate_seal_names
            bp_str   = str(mv.base_power) if mv.base_power > 0 else "—"
            cat_str  = mv.category.value[:8]
            tier_str = _TIER_STARS.get(tier, "")
            crit_tag = " ⚡" if getattr(mv, "always_crit", False) else ""

            if unlocked:
                status = "✓ UNLOCKED"
            elif meta.perm_currency >= cost:
                status = f"  {cost} 𝕮  (Lv{lv_req}+)"
            else:
                status = f"  {cost} 𝕮  (Lv{lv_req}+)  ✗ insufficient"

            print(f"  [{i:<2}] {mv.name + crit_tag:<22} {mv.essence:<10} {cat_str:<9} "
                  f"{bp_str:>4}  {tier_str:<10}  {lv_req:>4}  {cost:>5}𝕮  {status}")
        print()

    # Section 1
    if type_entries:
        print()
        print("  ┌─ Type Pool ─────────────────────────────────────────────┐")
        _print_section(type_entries, offset=0)

    # Section 2
    if cross_entries:
        print("  ┌─ Cross-Type Moves ──────────────────────────────────────┐")
        _print_section(cross_entries, offset=len(type_entries))


def _show_move_detail(entry: dict, unlocked: bool):
    """Print a move's full detail before confirming unlock."""
    mv   = entry["move"]
    bp   = str(mv.base_power) if mv.base_power > 0 else "—"
    crit = "  Always Critical Hit!" if getattr(mv, "always_crit", False) else ""
    cross_tag = "  [Cross-Type Move]" if entry.get("cross_type") else ""

    print()
    print(f"  ┌─ {mv.name} ──────────────────────────────────────────────┐")
    print(f"  │  Type: {mv.essence:<12}  Category: {mv.category.value:<12}        │")
    print(f"  │  BP:   {bp:<12}  Accuracy: {int(mv.accuracy*100)}%                  │")
    print(f"  │  MP:   {mv.mp_cost:<12}  Tier: {_TIER_LABEL.get(mv.tier, '?'):<20}      │")
    if mv.description:
        desc = mv.description[:52]
        print(f"  │  {desc:<56}  │")
    if crit:
        print(f"  │  {crit:<56}  │")
    if cross_tag:
        print(f"  │  {cross_tag:<56}  │")
    print(f"  └────────────────────────────────────────────────────────────┘")
    if unlocked:
        print("  Already unlocked for this champion.")


def _show_fate_seal_info(champ_name: str, meta: "MetaState"):
    """Show Fate Seal pool size, draw count, pity status."""
    from battle_engine import FATE_SEAL_POOL
    pool       = FATE_SEAL_POOL.get(champ_name, [])
    draws      = meta.fate_seal_draws.get(champ_name, 0)
    have       = len(meta.fate_seal_unlocked.get(champ_name, set()))
    next_pity  = FATE_SEAL_PITY_THRESHOLD - (draws % FATE_SEAL_PITY_THRESHOLD)
    if next_pity == FATE_SEAL_PITY_THRESHOLD:
        next_pity = FATE_SEAL_PITY_THRESHOLD  # no draws yet

    print()
    print(f"  ╔═ FATE SEAL  ═════════════════════════════════════════════╗")
    print(f"  ║  Cost: {FATE_SEAL_COST} 𝕮 per draw                                    ║")
    print(f"  ║  Pool: {have}/{len(pool)} moves obtained                              ║")
    print(f"  ║  Pity: {next_pity} draw(s) until guaranteed new move             ║")
    print(f"  ║  Dupe: duplicate draws refund {FATE_SEAL_DUPE_REFUND} 𝕮                       ║")
    print(f"  ╚══════════════════════════════════════════════════════════╝")
    if not pool:
        print(f"  (No Fate Seal moves available for {champ_name}.)")


# ═══════════════════════════════════════════════════════════════
# RESONANCE UPGRADE UI
# ═══════════════════════════════════════════════════════════════

def _resonance_upgrade_cost(current_value: int) -> int:
    """
    Cost (in 𝕮) to raise one stat by +1 from current_value.

    Formula: max(1, current_value // 5)
      •  0 → 10   costs  1 𝕮
      • 50 → 51   costs 10 𝕮
      • 95 → 96   costs 19 𝕮
      • 99 → 100  costs 19 𝕮
    """
    return max(1, current_value // 5)


def _resonance_stars(resonance: dict) -> str:
    """5-star quality indicator for a resonance dict."""
    stats = ("vit", "sta", "mgt", "mag", "grd", "wil", "swf")
    if not resonance:
        return "☆☆☆☆☆"
    avg = sum(resonance.get(s, 0) for s in stats) / len(stats)
    thresholds = (80, 60, 40, 20, 0)
    for threshold, stars in zip(thresholds, range(5, 0, -1)):
        if avg >= threshold:
            return "★" * stars + "☆" * (5 - stars)
    return "☆☆☆☆☆"


def _print_resonance_table(resonance: dict, meta_perm_currency: int):
    """
    Print the champion's current Resonance values with upgrade costs.

      #   Stat   Value  [bar]              Next+1 Cost
      1   VIT      45   ████████░░░░░░░░░░░  9 𝕮
    """
    print()
    print(f"  {'#':<3} {'Stat':<6} {'Value':>5}  {'Resonance Bar':<25}  {'Next +1':>8}")
    print("  " + "─" * 56)
    for i, (key, label) in enumerate(_RES_STAT_LABELS, 1):
        val      = resonance.get(key, 0)
        bar_len  = 20
        filled   = round(val / _RES_MAX * bar_len)
        bar      = "█" * filled + "░" * (bar_len - filled)
        if val >= _RES_MAX:
            cost_str = "  MAXED"
        else:
            cost     = _resonance_upgrade_cost(val)
            afford   = "✓" if meta_perm_currency >= cost else "✗"
            cost_str = f"  {cost} 𝕮 {afford}"
        print(f"  [{i}] {label:<5}  {val:>3}/100  [{bar}] {cost_str}")
    print()
    print(f"  Overall: {_resonance_stars(resonance)}")
    print()


def _sanctum_resonance_loop(
    champ_name: str,
    meta: "MetaState",
    save_fn,
):
    """
    Inner loop for the Resonance upgrade screen for a single champion.

    Cost per +1 point in any stat: max(1, current_value // 5) 𝕮
    This makes early upgrades cheap and late-game upgrades expensive,
    with the final push to 100 costing 19 𝕮 per point.
    """
    while True:
        _section(f"Sanctum — {champ_name} — Resonance Upgrade")
        _show_balance(meta)

        resonance = meta.champion_resonance.get(champ_name, {})
        if not resonance:
            print(f"\n  No Resonance data for {champ_name} yet.")
            print("  Deposit a copy of this champion from a run to establish a baseline.")
            input("  [Enter to go back]")
            return

        _print_resonance_table(resonance, meta.perm_currency)

        print("  [#]  Select a stat to upgrade (+1 point)")
        print("  [0]  Back")
        print()

        raw = input("  > ").strip()
        if raw == "0" or raw == "":
            return

        try:
            choice = int(raw) - 1
            if not (0 <= choice < len(_RES_STAT_LABELS)):
                raise ValueError
        except ValueError:
            print("  Enter a stat number (1-7) or 0 to go back.")
            continue

        key, label = _RES_STAT_LABELS[choice]
        current    = resonance.get(key, 0)

        if current >= _RES_MAX:
            print(f"\n  {label} is already maxed at {_RES_MAX}!")
            input("  [Enter to continue]")
            continue

        cost = _resonance_upgrade_cost(current)
        if meta.perm_currency < cost:
            print(f"\n  Not enough 𝕮 (need {cost}, have {meta.perm_currency}).")
            input("  [Enter to continue]")
            continue

        confirm = input(f"\n  Upgrade {champ_name}'s {label} from {current} → {current+1}"
                        f"  for {cost} 𝕮?  [y/N] ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            continue

        meta.perm_currency -= cost
        resonance[key] = current + 1
        meta.champion_resonance[champ_name] = resonance
        save_fn()

        print(f"\n  ✓  {champ_name}'s {label}: {current} → {current+1}"
              f"  (remaining: {meta.perm_currency} 𝕮)")
        log.info("Resonance upgrade: %s.%s %d → %d (cost %d, remaining %d)",
                 champ_name, key, current, current + 1, cost, meta.perm_currency)


# ═══════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def run_sanctum(
    meta:          "MetaState",
    all_champions: dict,
    save_fn,           # callable() — called after each unlock to persist
):
    """
    Full Sanctum interaction loop.

    Parameters
    ----------
    meta          : MetaState  — persistent account data (modified in-place)
    all_champions : dict       — full champion roster from battle_engine
    save_fn       : callable   — zero-argument callback that saves the account
    """
    print()
    print("  ══════════════════════════════════════════════════════════════")
    print("  ✦  THE SANCTUM  —  Permanent Progression")
    print("  ══════════════════════════════════════════════════════════════")
    print("  Spend Sage Currency (𝕮) to permanently unlock moves")
    print("  or to upgrade your champions' Resonance (stat potential).")
    print()

    if not meta.unlocked_champions:
        print("  No champions unlocked yet — complete a run first.")
        return

    # ── Champion selection loop ───────────────────────────────────
    while True:
        _show_balance(meta)
        print()
        print("  Your champions:")
        champion_list = sorted(meta.unlocked_champions)
        for i, name in enumerate(champion_list, 1):
            champ = all_champions.get(name.lower())
            if champ is None:
                continue
            browse_count = len(meta.unlocked_moves.get(name, set()))
            fate_count   = len(meta.fate_seal_unlocked.get(name, set()))
            type_str     = champ.type1 + (f"/{champ.type2}" if champ.type2 else "")
            seal_str     = f"  🔮 {fate_count} sealed" if fate_count else ""
            res          = meta.champion_resonance.get(name, {})
            res_str      = f"  {_resonance_stars(res)}" if res else ""
            print(f"    [{i}] {name:<18}  {type_str:<15}  {browse_count} unlocked{seal_str}{res_str}")
        print()
        print("  [0] Leave the Sanctum")
        print()

        raw = input("  Choose a champion > ").strip()
        if raw == "0" or raw == "":
            break

        try:
            idx = int(raw) - 1
            if not (0 <= idx < len(champion_list)):
                raise ValueError
        except ValueError:
            print("  Enter a number from the list, or 0 to leave.")
            continue

        champ_name = champion_list[idx]
        champion   = all_champions.get(champ_name.lower())
        if champion is None:
            print(f"  Champion data not found for {champ_name}.")
            continue

        _sanctum_champion_loop(champ_name, champion, meta, save_fn)

    print()
    print("  The Sanctum dims.  Your unlocks are saved.")


def _sanctum_champion_loop(
    champ_name: str,
    champion,
    meta: "MetaState",
    save_fn,
):
    """Inner loop for a single champion in the Sanctum."""
    while True:
        _section(f"Sanctum — {champ_name}")
        _show_balance(meta)

        entries         = sanctum_learn_list(champion, {})
        unlocked_names  = meta.unlocked_moves.get(champ_name, set())
        fate_names      = meta.fate_seal_unlocked.get(champ_name, set())

        if not entries:
            print("  No moves available to unlock for this champion.")
            # Still show Fate Seal option even if browse list is empty
        else:
            _print_learn_list(entries, unlocked_names, fate_names, meta)

        # ── Fate Seal info block ──────────────────────────────────
        from battle_engine import FATE_SEAL_POOL
        has_fate_pool = bool(FATE_SEAL_POOL.get(champ_name))
        if has_fate_pool:
            _show_fate_seal_info(champ_name, meta)

        # ── Prompt ───────────────────────────────────────────────
        print()
        print("  [#]   Select a browse move to unlock")
        if has_fate_pool:
            print(f"  [F]   Fate Seal draw  ({FATE_SEAL_COST} 𝕮)")
        print("  [R]   Resonance — upgrade stat potential")
        print("  [0]   Back to champion list")
        print()

        raw = input("  > ").strip().lower()

        if raw == "0" or raw == "":
            return

        # ── Resonance upgrade ─────────────────────────────────────
        if raw == "r":
            _sanctum_resonance_loop(champ_name, meta, save_fn)
            continue

        # ── Fate Seal ─────────────────────────────────────────────
        if raw == "f":
            if not has_fate_pool:
                print("  No Fate Seal pool for this champion.")
                continue
            if meta.perm_currency < FATE_SEAL_COST:
                print(f"\n  Not enough 𝕮 (need {FATE_SEAL_COST}, have {meta.perm_currency}).")
                input("  [Enter to continue]")
                continue

            draws      = meta.fate_seal_draws.get(champ_name, 0)
            next_pity  = FATE_SEAL_PITY_THRESHOLD - (draws % FATE_SEAL_PITY_THRESHOLD)
            fate_have  = meta.fate_seal_unlocked.get(champ_name, set())
            pity_note  = (f"  ⚡ This draw is GUARANTEED a new move!" if next_pity == 1
                          else f"  Pity: {next_pity} draw(s) until guaranteed.")

            print(f"\n  Fate Seal draw for {champ_name}?  Cost: {FATE_SEAL_COST} 𝕮")
            print(pity_note)
            confirm = input(f"  [y/N] ").strip().lower()
            if confirm != "y":
                print("  Cancelled.")
                continue

            meta.perm_currency -= FATE_SEAL_COST
            _fate_seal_draw(champ_name, meta, save_fn)
            input("\n  [Enter to continue]")
            continue

        # ── Browse unlock ─────────────────────────────────────────
        try:
            mv_idx = int(raw) - 1
            if not entries or not (0 <= mv_idx < len(entries)):
                raise ValueError
        except ValueError:
            print("  Enter a move number, [F] for Fate Seal, or 0 to go back.")
            continue

        entry    = entries[mv_idx]
        mv       = entry["move"]
        unlocked = mv.name in unlocked_names or mv.name in fate_names

        _show_move_detail(entry, unlocked)

        if unlocked:
            input("  [Enter to continue]")
            continue

        cost = entry["cost"]
        if meta.perm_currency < cost:
            print(f"\n  Not enough 𝕮 (need {cost}, have {meta.perm_currency}).")
            input("  [Enter to continue]")
            continue

        confirm = input(f"\n  Unlock {mv.name} for {champ_name} "
                        f"for {cost} 𝕮?  [y/N] ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            continue

        meta.perm_currency -= cost
        if champ_name not in meta.unlocked_moves:
            meta.unlocked_moves[champ_name] = set()
        meta.unlocked_moves[champ_name].add(mv.name)

        save_fn()
        print(f"\n  ✓  {mv.name} unlocked for {champ_name}!")
        print(f"     Remaining: {meta.perm_currency} 𝕮")
        log.info("Sanctum unlock: %s → %s  (cost %d, remaining %d)",
                 champ_name, mv.name, cost, meta.perm_currency)
