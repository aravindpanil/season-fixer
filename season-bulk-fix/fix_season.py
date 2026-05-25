#!/usr/bin/env python3
"""Spread first-watch episode timestamps across a date range and apply to Trakt."""

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trakt.cli import prompt_custom_dates, prompt_yes_no
from trakt.csv import find_show, load_rows, split_first_watch
from trakt.episodes import fetch_season_premiere
from trakt.schedule import build_blocked_intervals, schedule_episodes
from trakt.sync import apply_plan, apply_state_path, load_apply_state, write_preview

INPUT = Path("data/watch_history.csv")

WINDOW_START = datetime(2018, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

RELEASE_LAG_MIN = 30
RELEASE_LAG_MAX = 45
RELEASE_SPAN_MIN = 60
RELEASE_SPAN_MAX = 90


def in_window(dt):
    return WINDOW_START <= dt <= WINDOW_END


def release_date_range(premiere_date, seed):
    rng = random.Random(seed)
    start_date = premiere_date + timedelta(days=rng.randint(RELEASE_LAG_MIN, RELEASE_LAG_MAX))
    end_date = start_date + timedelta(days=rng.randint(RELEASE_SPAN_MIN, RELEASE_SPAN_MAX))
    return start_date, end_date


def prepare_season(rows, show_name, show_id, season_number):
    show_id = find_show(rows, show_name=show_name, show_id=show_id)
    season_entries = [
        row
        for row in rows
        if row["type"] == "episode"
        and row["show_id"] == show_id
        and row["season_number"] == season_number
    ]
    if not season_entries:
        raise SystemExit(f"No episode history found for show_id={show_id} season={season_number}.")

    first_watch, _ = split_first_watch(season_entries)
    to_fix = [entry for entry in first_watch if in_window(entry["watched_dt"])]
    if not to_fix:
        raise SystemExit("No first-watch entries in 2018–2024 for this season.")

    exclude_ids = {entry["history_id"] for entry in to_fix}
    blocked = build_blocked_intervals(rows, exclude_ids)
    show_name = to_fix[0]["show_name"]
    return show_id, to_fix, blocked, show_name


def build_plan(to_fix, blocked, start_date, end_date, seed):
    return schedule_episodes(to_fix, start_date, end_date, blocked, seed)


def print_plan(plan, show_id, season_number, start_date, end_date, *, premiere=None):
    preview_path = write_preview(plan, show_id, season_number)
    show_name = plan[0]["show_name"]
    print(f"\n{show_name} S{season_number:02d}: scheduling {len(plan)} first-watch episode(s)")
    if premiere is not None:
        print(f"Season premiere: {premiere.isoformat()}")
    print(f"Date range (IST): {start_date} → {end_date}")
    print(f"Preview written to {preview_path}")
    for row in plan:
        print(
            f"  E{row['episode_number']:02d}  {row['old_watched_at']}  ->  {row['new_watched_at']}"
        )
    return preview_path


def rebuild_plan_from_state(state, to_fix, blocked, show_id, season_number, seed):
    date_mode = state.get("date_mode")
    if date_mode == "custom":
        start_date = datetime.strptime(state["start_date"], "%Y-%m-%d").date()
        end_date = datetime.strptime(state["end_date"], "%Y-%m-%d").date()
        return build_plan(to_fix, blocked, start_date, end_date, seed), start_date, end_date, date_mode

    premiere = fetch_season_premiere(show_id, season_number)
    if premiere is None:
        raise SystemExit(
            f"Cannot rebuild release-date plan for resume (no premiere on Trakt). "
            f"Check state at {apply_state_path(show_id, season_number)}."
        )
    start_date, end_date = release_date_range(premiere, seed)
    return build_plan(to_fix, blocked, start_date, end_date, seed), start_date, end_date, "release"


def parse_args():
    parser = argparse.ArgumentParser(description="Fix bulk-imported season watch timestamps.")
    parser.add_argument("--show", help="Show title (case-insensitive exact match)")
    parser.add_argument("--show-id", type=int, help="Trakt show ID")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible schedules")
    parser.add_argument(
        "--resume-apply",
        action="store_true",
        help="Continue an interrupted apply using the saved state file (skips prompts)",
    )
    parser.add_argument(
        "--refresh-after-apply",
        action="store_true",
        help="Refresh data/watch_history.csv after a successful apply (may hit GET rate limits)",
    )
    args = parser.parse_args()

    if not args.show and args.show_id is None:
        parser.error("Provide --show or --show-id.")
    if args.show and args.show_id is not None:
        parser.error("Use only one of --show or --show-id.")

    return args


def main():
    args = parse_args()
    rows = load_rows(INPUT)
    show_id, to_fix, blocked, show_name = prepare_season(
        rows, args.show, args.show_id, args.season
    )

    if args.resume_apply:
        state_path = apply_state_path(show_id, args.season)
        state = load_apply_state(state_path)
        if not state:
            raise SystemExit(f"No apply state at {state_path}. Approve a plan first.")
        plan, start_date, end_date, date_mode = rebuild_plan_from_state(
            state, to_fix, blocked, show_id, args.season, args.seed
        )
        preview_path = print_plan(plan, show_id, args.season, start_date, end_date, premiere=None)
        print("\nResuming interrupted apply...")
        apply_plan(
            show_id,
            args.season,
            plan,
            preview_path,
            resume=True,
            refresh_after=args.refresh_after_apply,
            start_date=start_date,
            end_date=end_date,
            date_mode=date_mode,
        )
        return

    premiere = fetch_season_premiere(show_id, args.season)
    if premiere is not None:
        start_date, end_date = release_date_range(premiere, args.seed)
        plan = build_plan(to_fix, blocked, start_date, end_date, args.seed)
        preview_path = print_plan(plan, show_id, args.season, start_date, end_date, premiere=premiere)
        if prompt_yes_no("Apply this release-date plan to Trakt?"):
            print("\nApplying changes to Trakt...")
            apply_plan(
                show_id,
                args.season,
                plan,
                preview_path,
                refresh_after=args.refresh_after_apply,
                start_date=start_date,
                end_date=end_date,
                date_mode="release",
            )
            return
        print("\nEnter custom start/end dates instead.")
    else:
        print(f"\nNo season premiere found on Trakt for {show_name} S{args.season:02d}.")
        print("Enter custom start/end dates instead.")

    start_date, end_date = prompt_custom_dates()
    plan = build_plan(to_fix, blocked, start_date, end_date, args.seed)
    preview_path = print_plan(plan, show_id, args.season, start_date, end_date)
    if prompt_yes_no("Apply this plan to Trakt?"):
        print("\nApplying changes to Trakt...")
        apply_plan(
            show_id,
            args.season,
            plan,
            preview_path,
            refresh_after=args.refresh_after_apply,
            start_date=start_date,
            end_date=end_date,
            date_mode="custom",
        )
    else:
        print("Cancelled. No changes written to Trakt.")


if __name__ == "__main__":
    main()
