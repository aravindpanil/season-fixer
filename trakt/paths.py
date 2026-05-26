"""Shared filesystem paths."""

from pathlib import Path

DATA_DIR = Path("data")
DEFAULT_CSV = DATA_DIR / "watch_history.csv"
FLAGGED_CONFLICTS_CSV = DATA_DIR / "flagged_conflicts.csv"
