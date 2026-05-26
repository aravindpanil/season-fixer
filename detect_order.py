#!/usr/bin/env python3
"""Detect out-of-order episode watches in Trakt history."""

import csv
from collections import defaultdict
from pathlib import Path

from trakt.csv_to_python import load_rows
from trakt.paths import DEFAULT_CSV
from trakt.intervals import row_title

OUTPUT = Path(__file__).resolve().parent / "data" / "flagged_order.csv"

_CSV_FIELDNAMES = [
    "history_id",
    "show_name",
    "season_number",
    "episode_number",
    "watched_at",
    "expected_after_title",
    "expected_after_watched_at",
    "action",
]


def detect_violations(episodes):
    """Return out-of-order first-watch violations, excluding rewatches."""
    
    # Create a dict per show with watches of all seasons
    by_show = defaultdict(list)
    for row in episodes:
        by_show[row["show_id"]].append(row)

    violations = []
    for show_rows in by_show.values():
        # Keep only the first watch of each episode
        first_watch = {}
        for row in show_rows:
            key = (row["season_number"], row["episode_number"])
            if key not in first_watch or row["watched_dt"] < first_watch[key]["watched_dt"]:
                first_watch[key] = row

        # Walk first watches in chronological order; flag any that fall below the running max
        max_key, max_row = None, None
        for row in sorted(first_watch.values(), key=lambda r: r["watched_dt"]):
            key = (row["season_number"], row["episode_number"])
            if max_key is not None and key < max_key:
                violations.append({"row": row, "expected_after_row": max_row})
            elif max_key is None or key > max_key:
                max_key, max_row = key, row

    return violations


def _load_existing_actions(path):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {int(r["history_id"]): r.get("action", "") for r in csv.DictReader(f)}


def write_violations_csv(violations, output_path):
    output_path = Path(output_path)
    existing_actions = _load_existing_actions(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        for v in violations:
            row, ea = v["row"], v["expected_after_row"]
            writer.writerow({
                "history_id": row["history_id"],
                "show_name": row["show_name"],
                "season_number": row["season_number"],
                "episode_number": row["episode_number"],
                "watched_at": row["watched_at"],
                "expected_after_title": row_title(ea),
                "expected_after_watched_at": ea["watched_at"],
                "action": existing_actions.get(row["history_id"], ""),
            })


def main():
    episodes = [r for r in load_rows(DEFAULT_CSV) if r["type"] == "episode"]
    violations = detect_violations(episodes)

    print(f"Found {len(violations)} out-of-order first-watch episode(s).")
    for v in violations:
        row, ea = v["row"], v["expected_after_row"]
        print(f"  {row_title(row)} ({row['watched_at']}) — watched before {row_title(ea)} ({ea['watched_at']})")

    write_violations_csv(violations, OUTPUT)
    print(f"Wrote {len(violations)} violation(s) to {OUTPUT}")


if __name__ == "__main__":
    main()
