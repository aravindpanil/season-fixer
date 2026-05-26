#!/usr/bin/env python3
"""Unified entry point: fetch watch history and run conflict or order checks."""

import detect_conflicts
import detect_order
from trakt.client import TraktRateLimitError
from trakt.history import fetch_watch_history, print_fetch_stats


def main():
    try:
        path, stats = fetch_watch_history()
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None
    print_fetch_stats(path, stats)

    print()
    print("[1] Check for overlapping watch intervals")
    print("[2] Check for out-of-order episode watches")
    choice = input("Choice: ").strip()

    if choice == "1":
        detect_conflicts.main()
    elif choice == "2":
        detect_order.main()
    else:
        raise SystemExit(f"Invalid choice: {choice!r}")


if __name__ == "__main__":
    main()
