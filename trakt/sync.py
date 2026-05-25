"""Trakt history remove-then-re-add apply pattern with JSON checkpointing.

All fixer scripts share this module so the checkpoint contract (plan_hash,
phase progression, state file naming, --resume-apply semantics) is consistent
across the whole repo.
"""

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from trakt.client import TraktRateLimitError, trakt_post

REMOVE_CHUNK_SIZE = 50

PHASE_REMOVE = "remove"
PHASE_ADD = "add"
PHASE_COMPLETE = "complete"


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------


def apply_state_path(show_id, season_number):
    """Return the Path for the apply-state JSON for a given show/season."""
    return Path(f"data/fix_apply_{show_id}_s{season_number}.state.json")


def compute_plan_hash(plan):
    """Return a 16-hex-char SHA-256 fingerprint of the plan.

    Hash covers history_id, episode_number, and new_watched_at for each row.
    Used to detect plan drift when resuming an interrupted apply.
    """
    rows = [
        {
            "history_id": row["history_id"],
            "episode_number": row["episode_number"],
            "new_watched_at": row["new_watched_at"],
        }
        for row in plan
    ]
    payload = json.dumps(rows, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load_apply_state(path):
    """Load the apply-state JSON from ``path``, or return ``None`` if absent."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_apply_state(path, state):
    """Persist ``state`` to ``path``, stamping a UTC ``timestamp`` field."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state["timestamp"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def clear_apply_state(path):
    """Delete the apply-state file if it exists."""
    if path.exists():
        path.unlink()


def phase_status(state, total_chunks):
    """Return a human-readable progress string for the current apply phase."""
    phase = state.get("phase", PHASE_REMOVE)
    chunks_done = state.get("chunks_completed", 0)
    removed = len(state.get("removed_ids", []))
    total = state.get("total_to_remove", 0)

    if phase == PHASE_REMOVE:
        return (
            f"remove {chunks_done}/{total_chunks} chunks "
            f"({removed}/{total} ids removed), add not started"
        )
    if phase == PHASE_ADD:
        return f"remove complete ({removed}/{total} ids removed), add not started"
    if phase == PHASE_COMPLETE:
        return "remove and add complete"
    return phase


def recovery_message(state_path, state, total_chunks):
    """Return the --resume-apply hint string embedded in 429 error messages."""
    status = phase_status(state, total_chunks)
    return (
        f"{status}. Resume with the same args plus --resume-apply "
        f"(state: {state_path.name})"
    )


# ---------------------------------------------------------------------------
# Internal sync-response helpers
# ---------------------------------------------------------------------------


def _sync_deleted_count(body):
    deleted = body.get("deleted", {})
    return sum(
        deleted.get(key, 0)
        for key in ("movies", "episodes", "shows", "seasons", "people", "lists")
    )


def _sync_not_found_ids(body):
    not_found = body.get("not_found", {})
    ids = not_found.get("ids")
    return ids or []


# ---------------------------------------------------------------------------
# Trakt mutation helpers
# ---------------------------------------------------------------------------


def remove_history(history_ids, *, state_path, state):
    """Remove history IDs from Trakt in chunks, checkpointing after each chunk.

    Starts from ``state["chunks_completed"]`` so an interrupted remove can be
    resumed. Raises ``SystemExit`` on missing IDs or incomplete deletes.
    """
    chunks = [
        history_ids[i : i + REMOVE_CHUNK_SIZE]
        for i in range(0, len(history_ids), REMOVE_CHUNK_SIZE)
    ]
    total_chunks = len(chunks)
    start_chunk = state.get("chunks_completed", 0)

    if start_chunk >= total_chunks:
        state["phase"] = PHASE_ADD
        save_apply_state(state_path, state)
        return

    for chunk_index in range(start_chunk, total_chunks):
        chunk = chunks[chunk_index]
        chunk_num = chunk_index + 1
        context = f"removing {len(chunk)} history entries, chunk {chunk_num}/{total_chunks}"
        response = trakt_post(
            "/sync/history/remove",
            {"ids": chunk},
            context=context,
            timeout=120,
            phase=phase_status(state, total_chunks),
            recovery=recovery_message(state_path, state, total_chunks),
        )
        body = response.json()

        not_found_ids = _sync_not_found_ids(body)
        if not_found_ids:
            raise SystemExit(
                f"Remove failed on chunk {chunk_num}/{total_chunks}: "
                f"{len(not_found_ids)} history id(s) not found "
                f"(first few: {not_found_ids[:5]}). Aborting before add."
            )

        deleted_count = _sync_deleted_count(body)
        if deleted_count < len(chunk):
            raise SystemExit(
                f"Remove incomplete on chunk {chunk_num}/{total_chunks}: "
                f"expected {len(chunk)} deleted, got {deleted_count}. Aborting before add."
            )

        state.setdefault("removed_ids", []).extend(chunk)
        state["chunks_completed"] = chunk_num
        state["phase"] = PHASE_REMOVE
        save_apply_state(state_path, state)

    state["phase"] = PHASE_ADD
    save_apply_state(state_path, state)


def add_history(show_id, season_number, episodes, *, state_path, state, total_chunks):
    """Re-add rescheduled episodes to Trakt via POST /sync/history.

    Raises ``SystemExit`` when the API confirms fewer episodes added than sent.
    """
    payload = {
        "shows": [
            {
                "ids": {"trakt": show_id},
                "seasons": [
                    {
                        "number": season_number,
                        "episodes": [
                            {
                                "number": ep["episode_number"],
                                "watched_at": ep["new_watched_at"],
                            }
                            for ep in episodes
                        ],
                    }
                ],
            }
        ]
    }
    response = trakt_post(
        "/sync/history",
        payload,
        context=f"adding {len(episodes)} episodes for show_id={show_id} season={season_number}",
        timeout=120,
        phase=phase_status(state, total_chunks),
        recovery=recovery_message(state_path, state, total_chunks),
    )
    body = response.json()
    episodes_added = body.get("added", {}).get("episodes", 0)
    if episodes_added < len(episodes):
        raise SystemExit(
            f"Add incomplete: expected {len(episodes)} episodes, added {episodes_added}. "
            f"State saved at {state_path}. Re-run with --resume-apply after checking Trakt."
        )

    state["phase"] = PHASE_COMPLETE
    save_apply_state(state_path, state)


# ---------------------------------------------------------------------------
# Preview CSV
# ---------------------------------------------------------------------------


def write_preview(plan, show_id, season_number):
    """Write a preview CSV showing old vs new timestamps and return its Path."""
    path = Path(f"data/fix_preview_{show_id}_s{season_number}.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "history_id",
                "show_name",
                "season_number",
                "episode_number",
                "old_watched_at",
                "new_watched_at",
            ],
        )
        writer.writeheader()
        for row in plan:
            writer.writerow(
                {
                    "history_id": row["history_id"],
                    "show_name": row["show_name"],
                    "season_number": row["season_number"],
                    "episode_number": row["episode_number"],
                    "old_watched_at": row["old_watched_at"],
                    "new_watched_at": row["new_watched_at"],
                }
            )
    return path


