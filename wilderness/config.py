"""
wilderness/config.py
====================
All configurable constants for Wilderness Mode in one place.
Change values here without touching gameplay logic.
"""

# ── Party ────────────────────────────────────────────────────────
PARTY_MAX_SIZE          = 6
STARTING_LEVEL          = 5
STARTING_PARTY_SIZE     = 1

# ── Level Scaling ────────────────────────────────────────────────
# Enemy level = highest_player_level + offset (clamped to ≥ 1)
ENEMY_LEVEL_OFFSET_MIN  = -4   # weakest enemy in range
ENEMY_LEVEL_OFFSET_MAX  = -2   # strongest enemy in range

# Stat multiplier formula: round(maxStat * level / MAX_LEVEL)
# maxStat = base_stat × STAT_MULT (the engine's full-strength value)
# At MAX_LEVEL the champion has its full engine stats.
# At level 5, stats are 5% of max.
MAX_LEVEL               = 100
LEVEL_BASELINE          = MAX_LEVEL   # alias kept for back-compat

# ── Stage Structure ───────────────────────────────────────────────
NORMAL_BATTLES_PER_STAGE = 5

# Elite enemy count = ELITE_BASE + stage_number
# Stage 1 → 2 enemies,  Stage 2 → 3 enemies, etc.
ELITE_BASE_ENEMY_COUNT   = 1

# Soft design cap for future pivot (not enforced by engine yet)
ELITE_ENEMY_SOFT_CAP     = 6

# ── Rewards ───────────────────────────────────────────────────────
HEAL_AMOUNT_FRACTION     = 0.40   # % of max HP restored by heal reward
CURRENCY_NORMAL_MIN      = 10
CURRENCY_NORMAL_MAX      = 30
CURRENCY_ELITE_MIN       = 40
CURRENCY_ELITE_MAX       = 80
ITEM_DROP_CHANCE_NORMAL  = 0.15   # 15% chance post-normal battle
ITEM_DROP_CHANCE_ELITE   = 0.60   # 60% chance post-elite

# ── Recruitment ──────────────────────────────────────────────────
SHINY_CHANCE             = 1 / 4000
RECRUIT_FROM_ELITE       = True

# ── Map ──────────────────────────────────────────────────────────
MAP_BRANCH_COUNT         = 3      # paths offered after each stage
MAP_MAX_STAGES           = 10     # run ends after this many stages

# Node type weights for branch generation
# Format: {NodeType.value: weight}
# Extend here when Shop/Event are implemented
NODE_TYPE_WEIGHTS = {
    "battle": 70,
    "elite":  30,
}

# ── Shop (in-run currency) ───────────────────────────────────────
SHOP_HEAL_LOW_COST       = 15    # Essence Shard   — 25% HP
SHOP_HEAL_MED_COST       = 30    # Spirit Herb     — 50% HP
SHOP_HEAL_HIGH_COST      = 55    # Vital Elixir    — 75% HP
SHOP_HEAL_MAX_COST       = 80    # Spirit Water    — 100% HP + cleanse
SHOP_HEAL_STATUS_COST    = 25    # Purifying Dust  — cleanse status
SHOP_MP_RESTORE_COST     = 35    # Stamina Crystal — 100% MP
SHOP_REVIVE_COST         = 90    # Revival Spark   — revive at 50% HP

# ── Permanent currency (𝕮) ──────────────────────────────────────
# Earned per monster defeated: stage_number × PERM_CURRENCY_PER_MONSTER
PERM_CURRENCY_PER_MONSTER = 1    # base; multiplied by current stage number

# ── Sanctum — meta-progression screen (main menu) ───────────────
# Spend perm_currency here to permanently UNLOCK moves for a champion.
# Unlock cost = SANCTUM_BASE_COST × tier
#   Tier 1: 5 𝕮   Tier 2: 15 𝕮   Tier 3: 30 𝕮   Tier 4: 60 𝕮
SANCTUM_BASE_COST        = 5     # multiplied by move tier
# Min champion level required to unlock a move at a given tier:
#   Tier 1: any level   Tier 2: Lv 16   Tier 3: Lv 36   Tier 4: Lv 61
SANCTUM_TIER_LEVEL_REQ   = {1: 1, 2: 16, 3: 36, 4: 61}
# Max moves shown per champion in the Sanctum learn list
SANCTUM_MAX_LEARN_LIST   = 12

# ── Wandering Sage — in-run move equip (costs gold) ─────────────
# The Sage lets players EQUIP already-unlocked moves during a run.
# Cost to swap a move slot = SAGE_EQUIP_COST × move tier
#   Tier 1: 10 💰   Tier 2: 25 💰   Tier 3: 50 💰   Tier 4: 100 💰
SAGE_EQUIP_BASE_COST     = 10    # gold multiplied by move tier
# Level required to equip a move during a run (same as Sanctum thresholds)
SAGE_TIER_LEVEL_REQ      = {1: 1, 2: 16, 3: 36, 4: 61}

# ── Fate Seal — premium gacha tier in the Sanctum ──────────────────────────
# Each draw randomly pulls from a champion's FATE_SEAL_POOL (battle_engine.py).
# Pity: every FATE_SEAL_PITY_THRESHOLD draws guarantees a move not yet obtained.
# Duplicate draw: refunds FATE_SEAL_DUPE_REFUND 𝕮 instead of re-adding.
FATE_SEAL_COST            = 60   # base cost per draw in 𝕮
FATE_SEAL_PITY_THRESHOLD  = 3    # guaranteed new move every N draws
FATE_SEAL_DUPE_REFUND     = 15   # 𝕮 refunded on a duplicate pull

# ── Legacy tutor constants (kept for back-compat, not used by new system) ──
TUTOR_COST_PER_LEVEL     = 2
TUTOR_MIN_COST           = 10
NUM_TUTOR_MOVES          = 12
TUTOR_LEARN_LEVELS       = [10, 15, 20, 25, 30, 35, 40, 45, 55, 65, 75, 90]

# ── Realms ───────────────────────────────────────────────────────
ESSENCES = [
    "Inferno", "Aqua", "Flora", "Terra", "Wind", "Volt",
    "Frost", "Mind", "Spirit", "Cursed", "Bless",
    "Mythos", "Cyber", "Cosmic", "Neutral",
]

# Bridgeland pairs (dual-type zones) — sample; extend as needed
BRIDGELANDS = [
    ("Inferno", "Flora"),
    ("Aqua",    "Frost"),
    ("Volt",    "Cyber"),
    ("Mind",    "Spirit"),
    ("Cursed",  "Bless"),
    ("Terra",   "Mythos"),
    ("Wind",    "Cosmic"),
]

# ── Persistence ──────────────────────────────────────────────────
META_SAVE_FILENAME           = "wilderness_meta.json"
ACCOUNT_SAVE_FILENAME        = "account.json"

# Champions available to new accounts before any PC deposits
INITIAL_UNLOCKED_CHAMPIONS   = ["Solaire"]
