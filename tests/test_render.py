"""Rendering of search results to markdown."""

from fake_github import code, repo
from gh_search_mcp.render import DESCRIPTION_CHARS, FRAGMENT_CHARS, FRAGMENT_LINES, code as render_code
from gh_search_mcp.render import repositories as render_repositories


def result(items: list[dict], total: int | None = None, *, incomplete: bool = False, hidden_private: int = 0) -> dict:
    return {
        "query": "q",
        "total_count": len(items) if total is None else total,
        "incomplete_results": incomplete,
        "items": items,
        "hidden_private": hidden_private,
    }


# ---------- repositories ----------


def test_repository_header_window_sort_note_and_next_page_hint():
    text = render_repositories(
        result([repo("a/b"), repo("c/d")], total=45, incomplete=True), sort="stars", order="asc", page=2, limit=20
    )
    assert text.startswith("Showing 21–22 of 45 public repositories for: `q`\n")
    assert "GitHub reports this result set may be incomplete." in text
    assert "Sorted by stars (asc)." in text
    assert "21. a/b" in text and "22. c/d" in text
    assert "More results exist; call again with page=3 to see them." in text


def test_last_page_has_no_next_page_hint_and_relevance_has_no_sort_note():
    text = render_repositories(result([repo("a/b")], total=1), sort="best", order="desc", page=1, limit=20)
    assert text.startswith("Showing 1–1 of 1 public repositories for: `q`\n")
    assert "More results exist" not in text
    assert "Sorted by" not in text


def test_repository_facts_and_metadata_line():
    text = render_repositories(result([repo("a/b", archived=True)]), sort="best", order="desc", page=1, limit=20)
    assert "1. a/b — ★ 12 · Python · MIT · archived" in text
    assert "   https://github.com/a/b · pushed 2026-01-02" in text


def test_optional_repository_facts_are_omitted_when_absent():
    bare = {"full_name": "a/b", "private": False, "html_url": "u", "stargazers_count": 0,
            "language": None, "license": {"spdx_id": "NOASSERTION"}}
    text = render_repositories(result([bare]), sort="best", order="desc", page=1, limit=20)
    assert "1. a/b — ★ 0\n" in text


def test_description_is_flattened_and_clipped():
    text = render_repositories(result([repo("a/b", description="a  b\n\nc " + "x" * DESCRIPTION_CHARS)]),
                               sort="best", order="desc", page=1, limit=20)
    line = next(row for row in text.splitlines() if row.startswith("   a b c "))
    assert len(line.strip()) == DESCRIPTION_CHARS and line.endswith("…")


def test_empty_results_and_hidden_private_note():
    text = render_repositories(result([], hidden_private=2), sort="best", order="desc", page=1, limit=20)
    assert text.startswith("Showing 0 of 0 public repositories")
    assert "No public repositories matched." in text
    assert "2 result(s) from private repositories were hidden." in text


# ---------- code ----------


def test_code_renders_path_fragments_and_url():
    text = render_code(result([code("src/a.py", fragment="line one\nline two")], total=10), page=1, limit=20)
    assert text.startswith("Showing 1–1 of 10 code results in public repositories for: `q`\n")
    assert "1. owner/repo: src/a.py" in text
    assert "       line one\n       line two" in text
    assert "   https://github.com/owner/repo/blob/abc123/src/a.py" in text


def test_fragments_are_capped_in_count_lines_and_length():
    rows = "\n".join(f"row {n}" for n in range(FRAGMENT_LINES + 3))
    item = {
        "path": "p",
        "html_url": "u",
        "repository": {"full_name": "o/r"},
        "text_matches": [{"fragment": rows}, {"fragment": "w" * (FRAGMENT_CHARS + 50)}, {"fragment": "third"}],
    }
    text = render_code(result([item]), page=1, limit=20)
    assert "row 7" in text and "row 8" not in text
    assert text.count("w" * (FRAGMENT_CHARS - 1) + "…") == 1
    assert "third" not in text


def test_code_without_fragments_still_lists_path_and_url():
    item = {"path": "p", "html_url": "u", "repository": {"full_name": "o/r"}}
    text = render_code(result([item]), page=1, limit=20)
    assert "1. o/r: p\n   u" in text


def test_empty_code_results():
    text = render_code(result([]), page=1, limit=20)
    assert "No code in public repositories matched." in text
