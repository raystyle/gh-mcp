"""Async client for the GitHub Search API endpoints this server exposes.

Only public data leaves this module: repository queries carry the ``is:public``
qualifier so GitHub filters them server-side, and code results that come from
private repositories are dropped before the caller sees them.
"""

from __future__ import annotations

import base64
import binascii
import time
from typing import Any, Literal
from urllib.parse import quote

import httpx

from . import __version__

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"

MAX_QUERY_LENGTH = 256
"""GitHub's limit for the search ``q`` parameter, in characters."""

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
"""Per-call result cap. GitHub allows 100; the cap keeps tool output small."""

MAX_LINES = 400
"""Lines returned per file read at most."""

MAX_CHARS = 32_000
"""Characters returned per file read at most."""

MAX_TREE_ENTRIES = 400
"""Entries listed per recursive directory tree at most."""

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


def _repo_names(owner: str, repo: str) -> tuple[str, str]:
    owner, repo = owner.strip(), repo.strip()
    for label, value in (("owner", owner), ("repo", repo)):
        if not value or len(value) > 100 or "/" in value or any(ch.isspace() for ch in value):
            raise GitHubError(f"{label} must be a single GitHub {label} name without spaces or slashes, got {value!r}.")
    return owner, repo


def _split_lines(text: str) -> list[str]:
    """Split on newlines only, drop the trailing empty line and CR line endings."""
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        kb = size / 1024
        return f"{kb:.0f} KB" if kb >= 100 else f"{kb:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


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

    async def get_file_contents(
        self,
        owner: str,
        repo: str,
        *,
        path: str = "",
        ref: str | None = None,
        recursive: bool = False,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        """Read a text file, list a directory, or (with ``recursive``) show a directory tree of a public repository."""
        owner, repo = _repo_names(owner, repo)
        path = "/".join(part for part in (path or "").strip().strip("/").split("/") if part)
        if ref is not None and not ref.strip():
            ref = None
        if start_line < 1:
            raise GitHubError(f"start_line must be 1 or greater, got {start_line}.")
        if end_line is not None and end_line < start_line:
            raise GitHubError("end_line must not be before start_line.")

        # The visibility check is what keeps this public-only: the contents endpoint
        # itself does not say whether the repository is private.
        info = await self._require_public(owner, repo)
        shown_ref = ref or str(info.get("default_branch") or "default branch")
        if recursive:
            return await self._tree(owner, repo, path, shown_ref)

        where = f"{owner}/{repo} at `{shown_ref}`"
        data = await self._get(
            f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}",
            {"ref": ref} if ref else {},
            not_found=f"Nothing found at `{path or '/'}` in {where}. Check the path and ref.",
        )
        if isinstance(data, list):
            entries = [
                {
                    "name": str(entry.get("name", "")),
                    "path": str(entry.get("path", "")),
                    "type": str(entry.get("type", "")),
                    "size": int(entry.get("size") or 0),
                    "target": entry.get("target"),
                }
                for entry in data
                if isinstance(entry, dict)
            ]
            return {"kind": "directory", "owner": owner, "repo": repo, "ref": shown_ref, "path": path, "entries": entries}
        if isinstance(data, dict) and data.get("type") == "file":
            return self._file(owner, repo, shown_ref, path, data, start_line, end_line)
        kind = data.get("type") if isinstance(data, dict) else "unknown"
        raise GitHubError(f"`{path}` in {where} is a {kind}, not a file or directory.")

    async def _require_public(self, owner: str, repo: str) -> dict[str, Any]:
        info = await self._get(
            f"/repos/{owner}/{repo}",
            {},
            not_found=(
                f"Repository {owner}/{repo} was not found. It may not exist, or it may be private: "
                "private repositories are never served."
            ),
        )
        if info.get("private", True):  # a missing flag counts as not public
            raise GitHubError(f"{owner}/{repo} is not a public repository. Only public repositories are served.")
        return info

    async def _tree(self, owner: str, repo: str, path: str, shown_ref: str) -> dict[str, Any]:
        data = await self._get(
            f"/repos/{owner}/{repo}/git/trees/{quote(shown_ref, safe='')}",
            {"recursive": "1"},
            not_found=f"No tree at `{shown_ref}` in {owner}/{repo}. Check the ref.",
        )
        api_truncated = bool(data.get("truncated"))
        prefix = f"{path}/" if path else ""
        entries = []
        for entry in data.get("tree") or []:
            if not isinstance(entry, dict):
                continue
            entry_path = str(entry.get("path") or "")
            if entry_path == path or (prefix and not entry_path.startswith(prefix)):
                continue
            entries.append({"path": entry_path, "type": entry.get("type"), "size": int(entry.get("size") or 0)})
        if path and not entries:
            if api_truncated:
                raise GitHubError(
                    f"`{path}` is not within the part of the tree GitHub returned for {owner}/{repo} "
                    "(the tree is truncated). List its parent directory without `recursive`."
                )
            raise GitHubError(f"No directory `{path}` in {owner}/{repo} at `{shown_ref}`.")
        return {
            "kind": "tree",
            "owner": owner,
            "repo": repo,
            "ref": shown_ref,
            "path": path,
            "entries": entries[:MAX_TREE_ENTRIES],
            "total": len(entries),
            "api_truncated": api_truncated,
        }

    @staticmethod
    def _file(
        owner: str,
        repo: str,
        shown_ref: str,
        path: str,
        data: dict[str, Any],
        start_line: int,
        end_line: int | None,
    ) -> dict[str, Any]:
        where = f"`{path}` in {owner}/{repo} at `{shown_ref}`"
        size = int(data.get("size") or 0)
        content, encoding = data.get("content"), data.get("encoding")
        if content is None or encoding != "base64":
            raise GitHubError(f"{where} is {human_size(size)}: too large to read (GitHub serves file content up to 1 MB).")
        try:
            raw = base64.b64decode("".join(content.split()), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise GitHubError(f"GitHub returned content for {where} that is not valid base64.") from exc
        try:
            text = raw.decode("utf-8").lstrip("\ufeff")
        except UnicodeDecodeError:
            text = None
        if text is None or "\x00" in text:
            raise GitHubError(f"{where} is a binary file ({human_size(size)}); only text files are shown.")

        result: dict[str, Any] = {
            "kind": "file",
            "owner": owner,
            "repo": repo,
            "ref": shown_ref,
            "path": path,
            "sha": str(data.get("sha") or ""),
            "size": size,
            "total_lines": 0,
        }
        lines = _split_lines(text)
        result["total_lines"] = len(lines)
        if not lines:
            return {**result, "start_line": 0, "end_line": 0, "stop_reason": None, "lines": []}
        if start_line > len(lines):
            word = "line" if len(lines) == 1 else "lines"
            raise GitHubError(f"start_line is {start_line} but {where} has only {len(lines)} {word}.")
        last_allowed = min(end_line if end_line is not None else len(lines), len(lines), start_line + MAX_LINES - 1)
        shown: list[str] = []
        used = 0
        stop_reason: str | None = None
        for number in range(start_line, last_allowed + 1):
            line = lines[number - 1]
            if used + len(line) + 1 > MAX_CHARS:
                if shown:
                    stop_reason = "chars"
                    break
                line = line[:MAX_CHARS]  # always show at least where the first line starts
                shown.append(line)
                stop_reason = "long_line"
                break
            shown.append(line)
            used += len(line) + 1
        end = start_line + len(shown) - 1
        if stop_reason is None and end < len(lines):
            stop_reason = "lines"
        return {**result, "start_line": start_line, "end_line": end, "stop_reason": stop_reason, "lines": shown}

    async def _get(
        self,
        path: str,
        params: dict[str, Any],
        *,
        accept: str | None = None,
        not_found: str | None = None,
    ) -> Any:
        headers = {"Accept": accept} if accept else {}
        try:
            response = await self._client.get(path, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise GitHubError(f"GitHub API timed out ({type(exc).__name__}). Try again or narrow the query.") from exc
        except httpx.HTTPError as exc:
            raise GitHubError(f"Cannot reach the GitHub API. {_describe(exc)}") from exc
        if response.status_code == 404 and not_found:
            raise GitHubError(not_found)
        if response.status_code != 200:
            raise GitHubError(_error_message(response, path))
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubError(f"GitHub API returned a non-JSON response (HTTP {response.status_code}).") from exc
        if not isinstance(payload, (dict, list)):  # directory listings are JSON arrays
            raise GitHubError("GitHub API returned an unexpected response shape.")
        return payload
