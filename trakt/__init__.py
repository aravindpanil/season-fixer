"""Shared Trakt helpers used across all scripts in this repo.

Modules
-------
trakt.client     HTTP client, OAuth, token refresh, repo paths — rate limiting, POST pacing, 401 retry
trakt.history    Fetch full watch history from Trakt → CSV
trakt.dt         Datetime parsing (parse_dt) and formatting (to_trakt_iso)
trakt.csv        Load and query the local watch-history CSV
trakt.episodes   Trakt API calls for episode metadata (premiere, durations)
trakt.schedule   IST-biased evening scheduling and clash detection
trakt.sync       Remove-then-re-add apply pattern with JSON checkpointing
trakt.cli        Interactive CLI prompts (yes/no, date entry)
"""
