#!/usr/bin/env python3
"""
wilderness_mode.py
==================
Entry point for Kamikin Wilderness Mode.

Usage
-----
    python wilderness_mode.py            # normal play (load/create account)
    python wilderness_mode.py --pc       # view PC / unlock progress
    python wilderness_mode.py --reset    # wipe account save (full reset)
    python wilderness_mode.py --dev      # dev/test mode: pick any champion, no saves
"""

import sys
import os
import random

# Ensure project root is on the path so `wilderness` and `battle_engine` resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wilderness.save_manager import (
    AccountProfile, load_account, save_account,
    create_account, clear_active_run, ACCOUNT_FILENAME,
)
from wilderness.pc_system import update_run_stats, pc_summary
from wilderness.run_manager import run_wilderness, _banner, _section
from wilderness.battle_hooks import get_champion_roster
from wilderness.config import STARTING_LEVEL


SAVE_DIR = os.path.dirname(os.path.abspath(__file__))


# ═══════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════

def _print_roster(all_champions: dict):
    print("\n  Available Champions:")
    print("  " + "─" * 100)
    print(f"  {'#':<4} {'Name':<16} {'Type':<16} {'Role':<22}")
    print("  " + "─" * 100)
    names = sorted(all_champions.keys())
    for i, key in enumerate(names, 1):
        c        = all_champions[key]
        type_str = c.type1 + (f"/{c.type2}" if c.type2 else "")
        print(f"  {i:<4} {c.name:<16} {type_str:<16} {c.role[:20]:<22}")


def _pick_starter_unlocked(all_champions: dict, profile: AccountProfile) -> str:
    """
    Let the player choose from their unlocked starters.
    Called during normal (non-dev) new-run setup.
    """
    unlocked = profile.meta.unlocked_champions
    ul_list  = sorted(unlocked)

    print("\n  Unlocked starters:")
    print("  " + "─" * 60)
    for i, name in enumerate(ul_list, 1):
        c        = all_champions.get(name.lower())
        type_str = (c.type1 + (f"/{c.type2}" if c.type2 else "")) if c else "?"
        bonus    = profile.meta.pc_bonuses.get(name, 0)
        bonus_s  = f"  [IV ×{bonus}]" if bonus else ""
        print(f"  [{i}] {name} [{type_str}]{bonus_s}")
    print("  [r] Random")

    while True:
        raw = input("  Choose starter (# or name, r for random): ").strip().lower()

        if raw == "r":
            name = random.choice(ul_list)
            print(f"  Randomly selected: {name}")
            return name

        # By number
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(ul_list):
                return ul_list[idx]
        except ValueError:
            pass

        # By name (case-insensitive, unlocked only)
        for name in ul_list:
            if raw == name.lower():
                return name

        print("  Not found — enter a number, a name, or 'r' for random.")


def _pick_starter_dev(all_champions: dict) -> str:
    """Dev mode: pick any champion from the full roster."""
    _print_roster(all_champions)
    names = sorted(all_champions.keys())
    while True:
        raw = input("  Choose starter (name or #): ").strip().lower()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(names):
                return all_champions[names[idx]].name
        except ValueError:
            pass
        if raw in all_champions:
            return all_champions[raw].name
        print("  Not found — try again.")


def _confirm(prompt: str, default_yes: bool = True) -> bool:
    hint = "[Y/n]" if default_yes else "[y/N]"
    raw  = input(f"  {prompt} {hint}: ").strip().lower()
    if raw == "":
        return default_yes
    return raw.startswith("y")


# ═══════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════

def cmd_pc():
    """Print the PC / unlock summary."""
    profile = load_account(SAVE_DIR)
    if profile is None:
        print("  No account found — start a run first.")
        return
    print(pc_summary(profile.meta))


