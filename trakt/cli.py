"""Interactive CLI prompts shared across fixer scripts."""

from datetime import datetime


def prompt_yes_no(prompt, default=True):
    """Prompt for ``y``/``n`` and return the boolean result.

    Empty input returns ``default``. ``EOFError`` (non-interactive stdin)
    raises ``SystemExit``.
    """
    suffix = "Y/n" if default else "y/N"
    while True:
        try:
            value = input(f"{prompt} [{suffix}]: ").strip().lower()
        except EOFError:
            raise SystemExit("\nCancelled.")
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Enter y or n.")


def prompt_date(label):
    """Prompt for a ``YYYY-MM-DD`` date string and return a ``date`` object.

    Re-prompts on empty input or malformed dates. ``EOFError`` raises
    ``SystemExit``.
    """
    while True:
        try:
            value = input(f"{label} (IST, YYYY-MM-DD): ").strip()
        except EOFError:
            raise SystemExit("\nCancelled.")
        if not value:
            print("Enter a date in YYYY-MM-DD format.")
            continue
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            print("Invalid date. Use YYYY-MM-DD.")


def prompt_custom_dates():
    """Prompt for a start and end date, enforcing ``end >= start``.

    Returns ``(start_date, end_date)`` as ``date`` objects.
    """
    start_date = prompt_date("Start date")
    end_date = prompt_date("End date")
    if end_date < start_date:
        raise SystemExit("--end must be on or after --start.")
    return start_date, end_date
