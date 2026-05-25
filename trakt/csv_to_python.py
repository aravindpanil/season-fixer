"""Parse the local watch-history CSV into Python dicts."""

import csv
from pathlib import Path

from datetime import datetime, timezone

DEFAULT_CSV = Path("data/watch_history.csv")

def _parse_dt(value):
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def load_rows(path=DEFAULT_CSV):
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run fetch_history.py first.")
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["history_id"] = int(row["history_id"])
            row["watched_dt"] = _parse_dt(row["watched_at"])
            row["item_trakt_id"] = int(row["item_trakt_id"])
            if row["type"] == "episode":
                row["show_id"] = int(row["show_id"])
                row["season_number"] = int(row["season_number"])
                row["episode_number"] = int(row["episode_number"])
            raw = row.get("runtime", "")
            row["runtime"] = int(raw) if raw else None
            rows.append(row)
    return rows
