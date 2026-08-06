from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "Phantos/0.1 (Cloudit; Mozilla/5.0 compatible)"


def http_open(url: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None, timeout: int = 60):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    return urllib.request.urlopen(req, timeout=timeout)


def http_json(method: str, url: str, body: dict | None = None, timeout: int = 120) -> Any:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if body is not None else {}
    with http_open(url, method=method, data=data, headers=headers, timeout=timeout) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def http_bytes(url: str, timeout: int = 300) -> bytes:
    with http_open(url, timeout=timeout) as resp:
        return resp.read()


def http_ok(url: str, timeout: int = 10) -> tuple[bool, str | None]:
    try:
        with http_open(url, timeout=timeout) as resp:
            return resp.status == 200, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
