"""Get a Trakt OAuth token via device auth (or refresh an existing one)."""

import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

from trakt_client import (
    TraktRateLimitError,
    format_duration,
    parse_rate_limit,
    trakt_post,
)

ENV_PATH = Path(".env")
JSON_HEADERS = {"Content-Type": "application/json"}
TIMEOUT = 60
BASE = "https://api.trakt.tv"


def _load_credentials():
    load_dotenv(ENV_PATH)
    client_id = os.environ.get("TRAKT_CLIENT_ID")
    client_secret = os.environ.get("TRAKT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Set TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET in .env first.")
    return client_id, client_secret


def _post(path, json_body):
    try:
        return trakt_post(
            path,
            json_body,
            authed=False,
            context=path,
            timeout=TIMEOUT,
            recovery="Wait for the retry time, then run trakt_auth.py again.",
        )
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None


def device_login():
    """Walk through Trakt device auth and return token response."""

    client_id, client_secret = _load_credentials()

    codes = _post("/oauth/device/code", {"client_id": client_id}).json()
    url = codes.get("verification_url") or "https://trakt.tv/activate"
    print(f"Go to {url}")
    print(f"Enter code: {codes['user_code']}")
    print("Waiting for authorization...")

    device_code = codes["device_code"]
    interval = codes.get("interval", 5)
    expires_at = time.time() + codes.get("expires_in", 600)

    while time.time() < expires_at:
        token_response = requests.post(
            f"{BASE}/oauth/device/token",
            json={
                "code": device_code,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers=JSON_HEADERS,
            timeout=TIMEOUT,
        )

        if token_response.status_code == 200:
            return token_response.json()

        if token_response.status_code == 400:
            pass
        elif token_response.status_code == 429:
            limit_info = parse_rate_limit(token_response)
            wait_seconds = limit_info["retry_after_seconds"]
            print(
                f"Rate limited during device auth. Waiting {format_duration(wait_seconds)}..."
            )
            time.sleep(wait_seconds)
            interval = min(interval + 1, 10)
        else:
            token_response.raise_for_status()

        time.sleep(interval)

    raise SystemExit("Device code expired. Run again to get a new code.")


def refresh_access_token(refresh_token=None):
    """Use a refresh token to get a new access token."""
    client_id, client_secret = _load_credentials()
    refresh_token = refresh_token or os.environ.get("TRAKT_REFRESH_TOKEN")
    if not refresh_token:
        raise SystemExit("No TRAKT_REFRESH_TOKEN in .env. Run device login first.")

    return _post(
        "/oauth/token",
        {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": os.environ.get("TRAKT_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob"),
            "grant_type": "refresh_token",
        },
    ).json()


def save_tokens(tokens, env_path=ENV_PATH):
    """Write access + refresh tokens into .env."""
    env_path = Path(env_path)
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")

    set_key(env_path, "TRAKT_ACCESS_TOKEN", tokens["access_token"])
    if tokens.get("refresh_token"):
        set_key(env_path, "TRAKT_REFRESH_TOKEN", tokens["refresh_token"])


def main(refresh=False):
    tokens = refresh_access_token() if refresh else device_login()
    save_tokens(tokens)
    print(f"Saved tokens to {ENV_PATH}")


if __name__ == "__main__":
    main(refresh="--refresh" in sys.argv)
