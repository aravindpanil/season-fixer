#!/usr/bin/env python3
"""Spread first-watch episode timestamps across a date range and apply to Trakt."""

import argparse
import csv
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from trakt_auth import refresh_access_token, save_tokens

INPUT = Path("data/watch_history.csv")
BASE = "https://api.trakt.tv"
ENV_PATH = Path(".env")
IST = ZoneInfo("Asia/Kolkata")

WINDOW_START = datetime(2018, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

EPISODE_DURATION = timedelta(hours=1)
MOVIE_DURATION = timedelta(hours=3)
MIN_GAP = timedelta(minutes=2)
EVENING_WINDOW_RATIO = 0.85


def parse_dt(value):
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_trakt_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def in_window(dt):
    return WINDOW_START <= dt <= WINDOW_END


def _headers():
    load_dotenv(ENV_PATH)
    return {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": os.environ["TRAKT_CLIENT_ID"],
        "Authorization": f"Bearer {os.environ['TRAKT_ACCESS_TOKEN']}",
    }


def trakt_request(method, path, json_body=None, _retried=False):
    response = requests.request(
        method,
        f"{BASE}{path}",
        json=json_body,
        headers=_headers(),
        timeout=120,
    )
    if response.status_code == 401 and not _retried:
        tokens = refresh_access_token()
        save_tokens(tokens, ENV_PATH)
        return trakt_request(method, path, json_body, _retried=True)
    response.raise_for_status()
    return response


def load_rows():
    if not INPUT.exists():
        raise SystemExit(f"Missing {INPUT}. Run fetch_history.py first.")
    rows = []
    with INPUT.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["history_id"] = int(row["history_id"])
            row["watched_dt"] = parse_dt(row["watched_at"])
            if row["type"] == "episode":
                row["show_id"] = int(row["show_id"])
                row["season_number"] = int(row["season_number"])
                row["episode_number"] = int(row["episode_number"])
            rows.append(row)
    return rows


def split_first_watch(entries):
    entries = sorted(entries, key=lambda e: e["watched_dt"])
    all_episodes = {e["episode_number"] for e in entries}
    seen = set()
    first_watch = []
    complete = False

    for entry in entries:
        if complete:
            continue
        first_watch.append(entry)
        seen.add(entry["episode_number"])
        if seen >= all_episodes:
            complete = True

    return first_watch


def find_show(rows, show_name=None, show_id=None):
    episodes = [r for r in rows if r["type"] == "episode"]
    if show_id is not None:
        matches = {r["show_id"] for r in episodes if r["show_id"] == show_id}
        if not matches:
            raise SystemExit(f"No show found with id {show_id}.")
        return show_id

    name_lower = show_name.lower()
    matches = {r["show_id"]: r["show_name"] for r in episodes if r["show_name"].lower() == name_lower}
    if not matches:
        raise SystemExit(f"No show found matching {show_name!r}.")
    if len(matches) > 1:
        raise SystemExit(f"Multiple shows match {show_name!r}. Use --show-id instead.")
    return next(iter(matches))


def watch_interval(watched_at, duration=EPISODE_DURATION):
    return watched_at - duration, watched_at


def intervals_overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def build_blocked_intervals(rows, exclude_history_ids):
    blocked = []
    for row in rows:
        if row["history_id"] in exclude_history_ids:
            continue
        duration = MOVIE_DURATION if row["type"] == "movie" else EPISODE_DURATION
        start, end = watch_interval(row["watched_dt"], duration)
        blocked.append((start, end))
    return blocked


def pick_evening_time(day, rng):
    if rng.random() < EVENING_WINDOW_RATIO:
        if rng.random() < 0.8:
            hour = rng.randint(19, 23)
            minute = rng.choice([0, 15, 30, 45] if hour < 23 else [0, 15, 30])
            return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
        next_day = day + timedelta(days=1)
        hour = rng.randint(0, 2)
        minute = rng.choice([0, 15, 30, 45] if hour < 2 else [0, 15, 30])
        return datetime(next_day.year, next_day.month, next_day.day, hour, minute, tzinfo=IST)
    hour = rng.choice(list(range(10, 19)) + list(range(3, 10)))
    minute = rng.choice([0, 15, 30, 45])
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def assign_days(start_date, end_date, count, rng):
    total_days = (end_date - start_date).days
    if count == 1:
        return [start_date + timedelta(days=total_days // 2)]
    days = []
    prev_offset = 0
    for i in range(count):
        offset = round(i * total_days / (count - 1))
        jitter = rng.randint(-1, 1)
        offset = max(0, min(total_days, offset + jitter))
        offset = max(prev_offset, offset)
        prev_offset = offset
        days.append(start_date + timedelta(days=offset))
    return days


def has_clash(completion_time, blocked, scheduled):
    start, end = watch_interval(completion_time)
    for other_start, other_end in blocked + scheduled:
        if intervals_overlap(start, end, other_start, other_end):
            return True
        if abs((end - other_end).total_seconds()) < MIN_GAP.total_seconds():
            return True
        if abs((end - other_start).total_seconds()) < MIN_GAP.total_seconds():
            return True
    return False


def find_completion_time(day, blocked, scheduled, rng, earliest=None):
    def valid(candidate):
        if earliest is not None and candidate < earliest:
            return False
        return not has_clash(candidate, blocked, scheduled)

    for _ in range(40):
        candidate = pick_evening_time(day, rng)
        if valid(candidate):
            return candidate

    if earliest is not None:
        start_offset = max(0, (earliest.date() - day).days)
        for day_offset in range(start_offset, 4):
            for _ in range(20):
                candidate = pick_evening_time(day + timedelta(days=day_offset), rng)
                if valid(candidate):
                    return candidate

        search_day = max(day, earliest.date())
        for day_offset in range(0, 8):
            candidate_day = search_day + timedelta(days=day_offset)
            for hour in range(24):
                for minute in (0, 30):
                    candidate = datetime(
                        candidate_day.year,
                        candidate_day.month,
                        candidate_day.day,
                        hour,
                        minute,
                        tzinfo=IST,
                    )
                    if valid(candidate):
                        return candidate
    else:
        for day_offset in range(-3, 4):
            for _ in range(20):
                candidate = pick_evening_time(day + timedelta(days=day_offset), rng)
                if valid(candidate):
                    return candidate

        for hour in range(24):
            for minute in (0, 30):
                candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
                if valid(candidate):
                    return candidate

    raise SystemExit("Could not find clash-free timestamp. Try a wider date range.")


def schedule_episodes(entries, start_date, end_date, blocked, seed):
    rng = random.Random(seed)
    entries = sorted(entries, key=lambda e: e["episode_number"])
    days = assign_days(start_date, end_date, len(entries), rng)
    scheduled = []
    plan = []

    earliest = None
    for entry, day in zip(entries, days):
        completion = find_completion_time(day, blocked, scheduled, rng, earliest=earliest)
        start, end = watch_interval(completion)
        scheduled.append((start, end))
        earliest = completion + MIN_GAP
        plan.append(
            {
                "history_id": entry["history_id"],
                "show_id": entry["show_id"],
                "show_name": entry["show_name"],
                "season_number": entry["season_number"],
                "episode_number": entry["episode_number"],
                "old_watched_at": entry["watched_at"],
                "new_watched_at": to_trakt_iso(completion),
                "new_watched_dt": completion.astimezone(timezone.utc),
            }
        )

    return plan


def remove_history(history_ids):
    chunk_size = 50
    for i in range(0, len(history_ids), chunk_size):
        chunk = history_ids[i : i + chunk_size]
        trakt_request("POST", "/sync/history/remove", {"ids": chunk})


def add_history(show_id, season_number, episodes):
    payload = {
        "shows": [
            {
                "ids": {"trakt": show_id},
                "seasons": [
                    {
                        "number": season_number,
                        "episodes": [
                            {"number": ep["episode_number"], "watched_at": ep["new_watched_at"]}
                            for ep in episodes
                        ],
                    }
                ],
            }
        ]
    }
    trakt_request("POST", "/sync/history", payload)


def write_preview(plan, show_id, season_number):
    path = Path(f"data/fix_preview_{show_id}_s{season_number}.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "history_id",
                "show_name",
                "season_number",
                "episode_number",
                "old_watched_at",
                "new_watched_at",
            ],
        )
        writer.writeheader()
        for row in plan:
            writer.writerow(
                {
                    "history_id": row["history_id"],
                    "show_name": row["show_name"],
                    "season_number": row["season_number"],
                    "episode_number": row["episode_number"],
                    "old_watched_at": row["old_watched_at"],
                    "new_watched_at": row["new_watched_at"],
                }
            )
    return path


def parse_args():
    parser = argparse.ArgumentParser(description="Fix bulk-imported season watch timestamps.")
    parser.add_argument("--show", help="Show title (case-insensitive exact match)")
    parser.add_argument("--show-id", type=int, help="Trakt show ID")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--start", required=True, help="Start date in IST (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date in IST (YYYY-MM-DD)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible schedules")
    parser.add_argument("--apply", action="store_true", help="Write changes to Trakt (default is dry-run)")
    args = parser.parse_args()

    if not args.show and args.show_id is None:
        parser.error("Provide --show or --show-id.")
    if args.show and args.show_id is not None:
        parser.error("Use only one of --show or --show-id.")

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    if end_date < start_date:
        parser.error("--end must be on or after --start.")

    return args, start_date, end_date


def main():
    args, start_date, end_date = parse_args()
    rows = load_rows()
    show_id = find_show(rows, show_name=args.show, show_id=args.show_id)

    season_entries = [
        r
        for r in rows
        if r["type"] == "episode" and r["show_id"] == show_id and r["season_number"] == args.season
    ]
    if not season_entries:
        raise SystemExit(f"No episode history found for show_id={show_id} season={args.season}.")

    first_watch = split_first_watch(season_entries)
    to_fix = [e for e in first_watch if in_window(e["watched_dt"])]
    if not to_fix:
        raise SystemExit("No first-watch entries in 2018–2024 for this season.")


    exclude_ids = {e["history_id"] for e in to_fix} # Exclude episodes that are already scheduled from the blocked intervals
    blocked = build_blocked_intervals(rows, exclude_ids) 
    plan = schedule_episodes(to_fix, start_date, end_date, blocked, args.seed)
    preview_path = write_preview(plan, show_id, args.season)

    show_name = plan[0]["show_name"]
    print(f"{show_name} S{args.season:02d}: scheduling {len(plan)} first-watch episode(s)")
    print(f"Date range (IST): {start_date} → {end_date}")
    print(f"Preview written to {preview_path}")

    for row in plan:
        print(
            f"  E{row['episode_number']:02d}  {row['old_watched_at']}  ->  {row['new_watched_at']}"
        )

    if not args.apply:
        print("\nDry run only. Re-run with --apply to update Trakt.")
        return

    print("\nApplying changes to Trakt...")
    remove_history([row["history_id"] for row in plan])
    add_history(show_id, args.season, plan)
    print(f"Updated {len(plan)} episode(s). Re-run fetch_history.py to refresh local data.")


if __name__ == "__main__":
    main()
