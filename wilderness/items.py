"""
wilderness/items.py
===================
Item definitions and application logic.

Items are self-contained: each knows how to apply itself to a PartyMember.
The run_manager calls apply_item() and the item handles the side-effects.

Item tiers
----------
HEAL_LOW    — restores 25% HP  (cheap, common drop)
HEAL_MED    — restores 50% HP
HEAL_HIGH   — restores 75% HP
HEAL_MAX    — restores 100% HP + cleanses status
HEAL_STATUS — cleanses all status effects only
MP_RESTORE  — restores 100% MP
REVIVE      — revives a fainted monster at 50% HP
RARE_EQUIP  — placeholder for the equipment system (not yet implemented)
"""

from __future__ import annotations
import random
from typing import List, TYPE_CHECKING

from .models import Item, ItemType, PartyMember

if TYPE_CHECKING:
    from .models import RunState


# ── Item catalogue ───────────────────────────────────────────────

def _make_heal_low() -> Item:
    return Item(
        item_type   = ItemType.HEAL_LOW,
        name        = "Essence Shard",
        description = "Restores 25% HP to one monster.",
    )

def _make_heal_med() -> Item:
    return Item(
        item_type   = ItemType.HEAL_MED,
        name        = "Spirit Herb",
        description = "Restores 50% HP to one monster.",
    )

def _make_heal_high() -> Item:
    return Item(
        item_type   = ItemType.HEAL_HIGH,
        name        = "Vital Elixir",
        description = "Restores 75% HP to one monster.",
    )

def _make_heal_max() -> Item:
    return Item(
        item_type   = ItemType.HEAL_MAX,
        name        = "Spirit Water",
        description = "Fully restores HP and cleanses all status conditions.",
    )

def _make_heal_status() -> Item:
    return Item(
        item_type   = ItemType.HEAL_STATUS,
        name        = "Purifying Dust",
        description = "Cleanses all status conditions from one monster.",
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
    ItemType.HEAL_LOW:    _make_heal_low,
    ItemType.HEAL_MED:    _make_heal_med,
    ItemType.HEAL_HIGH:   _make_heal_high,
    ItemType.HEAL_MAX:    _make_heal_max,
    ItemType.HEAL_STATUS: _make_heal_status,
    ItemType.MP_RESTORE:  _make_mp_restore,
    ItemType.REVIVE:      _make_revive,
    ItemType.RARE_EQUIP:  _make_rare_equip,
}

# Drop weights for random reward generation (normal/elite post-battle drops)
ITEM_DROP_WEIGHTS = {
    ItemType.HEAL_LOW:    40,
    ItemType.HEAL_MED:    25,
    ItemType.HEAL_HIGH:   15,
    ItemType.HEAL_STATUS: 12,
    ItemType.MP_RESTORE:   8,
}
# HEAL_MAX and REVIVE intentionally excluded from random drops — too powerful


def random_item(allow_rare: bool = False) -> Item:
    """Generate a random item from the standard drop pool."""
    pool    = dict(ITEM_DROP_WEIGHTS)
    if allow_rare:
        pool[ItemType.RARE_EQUIP] = 5
    types   = list(pool.keys())
    weights = [pool[t] for t in types]
    chosen  = random.choices(types, weights=weights, k=1)[0]
    return ITEM_FACTORIES[chosen]()


# ── Application logic ────────────────────────────────────────────

def apply_item(item: Item, target: PartyMember) -> str:
    """
    Apply an item to a PartyMember. Returns a human-readable result message.
    Callers are responsible for choosing an appropriate target.
    """
    if item.item_type == ItemType.HEAL_LOW:
        amount = max(1, int(target.max_hp * 0.25))
        before = target.current_hp
        target.heal(amount)
        gained = target.current_hp - before
        return f"{target.champion_name} recovered {gained} HP."

    elif item.item_type == ItemType.HEAL_MED:
        amount = max(1, int(target.max_hp * 0.50))
        before = target.current_hp
        target.heal(amount)
        gained = target.current_hp - before
        return f"{target.champion_name} recovered {gained} HP."

    elif item.item_type == ItemType.HEAL_HIGH:
        amount = max(1, int(target.max_hp * 0.75))
        before = target.current_hp
        target.heal(amount)
        gained = target.current_hp - before
        return f"{target.champion_name} recovered {gained} HP."

    elif item.item_type == ItemType.HEAL_MAX:
        target.current_hp = target.max_hp
        target.is_fainted = False
        return f"{target.champion_name} was fully healed and all status cleared."

    elif item.item_type == ItemType.HEAL_STATUS:
        # Status is reset between battles on PartyMembers, but this is useful
        # in-battle (via battle_hooks) or for any future persistent status system.
        return f"{target.champion_name}'s status conditions were cleansed."

    elif item.item_type == ItemType.MP_RESTORE:
        target.current_mp = target.max_mp
        return f"{target.champion_name}'s MP was fully restored."

    elif item.item_type == ItemType.REVIVE:
        if not target.is_fainted:
            return f"{target.champion_name} isn't fainted — revive wasted!"
        target.revive(0.50)
        return f"{target.champion_name} was revived with {target.current_hp} HP!"

    elif item.item_type == ItemType.RARE_EQUIP:
        return f"[{item.name}] equipped to {target.champion_name} (passive system pending)."

    # Legacy fallback for old save-file item types
    elif item.item_type in (ItemType.HEAL_SMALL, ItemType.HEAL_FULL):
        amount = target.max_hp if item.item_type == ItemType.HEAL_FULL else int(target.max_hp * 0.40)
        before = target.current_hp
        target.heal(amount)
        gained = target.current_hp - before
        return f"{target.champion_name} recovered {gained} HP."

    return f"Applied {item.name} to {target.champion_name}."
