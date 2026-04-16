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
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

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
        "unlocked_champions": sorted(meta.unlocked_champions),
        "pc_bonuses":         meta.pc_bonuses,
        "total_runs":         meta.total_runs,
        "best_stage":         meta.best_stage,
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
    }


def _item_to_dict(item: Item) -> dict:
    return {
        "item_type":   item.item_type.value,
        "name":        item.name,
        "description": item.description,
    }


def _run_state_to_dict(run: RunState) -> dict:
    return {
        "stage":      run.stage,
        "stages_won": run.stages_won,
        "currency":   run.currency,
        "run_over":   run.run_over,
        "party":      [_party_member_to_dict(m) for m in run.party],
        "inventory":  [_item_to_dict(i) for i in run.inventory],
        "run_map":    _run_map_to_dict(run.run_map) if run.run_map else None,
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
    return MetaState(
        unlocked_champions = set(d.get("unlocked_champions", [])),
        pc_bonuses         = d.get("pc_bonuses", {}),
        total_runs         = d.get("total_runs", 0),
        best_stage         = d.get("best_stage", 0),
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
    )


def _item_from_dict(d: dict) -> Item:
    return Item(
        item_type   = ItemType(d["item_type"]),
        name        = d["name"],
        description = d["description"],
    )


def _run_state_from_dict(d: dict) -> RunState:
    run = RunState(
        party      = [_party_member_from_dict(m) for m in d.get("party", [])],
        inventory  = [_item_from_dict(i) for i in d.get("inventory", [])],
        currency   = d.get("currency", 0),
        stage      = d.get("stage", 1),
        run_over   = d.get("run_over", False),
        stages_won = d.get("stages_won", 0),
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
    Atomically persist the AccountProfile.

    Writes to a .tmp file first, then renames to the real path.
    This prevents a corrupt save if the process is killed mid-write.

    Returns the final save path.
    """
    path    = _account_path(save_dir)
    tmp     = path + ".tmp"
    payload = json.dumps(_account_to_dict(profile), indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, path)   # atomic on POSIX; best-effort on Windows
    return path


def load_account(save_dir: str = ".") -> Optional[AccountProfile]:
    """
    Load the AccountProfile from disk.

    Returns None if no save file exists (new player) or if the file is corrupt.
    Callers should use create_account() when this returns None.
    """
    path = _account_path(save_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return _account_from_dict(json.load(f))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None   # corrupt save — caller decides what to do


def create_account(save_dir: str = ".") -> AccountProfile:
    """
    Build a brand-new AccountProfile with initial starter unlocks.

    Does NOT write to disk — call save_account() afterward if desired.
    """
    meta = MetaState(unlocked_champions=set(INITIAL_UNLOCKED_CHAMPIONS))
    return AccountProfile(meta=meta)


def clear_active_run(profile: AccountProfile, save_dir: str = ".") -> None:
    """
    Null out the embedded run and persist.
    Call this after a run ends (victory, defeat, or abandon).
    """
    profile.active_run = None
    save_account(profile, save_dir)
