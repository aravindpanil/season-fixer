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
3. Runs `python trakt/client.py` for device login when no access token is set

Device login flow:
1. Open the URL printed in the terminal (usually [https://trakt.tv/activate](https://trakt.tv/activate))
2. Enter the user code shown in the terminal
3. Wait for authorisation to complete

`TRAKT_ACCESS_TOKEN` is written to `.env` on success.

### Re-authenticate

When the access token expires, run device login again:

```bash
python trakt/client.py
```

## Fetch watch history

All tools read from a local CSV snapshot of your Trakt history. Export it with:

```bash
python trakt/history.py
```

Writes `data/watch_history.csv` (episodes and movies with runtimes).

## detect_conflicts

Detects overlapping watch intervals in your Trakt history — pairs of entries where the computed watch windows physically overlap, indicating incorrect timestamps. Lists each pair, then asks whether to reschedule them on Trakt.

```bash
python detect_conflicts.py
```

Prints overlapping pairs with titles and watch timestamps. Answer `y` at the prompt to move entries to the nearest free slot; default is no (audit only).

## detect_order

Detects episodes recorded out of watch order — same-season (e.g. S1E8 before S1E7), skip-ahead, late-watch, and cross-season first-watch violations.

```bash
python detect_order.py
```

Prints a summary and writes flagged entries to `data/flagged_order.csv` with an `action` column for review (`fix` or `exclude`).

**Options:**

| Flag | Purpose |
| --- | --- |
| `--input PATH` | Use a different watch history CSV (default: `data/watch_history.csv`) |
| `--exclude SHOW_ID:SEASON:EPISODE` | Skip a specific episode from order checks (repeatable) |

## reschedule_season

Moves an entire season's first-watch episodes into a date range. Episodes keep narrative order; end times are spread randomly across equal slots in that window. Prints a preview and asks for approval before updating Trakt (two API calls: bulk remove + bulk add).

```bash
python reschedule_season.py --show-id 13855 --season 1 --start 2020-01-01 --end 2020-12-31
```

Use the `show_id` column from `data/watch_history.csv`. After applying, run `detect_conflicts.py` if overlaps may remain.

**Options:**

| Flag | Purpose |
| --- | --- |
| `--show-id ID` | Trakt show ID from watch history CSV (required) |
| `--season N` | Season number (required) |
| `--start` / `--end` | Date range `YYYY-MM-DD` (UTC start/end of day) |
| `--csv PATH` | Watch history CSV (default: `data/watch_history.csv`) |
