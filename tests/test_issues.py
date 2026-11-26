"""search_issues / get_issue / get_pull_request against a fake GitHub. No network."""

from __future__ import annotations

import httpx
import pytest
from fake_github import FakeGitHub, issue_item, ok, routes
from mcp.server.mcpserver.exceptions import ToolError

from gh_mcp.github import MAX_BODY_LINES, GitHubError
from gh_mcp.render import DESCRIPTION_CHARS
from gh_mcp.render import issue as render_issue
from gh_mcp.render import issues as render_issues
from gh_mcp.server import AppContext, get_issue, search_issues

REPO = "/repos/o/r"
ISSUE = REPO + "/issues/7"
PULL = REPO + "/pulls/8"
PUBLIC = {"private": False, "default_branch": "main"}


def params_of(request: httpx.Request) -> dict[str, str]:
    return {key: value for key, value in request.url.params.multi_items()}


def public(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=PUBLIC)


def static(payload: object):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


# ---------- request building ----------


@pytest.mark.anyio
async def test_issue_search_adds_is_public_and_is_issue():
    fake = FakeGitHub(lambda request: ok([issue_item()], total=25))
    async with fake.client("tok") as client:
        result = await client.search_issues("memory leak")

    (request,) = fake.requests
    assert request.url.path == "/search/issues"
    assert params_of(request) == {
        "q": "memory leak is:issue is:public", "per_page": "20", "page": "1", "advanced_search": "true"
    }
    assert request.headers["Accept"] == "application/vnd.github.text-match+json"
    assert result["total_count"] == 25 and result["kind"] == "issue"


@pytest.mark.anyio
async def test_pull_request_search_uses_is_pr_and_sends_sort_when_asked():
    fake = FakeGitHub(lambda request: ok([issue_item(1, pull_request=True)]))
    async with fake.client() as client:
        await client.search_issues("fuzz", kind="pull_request", sort="updated", order="asc", limit=5, page=3)

    assert params_of(fake.requests[0]) == {
        "q": "fuzz is:pr is:public", "per_page": "5", "page": "3",
        "sort": "updated", "order": "asc", "advanced_search": "true",
    }


@pytest.mark.anyio
async def test_existing_is_public_qualifier_is_not_duplicated():
    fake = FakeGitHub(lambda request: ok([issue_item()]))
    async with fake.client() as client:
        await client.search_issues("crash is:public")
    assert params_of(fake.requests[0])["q"] == "crash is:public is:issue"


@pytest.mark.anyio
async def test_items_without_a_repository_url_are_dropped_and_counted():
    bad = issue_item()
    del bad["repository_url"]
    fake = FakeGitHub(lambda request: ok([issue_item(), bad]))
    async with fake.client() as client:
        result = await client.search_issues("x")
    assert len(result["items"]) == 1 and result["hidden_private"] == 1


@pytest.mark.anyio
async def test_invalid_arguments_are_rejected_before_any_request():
    fake = FakeGitHub(lambda request: ok([]))
    async with fake.client() as client:
        with pytest.raises(GitHubError, match="must not be empty"):
            await client.search_issues("  ")
    assert fake.requests == []


# ---------- get_issue / get_pull_request ----------


@pytest.mark.anyio
async def test_get_issue_checks_visibility_then_reads_the_issue():
    table = {REPO: public, ISSUE: static(issue_item(7, title="Crash on start", state="closed", comments=3, labels=["bug"]))}
    fake = FakeGitHub(routes(table))
    async with fake.client() as client:
        result = await client.get_issue("o", "r", 7)

    assert [request.url.path for request in fake.requests] == [REPO, ISSUE]
    assert result["number"] == 7 and result["title"] == "Crash on start"
    assert result["state"] == "closed" and result["labels"] == ["bug"] and result["comments"] == 3


@pytest.mark.anyio
async def test_get_pull_request_uses_the_pulls_endpoint():
    item = issue_item(8, title="Fix leak", state="open", pull_request=True)
    item["draft"] = True
    table = {REPO: public, PULL: static(item)}
    fake = FakeGitHub(routes(table))
    async with fake.client() as client:
        result = await client.get_pull_request("o", "r", 8)
    assert [request.url.path for request in fake.requests] == [REPO, PULL]
    assert result["number"] == 8


