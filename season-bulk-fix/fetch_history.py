#!/usr/bin/env python3
"""Fetch Trakt episode and movie watch history and save to one CSV."""

from trakt.client import TraktRateLimitError
from trakt.history import fetch_watch_history


def main():
    try:
        path = fetch_watch_history()
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Wrote watch history to {path}")


if __name__ == "__main__":
    main()
