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

# ── Economy ──────────────────────────────────────────────────────
SHOP_HEAL_SMALL_COST     = 20
SHOP_HEAL_FULL_COST      = 60
SHOP_REVIVE_COST         = 80
SHOP_RARE_ITEM_COST      = 120

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
