# trakt-scripts

Personal CLI tools for Trakt. Shared `trakt/` modules live at the repo root; individual tools live in subfolders.

### Quick setup

Create and activate a venv first (see [Trakt setup](#trakt-setup)), then run the one-shot bootstrap from `season-bulk-fix/`:

```bash
cd season-bulk-fix
./setup.sh
```

The script installs the repo (`pip install -e ..`), copies `.env.example` to the repo-root `.env` if missing, runs `trakt-auth` when no access token is set, fetches watch history, and generates the flagged-seasons report.

On a fresh clone it creates `.env` and exits — edit the repo-root `.env` with your Trakt app credentials, then run `./setup.sh` again to finish.

Re-runs skip `trakt-auth` when tokens are already present but still refresh history and the report.

## Trakt setup

One-time setup from the repo root (`trakt-scripts/`). For season-bulk-fix, `./setup.sh` in `season-bulk-fix/` automates most of this plus the initial history export and report — see [Quick setup](#quick-setup) below.

### Virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # Linux / macOS
# .venv\Scripts\activate    # Windows
```

Activate the venv before running setup or any scripts.

### Install

With the venv active:

```bash
pip install -e .
```

This installs the shared `trakt` package and registers the `trakt-auth` CLI entry point.

### Environment file

```bash
cp .env.example .env
```

Create a Trakt App from App Settings. 
Edit `.env` and set:

- `TRAKT_CLIENT_ID` — from your Trakt API app
- `TRAKT_CLIENT_SECRET` — from your Trakt API app

Leave `TRAKT_ACCESS_TOKEN` and `TRAKT_REFRESH_TOKEN` empty until you authenticate.

### Authentication

After creating `.env`, run:

```bash
trakt-auth
```

This starts Trakt device auth:

1. Open the URL printed in the terminal (usually [https://trakt.tv/activate](https://trakt.tv/activate)).
2. Enter the user code shown in the terminal.
3. Wait for authorization to complete.

On success, `TRAKT_ACCESS_TOKEN` and `TRAKT_REFRESH_TOKEN` are written to `.env`.

### Refresh token

When the access token expires, refresh it without repeating device login:

```bash
trakt-auth --refresh
```

This uses `TRAKT_REFRESH_TOKEN` from `.env` to obtain a new access token and updates `.env` in place. If refresh fails or no refresh token exists, run `trakt-auth` again for a full device login.

## season-bulk-fix

Detect suspicious bulk-imported season watch patterns in your Trakt history, preview replacement timestamps, and apply fixes one season at a time after interactive approval.

### Manual pipeline

All pipeline scripts must be run from `**season-bulk-fix/**` so `data/*` paths resolve correctly:

```bash
cd season-bulk-fix
```

### 1. Export watch history

```bash
python fetch_history.py
```

Writes `data/watch_history.csv` from Trakt (episodes and movies).

### 2. Flag suspicious seasons

```bash
python report.py
```

Prints a console report and writes flagged seasons to `data/flagged_seasons.csv`. Optional binge exclusions go in `data/exclusions.json`.

### 3. Fix one season

```bash
python fix_season.py --show "Show Name" --season 1
```

Use `--show-id` instead of `--show` when titles collide:

```bash
python fix_season.py --show-id 12345 --season 1
```

The script shows a preview of old vs new timestamps and prompts for `Y`/`n` before writing anything to Trakt.

**Useful flags:**


| Flag                    | Purpose                                                                             |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `--seed 42`             | Reproducible date scheduling (default: 42)                                          |
| `--resume-apply`        | Continue an interrupted apply (same `--show`/`--show-id`, `--season`, and `--seed`) |
| `--refresh-after-apply` | Re-export `data/watch_history.csv` after a successful apply                         |


**Resume example:**

```bash
python fix_season.py --show "Show Name" --season 1 --seed 42 --resume-apply
```

After applying fixes, refresh local data with `python fetch_history.py` or use `--refresh-after-apply` on the next apply.