"""Request helpers shared by the web app and the alerts service."""

from __future__ import annotations

from fastapi import Request


def client_ip(request: Request) -> str:
    """The visitor's IP.

    Uvicorn rewrites `request.client` from X-Forwarded-For only for proxies listed in
    FORWARDED_ALLOW_IPS (see web.main), so this never parses the header itself: a header a
    visitor can set must not decide their rate limit.
    """
    return request.client.host if request.client else "unknown"
