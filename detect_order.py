#!/usr/bin/env python3
"""Detect out-of-order episode watches in Trakt history."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from trakt.csv_to_python import load_rows
from trakt.paths import DEFAULT_CSV
from trakt.intervals import row_title

OUTPUT = Path(__file__).resolve().parent / "data" / "flagged_order.csv"

_CSV_FIELDNAMES = [
    "history_id",
    "show_name",
    "season_number",
    "episode_number",
    "watched_at",
    "violation_type",
    "expected_after_title",
    "expected_after_watched_at",
    "action",
]


def parse_exclusion(value):
    """Parse ``show_id:season:episode`` into a tuple of ints."""
    parts = value.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"Expected show_id:season:episode, got {value!r}"
        )
    try:
        return tuple(int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Expected show_id:season:episode, got {value!r}"
        ) from exc


def episode_key(row):
    return (row["show_id"], row["season_number"], row["episode_number"])


def season_episode_key(row):
    return (row["season_number"], row["episode_number"])


def split_first_watch(entries):
    """Split episode entries into (first_watch, rewatches) for one season."""
    entries = sorted(entries, key=lambda e: e["watched_dt"])
    all_episodes = {e["episode_number"] for e in entries}
    seen = set()
    first_watch = []
    rewatches = []
    complete = False

    for entry in entries:
        if complete:
            rewatches.append(entry)
            continue
        if entry["episode_number"] in seen:
            rewatches.append(entry)
            continue
        first_watch.append(entry)
        seen.add(entry["episode_number"])
        if seen >= all_episodes:
            complete = True

    return first_watch, rewatches


def _first_watch_by_episode(entries):
    """Return earliest first-watch row per ``(season, episode)`` for a show."""
    by_key = {}
    for row in entries:
        key = season_episode_key(row)
        if key not in by_key or row["watched_dt"] < by_key[key]["watched_dt"]:
            by_key[key] = row
    return by_key


def _late_watch_violations(first_watch):
    """Yield episodes watched too late — after the next episode in sequence."""
    flagged = set()
    for j, row in enumerate(first_watch):
        for i in range(j):
            earlier = first_watch[i]
            if earlier["episode_number"] != row["episode_number"] + 1:
                continue
            if earlier["history_id"] in flagged:
                continue
            flagged.add(row["history_id"])
            yield row, earlier, "late_watch"
            break


def _skip_ahead_violations(first_watch, late_watch_ids=None):
    """Yield skip-ahead episodes watched before a lower-numbered first watch."""
    late_watch_ids = late_watch_ids or set()
    n = len(first_watch)
    for i, row in enumerate(first_watch):
        later_numbers = [
            first_watch[j]["episode_number"] for j in range(i + 1, n)
        ]
        if not later_numbers:
            continue
        later_min = min(later_numbers)
        if row["episode_number"] <= later_min:
            continue
        later_lower = [
            first_watch[j]
            for j in range(i + 1, n)
            if first_watch[j]["episode_number"] < row["episode_number"]
        ]
        if any(ep["history_id"] in late_watch_ids for ep in later_lower):
            continue
        if any(
            row["episode_number"] == ep["episode_number"] + 1 for ep in later_lower
        ):
            continue
        expected_after = max(later_lower, key=lambda r: r["episode_number"])
        yield row, expected_after, "skip_ahead"


def _same_season_violations(episodes, exclusions):
    """Yield ``(row, expected_after_row, violation_type)`` for same-season issues."""
    by_show_season = defaultdict(list)
    for row in episodes:
        if episode_key(row) in exclusions:
            continue
        by_show_season[(row["show_id"], row["season_number"])].append(row)

    for (_, _), season_entries in sorted(by_show_season.items()):
        first_watch, _ = split_first_watch(season_entries)
        late_watch_ids = {
            row["history_id"] for row, _, _ in _late_watch_violations(first_watch)
        }
        skip_ahead_ids = {
            row["history_id"]
            for row, _, _ in _skip_ahead_violations(first_watch, late_watch_ids)
        }
        for row, expected_after, violation_type in _late_watch_violations(first_watch):
            yield row, expected_after, violation_type
        for row, expected_after, violation_type in _skip_ahead_violations(
            first_watch, late_watch_ids
        ):
            yield row, expected_after, violation_type
        prev_episode = None
        prev_row = None
        for row in first_watch:
            episode_number = row["episode_number"]
            if prev_episode is not None and episode_number < prev_episode:
                if prev_row["history_id"] in skip_ahead_ids:
                    continue
                yield row, prev_row, "same_season"
            prev_episode = episode_number
            prev_row = row


def detect_order(episodes, exclusions=None):
    """Return out-of-order first-watch entries grouped by show and season."""
    exclusions = exclusions or set()
    return [row for row, _, _ in _same_season_violations(episodes, exclusions)]


def _cross_season_violations(episodes, exclusions):
    """Yield ``(row, expected_after_row, violation_type)`` for cross-show issues."""
    by_show = defaultdict(list)
    for row in episodes:
        if episode_key(row) in exclusions:
            continue
        by_show[row["show_id"]].append(row)

    for show_entries in by_show.values():
        first_watch = list(_first_watch_by_episode(show_entries).values())
        first_watch.sort(key=lambda row: row["watched_dt"])

        max_key = None
        max_row = None
        for row in first_watch:
            key = season_episode_key(row)
            if max_key is not None and key < max_key:
                yield row, max_row, "cross_season"
            if max_key is None or key > max_key:
                max_key = key
                max_row = row


def _is_cross_season_violation(row, expected_after):
    return expected_after["season_number"] > row["season_number"]


def detect_cross_season_order(episodes, exclusions=None):
    """Return out-of-order first-watch entries across seasons within a show."""
    exclusions = exclusions or set()
    return [
        row
        for row, expected_after, _ in _cross_season_violations(episodes, exclusions)
        if _is_cross_season_violation(row, expected_after)
    ]


def collect_violations(episodes, exclusions=None):
    """Return deduplicated violation dicts with expected-after metadata."""
    exclusions = exclusions or set()
    violations = []
    seen = set()

    for row, expected_after, violation_type in _same_season_violations(
        episodes, exclusions
    ):
        if row["history_id"] in seen:
            continue
        seen.add(row["history_id"])
        violations.append(
            {
                "row": row,
                "expected_after_row": expected_after,
                "violation_type": violation_type,
            }
        )

    for row, expected_after, violation_type in _cross_season_violations(
        episodes, exclusions
    ):
        if not _is_cross_season_violation(row, expected_after):
            continue
        if row["history_id"] in seen:
            continue
        seen.add(row["history_id"])
        violations.append(
            {
                "row": row,
                "expected_after_row": expected_after,
                "violation_type": violation_type,
            }
        )

    return violations


def violations_to_csv_rows(violations, existing_actions=None):
    """Build CSV row dicts from ``collect_violations`` output."""
    existing_actions = existing_actions or {}
    rows = []
    for violation in violations:
        row = violation["row"]
        expected_after = violation["expected_after_row"]
        history_id = row["history_id"]
        rows.append(
            {
                "history_id": history_id,
                "show_name": row["show_name"],
                "season_number": row["season_number"],
                "episode_number": row["episode_number"],
                "watched_at": row["watched_at"],
                "violation_type": violation["violation_type"],
                "expected_after_title": row_title(expected_after),
                "expected_after_watched_at": expected_after["watched_at"],
                "action": existing_actions.get(history_id, ""),
            }
        )
    return rows


def load_existing_actions(path):
    """Return ``history_id -> action`` from an existing flagged-order CSV."""
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {
            int(row["history_id"]): row.get("action", "")
            for row in csv.DictReader(f)
        }


def write_violations_csv(violations, output_path):
    """Write violations to CSV, preserving existing ``action`` values."""
    output_path = Path(output_path)
    existing_actions = load_existing_actions(output_path)
    csv_rows = violations_to_csv_rows(violations, existing_actions)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(csv_rows)

    return csv_rows


def print_summary(violations):
    same_season = sum(
        1 for violation in violations if violation["violation_type"] == "same_season"
    )
    skip_ahead = sum(
        1 for violation in violations if violation["violation_type"] == "skip_ahead"
    )
    late_watch = sum(
        1 for violation in violations if violation["violation_type"] == "late_watch"
    )
    cross_season = sum(
        1 for violation in violations if violation["violation_type"] == "cross_season"
    )
    print(
        f"Found {len(violations)} out-of-order first-watch episode(s) "
        f"({same_season} same-season, {skip_ahead} skip-ahead, {late_watch} late-watch, "
        f"{cross_season} cross-season)."
    )
    for violation in violations:
        row = violation["row"]
        expected_after = violation["expected_after_row"]
        if violation["violation_type"] == "late_watch":
            relation = "watched after"
        else:
            relation = "watched before"
        print(
            f"  [{violation['violation_type']}] "
            f"{row_title(row)} ({row['watched_at']}) "
            f"— {relation} {row_title(expected_after)} "
            f"({expected_after['watched_at']})"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_CSV, help="Watch history CSV")
    parser.add_argument(
        "--exclude",
        action="append",
        type=parse_exclusion,
        default=[],
        metavar="SHOW_ID:SEASON:EPISODE",
        help="Skip a specific episode from order checks (repeatable)",
    )
    args = parser.parse_args()

    episodes = [r for r in load_rows(args.input) if r["type"] == "episode"]
    exclusions = set(args.exclude)
    violations = collect_violations(episodes, exclusions=exclusions)
    print_summary(violations)

    csv_rows = write_violations_csv(violations, OUTPUT)
    print(f"Wrote {len(csv_rows)} violation(s) to {OUTPUT}")


if __name__ == "__main__":
    main()
