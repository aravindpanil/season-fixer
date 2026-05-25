"""Trakt API calls for show and episode metadata."""

from trakt.client import trakt_get
from trakt.dt import parse_dt


def fetch_season_premiere(show_id, season_number):
    """Return the first-aired date of the earliest episode in a season, or None.

    Fetches ``GET /shows/{id}/seasons/{n}/episodes?extended=full`` and
    returns the ``date`` of the first episode that has a ``first_aired`` value.
    Returns ``None`` when Trakt has no premiere data for the season.
    """
    response = trakt_get(
        f"/shows/{show_id}/seasons/{season_number}/episodes",
        {"extended": "full"},
        context=f"fetching season {season_number} episodes for show {show_id}",
    )
    episodes = sorted(response.json(), key=lambda e: e["number"])
    for episode in episodes:
        if episode.get("first_aired"):
            return parse_dt(episode["first_aired"]).date()
    return None


def fetch_episode_durations(show_id, season_number):
    """Return a dict mapping ``episode_number`` → runtime in minutes (or ``None``).

    Uses the ``runtime`` field from
    ``GET /shows/{id}/seasons/{n}/episodes?extended=full``.
    Episodes where Trakt has no runtime data map to ``None``.

    Used by the conflict fixer to determine accurate per-episode watch windows
    instead of the fixed 1-hour assumption used elsewhere.
    """
    response = trakt_get(
        f"/shows/{show_id}/seasons/{season_number}/episodes",
        {"extended": "full"},
        context=f"fetching episode durations for show {show_id} season {season_number}",
    )
    return {episode["number"]: episode.get("runtime") for episode in response.json()}
