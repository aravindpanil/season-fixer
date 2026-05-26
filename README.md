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

## detect_order

Detects episodes recorded out of watch order — same-season (e.g. S1E8 before S1E7) and cross-season (e.g. S1E12 before S2E3) first-watch violations.

```bash
python detect_order.py
```

Prints a summary and writes flagged entries to `data/flagged_order.csv` with an `action` column for review (`fix` or `exclude`).

**Options:**

| Flag | Purpose |
| --- | --- |
| `--input PATH` | Use a different watch history CSV (default: `data/watch_history.csv`) |
| `--exclude SHOW_ID:SEASON:EPISODE` | Skip a specific episode from order checks (repeatable) |
