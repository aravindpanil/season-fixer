"""Fetch Trakt watch history and write to CSV."""

import csv
import time

from trakt.client import TraktRateLimitError, trakt_get
from trakt.paths import DEFAULT_CSV

# Columns in the final watch history CSV
_FIELDNAMES = [
    "type",
    "history_id",
    "watched_at",
    "show_id",
    "show_name",
    "season_number",
    "episode_number",
    "movie_title",
    "runtime",
    "item_trakt_id",
]


def _fetch_pages(history_type):
    page = 1
    items = []
    while True:
        response = trakt_get(
            f"/sync/history/{history_type}",
            {"page": page, "limit": 1000, "extended": "full"},
        )
        items.extend(response.json())
        page_count = int(response.headers.get("X-Pagination-Page-Count", 1))
        if page >= page_count:
            break
        if page >= 5:
            time.sleep(0.35)
        page += 1
    return items


def _episode_row(item):
    show = item["show"]
    episode = item["episode"]
    return {
        "type": "episode",
        "history_id": item["id"],
        "watched_at": item["watched_at"],
        "show_id": show["ids"]["trakt"],
        "show_name": show["title"],
        "season_number": episode["season"],
        "episode_number": episode["number"],
        "movie_title": "",
        "runtime": episode.get("runtime") or "",
        "item_trakt_id": episode["ids"]["trakt"],
    }


def _movie_row(item):
    movie = item["movie"]
    return {
        "type": "movie",
        "history_id": item["id"],
        "watched_at": item["watched_at"],
        "show_id": "",
        "show_name": "",
        "season_number": "",
        "episode_number": "",
        "movie_title": movie["title"],
        "runtime": movie.get("runtime") or "",
        "item_trakt_id": movie["ids"]["trakt"],
    }


def fetch_watch_history():
    """Fetch episode and movie history from Trakt and write to a CSV file.

    Returns (output_path, stats) where stats has episodes, movies, and shows keys.
    """
    episode_rows = [_episode_row(item) for item in _fetch_pages("episodes")]
    movie_rows = [_movie_row(item) for item in _fetch_pages("movies")]
    rows = episode_rows + movie_rows
    rows.sort(key=lambda r: r["watched_at"])

    stats = {
        "episodes": len(episode_rows),
        "movies": len(movie_rows),
        "shows": len({r["show_id"] for r in episode_rows}),
    }

    DEFAULT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return DEFAULT_CSV, stats


if __name__ == "__main__":
    try:
        path, stats = fetch_watch_history()
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None
    print(
        f"Wrote {stats['episodes']} episode(s) from {stats['shows']} show(s) "
        f"and {stats['movies']} movie(s) to {path}"
    )
