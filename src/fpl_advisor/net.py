"""Request helpers shared by the web app and the alerts service."""

from __future__ import annotations

import os

from fastapi import Request


def client_ip(request: Request) -> str:
    """The visitor's IP.

    Uvicorn rewrites `request.client` from X-Forwarded-For only for proxies listed in
    FORWARDED_ALLOW_IPS (see web.main), so this never parses the header itself: a header a
    visitor can set must not decide their rate limit.

    On Vercel (VERCEL=1, set by the platform) there is no uvicorn in front; Vercel's edge sets
    `x-real-ip` to the visitor's address itself and overwrites any value the visitor sent, so
    that header is trusted there, and only there.
    """
    if os.environ.get("VERCEL") == "1":
        ip = (request.headers.get("x-real-ip") or "").strip()
        if ip:
            return ip
    return request.client.host if request.client else "unknown"
