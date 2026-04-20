"""
logger.py
=========
Centralized logging configuration for Kamikin.

Usage
-----
Entry point (wilderness_mode.py or battle_engine.py standalone):

    from logger import setup_logging
    setup_logging(debug=False)     # INFO on console, DEBUG in file
    setup_logging(debug=True)      # DEBUG on both (--debug flag)

All other modules — never call setup_logging(), just:

    import logging
    log = logging.getLogger(__name__)

Verbosity
---------
• Console  — INFO by default; DEBUG when debug=True (or --debug flag)
• Log file — always DEBUG: captures every move, damage roll, and save event
              for post-session analysis and bug reports

Log files
---------
Written to  <project_root>/logs/kamikin_YYYYMMDD_HHMMSS.log
Directory is auto-created if it doesn't exist.
"""

import logging
import os
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────
_HERE    = os.path.dirname(os.path.abspath(__file__))
LOG_DIR  = os.path.join(_HERE, "logs")

# ── Format strings ────────────────────────────────────────────────
#   Full format used by both handlers — timestamp, level, module, message
LOG_FMT  = "%(asctime)s [%(levelname)-8s] %(name)-30s — %(message)s"
DATE_FMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(debug: bool = False) -> logging.Logger:
    """
    Configure the root logger with a file handler (always DEBUG) and a
    console handler (INFO by default, DEBUG when debug=True).

    Safe to call only once.  Re-calling after handlers are attached is a
    no-op — handlers are not duplicated.

    Parameters
    ----------
    debug : bool
        Pass True to show DEBUG messages on the console as well as in the
        log file.  Equivalent to the --debug CLI flag.

    Returns
    -------
    The root Logger (rarely needed directly; modules use getLogger(__name__)).
    """
    root = logging.getLogger()

    # Guard: don't attach duplicate handlers if called more than once
    if root.handlers:
        return root

    root.setLevel(logging.DEBUG)   # handlers do their own filtering

    formatter = logging.Formatter(LOG_FMT, datefmt=DATE_FMT)

    # ── File handler — always DEBUG ───────────────────────────────
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file  = os.path.join(LOG_DIR, f"kamikin_{timestamp}.log")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # ── Console handler — WARNING by default (silent during normal play) ─
    # INFO and DEBUG go to the log file only; the terminal stays clean.
    # Pass debug=True (--debug flag) to see everything on-console.
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if debug else logging.WARNING)
    ch.setFormatter(formatter)
    root.addHandler(ch)

    root.info("Logging started → %s", log_file)
    return root


def get_logger(name: str) -> logging.Logger:
    """
    Convenience wrapper — identical to logging.getLogger(name).
    Provided so modules can write `from logger import get_logger` if preferred,
    but the standard `logging.getLogger(__name__)` pattern is equally valid.
    """
    return logging.getLogger(name)
