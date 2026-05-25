#!/usr/bin/env python3
"""Detect suspicious bulk-import binge patterns in Trakt watch history."""

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trakt.csv import load_episodes, split_first_watch

OUTPUT = Path("data/flagged_seasons.csv")
EXCLUSIONS = Path("data/exclusions.json")

WINDOW_START = datetime(2018, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

SHORT_SPAN_DAYS = 2
SHORT_SPAN_MIN_EPS = 8
HEAVY_BINGE_MIN_EPS = 4
HEAVY_BINGE_HOURS = 6
BINGE_GAP_MINUTES = 15
OUTLIER_CLUSTER_RATIO = 0.80
OUTLIER_CLUSTER_HOURS = 48
OUTLIER_DISTANCE_DAYS = 30


def in_window(dt):
    return WINDOW_START <= dt <= WINDOW_END


def load_exclusions(episodes):
    """Load exclusions from data/exclusions.json.

    Entries may identify a show by ``show_id`` (int) or ``show_name``
    (case-insensitive string).  Show-name entries are resolved against the
    loaded episode data; unresolvable names emit a warning and are skipped.

    Returns a dict mapping ``(show_id, season_number)`` tuples to a reason
    string (empty string when no reason was provided).
    """
    if not EXCLUSIONS.exists():
        return {}

    with EXCLUSIONS.open(encoding="utf-8") as f:
        entries = json.load(f)

    # Build a case-insensitive show-name → show_id index from episode data.
    name_to_id: dict[str, int] = {}
    for ep in episodes:
        name_to_id[ep["show_name"].lower()] = ep["show_id"]

    exclusions: dict[tuple[int, int], str] = {}
    for entry in entries:
        season = entry.get("season")
        if season is None:
            print(f"Warning: exclusion entry missing 'season', skipping: {entry}")
            continue
        reason = entry.get("reason", "")

        if "show_id" in entry:
            key = (int(entry["show_id"]), int(season))
            exclusions[key] = reason
        elif "show_name" in entry:
            show_id = name_to_id.get(entry["show_name"].lower())
            if show_id is None:
                print(
                    f"Warning: exclusion for '{entry['show_name']}' S{int(season):02d}"
                    " — show not found in history, skipping."
                )
                continue
            exclusions[(show_id, int(season))] = reason
        else:
            print(f"Warning: exclusion entry missing 'show_id' or 'show_name', skipping: {entry}")

    return exclusions


def span_days(start_dt, end_dt):
    return (end_dt.date() - start_dt.date()).days


def max_binge_block_hours(entries):
    if not entries:
        return 0.0
    sorted_entries = sorted(entries, key=lambda e: e["watched_dt"])
    max_block = 1
    block_start = 0

    for i in range(1, len(sorted_entries)):
        gap = sorted_entries[i]["watched_dt"] - sorted_entries[i - 1]["watched_dt"]
        if gap <= timedelta(minutes=BINGE_GAP_MINUTES):
            continue
        block_len = i - block_start
        max_block = max(max_block, block_len)
        block_start = i

    max_block = max(max_block, len(sorted_entries) - block_start)
    if max_block <= 1:
        return 0.0

    # Approximate block duration from first to last watch in longest block.
    best_hours = 0.0
    block_start = 0
    for i in range(1, len(sorted_entries)):
        gap = sorted_entries[i]["watched_dt"] - sorted_entries[i - 1]["watched_dt"]
        if gap <= timedelta(minutes=BINGE_GAP_MINUTES):
            continue
        block = sorted_entries[block_start:i]
        if len(block) >= HEAVY_BINGE_MIN_EPS:
            hours = (block[-1]["watched_dt"] - block[0]["watched_dt"]).total_seconds() / 3600
            best_hours = max(best_hours, hours)
        block_start = i

    block = sorted_entries[block_start:]
    if len(block) >= HEAVY_BINGE_MIN_EPS:
        hours = (block[-1]["watched_dt"] - block[0]["watched_dt"]).total_seconds() / 3600
        best_hours = max(best_hours, hours)

    return best_hours


def has_heavy_binge(entries):
    if len(entries) < HEAVY_BINGE_MIN_EPS:
        return False
    sorted_entries = sorted(entries, key=lambda e: e["watched_dt"])
    block_start = 0
    for i in range(1, len(sorted_entries)):
        gap = sorted_entries[i]["watched_dt"] - sorted_entries[i - 1]["watched_dt"]
        if gap <= timedelta(minutes=BINGE_GAP_MINUTES): # Are episodes watched within 15 minutes of each other?
            continue
        block = sorted_entries[block_start:i]
        if len(block) >= HEAVY_BINGE_MIN_EPS: 
            hours = (block[-1]["watched_dt"] - block[0]["watched_dt"]).total_seconds() / 3600
            if hours <= HEAVY_BINGE_HOURS: # Is the binge less than 6 hours?
                return True
        block_start = i

    block = sorted_entries[block_start:]
    if len(block) >= HEAVY_BINGE_MIN_EPS:
        hours = (block[-1]["watched_dt"] - block[0]["watched_dt"]).total_seconds() / 3600
        if hours <= HEAVY_BINGE_HOURS:
            return True
    return False


def has_outlier_cluster(entries):
    if len(entries) < 2:
        return False
    sorted_entries = sorted(entries, key=lambda e: e["watched_dt"])
    window = timedelta(hours=OUTLIER_CLUSTER_HOURS)
    min_in_cluster = max(1, int(len(sorted_entries) * OUTLIER_CLUSTER_RATIO + 0.999))

    best_cluster = set()
    for anchor in sorted_entries:
        anchor_dt = anchor["watched_dt"]
        cluster = {
            e["history_id"]
            for e in sorted_entries
            if anchor_dt <= e["watched_dt"] <= anchor_dt + window
        }
        if len(cluster) > len(best_cluster):
            best_cluster = cluster

    if len(best_cluster) < min_in_cluster:
        return False

    outliers = [e for e in sorted_entries if e["history_id"] not in best_cluster]
    if not outliers:
        return False

    cluster_entries = [e for e in sorted_entries if e["history_id"] in best_cluster]
    cluster_start = min(e["watched_dt"] for e in cluster_entries)
    cluster_end = max(e["watched_dt"] for e in cluster_entries)
    min_distance = timedelta(days=OUTLIER_DISTANCE_DAYS)

    for outlier in outliers:
        dt = outlier["watched_dt"]
        if dt < cluster_start:
            if cluster_start - dt >= min_distance:
                return True
        elif dt > cluster_end:
            if dt - cluster_end >= min_distance:
                return True
        else:
            # Inside cluster time range but not counted in best 48h window — treat as outlier.
            return True

    return False


def detect_flags(entries):
    flags = []

    # If season watched in less than 2 days, flag it as short span
    if len(entries) >= SHORT_SPAN_MIN_EPS:
        start = min(e["watched_dt"] for e in entries)
        end = max(e["watched_dt"] for e in entries)
        if span_days(start, end) <= SHORT_SPAN_DAYS:
            flags.append("short_span")

    if has_heavy_binge(entries):
        flags.append("heavy_binge")

    if has_outlier_cluster(entries):
        flags.append("outlier_cluster")

    return flags


def analyze_season(entries, exclusions=None):
    if exclusions is None:
        exclusions = {}

    first_watch, rewatches = split_first_watch(entries)
    window_first = [e for e in first_watch if in_window(e["watched_dt"])] # Filter out episodes outside the window

    if not window_first:
        return None

    # Calculate the start and end dates of the season
    start = min(e["watched_dt"] for e in window_first)
    end = max(e["watched_dt"] for e in window_first)
    
    flags = detect_flags(window_first)

    show_id = entries[0]["show_id"]
    season_number = entries[0]["season_number"]
    exclusion_key = (show_id, season_number)
    excluded = exclusion_key in exclusions

    return {
        "show_id": show_id,
        "show_name": entries[0]["show_name"],
        "season_number": season_number,
        "episode_count": len(window_first),
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "span_days": span_days(start, end),
        "max_binge_block_hours": round(max_binge_block_hours(window_first), 1),
        "rewatch_count": len(rewatches),
        "flags": flags,
        "history_ids": [e["history_id"] for e in window_first],
        "flagged": bool(flags),
        "excluded": excluded,
        "exclusion_reason": exclusions.get(exclusion_key, ""),
    }


def print_report(results):
    flagged = [r for r in results if r["flagged"] and not r["excluded"]]
    print(f"\nAnalyzed {len(results)} season(s) with first-watch activity in 2018–2024.")
    print(f"Flagged {len(flagged)} suspicious season(s).\n")

    by_show = defaultdict(list)
    for result in sorted(results, key=lambda r: (r["show_name"].lower(), r["season_number"])):
        by_show[result["show_name"]].append(result)

    for show_name, seasons in by_show.items():
        print(show_name)
        for season in seasons:
            if season["excluded"]:
                reason = season["exclusion_reason"] or "-"
                status_part = f"excluded: {reason}"
            elif season["flagged"]:
                flags = ",".join(season["flags"])
                status_part = f"FLAGGED: {flags}"
            else:
                status_part = "ok: -"
            print(
                f"  S{season['season_number']:02d}  {season['episode_count']} eps  "
                f"{season['start_date']} → {season['end_date']}  "
                f"({season['span_days']} days, max binge {season['max_binge_block_hours']}h)  "
                f"rewatches={season['rewatch_count']}  [{status_part}]"
            )
        print()


def main():
    episodes = load_episodes()
    exclusions = load_exclusions(episodes)
    grouped = defaultdict(list)
    for episode in episodes:
        grouped[(episode["show_id"], episode["season_number"])].append(episode)

    results = []
    for entries in grouped.values():
        result = analyze_season(entries, exclusions)
        if result:
            results.append(result)

    print_report(results)

    flagged_rows = [r for r in results if r["flagged"] and not r["excluded"]]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "show_id",
                "show_name",
                "season_number",
                "episode_count",
                "start_date",
                "end_date",
                "span_days",
                "max_binge_block_hours",
                "rewatch_count",
                "flags",
                "history_ids",
            ],
        )
        writer.writeheader()
        for row in flagged_rows:
            writer.writerow(
                {
                    **{k: row[k] for k in writer.fieldnames if k not in ("flags", "history_ids")},
                    "flags": ",".join(row["flags"]),
                    "history_ids": ",".join(str(i) for i in row["history_ids"]),
                }
            )

    print(f"Wrote {len(flagged_rows)} flagged season(s) to {OUTPUT}")


if __name__ == "__main__":
    main()
