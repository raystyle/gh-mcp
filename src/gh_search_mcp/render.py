"""Turn search results into compact markdown for the model to read."""

from __future__ import annotations

from typing import Any

DESCRIPTION_CHARS = 200
FRAGMENT_CHARS = 400
FRAGMENT_LINES = 8
FRAGMENTS_PER_ITEM = 2


def _clip(text: Any, limit: int) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _date(iso: str | None) -> str:
    return (iso or "")[:10]


def _window(page: int, count: int, limit: int) -> str:
    first = (page - 1) * limit + 1
    return f"{first:,}–{first + count - 1:,}" if count else "0"


def _footer(result: dict[str, Any], page: int, limit: int) -> list[str]:
    notes: list[str] = []
    if result.get("hidden_private"):
        notes.append(f"{result['hidden_private']} result(s) from private repositories were hidden.")
    if page * limit < result["total_count"]:
        notes.append(f"More results exist; call again with page={page + 1} to see them.")
    return notes


def repositories(result: dict[str, Any], *, sort: str, order: str, page: int, limit: int) -> str:
    items: list[dict[str, Any]] = result["items"]
    lines = [f"Showing {_window(page, len(items), limit)} of {result['total_count']:,} public repositories for: `{result['query']}`"]
    if result.get("incomplete_results"):
        lines.append("GitHub reports this result set may be incomplete.")
    if sort != "best":
        lines.append(f"Sorted by {sort} ({order}).")
    if not items:
        lines.append("No public repositories matched.")
    for number, item in enumerate(items, start=(page - 1) * limit + 1):
        facts = [f"★ {item.get('stargazers_count', 0):,}"]
        if item.get("language"):
            facts.append(str(item["language"]))
        license_spdx = (item.get("license") or {}).get("spdx_id")
        if license_spdx and license_spdx != "NOASSERTION":
            facts.append(license_spdx)
        if item.get("archived"):
            facts.append("archived")
        lines.append("")
        lines.append(f"{number}. {item.get('full_name', '?')} — " + " · ".join(facts))
        if item.get("description"):
            lines.append(f"   {_clip(item['description'], DESCRIPTION_CHARS)}")
        lines.append(f"   {item.get('html_url', '')} · pushed {_date(item.get('pushed_at'))}")
    lines.extend(note for note in _footer(result, page, limit))
    return "\n".join(lines) + "\n"


def _fragments(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for match in (item.get("text_matches") or [])[:FRAGMENTS_PER_ITEM]:
        fragment = str((match or {}).get("fragment") or "").strip("\n")
        if not fragment.strip():
            continue
        lines = fragment.splitlines()[:FRAGMENT_LINES]
        text = "\n".join(lines)
        if len(text) > FRAGMENT_CHARS:
            text = text[: FRAGMENT_CHARS - 1] + "…"
        out.append(text)
    return out


def code(result: dict[str, Any], *, page: int, limit: int) -> str:
    items: list[dict[str, Any]] = result["items"]
    lines = [f"Showing {_window(page, len(items), limit)} of {result['total_count']:,} code results in public repositories for: `{result['query']}`"]
    if result.get("incomplete_results"):
        lines.append("GitHub reports this result set may be incomplete.")
    if not items:
        lines.append("No code in public repositories matched.")
    for number, item in enumerate(items, start=(page - 1) * limit + 1):
        repo = (item.get("repository") or {}).get("full_name", "?")
        lines.append("")
        lines.append(f"{number}. {repo}: {item.get('path', '?')}")
        for fragment in _fragments(item):
            lines.append("")
            lines.extend("       " + row for row in fragment.splitlines())
        lines.append(f"   {item.get('html_url', '')}")
    lines.extend(note for note in _footer(result, page, limit))
    return "\n".join(lines) + "\n"
