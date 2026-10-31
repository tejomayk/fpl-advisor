"""Send alerts to stdout or to an ntfy server (phone push)."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from base64 import b64encode
from dataclasses import dataclass, field
from typing import Protocol

from fpl_advisor.config import Config


class NotifyError(RuntimeError):
    """Raised when a notification could not be delivered.

    `status` is the HTTP status the server answered with, or None for network errors and
    configuration problems.
    """

    def __init__(self, message: str = "", status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Notification:
    title: str
    body: str
    priority: int = 3  # ntfy scale: 1 (min) .. 5 (urgent)
    tags: list[str] = field(default_factory=list)
    click: str | None = None


class Notifier(Protocol):
    def send(self, n: Notification) -> None: ...


class StdoutNotifier:
    def send(self, n: Notification) -> None:
        print(f"[{n.title}] (priority {n.priority})")
        print(n.body)
        if n.click:
            print(n.click)
        print()


def _header_safe(text: str) -> str:
    """Make text safe for an HTTP header (ntfy decodes RFC 2047 encoded-words)."""
    text = " ".join(text.split())  # no newlines or control whitespace
    if text.isascii():
        return text
    encoded = b64encode(text.encode("utf-8")).decode("ascii")
    return f"=?UTF-8?B?{encoded}?="


def _ascii(text: str) -> str:
    return " ".join(text.encode("ascii", "ignore").decode("ascii").split())


class NtfyNotifier:
    def __init__(self, server: str, topic: str, token: str | None = None):
        self.server = server.rstrip("/")
        self.topic = topic
        self.token = token

    def build_request(self, n: Notification) -> urllib.request.Request:
        headers = {
            "Title": _header_safe(n.title),
            "Priority": str(max(1, min(5, int(n.priority)))),
        }
        tags = ",".join(t for t in (_ascii(t).replace(",", "") for t in n.tags) if t)
        if tags:
            headers["Tags"] = tags
        if n.click:
            headers["Click"] = _ascii(n.click)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(
            f"{self.server}/{self.topic}",
            data=n.body.encode("utf-8"),
            headers=headers,
            method="POST",
        )

    def send(self, n: Notification) -> None:
        req = self.build_request(n)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = getattr(resp, "status", 200)
        except urllib.error.HTTPError as e:
            raise NotifyError(
                f"ntfy server {self.server} rejected the notification: "
                f"HTTP {e.code} {e.reason}. Check the server URL, topic and token.",
                status=e.code,
            ) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NotifyError(f"Could not reach ntfy server {self.server}: {e}") from e
        if not 200 <= status < 300:
            raise NotifyError(
                f"ntfy server {self.server} returned HTTP {status}", status=status
            )


def get_notifier(cfg: Config) -> Notifier:
    if cfg.notify == "ntfy":
        if not cfg.ntfy_topic:
            raise NotifyError("notify is 'ntfy' but ntfy_topic is not set")
        return NtfyNotifier(cfg.ntfy_server, cfg.ntfy_topic, cfg.ntfy_token)
    return StdoutNotifier()


def run_url(env: dict[str, str] | None = None) -> str | None:
    """URL of the current GitHub Actions run, if running inside one."""
    e = os.environ if env is None else env
    server, repo, run_id = (
        e.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None
