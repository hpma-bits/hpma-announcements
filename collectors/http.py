"""Small HTTP helper with retries, a stable User-Agent and a polite delay."""

from __future__ import annotations

import time
from typing import Any, Mapping

import requests

USER_AGENT = (
    "hpma-announcements/1.0 "
    "(+https://github.com/hpma-bits/hpma-announcements)"
)
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(DEFAULT_HEADERS)
    return _session


def fetch(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 2.0,
    delay: float = 0.5,
) -> requests.Response:
    """Fetch ``url`` with retries.  Raises the last error after ``retries``."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session().get(url, headers=dict(headers or {}), timeout=timeout)
            response.raise_for_status()
            if delay:
                time.sleep(delay)
            return response
        except Exception as exc:  # noqa: BLE001 - retried below
            last_error = exc
            if attempt < retries:
                time.sleep(backoff * attempt)
    assert last_error is not None
    raise last_error


def fetch_text(url: str, **kwargs) -> str:
    response = fetch(url, **kwargs)
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def fetch_bytes(url: str, **kwargs) -> bytes:
    return fetch(url, **kwargs).content


def fetch_json(url: str, **kwargs) -> Any:
    """Fetch ``url`` and decode the body as JSON."""
    return fetch(url, **kwargs).json()
