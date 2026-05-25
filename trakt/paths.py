"""Repo paths shared across trakt modules."""

from pathlib import Path

# trakt-scripts/ (parent of the trakt package)
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
