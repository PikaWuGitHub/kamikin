"""
wilderness/pc_system.py
=======================
The PC: permanent champion unlock and IV bonus system.

The PC is NOT storage. It represents:
  • Which champions can be selected when starting future runs
  • Accumulated IV bonuses from depositing duplicates

Persistence
-----------
MetaState is serialised to JSON via save_meta / load_meta.
The path defaults to the project root but is configurable.
"""

from __future__ import annotations
import json
import os
from typing import Optional

from .config import META_SAVE_FILENAME
from .models import MetaState


# ── Serialisation ────────────────────────────────────────────────

def _meta_path(directory: str = ".") -> str:
    return os.path.join(directory, META_SAVE_FILENAME)


def save_meta(meta: MetaState, directory: str = "."):
    """Persist MetaState to JSON."""
    data = {
        "unlocked_champions": sorted(meta.unlocked_champions),
        "pc_bonuses":         meta.pc_bonuses,
        "total_runs":         meta.total_runs,
        "best_stage":         meta.best_stage,
    }
    path = _meta_path(directory)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def load_meta(directory: str = ".") -> MetaState:
    """Load MetaState from JSON, or return a fresh one if none exists."""
    path = _meta_path(directory)
    if not os.path.exists(path):
        return MetaState()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return MetaState(
            unlocked_champions = set(data.get("unlocked_champions", [])),
            pc_bonuses         = data.get("pc_bonuses", {}),
            total_runs         = data.get("total_runs", 0),
            best_stage         = data.get("best_stage", 0),
        )
    except (json.JSONDecodeError, KeyError):
        # Corrupt save — start fresh
        return MetaState()


# ── PC interaction helpers ───────────────────────────────────────

def _resonance_stars(resonance: dict) -> str:
    """Compact 5-star indicator."""
    stats = ("vit", "sta", "mgt", "mag", "grd", "wil", "swf")
    if not resonance:
        return ""
    avg = sum(resonance.get(s, 0) for s in stats) / len(stats)
    thresholds = (80, 60, 40, 20, 0)
    for threshold, stars in zip(thresholds, range(5, 0, -1)):
        if avg >= threshold:
            return "★" * stars + "☆" * (5 - stars)
    return "☆☆☆☆☆"


def pc_summary(meta: MetaState) -> str:
    """Return a formatted string showing the current PC state."""
    lines = ["\n  ── PC (Permanent Unlocks) ────────────────────────"]
    if not meta.unlocked_champions:
        lines.append("  (empty — deposit champions after elite battles to unlock them)")
    else:
        for name in sorted(meta.unlocked_champions):
            bonus       = meta.pc_bonuses.get(name, 0)
            bonus_str   = f"  [×{bonus} dupes]" if bonus > 0 else ""
            tutor_count = len(meta.unlocked_moves.get(name, set()))
            tutor_str   = f"  [{tutor_count} moves]" if tutor_count else ""
            res         = meta.champion_resonance.get(name, {})
            res_str     = f"  {_resonance_stars(res)}" if res else "  (no resonance)"
            lines.append(f"  • {name:<18}{res_str}{bonus_str}{tutor_str}")
    lines.append(
        f"\n  Total runs: {meta.total_runs}  |  Best stage: {meta.best_stage}"
        f"  |  Sanctum currency: {meta.perm_currency} 𝕮"
    )
    return "\n".join(lines)


def handle_pc_deposit(
    champion_name: str,
    meta: MetaState,
    save_dir: str = ".",
) -> str:
    """
    Deposit a champion to the PC and immediately persist the change.
    Returns the result message from MetaState.deposit_to_pc.
    """
    msg = meta.deposit_to_pc(champion_name)
    save_meta(meta, save_dir)
    return msg


def update_run_stats(meta: MetaState, stages_cleared: int, save_dir: str = "."):
    """Called at run end to update lifetime stats."""
    meta.total_runs += 1
    if stages_cleared > meta.best_stage:
        meta.best_stage = stages_cleared
    save_meta(meta, save_dir)
