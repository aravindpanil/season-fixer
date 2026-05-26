#!/usr/bin/env python3
"""Detect overlapping watch intervals in Trakt history."""

import csv

from trakt.csv_to_python import load_rows
from trakt.intervals import row_duration, row_interval, row_title
from trakt.paths import FLAGGED_CONFLICTS_CSV

_FIELDNAMES = [
    "history_id_a",
    "history_id_b",
    "type_a",
    "type_b",
    "title_a",
    "title_b",
    "watched_at_a",
    "watched_at_b",
    "runtime_a",
    "runtime_b",
    "computed_start_a",
    "computed_start_b",
]


def detect_conflicts(rows):
    """Return conflict dicts for every pair of overlapping watch intervals.

    Each dict has keys: row_a, row_b.
    Uses runtime from the row when present; falls back to default episode/movie
    durations from trakt.intervals.

    3-way (or N-way) pile-ups produce one dict per overlapping pair, so a
    3-way conflict yields three dicts: (A,B), (A,C), (B,C).
    """
    intervals = sorted(
        ((*row_interval(row), row) for row in rows),
        key=lambda item: item[0],
    )
    conflicts = []

    # Sweep line: sorted by start time; stop inner loop once b starts at/after a ends.
    for i, (a_start, a_end, row_a) in enumerate(intervals):
        for b_start, _, row_b in intervals[i + 1 :]:
            if b_start >= a_end:
                break
            conflicts.append({"row_a": row_a, "row_b": row_b})
    return conflicts


def conflict_to_csv_row(conflict):
    row_a = conflict["row_a"]
    row_b = conflict["row_b"]
    a_start, _ = row_interval(row_a)
    b_start, _ = row_interval(row_b)
    return {
        "history_id_a": row_a["history_id"],
        "history_id_b": row_b["history_id"],
        "type_a": row_a["type"],
        "type_b": row_b["type"],
        "title_a": row_title(row_a),
        "title_b": row_title(row_b),
        "watched_at_a": row_a["watched_at"],
        "watched_at_b": row_b["watched_at"],
        "runtime_a": int(row_duration(row_a).total_seconds() // 60),
        "runtime_b": int(row_duration(row_b).total_seconds() // 60),
        "computed_start_a": a_start.isoformat(),
        "computed_start_b": b_start.isoformat(),
    }


def main():
    conflicts = detect_conflicts(load_rows())
    print(f"Found {len(conflicts)} overlapping pair(s).")

    FLAGGED_CONFLICTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with FLAGGED_CONFLICTS_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        for conflict in conflicts:
            writer.writerow(conflict_to_csv_row(conflict))

    print(f"Wrote {len(conflicts)} conflict pair(s) to {FLAGGED_CONFLICTS_CSV}")


if __name__ == "__main__":
    main()
