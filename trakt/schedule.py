"""IST-biased evening scheduling and clash detection for Trakt history fixes."""

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from trakt.dt import to_trakt_iso

IST = ZoneInfo("Asia/Kolkata")

EPISODE_DURATION = timedelta(hours=1)
MOVIE_DURATION = timedelta(hours=3)
MIN_GAP = timedelta(minutes=2)
EVENING_WINDOW_RATIO = 0.85


def watch_interval(watched_at, duration=EPISODE_DURATION):
    """Return ``(start, end)`` for a watch event that ends at ``watched_at``."""
    return watched_at - duration, watched_at


def intervals_overlap(a_start, a_end, b_start, b_end):
    """Return ``True`` when two intervals share any time."""
    return a_start < b_end and b_start < a_end


def build_blocked_intervals(rows, exclude_history_ids):
    """Build ``(start, end)`` clash intervals from all history except excluded IDs.

    Episodes occupy ``EPISODE_DURATION``; movies occupy ``MOVIE_DURATION``.
    The excluded IDs are the entries being rescheduled — they must not block
    themselves.
    """
    blocked = []
    for row in rows:
        if row["history_id"] in exclude_history_ids:
            continue
        duration = MOVIE_DURATION if row["type"] == "movie" else EPISODE_DURATION
        start, end = watch_interval(row["watched_dt"], duration)
        blocked.append((start, end))
    return blocked


def pick_evening_time(day, rng):
    """Return a random IST evening timestamp on ``day``.

    ~85 % of picks land in the evening window (19:00–02:00 the next day);
    the remaining 15 % are spread across daytime hours. All minutes are
    rounded to 0/15/30/45.
    """
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
    """Spread ``count`` episodes roughly evenly across ``start_date``..``end_date``.

    Each episode offset gets ±1 day of jitter and is clamped so the sequence
    stays monotonically non-decreasing.
    """
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


def _has_clash(completion_time, blocked, scheduled):
    """Return ``True`` when placing an episode at ``completion_time`` overlaps any interval."""
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
    """Find a clash-free completion timestamp on or near ``day``.

    Attempts random evening picks first (40 tries on target day), then
    progressively expands the search window. ``earliest`` enforces a lower
    bound so episodes stay in episode-number order.

    Raises ``SystemExit`` when no slot can be found.
    """
    def valid(candidate):
        if earliest is not None and candidate < earliest:
            return False
        return not _has_clash(candidate, blocked, scheduled)

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
    """Assign clash-free IST timestamps to ``entries`` spread across ``start_date``..``end_date``.

    ``entries`` are sorted by ``episode_number`` before scheduling so that
    episode order is always preserved. Returns a list of plan dicts with
    ``old_watched_at``, ``new_watched_at``, ``new_watched_dt``, and the
    original row fields needed for the Trakt sync payload.
    """
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
