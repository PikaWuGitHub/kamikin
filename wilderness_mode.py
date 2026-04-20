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
import logging
import random
import time

# Ensure project root is on the path so `wilderness` and `battle_engine` resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logger import setup_logging

log = logging.getLogger(__name__)

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


# One-sentence lore descriptions for the starter ceremony card.
# Drawn from the Art & Visual Direction monster mockup documents.
_CHAMPION_LORE: dict[str, str] = {
    "Axerra":    "A pangolin-like volt wanderer that recalibrates rhythm rather than rushing, making opponents mistime their actions by rewriting the battlefield's tempo.",
    "Brunhoka":  "A bear-goat colossus whose stonewheel hooves tune to seismic pulses, becoming the living pulse of disruption when the earth stops resisting change.",
    "Caelira":   "A swan-peacock spirit of Halohaven whose rune-etched throat glows when malice nears, turning suffering into balm with a single luminous chord.",
    "Crynith":   "A hyena specter where laws unravel in its wake — glitching through time frames as a laughed curse, a living error no fate can correct.",
    "Elarin":    "A celestial deer spirit of Halohaven, healing simply by existing as the sacred guardian of prayer, warmth, and quiet protection.",
    "Eldrune":   "An ancient dragon-lizard relic carrying the living text of forgotten myths, growing stronger as the runes of past battles awaken from within.",
    "Elyuri":    "A gliding wind spirit of Skyrend's jetstream veilways, folding seconds like silk so allies act lighter and faster with hesitation lifted from their bodies.",
    "Eurgeist":  "A spectral eel warden of the drowned born where sacred tidal currents meet mourning depths, preserving the memories of the fallen in glowing tide-glyphs.",
    "Fernace":   "A fierce porcupine spirit of the Firethorn Wilds born from a storm fused with ancient sap, sowing wildfire and thorns to restore brutal harmony.",
    "Finyu":     "A meditative aquatic sage of Tidewake, cleansing ailments and shifting the battle's rhythm with the quiet mastery of flowing water and focused thought.",
    "Frisela":   "A foxlike celestial of Glacien's high sanctuaries who descends only during ancient rites, blessing allies with frost-rings of moonlight prayers and holy snowfall.",
    "Friselle":  "A frost flamingo of Glacien's frostlight canopies that paints the seasons' end in silence, sending spirals of refracted frostlight at foes with each twirl.",
    "Galeva":    "A mythic wind guardian whose windmill tail resets the battlefield to sacred neutrality, embodying the last breeze before clarity.",
    "Galivor":   "A myth-anima goat shaped by belief itself, whose presence alters the rules of battle and grows more vivid with each generation that dares speak its name.",
    "Glacyn":    "A silent tundra wolf watching from beneath the cold like a frozen sentinel — a being that waits until the wind finally carries its signal.",
    "Gravanel":  "A tragic stag-spirit caught between forest and rot, channeling the grove's grief as a corrupted guardian of once-sacred land.",
    "Gravyrn":   "A stoic griffon clad in granite-feathered armor that solidifies in storms, the wall that does not fall no matter the gale.",
    "Hexel":     "A self-assembling spiderbot of the Silicon Wastes built from broken algorithms and cursed encryption — a living error that confounds logic and deflects control.",
    "Ignovar":   "A molten boar of Emberveil's ashlands that collects heat from every defeat, building relentless momentum as an engine of smoldering, uninterrupted purpose.",
    "Kitzen":    "A fox spirit from Emberveil's volcanic plains carrying a dormant ancestral spark — playful and comforting, blazing brilliantly when truly awakened.",
    "Kyntra":    "A cyber-beetle sentinel of Netraxis that absorbs rogue data and traces threats in silence, an ancient firewall sentry of digital order.",
    "Lumira":    "A fractured angel-statue born of both blessing and grief, absorbing curses and suffering into itself as a healer that pays the price in silence.",
    "Lychbloom": "A gravegrown spirit-warden that blossoms only where grief has taken root, absorbing corruption beneath layers of sacred overgrowth as a ritual in bloom.",
    "Mimari":    "A shape-shifting spirit attuned to every region of the world, teaching that true strength lies in observation and understanding the rhythms of others.",
    "Miravi":    "A moon-dancer spirit hare of Umbradeep gliding between layers of wind where time flows in spirals, mirroring the magic of others with weightless precision.",
    "Mirellon":  "A cosmic jellyfish drifting between meteor trails above Zenithreach, where time bends and stars seem to listen wherever it quietly appears.",
    "Mokoro":    "An ancient tortoise spirit of Verdanta's oldest groves, nurturing life with deep-rooted patience as the timeless protector of nature's quiet resilience.",
    "Mourin":    "A doll-like spirit keeper hovering in quiet sorrow, guarding unspoken grief and laying curses not out of malice, but out of remembrance.",
    "Mylaren":   "A sacred deer-dragon hybrid of Eldralore's ancient groves walking with the wisdom of ages, born from the first bloom of the world tree itself.",
    "Myrabyte":  "A haunted archive construct built from broken mainframes, preserving the moves and voices of fallen Kiboru as memory given digital form.",
    "Narviu":    "A glacial narwhal guardian of the arctic deep, drifting with serene melancholy and spreading auras of draining cold with each pulse of its spiral horn.",
    "Neorift":   "A riftborn techno-beetle that blinks across the battlefield in shimmer and static, born where the stars glitch and time skips at the edge of two worlds.",
    "Noema":     "A contemplative dream moth that drifts between thoughts with psychic grace, keeper of forgotten visions and the silent truths that hide within stillness.",
    "Noxtar":    "A skeletal crustacean holding a dying galaxy in its shell, burning brighter the more it gives of itself in blasts of cursed stellar ruin.",
    "Nyoroa":    "A sacred water serpent of Tidewake embodying the fluid wisdom of sacred currents, revered as a spiritual guide drifting between worlds on the tide.",
    "Orrikai":   "A mole-like neutral wanderer who shifts unseen ley-lines beneath sacred crossroads, transforming the battlefield into something no one was prepared for.",
    "Otanei":    "A tranquil otter spirit of Tidewake, conjuring mist barriers and gentle waves as the eternal lighthouse guardian of those adrift at sea.",
    "Pandana":   "A sacred panda of Halohaven's canopy sanctuaries that cannot be summoned, only witnessed — arriving in silence to wrap the most fragile in protective light.",
    "Pyrrin":    "A ceremonial jackal guardian of Emberveil's sacred flame, a torchbearer of tradition who remembers every battle and lights the path for those who follow.",
    "Quenara":   "A platypus-like spirit conductor that finds the hum of a fallen ally's final heartbeat in the stillest pools of Umbradeep — and adds one more.",
    "Rokhara":   "A sabertooth colossus forged from the bones of extinct titans, awakening buried earth-runes with each ritual strike as a living monument to myth and stone.",
    "Rootmaw":   "A Flora guardian that roots deep into the earth mid-battle, becoming a living fortress that turns the soil beneath it into an unbreakable anchor.",
    "Scaithe":   "A cursed infernal hound whose floating chains carry runes of vengeance, scorching the earth with consequence as the spirit of justice left to burn.",
    "Skaiya":    "A divine swan spirit of Skyrend's sacred skyscape, carrying wind-carried prayers on silk banners that pulse with blessings for those nearby.",
    "Skirra":    "A gazelle-like wind spirit of Skyrend that chooses whether or not to be seen, threading narrow currents and rejecting momentum itself rather than outrunning it.",
    "Solaire":   "A sacred aerial phoenix born from the last breath of a volcano and the first gust of a storm, ascending in a radiant spiral that scorches patterns into the sky.",
    "Soltren":   "A phoenix-lizard born where Emberveil's calderas meet Stormspire's thunder-cliffs, igniting further with each victory as acceleration made sacred.",
    "Sombrae":   "A hovering veil-being of shifting dream glyphs, existing between steps as a mind-bending divine construct that tests your will to perceive.",
    "Somrel":    "A cursed wanderer of Malmortis born not of wrath but resignation, walking the battlefield to remind — every curse it casts is a quiet eulogy.",
    "Sonari":    "A metallic snail of Netraxis built to archive pattern-breaches, reflecting enemies back at themselves just slightly out of phase as silence weaponized.",
    "Sorin":     "A proud highland raptor of Skyrend bound to open skies, revered as a wind-born herald of duels and protector of highland honor.",
    "Synkra":    "A psychic monkey born when lightning struck a glass mind-tree in meditation, feeling thought before it happens and always one step ahead of inevitability.",
    "Thalassa":  "A vast Cosmic entity that creates fields where echoes of past moves replay, a burst-force anomaly born from the edge of forgotten constellations.",
    "Thryxa":    "A volt spider of Stormspire's spirefields that spins invisible static threads tuned to momentum — it waits for you to move before rerouting your intent.",
    "Torusk":    "An ancient badger shrine guardian of Gravemarch's canyons, an immovable bastion of stone and silent endurance patiently watching over the sacred earth.",
    "Trevolt":   "A swift jungle panther born from a storm fused with ancient canopy sap, leaving behind seed-sparks that bloom into chain-reactive jolts seconds later.",
    "Turtaura":  "A sanctuary-turtle designed not for war but restoration, resetting the battlefield with radiant circuits of harmony and peace for all who stand within.",
    "Vollox":    "A stoic ox with volt-charged horns and stone plating, standing at the living threshold between sky and stone — immovable and explosive.",
    "Zerine":    "A snow lynx hunter whose frost-etched fur crackles with aurora-like static, combining glacial grace with lethal electric strikes as the storm's silent edge.",
    "Zintrel":   "A lightning-fast electric courier of Stormspire's signal towers, encoding sacred messages in static and volt runes across the thunderous heights.",
}


