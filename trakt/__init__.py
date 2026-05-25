"""Shared Trakt helpers used across all scripts in this repo.

Modules
-------
trakt.client     HTTP client, OAuth, token refresh, repo paths — rate limiting, POST pacing, 401 retry
trakt.history    Fetch full watch history from Trakt → CSV
trakt.trakt_iso  Format datetimes as the UTC ISO string Trakt expects on POST
trakt.csv_to_python  Parse the local watch-history CSV into Python dicts
"""
