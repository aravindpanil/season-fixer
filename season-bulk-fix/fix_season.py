#!/usr/bin/env python3
"""Spread first-watch episode timestamps across a date range and apply to Trakt."""

import argparse
import csv
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from trakt.client import TraktRateLimitError, trakt_get, trakt_post
from trakt.history import fetch_watch_history

INPUT = Path("data/watch_history.csv")
IST = ZoneInfo("Asia/Kolkata")

WINDOW_START = datetime(2018, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

EPISODE_DURATION = timedelta(hours=1)
MOVIE_DURATION = timedelta(hours=3)
MIN_GAP = timedelta(minutes=2)
EVENING_WINDOW_RATIO = 0.85
REMOVE_CHUNK_SIZE = 50
RELEASE_LAG_MIN = 30
RELEASE_LAG_MAX = 45
RELEASE_SPAN_MIN = 60
RELEASE_SPAN_MAX = 90

PHASE_REMOVE = "remove"
PHASE_ADD = "add"
PHASE_COMPLETE = "complete"


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


def apply_state_path(show_id, season_number):
    return Path(f"data/fix_apply_{show_id}_s{season_number}.state.json")


def compute_plan_hash(plan):
    rows = [
        {
            "history_id": row["history_id"],
            "episode_number": row["episode_number"],
            "new_watched_at": row["new_watched_at"],
        }
        for row in plan
    ]
    payload = json.dumps(rows, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load_apply_state(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_apply_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    state["timestamp"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def clear_apply_state(path):
    if path.exists():
        path.unlink()


def phase_status(state, total_chunks):
    phase = state.get("phase", PHASE_REMOVE)
    chunks_done = state.get("chunks_completed", 0)
    removed = len(state.get("removed_ids", []))
    total = state.get("total_to_remove", 0)

    if phase == PHASE_REMOVE:
        return f"remove {chunks_done}/{total_chunks} chunks ({removed}/{total} ids removed), add not started"
    if phase == PHASE_ADD:
        return f"remove complete ({removed}/{total} ids removed), add not started"
    if phase == PHASE_COMPLETE:
        return "remove and add complete"
    return phase


def recovery_message(state_path, state, total_chunks):
    status = phase_status(state, total_chunks)
    return (
        f"{status}. Resume with the same args plus --resume-apply "
        f"(state: {state_path.name})"
    )


def sync_deleted_count(body):
    deleted = body.get("deleted", {})
    return sum(
        deleted.get(key, 0)
        for key in ("movies", "episodes", "shows", "seasons", "people", "lists")
    )


def sync_not_found_ids(body):
    not_found = body.get("not_found", {})
    ids = not_found.get("ids")
    return ids or []


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


def remove_history(history_ids, *, state_path, state):
    chunks = [
        history_ids[index : index + REMOVE_CHUNK_SIZE]
        for index in range(0, len(history_ids), REMOVE_CHUNK_SIZE)
    ]
    total_chunks = len(chunks)
    start_chunk = state.get("chunks_completed", 0)

    if start_chunk >= total_chunks:
        state["phase"] = PHASE_ADD
        save_apply_state(state_path, state)
        return

    for chunk_index in range(start_chunk, total_chunks):
        chunk = chunks[chunk_index]
        chunk_num = chunk_index + 1
        context = f"removing {len(chunk)} history entries, chunk {chunk_num}/{total_chunks}"
        response = trakt_post(
            "/sync/history/remove",
            {"ids": chunk},
            context=context,
            timeout=120,
            phase=phase_status(state, total_chunks),
            recovery=recovery_message(state_path, state, total_chunks),
        )
        body = response.json()

        not_found_ids = sync_not_found_ids(body)
        if not_found_ids:
            raise SystemExit(
                f"Remove failed on chunk {chunk_num}/{total_chunks}: "
                f"{len(not_found_ids)} history id(s) not found "
                f"(first few: {not_found_ids[:5]}). Aborting before add."
            )

        deleted_count = sync_deleted_count(body)
        if deleted_count < len(chunk):
            raise SystemExit(
                f"Remove incomplete on chunk {chunk_num}/{total_chunks}: "
                f"expected {len(chunk)} deleted, got {deleted_count}. Aborting before add."
            )

        state.setdefault("removed_ids", []).extend(chunk)
        state["chunks_completed"] = chunk_num
        state["phase"] = PHASE_REMOVE
        save_apply_state(state_path, state)

    state["phase"] = PHASE_ADD
    save_apply_state(state_path, state)


def add_history(show_id, season_number, episodes, *, state_path, state, total_chunks):
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
    response = trakt_post(
        "/sync/history",
        payload,
        context=f"adding {len(episodes)} episodes for show_id={show_id} season={season_number}",
        timeout=120,
        phase=phase_status(state, total_chunks),
        recovery=recovery_message(state_path, state, total_chunks),
    )
    body = response.json()
    added = body.get("added", {})
    episodes_added = added.get("episodes", 0)
    if episodes_added < len(episodes):
        raise SystemExit(
            f"Add incomplete: expected {len(episodes)} episodes, added {episodes_added}. "
            f"State saved at {state_path}. Re-run with --resume-apply after checking Trakt."
        )

    state["phase"] = PHASE_COMPLETE
    save_apply_state(state_path, state)


def fetch_season_premiere(show_id, season_number):
    response = trakt_get(
        f"/shows/{show_id}/seasons/{season_number}/episodes",
        {"extended": "full"},
        context=f"fetching season {season_number} episodes for premiere date",
    )
    episodes = sorted(response.json(), key=lambda episode: episode["number"])
    for episode in episodes:
        if episode.get("first_aired"):
            return parse_dt(episode["first_aired"]).date()
    return None


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

    first_watch = split_first_watch(season_entries)
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


def prompt_date(label):
    while True:
        try:
            value = input(f"{label} (IST, YYYY-MM-DD): ").strip()
        except EOFError:
            raise SystemExit("\nCancelled.")
        if not value:
            print("Enter a date in YYYY-MM-DD format.")
            continue
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            print("Invalid date. Use YYYY-MM-DD.")


def prompt_yes_no(prompt, default=True):
    suffix = "Y/n" if default else "y/N"
    while True:
        try:
            value = input(f"{prompt} [{suffix}]: ").strip().lower()
        except EOFError:
            raise SystemExit("\nCancelled.")
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Enter y or n.")


def prompt_custom_dates():
    start_date = prompt_date("Start date")
    end_date = prompt_date("End date")
    if end_date < start_date:
        raise SystemExit("--end must be on or after --start.")
    return start_date, end_date


def apply_plan(
    show_id,
    season_number,
    plan,
    preview_path,
    *,
    resume=False,
    refresh_after=False,
    start_date=None,
    end_date=None,
    date_mode=None,
):
    state_path = apply_state_path(show_id, season_number)
    plan_hash = compute_plan_hash(plan)
    history_ids = [row["history_id"] for row in plan]
    total_chunks = max(1, (len(history_ids) + REMOVE_CHUNK_SIZE - 1) // REMOVE_CHUNK_SIZE)

    if resume:
        state = load_apply_state(state_path)
        if not state:
            raise SystemExit(f"No apply state at {state_path}. Run --apply first.")
        if state.get("plan_hash") != plan_hash:
            raise SystemExit(
                "Plan hash mismatch between preview and saved state. "
                "Re-run with the same --show/--show-id, --season, and --seed, "
                "or delete the state file and start over."
            )
        if state.get("phase") == PHASE_COMPLETE:
            print(f"Apply already complete (state: {state_path}).")
            clear_apply_state(state_path)
            return
        print(f"Resuming apply from {state_path} ({phase_status(state, total_chunks)})")
    else:
        existing = load_apply_state(state_path)
        if existing and existing.get("phase") not in {None, PHASE_COMPLETE}:
            raise SystemExit(
                f"Incomplete apply found at {state_path} ({phase_status(existing, total_chunks)}). "
                "Use --resume-apply to continue or delete the state file to start over."
            )
        state = {
            "phase": PHASE_REMOVE,
            "show_id": show_id,
            "season_number": season_number,
            "plan_hash": plan_hash,
            "preview_path": str(preview_path),
            "removed_ids": [],
            "total_to_remove": len(plan),
            "chunks_completed": 0,
            "show_name": plan[0]["show_name"],
        }
        if start_date is not None:
            state["start_date"] = start_date.isoformat()
        if end_date is not None:
            state["end_date"] = end_date.isoformat()
        if date_mode is not None:
            state["date_mode"] = date_mode
        save_apply_state(state_path, state)

    try:
        if state.get("phase") == PHASE_REMOVE:
            remove_history(history_ids, state_path=state_path, state=state)
        if state.get("phase") == PHASE_ADD:
            add_history(
                show_id,
                season_number,
                plan,
                state_path=state_path,
                state=state,
                total_chunks=total_chunks,
            )
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None

    clear_apply_state(state_path)
    print(f"Updated {len(plan)} episode(s) on Trakt.")

    if refresh_after:
        print("\nRefreshing local watch history...")
        try:
            path = fetch_watch_history()
        except TraktRateLimitError as exc:
            raise SystemExit(
                f"{exc}\n\nApply succeeded on Trakt. Local CSV was not refreshed; "
                "run fetch_history.py after the rate limit clears."
            ) from None
        print(f"Refreshed watch history at {path}")
    else:
        print("\nRun fetch_history.py to refresh local watch history.")

    if preview_path.exists():
        preview_path.unlink()
        print(f"Removed preview file {preview_path}.")


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


def main():
    args = parse_args()
    rows = load_rows()
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
        preview_path = print_plan(
            plan, show_id, args.season, start_date, end_date, premiere=None
        )
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
