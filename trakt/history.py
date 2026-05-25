"""Fetch Trakt watch history and write to CSV."""

import csv
import time
from pathlib import Path

from trakt.client import GET_PAGE_PAUSE, maybe_pause_for_get_pagination, trakt_get
from trakt.episodes import fetch_episode_durations, fetch_movie_runtime

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
    "runtime",
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


def _runtime_csv_value(minutes):
    return minutes if minutes else ""


def _enrich_episode_runtimes(rows):
    """Fetch per-episode runtimes and set ``runtime`` on episode rows."""
    episode_rows = [row for row in rows if row["type"] == "episode"]
    seasons = sorted({(row["show_id"], row["season_number"]) for row in episode_rows})
    durations_by_season = {}
    total = len(seasons)
    for index, (show_id, season_number) in enumerate(seasons, start=1):
        print(
            f"Fetching episode runtimes ({index}/{total}): "
            f"show {show_id} season {season_number}"
        )
        durations_by_season[(show_id, season_number)] = fetch_episode_durations(
            show_id, season_number
        )
        if index < total:
            time.sleep(GET_PAGE_PAUSE)
    for row in episode_rows:
        runtime = durations_by_season[(row["show_id"], row["season_number"])].get(
            row["episode_number"]
        )
        row["runtime"] = _runtime_csv_value(runtime)
    return total


def _enrich_movie_runtimes(rows, pause_before_first=False):
    """Fetch per-movie runtimes and set ``runtime`` on movie rows."""
    movie_rows = [row for row in rows if row["type"] == "movie"]
    movie_ids = sorted({row["movie_trakt_id"] for row in movie_rows if row["movie_trakt_id"]})
    runtimes = {}
    total = len(movie_ids)
    for index, movie_id in enumerate(movie_ids, start=1):
        if index > 1 or pause_before_first:
            time.sleep(GET_PAGE_PAUSE)
        print(f"Fetching movie runtimes ({index}/{total}): movie {movie_id}")
        runtimes[movie_id] = fetch_movie_runtime(movie_id)
    for row in movie_rows:
        row["runtime"] = _runtime_csv_value(runtimes.get(row["movie_trakt_id"]))


def fetch_watch_history(output=DEFAULT_OUTPUT):
    """Fetch episode and movie history from Trakt and write to a CSV file."""
    rows = [_episode_row(item) for item in _fetch_pages("episodes")]
    rows.extend(_movie_row(item) for item in _fetch_pages("movies"))
    rows.sort(key=lambda r: r["watched_at"])

    episode_fetch_count = _enrich_episode_runtimes(rows)
    _enrich_movie_runtimes(rows, pause_before_first=episode_fetch_count > 0)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return output
