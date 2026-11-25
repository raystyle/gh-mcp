"""Turn search results into compact markdown for the model to read."""

from __future__ import annotations

from typing import Any

from gh_mcp.github import human_size

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


# ---------- get_file_contents ----------


def _count(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _at(result: dict[str, Any]) -> str:
    return f"`{result['owner']}/{result['repo']}` at `{result['ref']}`"


def directory(result: dict[str, Any]) -> str:
    entries = sorted(result["entries"], key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))
    dirs = sum(1 for entry in entries if entry["type"] == "dir")
    files = len(entries) - dirs
    where = f"`{result['path']}/`" if result["path"] else "`/`"
    lines = [
        f"{_at(result)} — directory {where} ("
        + (f"{_count(len(entries), 'entry', 'entries')}: " if len(entries) != 1 else "")
        + f"{dirs} director{'y' if dirs == 1 else 'ies'}, {_count(files, 'file')})"
    ]
    if not entries:
        lines.append("The directory is empty.")
    labels = {"dir": "DIR", "file": "FILE", "symlink": "LINK", "submodule": "SUBMODULE"}
    for entry in entries:
        kind = entry["type"]
        name = entry["name"] + ("/" if kind == "dir" else "")
        if kind == "file":
            detail = f" ({human_size(entry['size'])})" if entry["size"] else ""
        elif kind == "symlink":
            detail = f" -> {entry.get('target') or '?'}"
        else:
            detail = ""
        label = labels.get(kind, kind.upper() or "?")
        lines.append(f"{label:<8} {name}{detail}")
    lines.append("Open an entry with get_file_contents and its path.")
    return "\n".join(lines) + "\n"


def _nest(entries: list[dict[str, Any]], base: int) -> dict[str, Any]:
    root: dict[str, Any] = {}
    for entry in entries:
        parts = entry["path"][base:].split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if node is None:  # a file where a directory was expected: inconsistent data, skip it
                break
        else:
            node.setdefault(parts[-1], None if entry["type"] == "blob" else {})
    return root


def _walk(node: dict[str, Any], depth: int, out: list[str]) -> None:
    for name in sorted(node, key=lambda n: (node[n] is None, n.lower())):
        if node[name] is None:
            out.append("  " * depth + name)
        else:
            out.append("  " * depth + name + "/")
            _walk(node[name], depth + 1, out)


def tree(result: dict[str, Any]) -> str:
    shown = result["entries"]
    where = f"`{result['path']}/`" if result["path"] else "the repository root"
    lines = [f"{_at(result)} — tree under {where} ({_count(result['total'], 'entry', 'entries')})"]
    if not shown:
        lines.append("The tree is empty.")
    base = len(result["path"]) + 1 if result["path"] else 0
    _walk(_nest(shown, base), 0, lines)
    notes: list[str] = []
    if result["total"] > len(shown):
        notes.append(f"Showing the first {len(shown)} of {result['total']} entries. Narrow with path=<directory>.")
    if result.get("api_truncated"):
        notes.append("GitHub truncated this tree because the repository is very large; entries beyond its limit are missing.")
    for note in notes:
        lines.extend(["", note])
    return "\n".join(lines) + "\n"


def file(result: dict[str, Any]) -> str:
    header = (
        f"{_at(result)} — file `{result['path']}`: "
        f"{_count(result['total_lines'], 'line')}, {human_size(result['size'])}"
    )
    if result.get("sha"):
        header += f", blob {result['sha'][:12]}"
    lines = [header]
    total = result["total_lines"]
    if total == 0:
        lines.append("The file is empty.")
        return "\n".join(lines) + "\n"

    start, end = result["start_line"], result["end_line"]
    window = f"Lines {start}–{end} of {total}"
    if result.get("stop_reason") == "long_line":
        shown_chars = len(result["lines"][0])
        lines.append(f"{window}; line {start} is longer than the output limit, so only its first {shown_chars:,} characters are shown.")
    elif end >= total:
        lines.append(window + ".")
    elif result.get("stop_reason") == "chars":
        lines.append(f"{window}; stopped because the output limit was reached. Continue with start_line={end + 1}.")
    else:
        lines.append(f"{window}. Continue with start_line={end + 1}.")
    lines.append("")
    lines.extend(result["lines"])
    return "\n".join(lines) + "\n"


def file_contents(result: dict[str, Any]) -> str:
    """Render whatever ``get_file_contents`` returned: a file, a directory listing, or a tree."""
    if result["kind"] == "file":
        return file(result)
    if result["kind"] == "tree":
        return tree(result)
    return directory(result)
