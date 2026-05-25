"""Watch-interval helpers shared by detect_conflicts and fix_conflicts."""

from datetime import timedelta

EPISODE_DURATION = timedelta(hours=1)
MOVIE_DURATION = timedelta(hours=3)


def row_duration(row):
    if row["runtime"]:
        return timedelta(minutes=row["runtime"])
    return MOVIE_DURATION if row["type"] == "movie" else EPISODE_DURATION


def row_interval(row):
    end = row["watched_dt"]
    return end - row_duration(row), end


def intervals_overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def merge_intervals(intervals):
    """Merge a sorted list of (start, end) tuples into non-overlapping blocks."""
    if not intervals:
        return []
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if intervals_overlap(prev_start, prev_end, start, end):
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged
