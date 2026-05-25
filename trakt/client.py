"""Shared Trakt HTTP client with rate-limit handling and POST pacing."""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from trakt.paths import ENV_PATH

BASE = "https://api.trakt.tv"
IST = ZoneInfo("Asia/Kolkata")

POST_MIN_INTERVAL = 1.0
GET_PAGE_PAUSE = 0.35
GET_REMAINING_THRESHOLD = 10
GET_PAGE_PAUSE_AFTER = 5
DEFAULT_FALLBACK_RETRY_SECONDS = 60

_last_post_at = 0.0


class TraktRateLimitError(Exception):
    """Raised when Trakt (or Cloudflare) returns HTTP 429."""

    def __init__(self, message, *, phase=None, recovery=None):
        super().__init__(message)
        self.phase = phase
        self.recovery = recovery


def _load_env():
    load_dotenv(ENV_PATH)


def _headers(*, authed=True):
    _load_env()
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": os.environ["TRAKT_CLIENT_ID"],
    }
    if authed:
        headers["Authorization"] = f"Bearer {os.environ['TRAKT_ACCESS_TOKEN']}"
    return headers


def _wait_for_post_slot():
    global _last_post_at
    elapsed = time.monotonic() - _last_post_at
    if _last_post_at and elapsed < POST_MIN_INTERVAL:
        time.sleep(POST_MIN_INTERVAL - elapsed)
    _last_post_at = time.monotonic()


def _parse_x_ratelimit(header_value):
    if not header_value:
        return {}
    try:
        data = json.loads(header_value)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def parse_rate_limit(response):
    """Extract rate-limit metadata from response headers."""
    limit_info = _parse_x_ratelimit(response.headers.get("X-Ratelimit"))
    retry_after_raw = response.headers.get("Retry-After")
    retry_after_seconds = None

    if retry_after_raw is not None:
        try:
            retry_after_seconds = max(0.0, float(retry_after_raw))
        except ValueError:
            try:
                reset_dt = parsedate_to_datetime(retry_after_raw)
                if reset_dt.tzinfo is None:
                    reset_dt = reset_dt.replace(tzinfo=timezone.utc)
                retry_after_seconds = max(
                    0.0, (reset_dt - datetime.now(timezone.utc)).total_seconds()
                )
            except (TypeError, ValueError, OverflowError):
                pass

    until = limit_info.get("until")
    if retry_after_seconds is None and until:
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
            retry_after_seconds = max(
                0.0, (until_dt - datetime.now(timezone.utc)).total_seconds()
            )
        except (TypeError, ValueError):
            pass

    if retry_after_seconds is None:
        retry_after_seconds = float(DEFAULT_FALLBACK_RETRY_SECONDS)

    return {
        "retry_after_seconds": retry_after_seconds,
        "limit_name": limit_info.get("name"),
        "period": limit_info.get("period"),
        "limit": limit_info.get("limit"),
        "remaining": limit_info.get("remaining"),
        "until": until,
        "is_cloudflare": limit_info.get("name") is None,
    }


def format_duration(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m {secs}s"


def format_retry_time(seconds):
    retry_at_utc = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    retry_at_ist = retry_at_utc.astimezone(IST)
    return (
        f"{format_duration(seconds)} "
        f"(retry ~{retry_at_ist.strftime('%Y-%m-%d %H:%M:%S')} IST / "
        f"{retry_at_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC)"
    )


def format_rate_limit_message(
    *,
    method,
    path,
    context=None,
    limit_info,
    phase=None,
    recovery=None,
):
    limit_name = limit_info.get("limit_name") or "unknown (likely Cloudflare)"
    period = limit_info.get("period")
    limit = limit_info.get("limit")
    remaining = limit_info.get("remaining")

    limit_bits = [limit_name]
    if period is not None and limit is not None:
        limit_bits.append(f"{limit} per {period}s")
    if remaining is not None:
        limit_bits.append(f"{remaining} remaining")

    operation = f"{method} {path}"
    if context:
        operation = f"{operation} ({context})"

    lines = [
        "ERROR: Trakt rate limit exceeded",
        "",
        f"  Operation: {operation}",
        f"  Limit:     {', '.join(limit_bits)}",
        f"  Retry in:  {format_retry_time(limit_info['retry_after_seconds'])}",
    ]
    if phase:
        lines.append(f"  Phase:     {phase}")
    if recovery:
        lines.append(f"  Recovery:  {recovery}")
    lines.extend(
        [
            "",
            "Other Trakt apps on this account share the same limits. Pause them before retrying.",
            "Re-run the same command after the retry time (use --resume-apply if apply was interrupted).",
        ]
    )
    return "\n".join(lines)


def get_rate_limit_remaining(response):
    info = _parse_x_ratelimit(response.headers.get("X-Ratelimit"))
    remaining = info.get("remaining")
    if remaining is None:
        return None
    try:
        return int(remaining)
    except (TypeError, ValueError):
        return None


def maybe_pause_for_get_pagination(response, page):
    remaining = get_rate_limit_remaining(response)
    if remaining is not None and remaining <= GET_REMAINING_THRESHOLD:
        time.sleep(GET_PAGE_PAUSE)
        return
    if page >= GET_PAGE_PAUSE_AFTER:
        time.sleep(GET_PAGE_PAUSE)


def trakt_request(
    method,
    path,
    *,
    json_body=None,
    params=None,
    context=None,
    authed=True,
    timeout=120,
    phase=None,
    recovery=None,
    _retried_auth=False,
):
    method_upper = method.upper()
    if method_upper in {"POST", "PUT", "DELETE"}:
        _wait_for_post_slot()

    response = requests.request(
        method_upper,
        f"{BASE}{path}",
        json=json_body,
        params=params,
        headers=_headers(authed=authed),
        timeout=timeout,
    )

    if response.status_code == 401 and authed and not _retried_auth:
        from trakt.auth import refresh_access_token, save_tokens

        tokens = refresh_access_token()
        save_tokens(tokens, ENV_PATH)
        return trakt_request(
            method,
            path,
            json_body=json_body,
            params=params,
            context=context,
            authed=authed,
            timeout=timeout,
            phase=phase,
            recovery=recovery,
            _retried_auth=True,
        )

    if response.status_code == 429:
        limit_info = parse_rate_limit(response)
        message = format_rate_limit_message(
            method=method_upper,
            path=path,
            context=context,
            limit_info=limit_info,
            phase=phase,
            recovery=recovery,
        )
        raise TraktRateLimitError(message, phase=phase, recovery=recovery)

    response.raise_for_status()
    return response


def trakt_get(path, params=None, context=None, timeout=60, phase=None, recovery=None):
    return trakt_request(
        "GET",
        path,
        params=params,
        context=context,
        timeout=timeout,
        phase=phase,
        recovery=recovery,
    )


def trakt_post(
    path,
    json_body,
    *,
    context=None,
    authed=True,
    timeout=60,
    phase=None,
    recovery=None,
):
    return trakt_request(
        "POST",
        path,
        json_body=json_body,
        context=context,
        authed=authed,
        timeout=timeout,
        phase=phase,
        recovery=recovery,
    )
