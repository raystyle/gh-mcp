"""GitHubClient behaviour against a fake transport. No network access."""

from __future__ import annotations

import re
import time

import httpx
import pytest
from fake_github import FakeGitHub, code, error, ok, repo

from gh_mcp.github import MAX_LIMIT, MAX_QUERY_LENGTH, GitHubClient, GitHubError, _public_query
from gh_mcp.server import TOKEN_ENV_VARS, token_from_env


def params_of(request: httpx.Request) -> dict[str, str]:
    return {key: value for key, value in request.url.params.multi_items()}


# ---------- request building ----------


@pytest.mark.anyio
async def test_repository_search_adds_is_public_and_default_paging():
    fake = FakeGitHub(lambda request: ok([repo("a/b")], total=25))
    async with fake.client("tok") as client:
        result = await client.search_repositories("mcp server")

    (request,) = fake.requests
    assert request.url.path == "/search/repositories"
    assert params_of(request) == {"q": "mcp server is:public", "per_page": "20", "page": "1"}
    assert request.headers["Authorization"] == "Bearer tok"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert result["total_count"] == 25
    assert [item["full_name"] for item in result["items"]] == ["a/b"]


@pytest.mark.anyio
async def test_repository_search_sends_sort_and_order_only_when_asked():
    fake = FakeGitHub(lambda request: ok([repo("a/b")]))
    async with fake.client() as client:
        await client.search_repositories("x", sort="stars", order="asc", limit=5, page=3)

    assert params_of(fake.requests[0]) == {"q": "x is:public", "per_page": "5", "page": "3", "sort": "stars", "order": "asc"}


@pytest.mark.parametrize("query", ["foo IS:Public", "is:public foo", "foo is:public"])
def test_existing_is_public_qualifier_is_not_duplicated(query: str):
    assert _public_query(query) == query


@pytest.mark.anyio
async def test_code_search_requests_text_matches_and_keeps_query_as_is():
    fake = FakeGitHub(lambda request: ok([code("a.py")], total=3))
    async with fake.client("tok") as client:
        result = await client.search_code("O_NOFOLLOW language:python", limit=2, page=2)

    (request,) = fake.requests
    assert request.url.path == "/search/code"
    assert params_of(request) == {"q": "O_NOFOLLOW language:python", "per_page": "2", "page": "2"}
    assert request.headers["Accept"] == "application/vnd.github.text-match+json"
    assert result["total_count"] == 3


@pytest.mark.anyio
async def test_no_authorization_header_without_token():
    fake = FakeGitHub(lambda request: ok([]))
    async with fake.client(None) as client:
        await client.search_repositories("x")
    assert "Authorization" not in fake.requests[0].headers


# ---------- public-only filtering ----------


@pytest.mark.anyio
async def test_private_repositories_are_dropped_and_counted():
    fake = FakeGitHub(lambda request: ok([repo("a/b"), repo("c/d", private=True), repo("e/f", private=True)]))
    async with fake.client() as client:
        result = await client.search_repositories("x")
    assert [item["full_name"] for item in result["items"]] == ["a/b"]
    assert result["hidden_private"] == 2


@pytest.mark.anyio
async def test_code_from_private_repositories_is_dropped_and_counted():
    fake = FakeGitHub(lambda request: ok([code("a.py"), code("secret.py", "corp/vault", private=True)]))
    async with fake.client() as client:
        result = await client.search_code("password")
    assert [item["path"] for item in result["items"]] == ["a.py"]
    assert result["hidden_private"] == 1


@pytest.mark.anyio
async def test_missing_private_flag_is_treated_as_public():
    item = code("a.py")
    del item["repository"]["private"]
    fake = FakeGitHub(lambda request: ok([item]))
    async with fake.client() as client:
        result = await client.search_code("x")
    assert len(result["items"]) == 1


