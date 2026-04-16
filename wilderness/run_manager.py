"""
wilderness/run_manager.py
=========================
The main Wilderness Mode run loop.

Orchestrates:
  • Map traversal (normal battles → elite per stage)
  • Reward selection
  • Monster recruitment / PC deposit after elites
  • Item usage between battles
  • Run-end handling and meta-progression update

All IO is print/input for now, matching the existing battle engine's CLI.
"""

from __future__ import annotations
import random
from typing import Dict, List, Optional, TYPE_CHECKING

from .config import (
    PARTY_MAX_SIZE, STARTING_LEVEL, SHINY_CHANCE,
    MAP_BRANCH_COUNT,
)
from .models import (
    RunState, MetaState, PartyMember, NodeType,
    RewardType, Item,
)
from .items import apply_item, random_item
from .rewards import normal_battle_rewards, elite_battle_rewards, apply_reward
from .enemy_gen import (
    generate_normal_encounter, generate_elite_encounter,
    generate_recruit_candidate,
)
from .map_gen import generate_map, describe_branches
from .battle_hooks import run_wilderness_battle, get_champion_roster
from .pc_system import handle_pc_deposit, update_run_stats, pc_summary
from .scaling import party_member_scaled_stats, apply_level_up

# TYPE_CHECKING import keeps save_manager out of the runtime cycle
if TYPE_CHECKING:
    from .save_manager import AccountProfile


# ═══════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════

def _banner(text: str):
    print(f"\n{'═'*60}")
    print(f"  {text}")
    print(f"{'═'*60}")


def _section(text: str):
    print(f"\n  ── {text} {'─' * max(0, 50 - len(text))}")


def _print_party(run: RunState):
    _section("Your Party")
    for i, m in enumerate(run.party, 1):
        hp_bar_len = 20
        filled = int((m.current_hp / m.max_hp) * hp_bar_len) if m.max_hp else 0
        bar = "█" * filled + "░" * (hp_bar_len - filled)
        shiny = " ✨" if getattr(m, "is_shiny", False) else ""
        print(f"  [{i}] {m.champion_name}{shiny} Lv{m.level}  "
              f"[{bar}] {m.current_hp}/{m.max_hp} HP  "
              f"{'✗ FAINTED' if m.is_fainted else ''}")
    print(f"\n  Currency: {run.currency} 💰  |  Items: {len(run.inventory)}")


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


# ═══════════════════════════════════════════════════════════════
# ITEM USAGE
# ═══════════════════════════════════════════════════════════════

def offer_item_use(run: RunState):
    """Allow the player to use items from inventory before a battle."""
    if not run.inventory:
        return

    print("\n  You have items:")
    for i, item in enumerate(run.inventory, 1):
        print(f"  [{i}] {item}")
    print("  [0] Skip")

    while True:
        choice = input("  Use an item? > ").strip()
        if choice == "0" or choice == "":
            return

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(run.inventory):
                item = run.inventory[idx]
                # Pick target
                living = [m for m in run.party if not m.is_fainted]
                if not living:
                    print("  No living party members to use item on.")
                    return

                # For revives, show fainted too
                if item.item_type.value == "revive":
                    targets = [m for m in run.party if m.is_fainted]
                    if not targets:
                        print("  No fainted monsters to revive.")
                        continue
                else:
                    targets = living

                print("  Target:")
                for j, m in enumerate(targets, 1):
                    print(f"    [{j}] {m.summary()}")
                t_idx = _choose_int("Choose target", 1, len(targets)) - 1
                target = targets[t_idx]

                msg = apply_item(item, target)
                print(f"  {msg}")
                run.inventory.pop(idx)
                return
        except ValueError:
            pass

        print("  Invalid choice.")


# ═══════════════════════════════════════════════════════════════
# REWARD SELECTION
# ═══════════════════════════════════════════════════════════════

