"""Shared Trakt HTTP client, OAuth, token refresh, and repo paths."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv, set_key
from requests import Response

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

BASE = "https://api.trakt.tv"

POST_DELAY = 1.0
OAUTH_TIMEOUT = 60
MAX_5XX_RETRIES = 3
SERVER_ERROR_BACKOFF_SECONDS = 5


class TraktRateLimitError(Exception):
    """Raised when Trakt returns HTTP 429."""


def _retry_after_seconds(response: Response, *, fallback: float = 60.0) -> float:
    """Parse Retry-After as seconds or an HTTP-date; fall back when missing or invalid."""
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return fallback
    try:
        return max(0.0, float(retry_after))
    except ValueError:
        pass
    try:
        reset_dt = parsedate_to_datetime(retry_after)
        if reset_dt.tzinfo is None:
            reset_dt = reset_dt.replace(tzinfo=timezone.utc)
        return max(0.0, (reset_dt - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return fallback


def _rate_limit_message(response: Response) -> str:
    minutes = max(1, math.ceil(_retry_after_seconds(response) / 60))

    limit_name = ""
    try:
        info = json.loads(response.headers.get("X-Ratelimit", "{}"))
        if info.get("name"):
            limit_name = f" ({info['name']})"
    except (json.JSONDecodeError, TypeError):
        pass

    return f"Rate limited{limit_name}. Wait {minutes} minute(s) before retrying."


def _raw_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    authed: bool = True,
    timeout: float = 120,
) -> Response:
    """Make a raw HTTP request to the Trakt API without retry or error handling."""
    method_upper = method.upper()
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": os.environ["TRAKT_CLIENT_ID"],
    }
    if authed:
        headers["Authorization"] = f"Bearer {os.environ['TRAKT_ACCESS_TOKEN']}"
    response = requests.request(
        method_upper,
        f"{BASE}{path}",
        json=json_body,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    if method_upper in {"POST", "PUT", "DELETE"}:
        time.sleep(POST_DELAY)
    return response


def trakt_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    authed: bool = True,
    timeout: float = 120,
) -> Response:
    retried_auth = False
    retry_5xx = 0
    while True:
        response = _raw_request(
            method.upper(),
            path,
            json_body=json_body,
            params=params,
            authed=authed,
            timeout=timeout,
        )

        if response.status_code == 401 and authed and not retried_auth:
            tokens = refresh_access_token()
            save_tokens(tokens, ENV_PATH)
            retried_auth = True
            continue

        if response.status_code == 429:
            raise TraktRateLimitError(_rate_limit_message(response))

        if response.status_code >= 500 and retry_5xx < MAX_5XX_RETRIES:
            time.sleep(SERVER_ERROR_BACKOFF_SECONDS * (2**retry_5xx))
            retry_5xx += 1
            continue

        response.raise_for_status()
        return response


def trakt_get(
    path: str,
    params: dict[str, Any] | None = None,
    timeout: float = 60,
) -> Response:
    return trakt_request("GET", path, params=params, timeout=timeout)


def trakt_post(
    path: str,
    json_body: dict[str, Any],
    *,
    authed: bool = True,
    timeout: float = 60,
) -> Response:
    return trakt_request(
        "POST",
        path,
        json_body=json_body,
        authed=authed,
        timeout=timeout,
    )


def to_trakt_iso(dt: datetime) -> str:
    """Format a datetime as the millisecond-precision UTC string Trakt expects on POST."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------------------
# OAuth device login and token refresh
# ---------------------------------------------------------------------------


def _load_credentials() -> tuple[str, str]:
    client_id = os.environ.get("TRAKT_CLIENT_ID")
    client_secret = os.environ.get("TRAKT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Set TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET in .env first.")
    return client_id, client_secret


def _oauth_post(path: str, json_body: dict[str, Any]) -> Response:
    try:
        return trakt_post(path, json_body, authed=False, timeout=OAUTH_TIMEOUT)
    except TraktRateLimitError as exc:
        raise SystemExit(str(exc)) from None


def device_login() -> dict[str, Any]:
    """Walk through Trakt device auth and return token response."""
    client_id, client_secret = _load_credentials()

    codes = _oauth_post("/oauth/device/code", {"client_id": client_id}).json()
    url = codes.get("verification_url") or "https://trakt.tv/activate"
    print(f"Go to {url}")
    print(f"Enter code: {codes['user_code']}")
    print("Waiting for authorization...")

    device_code = codes["device_code"]
    interval = codes.get("interval", 5)
    expires_at = time.time() + codes.get("expires_in", 600)

    while time.time() < expires_at:
        token_response = _raw_request(
            "POST",
            "/oauth/device/token",
            json_body={
                "code": device_code,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            authed=False,
            timeout=OAUTH_TIMEOUT,
        )

        if token_response.status_code == 200:
            return token_response.json()

        if token_response.status_code == 400:
            pass
        elif token_response.status_code == 429:
            wait_seconds = _retry_after_seconds(token_response)
            print(_rate_limit_message(token_response))
            time.sleep(wait_seconds)
            interval = min(interval + 1, 10)
        elif token_response.status_code >= 500:
            time.sleep(SERVER_ERROR_BACKOFF_SECONDS)
        else:
            token_response.raise_for_status()

        time.sleep(interval)

    raise SystemExit("Device code expired. Run again to get a new code.")


def refresh_access_token(refresh_token: str | None = None) -> dict[str, Any]:
    """Use a refresh token to get a new access token."""
    client_id, client_secret = _load_credentials()
    refresh_token = refresh_token or os.environ.get("TRAKT_REFRESH_TOKEN")
    if not refresh_token:
        raise SystemExit("No TRAKT_REFRESH_TOKEN in .env. Run device login first.")

    return _oauth_post(
        "/oauth/token",
        {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": os.environ.get("TRAKT_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob"),
            "grant_type": "refresh_token",
        },
    ).json()


def save_tokens(tokens: dict[str, Any], env_path: Path | str = ENV_PATH) -> None:
    """Write access + refresh tokens into .env."""
    env_path = Path(env_path)
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")

    set_key(env_path, "TRAKT_ACCESS_TOKEN", tokens["access_token"])
    if tokens.get("refresh_token"):
        set_key(env_path, "TRAKT_REFRESH_TOKEN", tokens["refresh_token"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Trakt OAuth device login and token refresh.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh the access token instead of running device login.",
    )
    args = parser.parse_args()
    tokens = refresh_access_token() if args.refresh else device_login()
    save_tokens(tokens)
    print(f"Saved tokens to {ENV_PATH}")


if __name__ == "__main__":
    main()