def cmd_reset():
    """Wipe the account save file (full reset)."""
    path = os.path.join(SAVE_DIR, ACCOUNT_FILENAME)
    if os.path.exists(path):
        if _confirm("This will permanently delete your account and all progress. Continue?",
                    default_yes=False):
            os.remove(path)
            print("  Account wiped.")
        else:
            print("  Reset cancelled.")
    else:
        print("  No save file found.")


def cmd_run(dev_mode: bool = False):
    """
    Normal run entry point.

    Flow
    ----
    1. Load account (create fresh if none exists)
    2. If account has an active run → offer to continue or abandon
    3. If no active run → pick a starter and start fresh
    4. Call run_wilderness (account=None in dev mode to skip saves)
    """
    all_champions = get_champion_roster()

    # ── Load or create account ────────────────────────────────────
    profile = None if dev_mode else load_account(SAVE_DIR)

    if profile is None and not dev_mode:
        profile = create_account(SAVE_DIR)
        save_account(profile, SAVE_DIR)
        _banner("KAMIKIN — WILDERNESS MODE")
        print("  Welcome! Your account has been created.")
        print(f"  Starting unlock: {', '.join(sorted(profile.meta.unlocked_champions))}")
    elif not dev_mode:
        _banner("KAMIKIN — WILDERNESS MODE")
        print(f"  Total runs: {profile.meta.total_runs}  |  "
              f"Best stage: {profile.meta.best_stage}")
        print(f"  Unlocked champions: {len(profile.meta.unlocked_champions)}  |  "
              f"Currency saved: {_total_currency(profile)}")
    else:
        _banner("KAMIKIN — WILDERNESS MODE  [DEV MODE]")
        print("  ⚠  Dev mode active — no saves, no restrictions")

    # ── Continue or start fresh ───────────────────────────────────
    starting_name: str | None = None

    if not dev_mode and profile.active_run is not None:
        run      = profile.active_run
        node     = run.run_map.current_node() if run.run_map else None
        party_summary = ", ".join(
            f"{m.champion_name} Lv{m.level} ({m.current_hp}/{m.max_hp} HP)"
            for m in run.party if not m.is_fainted
        )
        print(f"\n  ⚡ You have an active run in progress:")
        print(f"     Stage {run.stage} — {node.node_type.value.title() if node else '?'} node")
        print(f"     Party: {party_summary}")
        print(f"     Currency: {run.currency} 💰")

        if _confirm("Continue this run?", default_yes=True):
            # resume — run_wilderness will detect account.active_run
            starting_name = None
        else:
            if _confirm("Abandon this run and start fresh?", default_yes=False):
                clear_active_run(profile, SAVE_DIR)
                print("  Run abandoned.")
                starting_name = _pick_starter_unlocked(all_champions, profile)
            else:
                print("  See you next time!")
                return
    else:
        # No active run — pick a starter
        if dev_mode:
            starting_name = _pick_starter_dev(all_champions)
        else:
            starting_name = _pick_starter_unlocked(all_champions, profile)

    print(f"\n  HP persists between battles. No automatic healing.")
    print("  Reach the end or die trying.\n")
    input("  Press Enter to begin...")

    run_wilderness(
        starting_champion_name = starting_name,
        meta      = profile.meta if not dev_mode else _blank_meta(),
        save_dir  = SAVE_DIR,
        verbose   = True,
        account   = profile if not dev_mode else None,
        dev_mode  = dev_mode,
    )

    input("\n  Press Enter to exit...")


# ═══════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═══════════════════════════════════════════════════════════════

def _total_currency(profile: AccountProfile) -> int:
    """Currency carried in the active run (0 if none)."""
    if profile.active_run:
        return profile.active_run.currency
    return 0


def _blank_meta():
    """A MetaState that never gets persisted — used in dev mode."""
    from wilderness.models import MetaState
    return MetaState()


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]

    if "--pc" in args:
        cmd_pc()
    elif "--reset" in args:
        cmd_reset()
    elif "--dev" in args:
        cmd_run(dev_mode=True)
    else:
        cmd_run(dev_mode=False)


if __name__ == "__main__":
    main()
