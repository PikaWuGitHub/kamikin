"""
wilderness/rewards.py
=====================
Post-battle reward generation and application.

After every normal battle the player chooses from 3 reward options:
  • Heal      — restore HP to party
  • Currency  — gain gold
  • Item      — receive a random item (low probability)

After elite battles a richer reward is always granted.

This module only generates and describes options. Actual application
(writing to RunState) is done by run_manager so the two stay decoupled.
"""

from __future__ import annotations
import random
from typing import List

from .config import (
    HEAL_AMOUNT_FRACTION,
    CURRENCY_NORMAL_MIN, CURRENCY_NORMAL_MAX,
    CURRENCY_ELITE_MIN,  CURRENCY_ELITE_MAX,
    ITEM_DROP_CHANCE_NORMAL, ITEM_DROP_CHANCE_ELITE,
)
from .models import RewardOption, RewardType, PartyMember
from .items import random_item


# ── Option generators ────────────────────────────────────────────

def _heal_option(party: List[PartyMember]) -> RewardOption:
    """Heal the most-wounded living member by HEAL_AMOUNT_FRACTION of their max HP."""
    living    = [m for m in party if not m.is_fainted]
    if not living:
        target    = party[0]
    else:
        target    = min(living, key=lambda m: m.hp_pct)

    amount = max(1, int(target.max_hp * HEAL_AMOUNT_FRACTION))
    actual = min(amount, target.max_hp - target.current_hp)
    return RewardOption(
        reward_type = RewardType.HEAL,
        label       = f"Heal — restore {actual} HP to {target.champion_name} "
                      f"({target.current_hp}/{target.max_hp} → "
                      f"{min(target.max_hp, target.current_hp + actual)}/{target.max_hp})",
        heal_amount = amount,
    )


def _currency_option(min_: int, max_: int) -> RewardOption:
    amount = random.randint(min_, max_)
    return RewardOption(
        reward_type = RewardType.CURRENCY,
        label       = f"Currency — gain {amount} 💰",
        currency    = amount,
    )


def _item_option(allow_rare: bool = False) -> RewardOption:
    item = random_item(allow_rare=allow_rare)
    return RewardOption(
        reward_type = RewardType.ITEM,
        label       = f"Item — receive [{item.name}]: {item.description}",
        item        = item,
    )


# ── Public interfaces ────────────────────────────────────────────

def normal_battle_rewards(party: List[PartyMember]) -> List[RewardOption]:
    """
    Generate the three post-normal-battle reward choices.
    Always includes Heal + Currency.
    Item slot appears based on ITEM_DROP_CHANCE_NORMAL.
    """
    options: List[RewardOption] = [
        _heal_option(party),
        _currency_option(CURRENCY_NORMAL_MIN, CURRENCY_NORMAL_MAX),
    ]

    # Third option: item or a second currency roll
    if random.random() < ITEM_DROP_CHANCE_NORMAL:
        options.append(_item_option(allow_rare=False))
    else:
        options.append(_currency_option(CURRENCY_NORMAL_MIN, CURRENCY_NORMAL_MAX))

    random.shuffle(options)
    return options


def elite_battle_rewards(party: List[PartyMember]) -> List[RewardOption]:
    """
    Post-elite rewards — richer currency range, higher item chance.
    Always yields exactly one reward (not a choice — elites are generous).
    Returns a single-item list for interface consistency.
    """
    if random.random() < ITEM_DROP_CHANCE_ELITE:
        return [_item_option(allow_rare=True)]
    return [_currency_option(CURRENCY_ELITE_MIN, CURRENCY_ELITE_MAX)]


def apply_reward(option: RewardOption, party: List[PartyMember], run_state) -> str:
    """
    Apply a chosen reward to run_state and party.

    Returns a message string for display.
    run_state is typed as Any to avoid circular imports;
    it must have .currency and .inventory attributes.
    """
    if option.reward_type == RewardType.HEAL:
        living = [m for m in party if not m.is_fainted]
        if not living:
            return "No living party members to heal."
        target = min(living, key=lambda m: m.hp_pct)
        before = target.current_hp
        target.heal(option.heal_amount)
        return f"  {target.champion_name} healed for {target.current_hp - before} HP."

    elif option.reward_type == RewardType.CURRENCY:
        run_state.currency += option.currency
        return f"  Gained {option.currency} 💰. Total: {run_state.currency} 💰."

    elif option.reward_type == RewardType.ITEM:
        if option.item:
            run_state.inventory.append(option.item)
            return f"  Added [{option.item.name}] to inventory."
        return "  Item reward had no item (bug)."

    return "  Unknown reward type."