# ---------- argument validation (no request is sent) ----------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"query": ""}, "must not be empty"),
        ({"query": "   "}, "must not be empty"),
        ({"query": "x" * (MAX_QUERY_LENGTH + 1)}, f"at most {MAX_QUERY_LENGTH}"),
        ({"query": "x", "limit": 0}, f"between 1 and {MAX_LIMIT}"),
        ({"query": "x", "limit": MAX_LIMIT + 1}, f"between 1 and {MAX_LIMIT}"),
        ({"query": "x", "page": 0}, "page must be 1 or greater"),
    ],
)
@pytest.mark.anyio
async def test_invalid_arguments_are_rejected_before_any_request(kwargs: dict, message: str):
    fake = FakeGitHub(lambda request: ok([]))
    async with fake.client() as client:
        with pytest.raises(GitHubError, match=message):
            await client.search_repositories(**kwargs)
        with pytest.raises(GitHubError, match=message):
            await client.search_code(**kwargs)
    assert fake.requests == []


# ---------- GitHub error mapping ----------


@pytest.mark.anyio
async def test_unauthenticated_code_search_explains_that_a_token_is_needed():
    fake = FakeGitHub(lambda request: error(401, "Requires authentication"))
    async with fake.client(None) as client:
        with pytest.raises(GitHubError, match="requires a token"):
            await client.search_code("x")


@pytest.mark.anyio
async def test_invalid_token_is_reported_as_such():
    fake = FakeGitHub(lambda request: error(401, "Bad credentials"))
    async with fake.client("wrong") as client:
        with pytest.raises(GitHubError, match="rejected the token"):
            await client.search_repositories("x")


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.anyio
async def test_retry_after_is_turned_into_a_wait_hint(status: int):
    fake = FakeGitHub(lambda request: error(status, "secondary rate limit", {"retry-after": "7"}))
    async with fake.client() as client:
        with pytest.raises(GitHubError, match=r"rate limit reached \(HTTP %d\)\. Retry in 7 s\." % status):
            await client.search_repositories("x")


@pytest.mark.anyio
async def test_primary_rate_limit_reports_time_until_reset():
    reset = int(time.time()) + 90
    fake = FakeGitHub(lambda request: error(403, "API rate limit exceeded", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)}))
    async with fake.client() as client:
        with pytest.raises(GitHubError, match=re.escape("Retry in 1 min ")):
            await client.search_code("x")


@pytest.mark.anyio
async def test_forbidden_without_rate_limit_keeps_githubs_message():
    fake = FakeGitHub(lambda request: error(403, "Resource not accessible by integration"))
    async with fake.client() as client:
        with pytest.raises(GitHubError, match="HTTP 403 for /search/repositories: Resource not accessible"):
            await client.search_repositories("x")


@pytest.mark.anyio
async def test_validation_failure_includes_githubs_details():
    body = {"errors": [{"message": "The search is longer than 256 characters."}]}
    fake = FakeGitHub(lambda request: error(422, "Validation Failed", **body))
    async with fake.client() as client:
        with pytest.raises(GitHubError, match="Validation Failed.*longer than 256"):
            await client.search_code("q")


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (httpx.ReadTimeout("slow"), "timed out \\(ReadTimeout\\)"),
        (httpx.ConnectError(""), "Cannot reach the GitHub API\\. ConnectError"),
    ],
)
@pytest.mark.anyio
async def test_transport_failures_name_the_exception_class(raised: Exception, expected: str):
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised

    async with FakeGitHub(handler).client() as client:
        with pytest.raises(GitHubError, match=expected):
            await client.search_repositories("x")


@pytest.mark.anyio
async def test_unexpected_status_and_non_json_body_are_reported():
    fake = FakeGitHub(lambda request: error(500, "boom"))
    async with fake.client() as client:
        with pytest.raises(GitHubError, match="HTTP 500 for /search/code: boom"):
            await client.search_code("x")

    async with FakeGitHub(lambda request: httpx.Response(200, text="<html>")).client() as client:
        with pytest.raises(GitHubError, match="non-JSON"):
            await client.search_code("x")


# ---------- token lookup ----------


def test_token_env_precedence_and_stripping():
    assert token_from_env({"GITHUB_PAT": " first ", "GITHUB_TOKEN": "second"}) == "first"
    assert token_from_env({"GITHUB_PAT": "  ", "GITHUB_TOKEN": "second"}) == "second"
    assert token_from_env({"GH_TOKEN": "ignored"}) is None
    assert token_from_env({}) is None
    assert TOKEN_ENV_VARS == ("GITHUB_PAT", "GITHUB_TOKEN")
