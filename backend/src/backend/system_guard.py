"""Local-only lock for process-control and queue-admin routes."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from .settings import settings

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def client_host(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return ""


def require_local_system(request: Request) -> None:
    host = client_host(request)
    if host not in LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="System controls are limited to this machine")
    expected = (settings.system_token or "").strip()
    if not expected:
        return
    provided = request.headers.get("X-Cutline-System-Token", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Invalid system token")
