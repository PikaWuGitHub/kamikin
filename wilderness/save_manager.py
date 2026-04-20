"""
wilderness/save_manager.py
==========================
Persistent account / meta-progression system for Wilderness Mode.

AccountProfile
--------------
Wraps MetaState (permanent across all runs) plus an optional active
RunState (the run currently in progress).  Saved as a single
`account.json` file in the project directory.

Design decisions
----------------
• Single file, atomic replace (write .tmp → os.replace) → no partial corruption
• Full round-trip JSON serialisation for every model type
• INITIAL_UNLOCKED_CHAMPIONS defines the starting pool for new accounts
• load_account  → returns None if no file exists (brand-new player)
• create_account → builds a fresh AccountProfile with initial unlocks
• clear_active_run → nulls the embedded run after it ends cleanly
"""

from __future__ import annotations
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

from .models import (
    MetaState, RunState, RunMap, MapNode,
    Realm, PartyMember, Item, ItemType, NodeType,
)
from .config import INITIAL_UNLOCKED_CHAMPIONS


ACCOUNT_FILENAME = "account.json"


# ═══════════════════════════════════════════════════════════════
# AccountProfile dataclass
# ═══════════════════════════════════════════════════════════════

@dataclass
class AccountProfile:
    """
    Top-level save object.

    meta        — permanent progression (unlocks, PC bonuses, lifetime stats)
    active_run  — embedded RunState if a run is in progress, else None
    account_id  — player identifier (reserved for future multi-profile support)
    created_at  — ISO date string of account creation
    """
    meta:        MetaState
    active_run:  Optional[RunState] = None
    account_id:  str = "player"
    created_at:  str = field(default_factory=lambda: date.today().isoformat())


# ═══════════════════════════════════════════════════════════════
# Serialisation helpers — model instances → plain dicts
# ═══════════════════════════════════════════════════════════════

def _meta_to_dict(meta: MetaState) -> dict:
    return {
        "unlocked_champions":  sorted(meta.unlocked_champions),
        "pc_bonuses":          meta.pc_bonuses,
        "total_runs":          meta.total_runs,
        "best_stage":          meta.best_stage,
        "perm_currency":       meta.perm_currency,
        "unlocked_moves":      {k: sorted(v) for k, v in meta.unlocked_moves.items()},
        "fate_seal_draws":     dict(meta.fate_seal_draws),
        "fate_seal_unlocked":  {k: sorted(v) for k, v in meta.fate_seal_unlocked.items()},
        "champion_resonance":  {k: dict(v) for k, v in meta.champion_resonance.items()},
    }


def _realm_to_dict(realm: Realm) -> dict:
    return {
        "name":      realm.name,
        "primary":   realm.primary,
        "secondary": realm.secondary,
    }


def _node_to_dict(node: MapNode) -> dict:
    return {
        "node_id":   node.node_id,
        "node_type": node.node_type.value,
        "stage":     node.stage,
        "realm":     _realm_to_dict(node.realm),
        "children":  node.children,
    }


def _run_map_to_dict(run_map: RunMap) -> dict:
    return {
        "current": run_map.current,
        "stage":   run_map.stage,
        "nodes":   {str(k): _node_to_dict(v) for k, v in run_map.nodes.items()},
    }


def _party_member_to_dict(m: PartyMember) -> dict:
    return {
        "champion_name": m.champion_name,
        "level":         m.level,
        "current_hp":    m.current_hp,
        "max_hp":        m.max_hp,
        "current_mp":    m.current_mp,
        "max_mp":        m.max_mp,
        "is_fainted":    m.is_fainted,
        "is_shiny":      m.is_shiny,
        "held_item":     m.held_item,
        "custom_moves":  m.custom_moves,
        "resonance":     dict(m.resonance) if m.resonance else {},
    }


def _item_to_dict(item: Item) -> dict:
    return {
        "item_type":   item.item_type.value,
        "name":        item.name,
        "description": item.description,
    }


