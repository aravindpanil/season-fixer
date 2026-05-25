"""Fetch Trakt watch history and write to CSV."""

import csv
from pathlib import Path

from trakt.client import maybe_pause_for_get_pagination, trakt_get

DEFAULT_OUTPUT = Path("data/watch_history.csv")

_FIELDNAMES = [
    "type",
    "history_id",
    "watched_at",
    "show_id",
    "show_name",
    "season_number",
    "episode_number",
    "episode_trakt_id",
    "movie_trakt_id",
    "movie_title",
]


def _fetch_pages(history_type):
    page = 1
    items = []
    while True:
        response = trakt_get(
            f"/sync/history/{history_type}",
            {"page": page, "limit": 1000},
            context=f"fetching {history_type} history page {page}",
            recovery="Re-run fetch_history.py after the rate limit clears.",
        )
        items.extend(response.json())
        page_count = int(response.headers.get("X-Pagination-Page-Count", 1))
        if page >= page_count:
            break
        maybe_pause_for_get_pagination(response, page)
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
        "episode_trakt_id": episode["ids"]["trakt"],
        "movie_trakt_id": "",
        "movie_title": "",
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
        "episode_trakt_id": "",
        "movie_trakt_id": movie["ids"]["trakt"],
        "movie_title": movie["title"],
    }


def fetch_watch_history(output=DEFAULT_OUTPUT):
    """Fetch episode and movie history from Trakt and write to a CSV file."""
    rows = [_episode_row(item) for item in _fetch_pages("episodes")]
    rows.extend(_movie_row(item) for item in _fetch_pages("movies"))
    rows.sort(key=lambda r: r["watched_at"])

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return output