def offer_rewards(run: RunState, options: list):
    """Show reward options and apply the player's choice."""
    _section("Choose a Reward")
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt.label}")

    choice = _choose_int("Choose", 1, len(options))
    chosen = options[choice - 1]
    msg = apply_reward(chosen, run.party, run)
    print(msg)


# ═══════════════════════════════════════════════════════════════
# RECRUITMENT
# ═══════════════════════════════════════════════════════════════

def _make_party_member(champion, level: int, is_shiny: bool,
                        all_champions: Dict) -> PartyMember:
    """Create a fresh PartyMember at full HP/MP for the given level."""
    stats = party_member_scaled_stats(champion, level)
    return PartyMember(
        champion_name = champion.name,
        level         = level,
        current_hp    = stats["max_hp"],
        max_hp        = stats["max_hp"],
        current_mp    = stats["max_mp"],
        max_mp        = stats["max_mp"],
        is_shiny      = is_shiny,
    )


def award_exp(run: RunState, all_champions: Dict) -> None:
    """
    Award EXP (placeholder: +1 level) to all living party members after a win.

    Prints level-up messages for any member that gains a level.
    Once a proper EXP curve is designed, replace the +1 logic here —
    the stat recalculation inside apply_level_up() is already correct.
    """
    _section("EXP Gained")
    for member in run.party:
        if member.is_fainted:
            continue
        champion = all_champions.get(member.champion_name.lower())
        if champion is None:
            continue
        msg = apply_level_up(member, champion)
        print(msg)


def handle_recruitment(
    run:          RunState,
    meta:         MetaState,
    champion,
    level:        int,
    is_shiny:     bool,
    all_champions: Dict,
    save_dir:     str = ".",
):
    """
    Handle post-elite monster recruitment.

    Party < 6  → add directly
    Party = 6  → player chooses: replace a member or send to PC
    """
    shiny_tag = " ✨ SHINY" if is_shiny else ""
    type_str  = champion.type1 + (f"/{champion.type2}" if champion.type2 else "")
    _section(f"Recruitment Offer")
    print(f"  A wild {champion.name}{shiny_tag} [{type_str}] Lv{level} appeared!")

    print("\n  [1] Add to party")
    print("  [2] Send to PC (permanent unlock)")
    print("  [3] Release (skip)")

    choice = _choose_int("Choose", 1, 3)

    if choice == 3:
        print(f"  {champion.name} was released.")
        return

    new_member = _make_party_member(champion, level, is_shiny, all_champions)

    if choice == 2 or (choice == 1 and len(run.party) >= PARTY_MAX_SIZE):
        if choice == 1:
            # Party full — forced choice
            print(f"\n  Party is full ({PARTY_MAX_SIZE}/{PARTY_MAX_SIZE})!")
            print("  [1] Replace a party member")
            print("  [2] Send to PC instead")
            sub = _choose_int("Choose", 1, 2)
            if sub == 2:
                choice = 2

        if choice == 2:
            msg = handle_pc_deposit(champion.name, meta, save_dir)
            print(f"  {msg}")
            return

    # Add to party (possibly replacing someone)
    if len(run.party) < PARTY_MAX_SIZE:
        run.party.append(new_member)
        print(f"  {champion.name} joined the party! (Slot {len(run.party)}/{PARTY_MAX_SIZE})")
    else:
        # Replace
        print("\n  Choose a party member to replace:")
        for i, m in enumerate(run.party, 1):
            print(f"  [{i}] {m.summary()}")
        slot = _choose_int("Replace slot", 1, len(run.party)) - 1
        old_name = run.party[slot].champion_name
        run.party[slot] = new_member
        print(f"  {old_name} was sent to the PC.")
        msg = handle_pc_deposit(old_name, meta, save_dir)
        print(f"  {msg}")


# ═══════════════════════════════════════════════════════════════
# STAGE FLOW
# ═══════════════════════════════════════════════════════════════

