"""A fake GitHub Search API for tests: records requests, answers from a handler, no network."""

from __future__ import annotations

import base64
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


def _raw(text: str | bytes) -> bytes:
    return text.encode("utf-8") if isinstance(text, str) else text


def file_entry(
    path: str, text: str | bytes = "hello\n", *, sha: str = "f" * 40, size: int | None = None, encoding: str | None = "base64"
) -> dict[str, Any]:
    """A `/repos/{}/contents/{path}` response for a file."""
    raw = _raw(text)
    return {
        "type": "file",
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "sha": sha,
        "size": len(raw) if size is None else size,
        "encoding": encoding,
        "content": base64.b64encode(raw).decode("ascii") if encoding == "base64" else "",
    }


def entry(name: str, path: str | None = None, kind: str = "dir", size: int = 0, **extra: Any) -> dict[str, Any]:
    """One element of a directory listing."""
    return {"name": name, "path": path if path is not None else name, "type": kind, "size": size, **extra}


def tree(*paths: str, truncated: bool = False) -> httpx.Response:
    """A recursive Git Trees response; a path ending in `/` becomes a directory."""
    items = [
        {"path": p.rstrip("/"), "type": "tree" if p.endswith("/") else "blob", "size": None if p.endswith("/") else 10}
        for p in paths
    ]
    return httpx.Response(200, json={"sha": "t" * 40, "truncated": truncated, "tree": items})


def routes(table: dict[str, Handler], default: Handler | None = None) -> Handler:
    """Answer requests by path; anything unexpected fails the test."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in table:
            return table[request.url.path](request)
        if default is not None:
            return default(request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return handler
