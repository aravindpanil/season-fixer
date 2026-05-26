#!/usr/bin/env python3
"""Reschedule overlapping watch entries to the nearest free time slot."""

from detect_conflicts import detect_conflicts
from trakt.client import TraktRateLimitError, to_trakt_iso
from trakt.csv_to_python import load_rows
from trakt.history import fetch_watch_history
from trakt.intervals import row_duration, row_interval, row_title
from trakt.scheduler import find_nearest_slot, reschedule_on_trakt


def _season_episode_key(row):
    return (row["season_number"], row["episode_number"])


def _is_out_of_order(row_a, row_b):
    """Return the row to move when a later episode starts before an earlier one."""
    if row_a["type"] != "episode" or row_b["type"] != "episode":
        return None
    if row_a["show_id"] != row_b["show_id"]:
        return None

    key_a = _season_episode_key(row_a)
    key_b = _season_episode_key(row_b)
    if key_a == key_b:
        return None

    start_a, _ = row_interval(row_a)
    start_b, _ = row_interval(row_b)
    if key_a > key_b and start_a < start_b:
        return row_a
    if key_b > key_a and start_b < start_a:
        return row_b
    return None


def _movie_vs_episode_to_move(row_a, row_b):
    """Return the movie when it conflicts with an episode; movies are not episodic."""
    if row_a["type"] == "movie" and row_b["type"] == "episode":
        return row_a
    if row_b["type"] == "movie" and row_a["type"] == "episode":
        return row_b
    return None


def pick_entry_to_move(row_a, row_b):
    out_of_order = _is_out_of_order(row_a, row_b)
    if out_of_order is not None:
        return out_of_order

    interleaved = _movie_vs_episode_to_move(row_a, row_b)
    if interleaved is not None:
        return interleaved

    dur_a = row_duration(row_a)
    dur_b = row_duration(row_b)
    if dur_a != dur_b:
        return row_a if dur_a < dur_b else row_b
    start_a, _ = row_interval(row_a)
    start_b, _ = row_interval(row_b)
    return row_a if start_a > start_b else row_b


def entries_to_move(conflicts):
    seen = set()
    rows = []
    for conflict in conflicts:
        row = pick_entry_to_move(conflict["row_a"], conflict["row_b"])
        if row["history_id"] in seen:
            continue
        seen.add(row["history_id"])
        rows.append(row)
    return rows


def fix_conflicts(rows):
    moves = 0
    while True:
        conflicts = detect_conflicts(rows)
        if not conflicts:
            break
        for row in entries_to_move(conflicts):
            new_end = find_nearest_slot(row, rows)
            new_at = to_trakt_iso(new_end)
            print(f"Moving {row_title(row)}: {row['watched_at']} -> {new_at}")
            reschedule_on_trakt(row, new_end)
            row["watched_dt"] = new_end
            row["watched_at"] = new_at
            moves += 1
    return moves


def main():
    try:
        rows = load_rows()
        initial = len(detect_conflicts(rows))
        if initial == 0:
            print("No conflicts to fix.")
            return

        print(f"Found {initial} overlapping pair(s).")
        moves = fix_conflicts(rows)
        path = fetch_watch_history()
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None

    remaining = len(detect_conflicts(load_rows()))
    print(f"Moved {moves} entr{'y' if moves == 1 else 'ies'}.")
    print(f"Refreshed watch history at {path}")
    print(f"Remaining conflicts: {remaining}")


if __name__ == "__main__":
    main()