def run_normal_battle(run: RunState, all_champions: Dict, verbose: bool = True) -> bool:
    """Run one normal battle. Returns True if player won."""
    realm   = run.run_map.current_node().realm
    enemies = generate_normal_encounter(all_champions, realm, run.highest_level)

    names   = ", ".join(
        f"{e.name} Lv{e.level}" if e.level is not None else e.name
        for e in enemies
    )
    _banner(f"Stage {run.stage}  ⚔  Wild Encounter")
    print(f"  Realm: {realm}")
    print(f"  Enemy: {names}")

    # Item use before battle
    offer_item_use(run)

    result = run_wilderness_battle(run.party, enemies, all_champions, verbose)
    run.apply_battle_result(result)

    if result.player_won:
        print("\n  ✦ Victory!")
        award_exp(run, all_champions)
        rewards = normal_battle_rewards(run.party)
        offer_rewards(run, rewards)
    else:
        print("\n  ✦ Defeat...")

    return result.player_won


def run_elite_battle(
    run:           RunState,
    meta:          MetaState,
    all_champions: Dict,
    verbose:       bool = True,
    save_dir:      str = ".",
) -> bool:
    """Run the elite battle, handle rewards and recruitment. Returns True if won."""
    realm   = run.run_map.current_node().realm
    enemies = generate_elite_encounter(all_champions, realm, run.highest_level, run.stage)

    names   = ", ".join(
        f"{e.name} Lv{e.level}" if e.level is not None else e.name
        for e in enemies
    )
    _banner(f"Stage {run.stage}  💀  Elite Encounter  ({len(enemies)} enemies)")
    print(f"  Realm: {realm}")
    print(f"  Enemies: {names}")

    offer_item_use(run)

    result = run_wilderness_battle(run.party, enemies, all_champions, verbose)
    run.apply_battle_result(result)

    if result.player_won:
        print("\n  ✦ Elite defeated!")
        award_exp(run, all_champions)

        # Elite loot
        elite_opts = elite_battle_rewards(run.party)
        offer_rewards(run, elite_opts)

        # Recruitment
        champ, level, is_shiny = generate_recruit_candidate(
            all_champions, realm, run.highest_level, SHINY_CHANCE
        )
        handle_recruitment(run, meta, champ, level, is_shiny, all_champions, save_dir)
        run.stages_won += 1
    else:
        print("\n  ✦ Defeat...")

    return result.player_won


# ═══════════════════════════════════════════════════════════════
# MAP TRAVERSAL
# ═══════════════════════════════════════════════════════════════

def advance_map(run: RunState) -> bool:
    """
    Move to the next node on the map.
    If multiple children (branch point), prompt the player.
    Returns True if there are more nodes to visit.
    """
    choices = run.run_map.next_choices()

    if not choices:
        return False  # Run complete (reached final stage)

    if len(choices) == 1:
        run.run_map.current = choices[0].node_id
        return True

    # Branch point — show options
    _section("Choose Your Path")
    lines = describe_branches(run.run_map)
    for line in lines:
        print(line)

    idx = _choose_int("Choose path", 1, len(choices))
    chosen = choices[idx - 1]
    run.run_map.current = chosen.node_id
    run.stage = chosen.stage
    return True


# ═══════════════════════════════════════════════════════════════
# MAIN RUN LOOP
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# AUTOSAVE
# ═══════════════════════════════════════════════════════════════

def _autosave(run: RunState, account: "AccountProfile | None", save_dir: str):
    """
    Embed the current RunState in the account and persist to disk.
    No-op when account is None (dev mode or legacy callers).
    """
    if account is None:
        return
    # Lazy import to avoid a circular dependency at module load time
    from .save_manager import save_account
    account.active_run = run
    save_account(account, save_dir)


