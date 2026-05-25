"""Get a Trakt OAuth token via device auth (or refresh an existing one)."""

import sys
from pathlib import Path

from trakt.auth import device_login, refresh_access_token, save_tokens

ENV_PATH = Path(".env")


def main(refresh=False):
    tokens = refresh_access_token() if refresh else device_login()
    save_tokens(tokens)
    print(f"Saved tokens to {ENV_PATH}")


if __name__ == "__main__":
    main(refresh="--refresh" in sys.argv)
