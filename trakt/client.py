"""Shared Trakt HTTP client and repo paths."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv, set_key
from requests import Response

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

BASE = "https://api.trakt.tv"

POST_DELAY = 1.0
LOGIN_TIMEOUT = 60

RATE_LIMIT_MESSAGE = "Rate limited by Trakt. Wait a minute and re-run."
LOGIN_HINT = "python trakt/client.py"


class TraktRateLimitError(Exception):
    """Raised when Trakt returns HTTP 429."""


def _http_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    authed: bool = True,
    timeout: float = 120,
) -> Response:
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
    response = _http_request(
        method,
        path,
        json_body=json_body,
        params=params,
        authed=authed,
        timeout=timeout,
    )

    if response.status_code == 401 and authed:
        raise SystemExit(f"Trakt access token expired or invalid. Run: {LOGIN_HINT}")

    if response.status_code == 429:
        raise TraktRateLimitError(RATE_LIMIT_MESSAGE)

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


def device_login() -> None:
    """Device OAuth flow; writes TRAKT_ACCESS_TOKEN to .env."""
    client_id = os.environ.get("TRAKT_CLIENT_ID")
    client_secret = os.environ.get("TRAKT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Set TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET in .env first.")

    code_response = _http_request(
        "POST",
        "/oauth/device/code",
        json_body={"client_id": client_id},
        authed=False,
        timeout=LOGIN_TIMEOUT,
    )
    
    # We catch 429 first and raise_for_status() to catch 4xx and 5xx errors.
    if code_response.status_code == 429:
        raise SystemExit(RATE_LIMIT_MESSAGE)
    code_response.raise_for_status()
    codes = code_response.json()

    url = codes.get("verification_url")
    print(f"Go to {url}")
    print(f"Enter code: {codes['user_code']}")
    print("Waiting for authorization...")

    device_code = codes["device_code"]
    interval = codes.get("interval", 5)
    expires_at = time.time() + codes.get("expires_in", 600)

    while time.time() < expires_at:
        token_response = _http_request(
            "POST",
            "/oauth/device/token",
            json_body={
                "code": device_code,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            authed=False,
            timeout=LOGIN_TIMEOUT,
        )

        if token_response.status_code == 200:
            if not ENV_PATH.exists():
                ENV_PATH.write_text("", encoding="utf-8")
            set_key(ENV_PATH, "TRAKT_ACCESS_TOKEN", token_response.json()["access_token"])
            print(f"Saved TRAKT_ACCESS_TOKEN to {ENV_PATH}")
            return

        elif token_response.status_code == 429:
            raise SystemExit(RATE_LIMIT_MESSAGE)


        time.sleep(interval)

    raise SystemExit("Device code expired. Run again to get a new code.")


if __name__ == "__main__":
    device_login()
