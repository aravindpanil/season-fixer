#!/usr/bin/env python3
"""Fetch Trakt episode and movie watch history and save to one CSV."""

import csv
import os
from pathlib import Path

from dotenv import load_dotenv

from trakt_client import (
    TraktRateLimitError,
    maybe_pause_for_get_pagination,
    trakt_get,
)

OUTPUT = Path("data/watch_history.csv")
ENV_PATH = Path(".env")

FIELDNAMES = [
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


def fetch_all_history(history_type):
    page = 1
    items = []
    while True:
        response = trakt_get(
            f"/sync/history/{history_type}",
            {"page": page, "limit": 1000},
            context=f"fetching {history_type} history page {page}",
            recovery="Re-run fetch_history.py after the rate limit clears.",
        )
        batch = response.json()
        items.extend(batch)
        page_count = int(response.headers.get("X-Pagination-Page-Count", 1))
        if page >= page_count:
            break
        maybe_pause_for_get_pagination(response, page)
        page += 1
    return items


def episode_row(item):
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


def movie_row(item):
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


def fetch_history_rows():
    load_dotenv(ENV_PATH)
    episodes = [episode_row(item) for item in fetch_all_history("episodes")]
    movies = [movie_row(item) for item in fetch_all_history("movies")]
    rows = episodes + movies
    rows.sort(key=lambda r: r["watched_at"])
    return rows


def write_watch_history(rows, output=OUTPUT):
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return output


def refresh_watch_history(output=OUTPUT):
    rows = fetch_history_rows()
    path = write_watch_history(rows, output)
    return rows, path


def main():
    try:
        rows, path = refresh_watch_history()
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None

    episodes = sum(1 for r in rows if r["type"] == "episode")
    movies = sum(1 for r in rows if r["type"] == "movie")
    print(f"Wrote {len(rows)} rows to {path} ({episodes} episodes, {movies} movies)")


if __name__ == "__main__":
    main()
