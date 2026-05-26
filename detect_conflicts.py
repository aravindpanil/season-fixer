#!/usr/bin/env python3
"""Detect overlapping watch intervals and optionally reschedule them on Trakt."""

from trakt.client import TraktRateLimitError, to_trakt_iso, trakt_post
from trakt.csv_to_python import load_rows
from trakt.history import fetch_watch_history
from trakt.intervals import merge_intervals, row_duration, row_interval, row_title


def detect_conflicts(rows):
    """Return (row_a, row_b) for each pair of overlapping watch intervals."""
    intervals = sorted(
        ((*row_interval(row), row) for row in rows),
        key=lambda item: item[0],
    )
    conflicts = []
    for i, (_, a_end, row_a) in enumerate(intervals):
        for b_start, _, row_b in intervals[i + 1 :]:
            if b_start >= a_end:
                break
            conflicts.append((row_a, row_b))
    return conflicts


def find_nearest_slot(row, rows):
    duration = row_duration(row)
    original_end = row["watched_dt"]
    merged = merge_intervals(
        sorted(
            row_interval(other)
            for other in rows
            if other["history_id"] != row["history_id"]
        )
    )
    gaps = (
        [(None, None)]
        if not merged
        else [(None, merged[0][0])]
        + [(merged[i][1], merged[i + 1][0]) for i in range(len(merged) - 1)]
        + [(merged[-1][1], None)]
    )

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
    types = (row_a["type"], row_b["type"])
    if types == ("movie", "episode"):
        return row_a
    if types == ("episode", "movie"):
        return row_b

    if types == ("episode", "episode") and row_a["show_id"] == row_b["show_id"]:
        key_a = (row_a["season_number"], row_a["episode_number"])
        key_b = (row_b["season_number"], row_b["episode_number"])
        if key_a != key_b:
            start_a, start_b = row_interval(row_a)[0], row_interval(row_b)[0]
            if key_a > key_b and start_a < start_b:
                return row_a
            if key_b > key_a and start_b < start_a:
                return row_b

    dur_a, dur_b = row_duration(row_a), row_duration(row_b)
    if dur_a != dur_b:
        return row_a if dur_a < dur_b else row_b
    return row_a if row_interval(row_a)[0] > row_interval(row_b)[0] else row_b


def main():
    try:
        rows = load_rows()
        conflicts = detect_conflicts(rows)
        if not conflicts:
            print("No overlapping watch intervals found.")
            return

        print(f"Found {len(conflicts)} overlapping pair(s).")
        for row_a, row_b in conflicts:
            print(
                f"{row_title(row_a)} ({row_a['watched_at']}) vs "
                f"{row_title(row_b)} ({row_b['watched_at']})"
            )
        if input("Fix these conflicts? [y/N]: ").strip().casefold() not in ("y", "yes"):
            return

        moves = 0
        while conflicts:
            seen = set()
            for row_a, row_b in conflicts:
                row = pick_entry_to_move(row_a, row_b)
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

        path, _ = fetch_watch_history()
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None

    remaining = len(detect_conflicts(load_rows()))
    print(f"Moved {moves} entr{'y' if moves == 1 else 'ies'}.")
    print(f"Refreshed watch history at {path}")
    print(f"Remaining conflicts: {remaining}")


if __name__ == "__main__":
    main()