def run_wilderness(
    starting_champion_name: Optional[str],
    meta:      MetaState,
    save_dir:  str  = ".",
    verbose:   bool = True,
    account:   "AccountProfile | None" = None,
    dev_mode:  bool = False,
) -> RunState:
    """
    Execute a full Wilderness Mode run from start to finish.

    Parameters
    ----------
    starting_champion_name
        Name of the starter champion.  Ignored if account.active_run is set
        (i.e. when resuming a saved run).
    meta
        Permanent MetaState (unlocks, PC bonuses, stats).
    save_dir
        Directory for save files.  Autosave is skipped when account=None.
    verbose
        Pass False to suppress battle output (useful in tests).
    account
        AccountProfile for autosave.  If account.active_run is set, the run
        is resumed from that saved state instead of starting fresh.
        Pass None to skip all saves (dev mode / legacy callers).
    dev_mode
        If True, prints a dev-mode banner and skips post-run save cleanup.

    Returns
    -------
    The final RunState for inspection / summary display.
    """
    all_champions = get_champion_roster()

    # ── Resume or start fresh ─────────────────────────────────────
    if account is not None and account.active_run is not None:
        run = account.active_run
        _banner("WILDERNESS MODE — Resuming Run")
        node = run.run_map.current_node() if run.run_map else None
        print(f"  Resumed at stage {run.stage}"
              + (f"  ({node.node_type.value.title()} node)" if node else ""))
        _print_party(run)
    else:
        if not starting_champion_name:
            raise ValueError("starting_champion_name required when starting a new run.")
        start_champ = all_champions.get(starting_champion_name.lower())
        if not start_champ:
            raise ValueError(f"Starting champion '{starting_champion_name}' not found.")

        starter = _make_party_member(start_champ, STARTING_LEVEL, False, all_champions)
        run     = RunState(party=[starter], currency=0, stage=1)
        run.run_map = generate_map()

        _banner("WILDERNESS MODE — Run Start")
        if dev_mode:
            print("  ⚠  DEV MODE — progress will not be saved")
        print(f"  Starting champion: {starter.champion_name} Lv{starter.level}")
        print(f"  Realm: {run.run_map.current_node().realm}")

        # Persist the fresh run immediately so a crash before the first
        # battle doesn't silently drop the run.
        _autosave(run, account, save_dir)

    # ── Main loop ─────────────────────────────────────────────────
    while True:
        if run.is_defeated():
            run.run_over = True
            break

        node = run.run_map.current_node()

        if node.node_type == NodeType.BATTLE:
            won = run_normal_battle(run, all_champions, verbose)
            # Autosave: battle result (HP, faint state) written back to run
            _autosave(run, account, save_dir)

            if not won and run.is_defeated():
                run.run_over = True
                break
            has_next = advance_map(run)
            # Autosave: map position updated
            _autosave(run, account, save_dir)
            if not has_next:
                break

        elif node.node_type == NodeType.ELITE:
            won = run_elite_battle(run, meta, all_champions, verbose, save_dir)
            _autosave(run, account, save_dir)

            if not won and run.is_defeated():
                run.run_over = True
                break
            has_next = advance_map(run)
            _autosave(run, account, save_dir)
            if not has_next:
                _banner("🏆 You've conquered all stages!")
                break

        elif node.node_type == NodeType.SHOP:
            # Placeholder — not yet implemented
            print("\n  [SHOP not yet implemented — skipping]")
            advance_map(run)
            _autosave(run, account, save_dir)

        elif node.node_type == NodeType.EVENT:
            # Placeholder — not yet implemented
            print("\n  [EVENT not yet implemented — skipping]")
            advance_map(run)
            _autosave(run, account, save_dir)

        _print_party(run)

    # ── Run end ───────────────────────────────────────────────────
    _banner("Run Over")
    if run.run_over:
        print(f"  Your party was defeated on stage {run.stage}.")
    else:
        print(f"  Run complete! Stages cleared: {run.stages_won}")

    print(f"  Final currency: {run.currency} 💰")
    _print_party(run)

    # ── Meta update & save cleanup ────────────────────────────────
    update_run_stats(meta, run.stages_won, save_dir)
    print(pc_summary(meta))

    if account is not None and not dev_mode:
        # Sync meta into account and clear the finished run
        account.meta = meta
        from .save_manager import clear_active_run
        clear_active_run(account, save_dir)

    return run
