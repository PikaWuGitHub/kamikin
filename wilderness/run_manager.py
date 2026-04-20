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
import logging
import random
from typing import Dict, List, Optional, TYPE_CHECKING

log = logging.getLogger(__name__)

from .config import (
    PARTY_MAX_SIZE, STARTING_LEVEL, SHINY_CHANCE,
    MAP_BRANCH_COUNT, PERM_CURRENCY_PER_MONSTER,
    SHOP_HEAL_LOW_COST, SHOP_HEAL_MED_COST, SHOP_HEAL_HIGH_COST,
    SHOP_HEAL_MAX_COST, SHOP_HEAL_STATUS_COST, SHOP_MP_RESTORE_COST,
    SHOP_REVIVE_COST,
)
from .models import (
    RunState, MetaState, PartyMember, NodeType,
    RewardType, Item, ItemType,
)
from .items import apply_item, random_item, ITEM_FACTORIES
from .move_tutor import run_move_tutor
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

def _use_item_menu(run: RunState):
    """Let the player use an item from their inventory (out-of-battle)."""
    if not run.inventory:
        print("  Your bag is empty.")
        return

    print("\n  Your items:")
    for i, item in enumerate(run.inventory, 1):
        print(f"  [{i}] {item}")
    print("  [0] Cancel")

    while True:
        choice = input("  Use item > ").strip()
        if choice == "0" or choice == "":
            return

        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(run.inventory)):
                raise ValueError
        except ValueError:
            print("  Invalid choice.")
            continue

        item = run.inventory[idx]
        living = [m for m in run.party if not m.is_fainted]

        if item.item_type == ItemType.REVIVE:
            targets = [m for m in run.party if m.is_fainted]
            if not targets:
                print("  No fainted monsters to revive.")
                continue
        else:
            targets = living
            if not targets:
                print("  No living party members.")
                return

        if len(targets) == 1:
            target = targets[0]
        else:
            print("  Target:")
            for j, m in enumerate(targets, 1):
                print(f"    [{j}] {m.summary()}")
            t_idx = _choose_int("Choose target", 1, len(targets)) - 1
            target = targets[t_idx]

        msg = apply_item(item, target)
        print(f"  {msg}")
        run.inventory.pop(idx)
        return


# ═══════════════════════════════════════════════════════════════
# SHOP
# ═══════════════════════════════════════════════════════════════

# Price list for the between-battle shop (in-run currency)
_SHOP_ITEMS: list = [
    (ItemType.HEAL_LOW,    SHOP_HEAL_LOW_COST),
    (ItemType.HEAL_MED,    SHOP_HEAL_MED_COST),
    (ItemType.HEAL_HIGH,   SHOP_HEAL_HIGH_COST),
    (ItemType.HEAL_MAX,    SHOP_HEAL_MAX_COST),
    (ItemType.HEAL_STATUS, SHOP_HEAL_STATUS_COST),
    (ItemType.MP_RESTORE,  SHOP_MP_RESTORE_COST),
    (ItemType.REVIVE,      SHOP_REVIVE_COST),
]


def _run_shop(run: RunState):
    """Simple item shop — spend currency to stock up between battles."""
    _section("Shop")
    while True:
        print(f"  Currency: {run.currency} 💰")
        print()
        for i, (itype, price) in enumerate(_SHOP_ITEMS, 1):
            item   = ITEM_FACTORIES[itype]()
            afford = "✓" if run.currency >= price else "✗"
            print(f"  [{i}] {item.name}  —  {price} 💰  {afford}")
            print(f"       {item.description}")
        print("  [0] Leave shop")
        print()

        choice = input("  Buy > ").strip()
        if choice == "0" or choice == "":
            return
        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(_SHOP_ITEMS)):
                raise ValueError
        except ValueError:
            print("  Invalid choice.")
            continue

        itype, price = _SHOP_ITEMS[idx]
        if run.currency < price:
            print(f"  Not enough currency (need {price}, have {run.currency}).")
            continue

        item = ITEM_FACTORIES[itype]()
        run.inventory.append(item)
        run.currency -= price
        print(f"  Bought {item.name}! ({run.currency} 💰 remaining)")


# ═══════════════════════════════════════════════════════════════
# SWITCH LEAD
# ═══════════════════════════════════════════════════════════════

def _switch_lead(run: RunState):
    """Reorder the party — move a living bench member to the front slot."""
    living = [(i, m) for i, m in enumerate(run.party) if not m.is_fainted]
    if len(living) <= 1:
        print("  Only one living party member — nothing to switch.")
        return

    lead = run.party[0]
    print(f"\n  Current lead: {lead.champion_name} Lv{lead.level} "
          f"({lead.current_hp}/{lead.max_hp} HP)")
    print("  Switch to:")
    bench_living = [(i, m) for i, m in living if i != 0]
    for j, (_, m) in enumerate(bench_living, 1):
        print(f"  [{j}] {m.champion_name} Lv{m.level}  "
              f"({m.current_hp}/{m.max_hp} HP)")
    print("  [0] Cancel")

    while True:
        raw = input("  Switch > ").strip()
        if raw == "0" or raw == "":
            return
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(bench_living):
                slot, _ = bench_living[idx]
                # Swap with party[0]
                run.party[0], run.party[slot] = run.party[slot], run.party[0]
                new_lead = run.party[0]
                print(f"  {new_lead.champion_name} is now the lead!")
                return
        except ValueError:
            pass
        print("  Invalid choice.")