def _starter_ceremony(all_champions: dict) -> str:
    """
    Dramatically reveal 3 random champions and let the player choose one
    as their permanent first unlock.  Returns the chosen champion's name.
    """
    _banner("KAMIKIN — WILDERNESS MODE")
    print("  A new journey begins...")
    print()
    input("  Press Enter to discover your first champion...")

    # Pick 3 distinct champions at random from the full roster
    keys     = list(all_champions.keys())
    selected = random.sample(keys, min(3, len(keys)))
    picks    = [all_champions[k] for k in selected]

    # ── Anticipation build-up ──────────────────────────────────────
    print()
    print("  ═" * 30)
    print()
    _pause = 0.06   # seconds between characters in the typewriter effect

    def _typewrite(text: str, delay: float = _pause):
        for ch in text:
            print(ch, end="", flush=True)
            time.sleep(delay)
        print()

    _typewrite("  The wilderness stirs...")
    time.sleep(0.6)
    _typewrite("  Three champions answer the call.")
    time.sleep(0.8)
    print()

    # ── Reveal each champion one at a time ────────────────────────
    for i, champ in enumerate(picks, 1):
        input(f"  Press Enter to reveal champion {i} of {len(picks)}...")
        type_str  = champ.type1 + (f" / {champ.type2}" if champ.type2 else "")
        role_str  = champ.role[:34] if hasattr(champ, "role") else ""
        lore_str  = _CHAMPION_LORE.get(champ.name, champ.niche or "")

        # Base stats
        vit = champ.base_vit; sta = champ.base_sta; mgt = champ.base_mgt
        mag = champ.base_mag; grd = champ.base_grd; wil = champ.base_wil
        swf = champ.base_swf
        total = vit + sta + mgt + mag + grd + wil + swf

        # Single-width ASCII bar — 16 pips proportional to 0-100 range.
        # Using = and - (guaranteed single-column in any terminal font).
        def _bar(val: int, width: int = 16) -> str:
            filled = min(width, round(val * width / 100))
            return "=" * filled + "-" * (width - filled)

        print()
        print( "  ┌───────────────────────────────────────────────────────────────┐")
        _typewrite(
            f"  │  [{i}]  {champ.name:<18}  {type_str:<16}  {role_str:<16}  │",
            delay=0.03,
        )
        print( "  │  ───────────────────────────────────────────────────────────  │")
        # Two-column stat layout — each interior line is exactly 63 chars wide
        print(f"  │   VIT  {_bar(vit)}  {vit:>3}     MGT  {_bar(mgt)}  {mgt:>3}   │")
        print(f"  │   STA  {_bar(sta)}  {sta:>3}     MAG  {_bar(mag)}  {mag:>3}   │")
        print(f"  │   GRD  {_bar(grd)}  {grd:>3}     WIL  {_bar(wil)}  {wil:>3}   │")
        print(f"  │   SWF  {_bar(swf)}  {swf:>3}     TOTAL                 {total:>4}   │")
        if lore_str:
            print( "  │  ───────────────────────────────────────────────────────────  │")
            words, line, lines = lore_str.split(), "", []
            for w in words:
                if len(line) + len(w) + 1 > 58:
                    lines.append(line.strip())
                    line = w + " "
                else:
                    line += w + " "
            if line.strip():
                lines.append(line.strip())
            for ln in lines:
                print(f"  │   {ln:<58}  │")
        print( "  └───────────────────────────────────────────────────────────────┘")
        time.sleep(0.3)

    # ── Player choice ─────────────────────────────────────────────
    print()
    print("  ─" * 30)
    print()
    while True:
        raw = input(f"  Choose your champion [1-{len(picks)}]: ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(picks):
                chosen = picks[idx]
                print()
                _typewrite(f"  ✦  {chosen.name} joins your journey!", delay=0.04)
                time.sleep(0.4)
                print()
                return chosen.name
        except ValueError:
            pass
        # also accept by name
        for champ in picks:
            if raw.lower() == champ.name.lower():
                print()
                _typewrite(f"  ✦  {champ.name} joins your journey!", delay=0.04)
                time.sleep(0.4)
                print()
                return champ.name
        print(f"  Enter a number between 1 and {len(picks)}.")


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
    5. After run ends, loop back to the main screen automatically
    """
    all_champions = get_champion_roster()

    while True:
        # ── Load or create account (reloaded each loop so stats are fresh) ──
        profile = None if dev_mode else load_account(SAVE_DIR)

        if profile is None and not dev_mode:
            profile = create_account(SAVE_DIR)
            # Run the starter ceremony — player picks from 3 random champions
            chosen = _starter_ceremony(all_champions)
            profile.meta.unlocked_champions.add(chosen)
            save_account(profile, SAVE_DIR)
            # Banner already printed inside the ceremony; just confirm the unlock
            print(f"  Your account has been created.  First unlock: {chosen}")
        elif not dev_mode:
            _banner("KAMIKIN — WILDERNESS MODE")
            print(f"  Total runs: {profile.meta.total_runs}  |  "
                  f"Best stage: {profile.meta.best_stage}")
            print(f"  Unlocked champions: {len(profile.meta.unlocked_champions)}  |  "
                  f"Sage currency: {_total_currency(profile)} 𝕮")
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

        # ── Run ended — return to main screen ────────────────────────
        # In dev mode, ask whether to play again; normal mode loops back
        # automatically (account is reloaded at the top of the next iteration).
        if dev_mode:
            print()
            if not _confirm("Play again?", default_yes=True):
                print("  See you next time!")
                return


# ═══════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═══════════════════════════════════════════════════════════════

def _total_currency(profile: AccountProfile) -> int:
    """Permanent meta-currency (𝕮) accumulated across all runs."""
    return profile.meta.perm_currency


def _blank_meta():
    """A MetaState that never gets persisted — used in dev mode."""
    from wilderness.models import MetaState
    return MetaState()


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]
    setup_logging(debug="--debug" in args)

    log.info("Kamikin Wilderness Mode starting (dev=%s)", "--dev" in args)

    try:
        if "--pc" in args:
            cmd_pc()
        elif "--reset" in args:
            cmd_reset()
        elif "--dev" in args:
            cmd_run(dev_mode=True)
        else:
            cmd_run(dev_mode=False)
    except KeyboardInterrupt:
        log.info("Session interrupted by player (Ctrl-C)")
        print("\n  Goodbye!")
    except Exception:
        log.exception("Unhandled error in wilderness_mode — aborting")
        raise

    log.info("Kamikin Wilderness Mode exiting normally")


if __name__ == "__main__":
    main()
