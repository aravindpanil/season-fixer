#!/usr/bin/env python3
"""Unified entry point: fetch watch history and run conflict or order checks."""

import argparse

import detect_conflicts
import detect_order
from trakt.client import TraktRateLimitError
from trakt.csv_to_python import DEFAULT_CSV
from trakt.history import fetch_watch_history, print_fetch_stats


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Use existing watch history CSV instead of fetching from Trakt",
    )
    return parser.parse_args(argv)


def prepare_history(no_fetch):
    if no_fetch:
        if not DEFAULT_CSV.exists():
            raise SystemExit(
                f"Missing {DEFAULT_CSV}. Run without --no-fetch or run python trakt/history.py first."
            )
        print(f"Using existing watch history at {DEFAULT_CSV}")
        return

    try:
        path, stats = fetch_watch_history()
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None
    print_fetch_stats(path, stats)


def show_menu():
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


def main(argv=None):
    args = parse_args(argv)
    prepare_history(args.no_fetch)
    show_menu()


if __name__ == "__main__":
    main()