# ---------------------------------------------------------------------------
# Full apply workflow
# ---------------------------------------------------------------------------


def apply_plan(
    show_id,
    season_number,
    plan,
    preview_path,
    *,
    resume=False,
    refresh_after=False,
    start_date=None,
    end_date=None,
    date_mode=None,
):
    """Execute or resume a remove-then-re-add apply for a rescheduled plan.

    ``resume=True`` loads the existing state file, verifies the plan hash, and
    continues from where the previous run left off. ``refresh_after=True``
    re-fetches the local watch-history CSV after a successful apply.
    """
    from trakt.history import fetch_watch_history

    state_path = apply_state_path(show_id, season_number)
    plan_hash = compute_plan_hash(plan)
    history_ids = [row["history_id"] for row in plan]
    total_chunks = max(1, (len(history_ids) + REMOVE_CHUNK_SIZE - 1) // REMOVE_CHUNK_SIZE)

    if resume:
        state = load_apply_state(state_path)
        if not state:
            raise SystemExit(f"No apply state at {state_path}. Run --apply first.")
        if state.get("plan_hash") != plan_hash:
            raise SystemExit(
                "Plan hash mismatch between preview and saved state. "
                "Re-run with the same --show/--show-id, --season, and --seed, "
                "or delete the state file and start over."
            )
        if state.get("phase") == PHASE_COMPLETE:
            print(f"Apply already complete (state: {state_path}).")
            clear_apply_state(state_path)
            return
        print(f"Resuming apply from {state_path} ({phase_status(state, total_chunks)})")
    else:
        existing = load_apply_state(state_path)
        if existing and existing.get("phase") not in {None, PHASE_COMPLETE}:
            raise SystemExit(
                f"Incomplete apply found at {state_path} "
                f"({phase_status(existing, total_chunks)}). "
                "Use --resume-apply to continue or delete the state file to start over."
            )
        state = {
            "phase": PHASE_REMOVE,
            "show_id": show_id,
            "season_number": season_number,
            "plan_hash": plan_hash,
            "preview_path": str(preview_path),
            "removed_ids": [],
            "total_to_remove": len(plan),
            "chunks_completed": 0,
            "show_name": plan[0]["show_name"],
        }
        if start_date is not None:
            state["start_date"] = start_date.isoformat()
        if end_date is not None:
            state["end_date"] = end_date.isoformat()
        if date_mode is not None:
            state["date_mode"] = date_mode
        save_apply_state(state_path, state)

    try:
        if state.get("phase") == PHASE_REMOVE:
            remove_history(history_ids, state_path=state_path, state=state)
        if state.get("phase") == PHASE_ADD:
            add_history(
                show_id,
                season_number,
                plan,
                state_path=state_path,
                state=state,
                total_chunks=total_chunks,
            )
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None

    clear_apply_state(state_path)
    print(f"Updated {len(plan)} episode(s) on Trakt.")

    if refresh_after:
        print("\nRefreshing local watch history...")
        try:
            path = fetch_watch_history()
        except TraktRateLimitError as exc:
            raise SystemExit(
                f"{exc}\n\nApply succeeded on Trakt. Local CSV was not refreshed; "
                "run fetch_history.py after the rate limit clears."
            ) from None
        print(f"Refreshed watch history at {path}")
    else:
        print("\nRun fetch_history.py to refresh local watch history.")

    if preview_path.exists():
        preview_path.unlink()
        print(f"Removed preview file {preview_path}.")