def _run_state_to_dict(run: RunState) -> dict:
    return {
        "stage":               run.stage,
        "stages_won":          run.stages_won,
        "currency":            run.currency,
        "run_over":            run.run_over,
        "perm_currency_earned": run.perm_currency_earned,
        "party":               [_party_member_to_dict(m) for m in run.party],
        "inventory":           [_item_to_dict(i) for i in run.inventory],
        "run_map":             _run_map_to_dict(run.run_map) if run.run_map else None,
    }


def _account_to_dict(profile: AccountProfile) -> dict:
    return {
        "account_id": profile.account_id,
        "created_at": profile.created_at,
        "meta":       _meta_to_dict(profile.meta),
        "active_run": _run_state_to_dict(profile.active_run) if profile.active_run else None,
    }


# ═══════════════════════════════════════════════════════════════
# Deserialisation helpers — plain dicts → model instances
# ═══════════════════════════════════════════════════════════════

def _meta_from_dict(d: dict) -> MetaState:
    raw_moves        = d.get("unlocked_moves", {})
    raw_fs_unlocked  = d.get("fate_seal_unlocked", {})
    raw_champ_res    = d.get("champion_resonance", {})
    return MetaState(
        unlocked_champions  = set(d.get("unlocked_champions", [])),
        pc_bonuses          = d.get("pc_bonuses", {}),
        total_runs          = d.get("total_runs", 0),
        best_stage          = d.get("best_stage", 0),
        perm_currency       = d.get("perm_currency", 0),
        unlocked_moves      = {k: set(v) for k, v in raw_moves.items()},
        fate_seal_draws     = dict(d.get("fate_seal_draws", {})),
        fate_seal_unlocked  = {k: set(v) for k, v in raw_fs_unlocked.items()},
        champion_resonance  = {k: dict(v) for k, v in raw_champ_res.items()},
    )


def _realm_from_dict(d: dict) -> Realm:
    return Realm(
        name      = d["name"],
        primary   = d["primary"],
        secondary = d.get("secondary"),
    )


def _node_from_dict(d: dict) -> MapNode:
    return MapNode(
        node_id   = d["node_id"],
        node_type = NodeType(d["node_type"]),
        stage     = d["stage"],
        realm     = _realm_from_dict(d["realm"]),
        children  = d.get("children", []),
    )


def _run_map_from_dict(d: dict) -> RunMap:
    nodes = {int(k): _node_from_dict(v) for k, v in d["nodes"].items()}
    return RunMap(
        nodes   = nodes,
        current = d["current"],
        stage   = d.get("stage", 1),
    )


def _party_member_from_dict(d: dict) -> PartyMember:
    return PartyMember(
        champion_name = d["champion_name"],
        level         = d["level"],
        current_hp    = d["current_hp"],
        max_hp        = d["max_hp"],
        current_mp    = d["current_mp"],
        max_mp        = d["max_mp"],
        is_fainted    = d.get("is_fainted", False),
        is_shiny      = d.get("is_shiny", False),
        held_item     = d.get("held_item"),
        custom_moves  = d.get("custom_moves", []),
        resonance     = d.get("resonance", {}),
    )


def _item_from_dict(d: dict) -> Item:
    try:
        itype = ItemType(d["item_type"])
    except ValueError:
        # Unknown item type from an old save — treat as a small heal item
        itype = ItemType.HEAL_LOW
    return Item(
        item_type   = itype,
        name        = d["name"],
        description = d["description"],
    )


def _run_state_from_dict(d: dict) -> RunState:
    run = RunState(
        party                = [_party_member_from_dict(m) for m in d.get("party", [])],
        inventory            = [_item_from_dict(i) for i in d.get("inventory", [])],
        currency             = d.get("currency", 0),
        stage                = d.get("stage", 1),
        run_over             = d.get("run_over", False),
        stages_won           = d.get("stages_won", 0),
        perm_currency_earned = d.get("perm_currency_earned", 0),
    )
    if d.get("run_map"):
        run.run_map = _run_map_from_dict(d["run_map"])
    return run


def _account_from_dict(d: dict) -> AccountProfile:
    return AccountProfile(
        account_id = d.get("account_id", "player"),
        created_at = d.get("created_at", date.today().isoformat()),
        meta       = _meta_from_dict(d.get("meta", {})),
        active_run = _run_state_from_dict(d["active_run"]) if d.get("active_run") else None,
    )


