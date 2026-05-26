"""Shared Trakt helpers used across all scripts in this repo.

Modules
-------
trakt.client     HTTP client, OAuth, token refresh, POST pacing, 429/401 handling
trakt.history    Fetch full watch history from Trakt → CSV
trakt.csv_to_python  Parse the local watch-history CSV into Python dicts
trakt.intervals      Watch-interval helpers: duration, row_interval, merge
"""