@pytest.mark.anyio
async def test_private_repository_is_refused_before_content_is_requested():
    for info in ({"private": True, "default_branch": "main"}, {"default_branch": "main"}):
        fake = FakeGitHub(routes({REPO: static(info)}))
        async with fake.client() as client:
            with pytest.raises(GitHubError, match="not a public repository"):
                await client.get_issue("o", "r", 7)
        assert [request.url.path for request in fake.requests] == [REPO]


@pytest.mark.anyio
async def test_not_found_and_invalid_numbers():
    issue_9 = REPO + "/issues/9"
    pull_9 = REPO + "/pulls/9"
    with pytest.raises(GitHubError, match="No issue #9 found in o/r"):
        async with FakeGitHub(routes({REPO: public, issue_9: lambda request: httpx.Response(404, json={})})).client() as client:
            await client.get_issue("o", "r", 9)

    with pytest.raises(GitHubError, match="No pull request #9 found in o/r"):
        async with FakeGitHub(routes({REPO: public, pull_9: lambda request: httpx.Response(404, json={})})).client() as client:
            await client.get_pull_request("o", "r", 9)

    fake = FakeGitHub(routes({REPO: public}))
    async with fake.client() as client:
        with pytest.raises(GitHubError, match="number must be 1 or greater"):
            await client.get_issue("o", "r", 0)
    assert fake.requests == []


@pytest.mark.anyio
async def test_body_is_clipped_to_the_line_and_character_budget():
    long_body = "".join(f"line {n}\n" for n in range(1, MAX_BODY_LINES + 50))
    table = {REPO: public, ISSUE: static(issue_item(7, body=long_body))}
    async with FakeGitHub(routes(table)).client() as client:
        result = await client.get_issue("o", "r", 7)
    assert len(result["body"]["lines"]) == MAX_BODY_LINES
    assert result["body"]["total_lines"] == MAX_BODY_LINES + 49
    assert result["body"]["truncated"] is True


@pytest.mark.anyio
async def test_none_body_is_rendered_as_empty():
    table = {REPO: public, ISSUE: static(issue_item(7, body=None))}
    async with FakeGitHub(routes(table)).client() as client:
        result = await client.get_issue("o", "r", 7)
    assert result["body"] == {"total_lines": 0, "lines": [], "truncated": False}


# ---------- rendering ----------


def search_result(items, total=None, *, kind="issue", hidden_private=0):
    return {
        "query": "q",
        "kind": kind,
        "total_count": len(items) if total is None else total,
        "incomplete_results": False,
        "items": items,
        "hidden_private": hidden_private,
    }


def test_issue_list_rendering():
    item = issue_item(1, title="Crash on start", state="closed", comments=2, labels=["bug", "crash"], reactions=7)
    text = render_issues(search_result([item], total=45), kind="issue", sort="best", order="desc", page=1, limit=20)
    assert text.startswith("Showing 1–1 of 45 public issues for: `q`\n")
    assert "1. Crash on start — issue #1 · closed · 2 comments · 7 reactions" in text
    assert "   owner/repo · opened by alice · https://github.com/owner/repo/issues/1" in text
    assert "   `bug` `crash`" in text
    assert "   steps to reproduce more detail" in text
    assert "More results exist; call again with page=2 to see them." in text


def test_pull_request_list_rendering():
    item = issue_item(5, title="Fix leak", state="open", pull_request=True, draft=True, user="bob")
    text = render_issues(search_result([item], total=1), kind="pull_request", sort="updated", order="asc", page=1, limit=20)
    assert text.startswith("Showing 1–1 of 1 public pull requests for: `q`\n")
    assert "1. Fix leak — pull request #5 · open draft" in text
    assert "   owner/repo · opened by bob · https://github.com/owner/repo/pull/5" in text
    assert "Sorted by updated (asc)." in text
    assert "More results" not in text