# ═══════════════════════════════════════════════════════════════
# File I/O
# ═══════════════════════════════════════════════════════════════

def _account_path(save_dir: str) -> str:
    return os.path.join(save_dir, ACCOUNT_FILENAME)


def save_account(profile: AccountProfile, save_dir: str = ".") -> str:
    """
    Persist the AccountProfile as safely as possible.

    Strategy
    --------
    1. Serialise to JSON in memory.
    2. Write to a .tmp file alongside the real file.
    3. Attempt os.replace() — atomic on POSIX, best-effort on Windows.
       On Windows, OneDrive / antivirus scanners can briefly lock the target
       file, causing a PermissionError.  We retry up to _REPLACE_RETRIES
       times with a short sleep between attempts.
    4. If all replace attempts fail (e.g. file persistently locked), fall back
       to writing directly to the target path.  This is not atomic but ensures
       the save is never silently lost.
    5. The .tmp file is always cleaned up, even on failure.

    Returns the final save path.
    """
    import time

    _REPLACE_RETRIES  = 5
    _REPLACE_DELAY_S  = 0.15   # seconds between retry attempts

    path    = _account_path(save_dir)
    tmp     = path + ".tmp"

    try:
        payload = json.dumps(_account_to_dict(profile), indent=2, ensure_ascii=False)

        # ── Step 1: write to temp file ────────────────────────────
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)

        # ── Step 2: atomic replace with retry for Windows locks ───
        replaced = False
        last_err: Exception | None = None
        for attempt in range(1, _REPLACE_RETRIES + 1):
            try:
                os.replace(tmp, path)
                replaced = True
                break
            except PermissionError as exc:
                last_err = exc
                log.warning(
                    "save_account: os.replace() denied (attempt %d/%d) — "
                    "file may be locked by OneDrive or antivirus. Retrying in %.0fms…",
                    attempt, _REPLACE_RETRIES, _REPLACE_DELAY_S * 1000,
                )
                time.sleep(_REPLACE_DELAY_S)

        # ── Step 3: fallback direct write if replace never succeeded ─
        if not replaced:
            log.warning(
                "save_account: os.replace() failed after %d attempts (%s). "
                "Falling back to direct write — save may not be atomic.",
                _REPLACE_RETRIES, last_err,
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload)
            # Clean up the orphaned .tmp
            try:
                os.remove(tmp)
            except OSError:
                pass

        log.debug("Account saved: %s (active_run=%s, total_runs=%d)",
                  path, profile.active_run is not None, profile.meta.total_runs)

    except Exception:
        log.exception("Failed to save account to %s", path)
        raise

    return path


def load_account(save_dir: str = ".") -> Optional[AccountProfile]:
    """
    Load the AccountProfile from disk.

    Returns None if no save file exists (new player) or if the file is corrupt.
    Callers should use create_account() when this returns None.
    """
    path = _account_path(save_dir)
    if not os.path.exists(path):
        log.debug("No account file at %s — new player", path)
        return None
    try:
        with open(path, encoding="utf-8") as f:
            profile = _account_from_dict(json.load(f))
        log.debug("Account loaded: %s (runs=%d best_stage=%d active_run=%s)",
                  path, profile.meta.total_runs, profile.meta.best_stage,
                  profile.active_run is not None)
        return profile
    except (json.JSONDecodeError, KeyError, ValueError):
        log.exception("Corrupt account save at %s — returning None", path)
        return None


def create_account(save_dir: str = ".") -> AccountProfile:
    """
    Build a brand-new AccountProfile with no initial unlocks.

    The first champion is chosen during the starter ceremony in wilderness_mode.py
    and added to meta.unlocked_champions before the first run begins.

    Does NOT write to disk — call save_account() afterward if desired.
    """
    meta = MetaState(unlocked_champions=set())
    profile = AccountProfile(meta=meta)
    log.info("New account created (no initial unlocks — awaiting starter ceremony)")
    return profile


def clear_active_run(profile: AccountProfile, save_dir: str = ".") -> None:
    """
    Null out the embedded run and persist.
    Call this after a run ends (victory, defeat, or abandon).
    """
    profile.active_run = None
    save_account(profile, save_dir)
