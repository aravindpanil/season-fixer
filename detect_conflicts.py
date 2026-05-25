#!/usr/bin/env python3
"""Detect overlapping watch intervals in Trakt history."""

import argparse
import csv
from datetime import timedelta
from pathlib import Path

from trakt.csv_to_python import DEFAULT_CSV, load_rows

EPISODE_DURATION = timedelta(hours=1)
MOVIE_DURATION = timedelta(hours=3)

OUTPUT = Path(__file__).resolve().parent / "data" / "flagged_conflicts.csv"

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
    "overlap_minutes",
]


def row_duration(row):
    if row["runtime"]:
        return timedelta(minutes=row["runtime"])
    return MOVIE_DURATION if row["type"] == "movie" else EPISODE_DURATION


def row_interval(row):
    end = row["watched_dt"]
    return end - row_duration(row), end


def row_title(row):
    if row["type"] == "episode":
        return (
            f"{row['show_name']} "
            f"S{row['season_number']:02d}E{row['episode_number']:02d}"
        )
    return row["movie_title"]


def detect_conflicts(rows):
    """Return conflict dicts for every pair of overlapping watch intervals.

    Each dict has keys: row_a, row_b, overlap_minutes.
    Uses runtime from the row when present; falls back to MOVIE_DURATION /
    EPISODE_DURATION.

    3-way (or N-way) pile-ups produce one dict per overlapping pair, so a
    3-way conflict yields three dicts: (A,B), (A,C), (B,C).
    """
    intervals = sorted(
        ((*row_interval(row), row) for row in rows),
        key=lambda item: item[0],
    )
    conflicts = []
    for i, (a_start, a_end, row_a) in enumerate(intervals):
        for b_start, b_end, row_b in intervals[i + 1 :]:
            if b_start >= a_end:
                break
            overlap_min = round(
                (min(a_end, b_end) - max(a_start, b_start)).total_seconds() / 60,
                1,
            )
            conflicts.append(
                {
                    "row_a": row_a,
                    "row_b": row_b,
                    "overlap_minutes": overlap_min,
                }
            )
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
        "overlap_minutes": conflict["overlap_minutes"],
    }


def print_summary(conflicts):
    print(f"Found {len(conflicts)} overlapping pair(s).")
    if not conflicts:
        return

    worst = max(conflicts, key=lambda c: c["overlap_minutes"])
    print(
        "Worst overlap: "
        f"{worst['overlap_minutes']} min — "
        f"{row_title(worst['row_a'])} vs {row_title(worst['row_b'])}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_CSV, help="Watch history CSV")
    args = parser.parse_args()

    conflicts = detect_conflicts(load_rows(args.input))
    print_summary(conflicts)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        for conflict in conflicts:
            writer.writerow(conflict_to_csv_row(conflict))

    print(f"Wrote {len(conflicts)} conflict pair(s) to {OUTPUT}")


if __name__ == "__main__":
    main()
