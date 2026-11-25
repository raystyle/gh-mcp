"""A fake GitHub Search API for tests: records requests, answers from a handler, no network."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from gh_search_mcp.github import GitHubClient

Handler = Callable[[httpx.Request], httpx.Response]


class FakeGitHub:
    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    def client(self, token: str | None = "test-token") -> GitHubClient:
        return GitHubClient(token, transport=httpx.MockTransport(self))


def repo(full_name: str, private: bool = False, **extra: Any) -> dict[str, Any]:
    return {
        "full_name": full_name,
        "private": private,
        "html_url": f"https://github.com/{full_name}",
        "stargazers_count": 12,
        "language": "Python",
        "license": {"spdx_id": "MIT"},
        "pushed_at": "2026-01-02T03:04:05Z",
        **extra,
    }


def code(path: str, full_name: str = "owner/repo", private: bool = False, fragment: str = "x = 1") -> dict[str, Any]:
    return {
        "path": path,
        "html_url": f"https://github.com/{full_name}/blob/abc123/{path}",
        "repository": {"full_name": full_name, "private": private},
        "text_matches": [{"fragment": fragment}],
    }


def ok(items: list[dict[str, Any]], total: int | None = None, incomplete: bool = False) -> httpx.Response:
    return httpx.Response(
        200, json={"total_count": len(items) if total is None else total, "incomplete_results": incomplete, "items": items}
    )


def error(status: int, message: str = "", headers: dict[str, str] | None = None, **body: Any) -> httpx.Response:
    return httpx.Response(status, json={"message": message, **body}, headers=headers)
