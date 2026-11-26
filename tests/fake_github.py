"""A fake GitHub Search API for tests: records requests, answers from a handler, no network."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import httpx

from gh_mcp.github import GitHubClient

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


def issue_item(
    number: int = 1,
    full_name: str = "owner/repo",
    title: str = "A bug",
    *,
    state: str = "open",
    body: str | None = "steps to reproduce\nmore detail",
    comments: int = 0,
    labels: list[str] | None = None,
    user: str = "alice",
    reactions: int = 0,
    draft: bool = False,
    pull_request: bool = False,
    state_reason: str | None = None,
) -> dict[str, Any]:
    """A `/search/issues` result item, or a `/repos/{}/{}/issues|pulls/{n}` response."""
    item: dict[str, Any] = {
        "number": number,
        "title": title,
        "state": state,
        "state_reason": state_reason,
        "body": body,
        "comments": comments,
        "labels": [{"name": name} for name in labels or []],
        "user": {"login": user},
        "html_url": f"https://github.com/{full_name}/issues/{number}",
        "repository_url": f"https://api.github.com/repos/{full_name}",
        "created_at": "2026-01-02T03:04:05Z",
        "updated_at": "2026-02-03T04:05:06Z",
        "closed_at": None,
        "reactions": {"total_count": reactions},
    }
    if draft:
        item["draft"] = True
    if pull_request:
        item["pull_request"] = {"url": "https://api.github.com/repos/owner/repo/pulls/1"}
        item["html_url"] = f"https://github.com/{full_name}/pull/{number}"
    return item


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