# ═══════════════════════════════════════════════════════════════
# BETWEEN-BATTLE MENU
# ═══════════════════════════════════════════════════════════════

def between_battle_menu(run: RunState, meta: MetaState = None, all_champions: Dict = None):
    """
    Optional actions between battles: shop, use item, switch lead, Move Tutor.
    Loops until the player chooses to continue.
    """
    has_living_bench = sum(1 for m in run.party if not m.is_fainted) > 1
    has_tutor = meta is not None and all_champions is not None

    while True:
        _section("Between Battles")
        _print_party(run)
        print()
        print("  [1] Continue to next battle")
        print("  [2] Enter shop  💰")
        if run.inventory:
            print("  [3] Use an item")
        if has_living_bench:
            print("  [4] Switch lead champion")
        if has_tutor:
            print(f"  [5] Visit the Wandering Sage (Move Tutor)  [{meta.perm_currency} 𝕮]")
        print()

        choice = input("  > ").strip()
        if choice == "1" or choice == "":
            return
        elif choice == "2":
            _run_shop(run)
        elif choice == "3" and run.inventory:
            _use_item_menu(run)
        elif choice == "4" and has_living_bench:
            _switch_lead(run)
        elif choice == "5" and has_tutor:
            run_move_tutor(run, meta, all_champions)
        else:
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
        log.info("Level-up: %s → Lv%d (max HP %d)", member.champion_name, member.level, member.max_hp)
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

    Party has space  → offer Add or Release
    Party is full    → offer Replace, Send to PC, or Release

    Adding to the party also permanently unlocks the champion in meta
    so they're available as a starter in future runs.
    """
    shiny_tag = " ✨ SHINY" if is_shiny else ""
    type_str  = champion.type1 + (f"/{champion.type2}" if champion.type2 else "")
    _section("Recruitment Offer")
    print(f"  A wild {champion.name}{shiny_tag} [{type_str}] Lv{level} appeared!")

    party_full = len(run.party) >= PARTY_MAX_SIZE

    if not party_full:
        # Party has room — simple add or release
        print("\n  [1] Add to party  (also unlocks for future runs)")
        print("  [2] Release (skip)")
        choice = _choose_int("Choose", 1, 2)
        if choice == 2:
            print(f"  {champion.name} was released.")
            return
        # Add to party and unlock
        new_member = _make_party_member(champion, level, is_shiny, all_champions)
        run.party.append(new_member)
        meta.unlocked_champions.add(champion.name)
        print(f"  {champion.name} joined the party! (Slot {len(run.party)}/{PARTY_MAX_SIZE})")
        print(f"  {champion.name} has been permanently unlocked!")
    else:
        # Party is full — replace, send to PC, or release
        print(f"\n  Party is full ({PARTY_MAX_SIZE}/{PARTY_MAX_SIZE})!")
        print("  [1] Replace a party member  (replaced mon goes to PC)")
        print("  [2] Send to PC  (permanent unlock, skip party slot)")
        print("  [3] Release (skip)")
        choice = _choose_int("Choose", 1, 3)

        if choice == 3:
            print(f"  {champion.name} was released.")
            return

        if choice == 2:
            msg = handle_pc_deposit(champion.name, meta, save_dir)
            print(f"  {msg}")
            return

        # Replace a party member
        new_member = _make_party_member(champion, level, is_shiny, all_champions)
        print("\n  Choose a party member to replace:")
        for i, m in enumerate(run.party, 1):
            print(f"  [{i}] {m.summary()}")
        slot = _choose_int("Replace slot", 1, len(run.party)) - 1
        old_name = run.party[slot].champion_name
        run.party[slot] = new_member
        # Replaced mon goes to PC; new mon also gets unlocked
        msg = handle_pc_deposit(old_name, meta, save_dir)
        print(f"  {old_name} was sent to the PC.  {msg}")
        meta.unlocked_champions.add(champion.name)
        print(f"  {champion.name} joined the party! (Slot {slot + 1}/{PARTY_MAX_SIZE})")
        print(f"  {champion.name} has been permanently unlocked!")


# ═══════════════════════════════════════════════════════════════
# STAGE FLOW
# ═══════════════════════════════════════════════════════════════

def run_normal_battle(
    run: RunState,
    meta: MetaState,
    all_champions: Dict,
    verbose: bool = True,
) -> bool:
    """Run one normal battle. Returns True if player won."""
    realm   = run.run_map.current_node().realm
    enemies = generate_normal_encounter(all_champions, realm, run.highest_level)

    names   = ", ".join(
        f"{e.name} Lv{e.level}" if e.level is not None else e.name
        for e in enemies
    )
    log.info("Normal battle: stage=%d realm=%s enemy=%s", run.stage, realm.name, names)
    _banner(f"Stage {run.stage}  ⚔  Wild Encounter")
    print(f"  Realm: {realm}")
    print(f"  Enemy: {names}")

    try:
        result = run_wilderness_battle(run.party, enemies, all_champions, verbose,
                                       inventory=run.inventory)
    except Exception:
        log.exception("Exception during normal battle — treating as defeat")
        return False

    run.apply_battle_result(result)

    if result.player_won:
        # Perm currency: 1 monster × stage_number
        coins = PERM_CURRENCY_PER_MONSTER * run.stage
        run.perm_currency_earned += coins
        log.info("Normal battle won in %d turns | perm_coins +%d | party HP: %s",
                 result.turns_taken, coins,
                 [f"{h}/{m.max_hp}" for h, m in zip(result.party_hp_after, run.party)])
        print("\n  ✦ Victory!")
        award_exp(run, all_champions)
        rewards = normal_battle_rewards(run.party)
        offer_rewards(run, rewards)
        between_battle_menu(run, meta, all_champions)
    else:
        log.info("Normal battle lost | party HP: %s",
                 [f"{h}/{m.max_hp}" for h, m in zip(result.party_hp_after, run.party)])
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
    log.info("Elite battle: stage=%d realm=%s enemies=[%s]", run.stage, realm.name, names)
    _banner(f"Stage {run.stage}  💀  Elite Encounter  ({len(enemies)} enemies)")
    print(f"  Realm: {realm}")
    print(f"  Enemies: {names}")

    try:
        result = run_wilderness_battle(run.party, enemies, all_champions, verbose,
                                       inventory=run.inventory)
    except Exception:
        log.exception("Exception during elite battle — treating as defeat")
        return False

    run.apply_battle_result(result)

    if result.player_won:
        # Perm currency: len(enemies) monsters × stage_number
        coins = PERM_CURRENCY_PER_MONSTER * run.stage * len(enemies)
        run.perm_currency_earned += coins
        log.info("Elite battle won in %d turns | perm_coins +%d | party HP: %s",
                 result.turns_taken, coins,
                 [f"{h}/{m.max_hp}" for h, m in zip(result.party_hp_after, run.party)])
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

        between_battle_menu(run, meta, all_champions)
    else:
        log.info("Elite battle lost | party HP: %s",
                 [f"{h}/{m.max_hp}" for h, m in zip(result.party_hp_after, run.party)])
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
    try:
        save_account(account, save_dir)
        log.debug("Autosave: stage=%d node=%d hp=%s",
                  run.stage,
                  run.run_map.current if run.run_map else -1,
                  [f"{m.current_hp}/{m.max_hp}" for m in run.party])
    except Exception:
        log.exception("Autosave failed — game state NOT persisted")


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
        node = run.run_map.current_node() if run.run_map else None
        log.info("Run resumed: stage=%d node=%s party=[%s]",
                 run.stage,
                 node.node_type.value if node else "?",
                 ", ".join(f"{m.champion_name} Lv{m.level}" for m in run.party))
        _banner("WILDERNESS MODE — Resuming Run")
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

        log.info("Run started: starter=%s Lv%d dev_mode=%s",
                 starter.champion_name, starter.level, dev_mode)
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
        log.debug("Map node: stage=%d type=%s realm=%s id=%d",
                  node.stage, node.node_type.value, node.realm.name, node.node_id)

        if node.node_type == NodeType.BATTLE:
            won = run_normal_battle(run, meta, all_champions, verbose)
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
    log.info("Run ended: stages_won=%d defeated=%s currency=%d party=[%s]",
             run.stages_won, run.run_over, run.currency,
             ", ".join(f"{m.champion_name} Lv{m.level} {m.current_hp}/{m.max_hp}HP"
                       for m in run.party))
    _banner("Run Over")
    if run.run_over:
        print(f"  Your party was defeated on stage {run.stage}.")
    else:
        print(f"  Run complete! Stages cleared: {run.stages_won}")

    print(f"  Final currency: {run.currency} 💰")
    _print_party(run)

    # ── Meta update & save cleanup ────────────────────────────────
    # Award permanent currency earned this run
    if run.perm_currency_earned > 0:
        meta.perm_currency += run.perm_currency_earned
        print(f"\n  ✦ You earned {run.perm_currency_earned} 𝕮 for the Wandering Sage!")
        print(f"    Total: {meta.perm_currency} 𝕮")
        log.info("Perm currency awarded: +%d → total %d",
                 run.perm_currency_earned, meta.perm_currency)

    update_run_stats(meta, run.stages_won, save_dir)
    print(pc_summary(meta))

    if account is not None and not dev_mode:
        # Sync meta into account and clear the finished run
        account.meta = meta
        from .save_manager import clear_active_run
        clear_active_run(account, save_dir)

    return run
