"""Parse the local watch-history CSV into Python dicts."""

import csv
from datetime import datetime, timezone
from pathlib import Path

from trakt.paths import DEFAULT_CSV


def _parse_dt(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_row(row):
    for key in ("history_id", "item_trakt_id"):
        row[key] = int(row[key])
    for key in ("show_id", "season_number", "episode_number"):
        if row[key]:
            row[key] = int(row[key])
    row["watched_dt"] = _parse_dt(row["watched_at"])
    row["runtime"] = int(row["runtime"]) if row.get("runtime") else None
    return row


def load_rows(path=DEFAULT_CSV):
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run python trakt/history.py first.")
    with path.open(newline="", encoding="utf-8") as f:
        return [_parse_row(row) for row in csv.DictReader(f)]
