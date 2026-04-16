"""
wilderness/items.py
===================
Item definitions and application logic.

Items are self-contained: each knows how to apply itself to a PartyMember.
The run_manager calls apply_item() and the item handles the side-effects.
"""

from __future__ import annotations
import random
from typing import List, Callable, TYPE_CHECKING

from .models import Item, ItemType, PartyMember

if TYPE_CHECKING:
    from .models import RunState


# ── Item catalogue ───────────────────────────────────────────────

def _make_heal_small() -> Item:
    return Item(
        item_type   = ItemType.HEAL_SMALL,
        name        = "Essence Shard",
        description = "Restores 40% HP to one monster.",
    )

def _make_heal_full() -> Item:
    return Item(
        item_type   = ItemType.HEAL_FULL,
        name        = "Spirit Water",
        description = "Fully restores HP and cleanses status of one monster.",
    )

def _make_mp_restore() -> Item:
    return Item(
        item_type   = ItemType.MP_RESTORE,
        name        = "Stamina Crystal",
        description = "Fully restores MP of one monster.",
    )

def _make_revive() -> Item:
    return Item(
        item_type   = ItemType.REVIVE,
        name        = "Revival Spark",
        description = "Revives a fainted monster with 50% HP.",
    )

def _make_rare_equip() -> Item:
    return Item(
        item_type   = ItemType.RARE_EQUIP,
        name        = "Mythic Relic",
        description = "[Equipment — passive bonus system coming soon]",
    )


# Catalogue map: ItemType → factory function
ITEM_FACTORIES: dict = {
    ItemType.HEAL_SMALL: _make_heal_small,
    ItemType.HEAL_FULL:  _make_heal_full,
    ItemType.MP_RESTORE: _make_mp_restore,
    ItemType.REVIVE:     _make_revive,
    ItemType.RARE_EQUIP: _make_rare_equip,
}

# Drop weights for random item generation (excluding RARE_EQUIP which has its own path)
ITEM_DROP_WEIGHTS = {
    ItemType.HEAL_SMALL: 50,
    ItemType.HEAL_FULL:  20,
    ItemType.MP_RESTORE: 20,
    ItemType.REVIVE:     10,
}


def random_item(allow_rare: bool = False) -> Item:
    """Generate a random item, optionally including rare equipment."""
    pool = dict(ITEM_DROP_WEIGHTS)
    if allow_rare:
        pool[ItemType.RARE_EQUIP] = 5
    types  = list(pool.keys())
    weights = [pool[t] for t in types]
    chosen = random.choices(types, weights=weights, k=1)[0]
    return ITEM_FACTORIES[chosen]()


# ── Application logic ────────────────────────────────────────────

def apply_item(item: Item, target: PartyMember) -> str:
    """
    Apply an item to a PartyMember. Returns a human-readable result message.
    Callers are responsible for choosing an appropriate target.
    """
    if item.item_type == ItemType.HEAL_SMALL:
        amount = int(target.max_hp * 0.40)
        before = target.current_hp
        target.heal(amount)
        gained = target.current_hp - before
        return f"{target.champion_name} recovered {gained} HP."

    elif item.item_type == ItemType.HEAL_FULL:
        target.current_hp = target.max_hp
        target.is_fainted = False
        return f"{target.champion_name} was fully healed and all status cleared."

    elif item.item_type == ItemType.MP_RESTORE:
        target.current_mp = target.max_mp
        return f"{target.champion_name}'s MP was fully restored."

    elif item.item_type == ItemType.REVIVE:
        if not target.is_fainted:
            return f"{target.champion_name} isn't fainted — revive wasted!"
        target.revive(0.50)
        return f"{target.champion_name} was revived with {target.current_hp} HP!"

    elif item.item_type == ItemType.RARE_EQUIP:
        # Placeholder — equipment system not yet implemented
        return f"[{item.name}] equipped to {target.champion_name} (passive system pending)."

    return f"Applied {item.name} to {target.champion_name}."
