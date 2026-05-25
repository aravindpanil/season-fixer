"""Datetime parsing and formatting utilities for the Trakt API."""

from datetime import datetime, timezone


def parse_dt(value):
    """Parse an ISO 8601 datetime string to a UTC-aware datetime.

    Handles both ``Z`` suffix and ``+00:00`` offset forms returned by the API.
    Naive datetimes are treated as UTC.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_trakt_iso(dt):
    """Format a datetime as the millisecond-precision UTC string Trakt expects on POST."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
