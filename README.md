# trakt-scripts

Personal CLI tools for correcting Trakt watch history. A shared `trakt/` package handles API access and CSV I/O; root-level scripts run analysis and fixes against a local snapshot.

All analysis runs offline against `data/watch_history.csv`. API calls happen only during history fetch and when you explicitly approve a fix.

---

## Setup

### Virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # Linux / macOS / WSL
# .venv\Scripts\activate    # Windows
```

### Bootstrap

Create a Trakt app at [trakt.tv/oauth/applications](https://trakt.tv/oauth/applications), set your credentials in `.env`, then run:

```bash
./setup.sh
```

The script:

1. Installs the repo as an editable package (`pip install -e .`)
2. Copies `.env.example` → `.env` if missing — edit `TRAKT_CLIENT_ID` and `TRAKT_CLIENT_SECRET`, then re-run
3. Runs `python trakt/client.py` for device login when `TRAKT_ACCESS_TOKEN` is empty

Device login flow:

1. Open the URL printed in the terminal (usually [https://trakt.tv/activate](https://trakt.tv/activate))
2. Enter the user code shown in the terminal
3. Wait for authorisation to complete

`TRAKT_ACCESS_TOKEN` is written to `.env` on success.

### Re-authenticate

When the access token expires:

```bash
python trakt/client.py
```

---

## Fetch watch history

Every tool reads from a local CSV snapshot. Refresh it with:

```bash
python trakt/history.py
```

Writes `data/watch_history.csv` (episodes and movies, including runtimes when Trakt provides them).

Re-run after applying fixes or when you want a fresh snapshot.

---

## detect_conflicts

Detects overlapping watch intervals — pairs of entries whose computed watch windows overlap (impossible to watch both at once). Prints each pair, then optionally fixes them on Trakt.

```bash
python detect_conflicts.py
```

**Behaviour:**

- Prints overlapping pairs with titles and timestamps
- Prompts `Fix these conflicts? [y/N]` — default is no (audit only)
- On `y`, moves the second entry in each pair to start immediately after the first entry ends, re-checks until no overlaps remain, then re-fetches `data/watch_history.csv`

**Options:** none (reads `data/watch_history.csv`)

---

## detect_order

Detects out-of-order **first-watch** episodes — entries logged before a narrative predecessor in the same show (within-season or cross-season). Rewatches are ignored.

```bash
python detect_order.py
```

Prints a summary and writes violations to `data/flagged_order.csv` with an `action` column for manual review.

**Review workflow:**

1. Open `data/flagged_order.csv`
2. Set `action = exclude` for intentional non-linear watches (e.g. anthology shows)
3. Re-run `detect_order.py` — existing `action` values are preserved

There is no automated fix script yet; `action = fix` is reserved for future tooling.

**Options:** none (reads `data/watch_history.csv`)

---

## reschedule_season

Moves an entire season's first-watch episodes into a date range. Episodes stay in narrative order; end times are spread randomly within equal slots across the window. Prints a preview and asks for approval before updating Trakt (two API calls: bulk remove + bulk add).

```bash
python reschedule_season.py --show-id 13855 --season 1 --start 2020-01-01 --end 2020-12-31
```

Use the `show_id` column from `data/watch_history.csv`. After applying, run `detect_conflicts.py` if new overlaps may exist.

**Options:**


| Flag                | Purpose                                               |
| ------------------- | ----------------------------------------------------- |
| `--show-id ID`      | Trakt show ID from watch history CSV (required)       |
| `--season N`        | Season number (required)                              |
| `--start` / `--end` | Date range `YYYY-MM-DD` (UTC start/end of day)        |
| `--csv PATH`        | Watch history CSV (default: `data/watch_history.csv`) |


---

## Typical workflow

```bash
source .venv/bin/activate

python trakt/history.py          # fetch snapshot
python detect_conflicts.py         # fix overlaps (optional)
python detect_order.py             # flag order issues → review flagged_order.csv
python reschedule_season.py ...    # bulk-reschedule a season (optional)

python trakt/history.py            # confirm final state
python detect_conflicts.py
```

