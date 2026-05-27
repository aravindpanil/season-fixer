# trakt-scripts

Personal CLI tools for managing Trakt watch history. Shared `trakt/` package and CLI scripts live at the repo root.

## Setup

### Virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # Linux / macOS
# .venv\Scripts\activate    # Windows
```

### Bootstrap

Create a Trakt app at [trakt.tv/oauth/applications](https://trakt.tv/oauth/applications), copy your credentials into `.env`, then run the one-shot setup script:

```bash
./setup.sh
```

The script:
1. Installs the repo as an editable package (`pip install -e .`)
2. Copies `.env.example` → `.env` if missing — edit it with your `TRAKT_CLIENT_ID` and `TRAKT_CLIENT_SECRET`, then re-run
3. Runs `trakt-auth` for device login when no access token is set

Device login flow:
1. Open the URL printed in the terminal (usually [https://trakt.tv/activate](https://trakt.tv/activate))
2. Enter the user code shown in the terminal
3. Wait for authorisation to complete

`TRAKT_ACCESS_TOKEN` and `TRAKT_REFRESH_TOKEN` are written to `.env` on success.

### Refresh token

When the access token expires:

```bash
trakt-auth --refresh
```

Uses `TRAKT_REFRESH_TOKEN` from `.env` to obtain a new access token. If refresh fails or no refresh token exists, run `trakt-auth` again for a full device login.

## Fetch watch history

All tools read from a local CSV snapshot of your Trakt history. Export it with:

```bash
python fetch_history.py
```

Writes `data/watch_history.csv` (episodes and movies with runtimes).

## detect_conflicts

Detects overlapping watch intervals in your Trakt history — pairs of entries where the computed watch windows physically overlap, indicating incorrect timestamps.

```bash
python detect_conflicts.py
```

Prints a summary and writes flagged pairs to `data/flagged_conflicts.csv`.

## reschedule_season

Moves an entire season's first-watch episodes into a date range. Episodes keep narrative order; end times are spread randomly across equal slots in that window. Prints a preview and asks for approval before updating Trakt (two API calls: bulk remove + bulk add).

```bash
python reschedule_season.py --show-name "Breaking Bad" --season 1 --start 2020-01-01 --end 2020-12-31
```

The show name is matched against `show_name` values in `data/watch_history.csv` (case and punctuation are ignored). Partial matches work as it does a fuzzy search. If more than one show matches, you get a numbered list to pick from (or `0` to cancel). After applying, run `fix_conflicts.py` if overlaps may remain.

**Options:**

| Flag | Purpose |
| --- | --- |
| `--show-name NAME` | Show name from watch history CSV (required) |
| `--season N` | Season number (required) |
| `--start` / `--end` | Date range `YYYY-MM-DD` (UTC start/end of day) |
