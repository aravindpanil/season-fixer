#!/usr/bin/env python3
"""Detect overlapping watch intervals and optionally reschedule them on Trakt."""

from trakt.client import TraktRateLimitError, to_trakt_iso, trakt_post
from trakt.csv_to_python import load_rows
from trakt.history import fetch_watch_history
from trakt.intervals import merge_intervals, row_duration, row_interval, row_title


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
    for i, (_, a_end, row_a) in enumerate(intervals):
        for b_start, _, row_b in intervals[i + 1 :]:
            if b_start >= a_end:
                break
            conflicts.append({"row_a": row_a, "row_b": row_b})
    return conflicts


def print_conflicts(conflicts):
    print(f"Found {len(conflicts)} overlapping pair(s).")
    for row_a, row_b in ((c["row_a"], c["row_b"]) for c in conflicts):
        print(
            f"{row_title(row_a)} ({row_a['watched_at']}) vs "
            f"{row_title(row_b)} ({row_b['watched_at']})"
        )


def find_nearest_slot(row, rows):
    """Return the end time closest to row's current end that fits without overlap."""
    duration = row_duration(row)
    original_end = row["watched_dt"]
    merged = merge_intervals(
        sorted(
            row_interval(other)
            for other in rows
            if other["history_id"] != row["history_id"]
        )
    )

    if merged:
        gaps = [(None, merged[0][0])]
        gaps += [(merged[i][1], merged[i + 1][0]) for i in range(len(merged) - 1)]
        gaps.append((merged[-1][1], None))
    else:
        gaps = [(None, None)]

    best_end, best_dist = None, None
    for gap_start, gap_end in gaps:
        if (
            gap_start is not None
            and gap_end is not None
            and gap_end - gap_start < duration
        ):
            continue
        candidate = original_end
        if gap_start is not None:
            candidate = max(candidate, gap_start + duration)
        if gap_end is not None:
            candidate = min(candidate, gap_end)
        dist = abs((candidate - original_end).total_seconds())
        if best_dist is None or dist < best_dist:
            best_end, best_dist = candidate, dist

    if best_end is None:
        raise ValueError(
            f"No gap large enough for {duration} — history_id={row['history_id']}"
        )
    return best_end


def reschedule_on_trakt(row, new_end):
    """Remove row from Trakt history and re-add at new_end."""
    trakt_post("/sync/history/remove", {"ids": [row["history_id"]]})
    watched_at = to_trakt_iso(new_end)
    if row["type"] == "episode":
        trakt_post(
            "/sync/history",
            {
                "shows": [
                    {
                        "ids": {"trakt": row["show_id"]},
                        "seasons": [
                            {
                                "number": row["season_number"],
                                "episodes": [
                                    {
                                        "number": row["episode_number"],
                                        "watched_at": watched_at,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
    else:
        trakt_post(
            "/sync/history",
            {
                "movies": [
                    {
                        "ids": {"trakt": row["item_trakt_id"]},
                        "watched_at": watched_at,
                    }
                ],
            },
        )


def pick_entry_to_move(row_a, row_b):
    if row_a["type"] == "episode" and row_b["type"] == "episode":
        if row_a["show_id"] == row_b["show_id"]:
            key_a = (row_a["season_number"], row_a["episode_number"])
            key_b = (row_b["season_number"], row_b["episode_number"])
            if key_a != key_b:
                start_a, _ = row_interval(row_a)
                start_b, _ = row_interval(row_b)
                if key_a > key_b and start_a < start_b:
                    return row_a
                if key_b > key_a and start_b < start_a:
                    return row_b

    if row_a["type"] == "movie" and row_b["type"] == "episode":
        return row_a
    if row_b["type"] == "movie" and row_a["type"] == "episode":
        return row_b

    dur_a, dur_b = row_duration(row_a), row_duration(row_b)
    if dur_a != dur_b:
        return row_a if dur_a < dur_b else row_b
    start_a, _ = row_interval(row_a)
    start_b, _ = row_interval(row_b)
    return row_a if start_a > start_b else row_b


def fix_conflicts(rows, conflicts):
    moves = 0
    while conflicts:
        seen = set()
        for conflict in conflicts:
            row = pick_entry_to_move(conflict["row_a"], conflict["row_b"])
            if row["history_id"] in seen:
                continue
            seen.add(row["history_id"])
            new_end = find_nearest_slot(row, rows)
            new_at = to_trakt_iso(new_end)
            print(f"Moving {row_title(row)}: {row['watched_at']} -> {new_at}")
            reschedule_on_trakt(row, new_end)
            row["watched_dt"] = new_end
            row["watched_at"] = new_at
            moves += 1
        conflicts = detect_conflicts(rows)
    return moves


def confirm_fix():
    answer = input("Fix these conflicts? [y/N]: ").strip().casefold()
    return answer in ("y", "yes")


def main():
    try:
        rows = load_rows()
        conflicts = detect_conflicts(rows)
        if not conflicts:
            print("No overlapping watch intervals found.")
            return

        print_conflicts(conflicts)
        if not confirm_fix():
            return

        moves = fix_conflicts(rows, conflicts)
        path, _ = fetch_watch_history()
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None

    remaining = len(detect_conflicts(load_rows()))
    print(f"Moved {moves} entr{'y' if moves == 1 else 'ies'}.")
    print(f"Refreshed watch history at {path}")
    print(f"Remaining conflicts: {remaining}")


if __name__ == "__main__":
    main()
