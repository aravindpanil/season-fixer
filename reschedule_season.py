#!/usr/bin/env python3
"""Reschedule first-watch episodes for a show season into a date range."""

import argparse
import difflib
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from trakt.client import TraktRateLimitError, to_trakt_iso, trakt_post
from trakt.csv_to_python import DEFAULT_CSV, load_rows
from trakt.history import fetch_watch_history
from trakt.utils import row_duration, row_title, safe_input as input


def normalize_show_name(name):
    """Return lowercase name with punctuation removed for matching."""
    return re.sub(r"[^\w\s]", "", name.casefold())


def build_show_name_map(rows):
    """Return normalized show name -> (original show name, show_id)."""
    show_map = {}
    for row in rows:
        if row["type"] != "episode":
            continue
        show_name = row["show_name"]
        if not show_name:
            continue
        show_map[normalize_show_name(show_name)] = (show_name, row["show_id"])
    return show_map


def find_show_matches(query, show_map):
    """Return (show_name, show_id) candidates: exact, then substring, then fuzzy."""
    normalized = normalize_show_name(query)

    if normalized in show_map:
        return [show_map[normalized]]

    # SubString matching if exact match not found
    # Searches for multiple entries and returns all of them. Only adds to candidates if the value is not already in the set.
    candidates = []
    seen_ids = set()
    for key, value in show_map.items():
        if normalized in key or key in normalized:
            show_id = value[1]
            if show_id not in seen_ids:
                seen_ids.add(show_id)
                candidates.append(value)
    if candidates:
        return candidates

    # Fuzzy matching
    for key in difflib.get_close_matches(normalized, show_map, n=5, cutoff=0.6):
        value = show_map[key]
        show_id = value[1]
        if show_id not in seen_ids:
            seen_ids.add(show_id)
            candidates.append(value)
    return candidates


def prompt_show_choice(candidates):
    """Return chosen (show_name, show_id) or exit when the user cancels."""
    if len(candidates) == 1:
        return candidates[0]

    print("Multiple shows matched:")
    for index, (show_name, show_id) in enumerate(candidates, 1):
        print(f"  {index}. {show_name} (show_id {show_id})")
    print("  0. Cancel")

    while True:
        answer = input(f"Choose a show [1-{len(candidates)} / 0 cancel]: ").strip()
        if answer == "0":
            raise SystemExit("Cancelled.")
        try:
            choice = int(answer)
        except ValueError:
            print("Enter a number.")
            continue
        if 1 <= choice <= len(candidates):
            return candidates[choice - 1]
        print("Invalid choice.")


def resolve_show(query, show_map):
    """Resolve a show name query to (show_name, show_id)."""
    matches = find_show_matches(query, show_map)
    if not matches:
        raise ValueError(f"No show found matching {query!r}")
    return prompt_show_choice(matches)


def parse_date_range(start, end):
    """Return UTC start-of-day and end-of-day datetimes for ``YYYY-MM-DD`` strings."""
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )
    if end_dt < start_dt:
        raise ValueError(f"End date {end!r} is before start date {start!r}")
    return start_dt, end_dt


def find_season_rows(rows, show_id, season):
    """Return first-watch episodes for a show, sorted by episode number."""
    matched = [
        row
        for row in rows
        if row["type"] == "episode" and row["show_id"] == show_id
    ]

    if not matched:
        raise ValueError(f"No episodes found for show_id {show_id}")

    show_name = matched[0]["show_name"]
    season_rows = sorted(
        [row for row in matched if row["season_number"] == season],
        key=lambda row: row["watched_dt"],
    )
    all_episodes = {row["episode_number"] for row in season_rows}
    seen = set()
    first_watch = []
    complete = False
    for entry in season_rows:
        if complete:
            break
        if entry["episode_number"] in seen:
            continue
        first_watch.append(entry)
        seen.add(entry["episode_number"])
        if seen >= all_episodes:
            complete = True
    first_watch.sort(key=lambda row: row["episode_number"])

    if not first_watch:
        raise ValueError(
            f"No first-watch episodes found for {show_name!r} "
            f"(show_id {show_id}) season {season}"
        )

    return first_watch


def generate_target_times(episodes, start_dt, end_dt):
    """Return one random end time per episode, spread across equal slots in order."""
    n = len(episodes)
    if n == 0:
        return []

    range_size = end_dt - start_dt
    durations = [row_duration(episode) for episode in episodes]
    total_duration = sum(durations, timedelta())

    if total_duration > range_size:
        raise ValueError(
            f"Total episode runtime ({total_duration}) exceeds date range "
            f"({range_size})"
        )

    slot_size = range_size / n
    target_times = []

    for episode, duration in zip(episodes, durations):
        if duration > slot_size:
            raise ValueError(
                f"{row_title(episode)} runtime ({duration}) exceeds slot size "
                f"({slot_size})"
            )
        slot_index = len(target_times)
        slot_start = start_dt + slot_size * slot_index
        offset = random.random() * (slot_size - duration)
        target_times.append(slot_start + duration + offset)

    return target_times


def print_timetable(episodes, target_times):
    """Print one old -> new line per episode."""
    for episode, target_time in zip(episodes, target_times):
        print(
            f"{row_title(episode)}: {episode['watched_at']} -> "
            f"{to_trakt_iso(target_time)}"
        )


def confirm_apply():
    """Return True only when the user approves with y/yes."""
    answer = input("Apply these changes? [y/N]: ").strip().casefold()
    return answer in ("y", "yes")


def batch_reschedule(episodes, target_times):
    """Remove all episodes in one call and re-add them at new times."""
    trakt_post(
        "/sync/history/remove",
        {"ids": [episode["history_id"] for episode in episodes]},
    )
    trakt_post(
        "/sync/history",
        {
            "shows": [
                {
                    "ids": {"trakt": episodes[0]["show_id"]},
                    "seasons": [
                        {
                            "number": episodes[0]["season_number"],
                            "episodes": [
                                {
                                    "number": episode["episode_number"],
                                    "watched_at": to_trakt_iso(target_time),
                                }
                                for episode, target_time in zip(episodes, target_times)
                            ],
                        }
                    ],
                }
            ],
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show-name",
        required=True,
        help="Show name from watch history CSV (show_name column)",
    )
    parser.add_argument("--season", type=int, required=True, help="Season number")
    parser.add_argument(
        "--start", required=True, help="Start date (YYYY-MM-DD, UTC start of day)"
    )
    parser.add_argument(
        "--end", required=True, help="End date (YYYY-MM-DD, UTC end of day)"
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Watch history CSV (default: {DEFAULT_CSV})",
    )
    args = parser.parse_args()

    try:
        rows = load_rows(args.csv)
        show_map = build_show_name_map(rows)
        _, show_id = resolve_show(args.show_name, show_map)
        start_dt, end_dt = parse_date_range(args.start, args.end)
        episodes = find_season_rows(rows, show_id, args.season)
        target_times = generate_target_times(episodes, start_dt, end_dt)
        print_timetable(episodes, target_times)
        if not confirm_apply():
            print("Aborted.")
            return

        batch_reschedule(episodes, target_times)
        path, _ = fetch_watch_history()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None

    print(f"Rescheduled {len(episodes)} episode(s).")
    print(f"Refreshed watch history at {path}")
    print("Run detect_conflicts.py if new overlaps may exist.")


if __name__ == "__main__":
    main()