def test_issue_list_without_optional_fields():
    bare = {"number": 1, "title": "T", "state": "open", "body": None, "comments": 0, "labels": [],
            "user": None, "html_url": "u", "repository_url": "https://api.github.com/repos/o/r"}
    text = render_issues(search_result([bare]), kind="issue", sort="best", order="desc", page=1, limit=20)
    assert "1. T — issue #1 · open" in text
    assert "opened by ?" in text


def test_long_body_in_search_results_is_clipped():
    item = issue_item(1, body="x" * (DESCRIPTION_CHARS + 50))
    text = render_issues(search_result([item]), kind="issue", sort="best", order="desc", page=1, limit=20)
    assert "x" * (DESCRIPTION_CHARS - 1) + "…" in text


def test_issue_detail_rendering():
    result = {
        "owner": "o", "repo": "r", "number": 7, "title": "Crash on start", "state": "closed",
        "user": "alice", "created_at": "2026-01-02T03:04:05Z", "updated_at": "2026-02-03T04:05:06Z",
        "closed_at": None, "labels": ["bug", "crash"], "comments": 3,
        "html_url": "https://github.com/o/r/issues/7",
        "body": {"total_lines": 2, "lines": ["steps to reproduce", "more detail"], "truncated": False},
    }
    text = render_issue(result, "issue")
    assert text == (
        "`o/r` — issue #7: Crash on start\n"
        "closed · bug, crash · 3 comments · opened by alice · 2026-01-02\n"
        "https://github.com/o/r/issues/7\n"
        "Lines 1–2 of 2 of the description.\n"
        "\n"
        "steps to reproduce\n"
        "more detail\n"
    )


def test_issue_detail_with_truncated_body_and_empty_body():
    result = {
        "owner": "o", "repo": "r", "number": 1, "title": "T", "state": "open", "user": "u",
        "created_at": "", "updated_at": "", "closed_at": None, "labels": [], "comments": 0,
        "html_url": "h",
        "body": {"total_lines": 150, "lines": ["a", "b"], "truncated": True},
    }
    text = render_issue(result, "pull_request")
    assert "`o/r` — pull request #1: T\n" in text
    assert "Lines 1–2 of 150 of the description; clipped to the first 2 lines. Read the rest at the URL above." in text

    result["body"] = {"total_lines": 0, "lines": [], "truncated": False}
    text = render_issue(result, "issue")
    assert "The description is empty." in text


# ---------- tool logic ----------


@pytest.mark.anyio
async def test_tool_logic_search_lists_issues_and_get_issue_reads_one():
    async with FakeGitHub(lambda request: ok([issue_item(1, title="Crash")])).client() as client:
        app = AppContext(client=client, has_token=True)
        text = await search_issues(app, "x")
        assert "1. Crash — issue #1" in text

    table = {REPO: public, ISSUE: static(issue_item(7, title="T"))}
    async with FakeGitHub(routes(table)).client() as client:
        app = AppContext(client=client, has_token=True)
        text = await get_issue(app, "o", "r", 7)
        assert "`o/r` — issue #7: T" in text
        assert "The description is empty." not in text  # body present by default


@pytest.mark.anyio
async def test_tool_logic_get_pull_request_uses_the_pulls_endpoint():
    table = {REPO: public, PULL: static(issue_item(8, title="Fix", pull_request=True))}
    async with FakeGitHub(routes(table)).client() as client:
        app = AppContext(client=client, has_token=True)
        text = await get_issue(app, "o", "r", 8, kind="pull_request")
        assert "`o/r` — pull request #8: Fix" in text


@pytest.mark.anyio
async def test_tool_logic_maps_github_errors_to_tool_errors():
    table = {REPO: public, ISSUE: lambda request: httpx.Response(500, json={"message": "boom"})}
    with pytest.raises(ToolError, match="HTTP 500"):
        async with FakeGitHub(routes(table)).client() as client:
            await get_issue(AppContext(client=client, has_token=True), "o", "r", 7)
