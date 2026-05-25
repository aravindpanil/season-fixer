"""Load and query the local watch-history CSV produced by trakt.history."""

import csv
from pathlib import Path

from trakt.dt import parse_dt

DEFAULT_CSV = Path("data/watch_history.csv")


def load_rows(path=DEFAULT_CSV):
    """Load all rows (episodes + movies) from the watch-history CSV.

    Parsed fields added to each row:
      - ``watched_dt``: UTC-aware datetime
      - ``history_id``: int
      - ``show_id``, ``season_number``, ``episode_number``: int (episodes only)
    """
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run fetch_history.py first.")
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["history_id"] = int(row["history_id"])
            row["watched_dt"] = parse_dt(row["watched_at"])
            if row["type"] == "episode":
                row["show_id"] = int(row["show_id"])
                row["season_number"] = int(row["season_number"])
                row["episode_number"] = int(row["episode_number"])
            rows.append(row)
    return rows


def load_episodes(path=DEFAULT_CSV):
    """Load episode-only rows from the watch-history CSV.

    Same parsed fields as ``load_rows`` but movies are excluded.
    """
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run fetch_history.py first.")
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["type"] != "episode":
                continue
            row["history_id"] = int(row["history_id"])
            row["show_id"] = int(row["show_id"])
            row["season_number"] = int(row["season_number"])
            row["episode_number"] = int(row["episode_number"])
            row["watched_dt"] = parse_dt(row["watched_at"])
            rows.append(row)
    return rows


def split_first_watch(entries):
    """Split episode entries into ``(first_watch, rewatches)``.

    Entries are sorted by ``watched_dt``. The first-watch run ends once every
    episode number in the set has appeared exactly once; all later entries are
    rewatches. Both returned lists preserve chronological order.
    """
    entries = sorted(entries, key=lambda e: e["watched_dt"])
    all_episodes = {e["episode_number"] for e in entries}
    seen = set()
    first_watch = []
    rewatches = []
    complete = False

    for entry in entries:
        if complete:
            rewatches.append(entry)
            continue
        first_watch.append(entry)
        seen.add(entry["episode_number"])
        if seen >= all_episodes:
            complete = True

    return first_watch, rewatches


def find_show(rows, show_name=None, show_id=None):
    """Return the ``show_id`` matching the given name or id from loaded rows.

    Exactly one of ``show_name`` or ``show_id`` must be provided.
    Raises ``SystemExit`` when no match is found or when a name matches
    multiple distinct show IDs (use ``show_id`` to disambiguate).
    """
    episodes = [r for r in rows if r["type"] == "episode"]
    if show_id is not None:
        matches = {r["show_id"] for r in episodes if r["show_id"] == show_id}
        if not matches:
            raise SystemExit(f"No show found with id {show_id}.")
        return show_id

    name_lower = show_name.lower()
    matches = {
        r["show_id"]: r["show_name"]
        for r in episodes
        if r["show_name"].lower() == name_lower
    }
    if not matches:
        raise SystemExit(f"No show found matching {show_name!r}.")
    if len(matches) > 1:
        raise SystemExit(
            f"Multiple shows match {show_name!r}. Use --show-id instead."
        )
    return next(iter(matches))
