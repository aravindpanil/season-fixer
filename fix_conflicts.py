#!/usr/bin/env python3
"""Reschedule overlapping watch entries to the nearest free time slot."""

from trakt.client import to_trakt_iso, trakt_post
from trakt.intervals import merge_intervals, row_duration, row_interval


def _remove_history(history_id):
    trakt_post("/sync/history/remove", {"ids": [history_id]})


def _add_episode(row, new_end):
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
                                    "watched_at": to_trakt_iso(new_end),
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )


def _add_movie(row, new_end):
    trakt_post(
        "/sync/history",
        {
            "movies": [
                {
                    "ids": {"trakt": row["item_trakt_id"]},
                    "watched_at": to_trakt_iso(new_end),
                }
            ],
        },
    )


def reschedule_on_trakt(row, new_end):
    """Remove row from Trakt history and re-add at new_end."""
    _remove_history(row["history_id"])
    if row["type"] == "episode":
        _add_episode(row, new_end)
    else:
        _add_movie(row, new_end)


def _clamp(value, lo, hi):
    if lo is not None and value < lo:
        value = lo
    if hi is not None and value > hi:
        value = hi
    return value


def _free_gaps(merged):
    """Yield (gap_start, gap_end) pairs; None means unbounded on that side."""
    if not merged:
        yield None, None
        return

    yield None, merged[0][0]
    for i in range(len(merged) - 1):
        yield merged[i][1], merged[i + 1][0]
    yield merged[-1][1], None


def _gap_fits(gap_start, gap_end, duration):
    if gap_start is None or gap_end is None:
        return True
    return gap_end - gap_start >= duration


def _candidate_end(original_end, gap_start, gap_end, duration):
    lo = gap_start + duration if gap_start is not None else None
    hi = gap_end
    return _clamp(original_end, lo, hi)


def find_nearest_slot(row, rows):
    """Return the end time closest to row's current end that fits without overlap.

    Uses every other row as occupied blocks.
    """
    duration = row_duration(row)
    original_end = row["watched_dt"]
    others = [
        row_interval(other)
        for other in rows
        if other["history_id"] != row["history_id"]
    ]
    merged = merge_intervals(sorted(others))

    best_end = None
    best_distance = None

    for gap_start, gap_end in _free_gaps(merged):
        if not _gap_fits(gap_start, gap_end, duration):
            continue
        candidate = _candidate_end(original_end, gap_start, gap_end, duration)
        distance = abs((candidate - original_end).total_seconds())
        if best_distance is None or distance < best_distance:
            best_end = candidate
            best_distance = distance

    if best_end is None:
        raise ValueError(
            f"No gap large enough for {duration} — history_id={row['history_id']}"
        )
    return best_end
