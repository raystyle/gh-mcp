"""Async client for the GitHub Search API endpoints this server exposes.

Only public data leaves this module: repository queries carry the ``is:public``
qualifier so GitHub filters them server-side, and code results that come from
private repositories are dropped before the caller sees them.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx

from . import __version__

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"

MAX_QUERY_LENGTH = 256
"""GitHub's limit for the search ``q`` parameter, in characters."""

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
"""Per-call result cap. GitHub allows 100; the cap keeps tool output small."""

RepoSort = Literal["best", "stars", "forks", "help-wanted-issues", "updated"]
Order = Literal["desc", "asc"]


class GitHubError(Exception):
    """A failure reported to the caller as a tool error. The message is user-facing."""


def _check_page_args(query: str, limit: int, page: int) -> None:
    if not query or not query.strip():
        raise GitHubError("query must not be empty.")
    if len(query) > MAX_QUERY_LENGTH:
        raise GitHubError(f"query is {len(query)} characters; GitHub allows at most {MAX_QUERY_LENGTH}.")
    if not 1 <= limit <= MAX_LIMIT:
        raise GitHubError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}.")
    if page < 1:
        raise GitHubError(f"page must be 1 or greater, got {page}.")


def _public_query(query: str) -> str:
    """Add ``is:public`` unless the caller already wrote it."""
    if "is:public" in query.lower():
        return query
    return f"{query} is:public"


def _is_public_repo(item: dict[str, Any]) -> bool:
    return not item.get("private")


def _is_public_code(item: dict[str, Any]) -> bool:
    return not (item.get("repository") or {}).get("private")


def _split_rate_limited(response: httpx.Response) -> tuple[bool, str]:
    """Report whether GitHub is rate limiting, and for how long to wait."""
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        return True, retry_after
    if response.headers.get("x-ratelimit-remaining") == "0":
        reset = response.headers.get("x-ratelimit-reset", "")
        if reset.isdigit():
            return True, str(max(0, int(reset) - int(time.time())))
    return False, ""


def _error_message(response: httpx.Response, path: str) -> str:
    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = ""
    if isinstance(body, dict):
        detail = str(body.get("message") or "")
        errors = body.get("errors")
        if isinstance(errors, list):
            extra = "; ".join(str(e.get("message", e)) for e in errors if isinstance(e, dict))
            if extra:
                detail = f"{detail} ({extra[:300]})"

    if status in (401, 403) and "bad credentials" in detail.lower():
        return "GitHub rejected the token. Check that GITHUB_PAT holds a valid, unexpired token."
    limited, wait = _split_rate_limited(response)
    if status == 429 or (status == 403 and limited) or "rate limit" in detail.lower():
        if wait.isdigit():
            minutes, seconds = divmod(int(wait), 60)
            when = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
        else:
            when = "about a minute"
        return f"GitHub search rate limit reached (HTTP {status}). Retry in {when}."
    if status == 401:
        return "GitHub requires a token for this search (HTTP 401). Set GITHUB_PAT and try again."
    if status == 422:
        return f"GitHub rejected the query (HTTP 422): {detail or 'validation failed'}."
    return f"GitHub API returned HTTP {status} for {path}{': ' + detail if detail else ''}."


def _describe(exc: BaseException) -> str:
    """Name the exception class too: some low-level errors carry an empty message."""
    parts = [type(exc).__name__]
    text = str(exc)
    if text:
        parts.append(text)
    cause = exc.__cause__
    if cause is not None and str(cause) and str(cause) not in text:
        parts.append(f"caused by {type(cause).__name__}: {cause}")
    return ": ".join(parts)


class GitHubClient:
    """A thin async wrapper over ``GET /search/repositories`` and ``GET /search/code``."""

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = API_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"gh-search-mcp/{__version__}",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def search_repositories(
        self,
        query: str,
        *,
        sort: RepoSort = "best",
        order: Order = "desc",
        limit: int = DEFAULT_LIMIT,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search public repositories. ``sort="best"`` leaves GitHub's relevance ranking in place."""
        _check_page_args(query, limit, page)
        qualified = _public_query(query)
        params: dict[str, Any] = {"q": qualified, "per_page": limit, "page": page}
        if sort != "best":
            params["sort"] = sort
            params["order"] = order
        data = await self._get("/search/repositories", params)
        raw = data.get("items") or []
        items = [item for item in raw if _is_public_repo(item)]
        return {
            "query": qualified,
            "total_count": int(data.get("total_count") or 0),
            "incomplete_results": bool(data.get("incomplete_results")),
            "items": items,
            "hidden_private": len(raw) - len(items),
        }

    async def search_code(
        self,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search code in public repositories. Requires a token (GitHub returns 401 without one)."""
        _check_page_args(query, limit, page)
        params = {"q": query, "per_page": limit, "page": page}
        data = await self._get("/search/code", params, accept="application/vnd.github.text-match+json")
        raw = data.get("items") or []
        items = [item for item in raw if _is_public_code(item)]
        return {
            "query": query,
            "total_count": int(data.get("total_count") or 0),
            "incomplete_results": bool(data.get("incomplete_results")),
            "items": items,
            "hidden_private": len(raw) - len(items),
        }

    async def _get(self, path: str, params: dict[str, Any], *, accept: str | None = None) -> dict[str, Any]:
        headers = {"Accept": accept} if accept else {}
        try:
            response = await self._client.get(path, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise GitHubError(f"GitHub API timed out ({type(exc).__name__}). Try again or narrow the query.") from exc
        except httpx.HTTPError as exc:
            raise GitHubError(f"Cannot reach the GitHub API. {_describe(exc)}") from exc
        if response.status_code != 200:
            raise GitHubError(_error_message(response, path))
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubError(f"GitHub API returned a non-JSON response (HTTP {response.status_code}).") from exc
        if not isinstance(payload, dict):
            raise GitHubError("GitHub API returned an unexpected response shape.")
        return payload
