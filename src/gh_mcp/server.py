"""MCP server: two read-only GitHub search tools, served over stdio."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import __version__
from .github import DEFAULT_LIMIT, MAX_LINES, MAX_LIMIT, MAX_QUERY_LENGTH, GitHubClient, GitHubError, Order, RepoSort
from .render import code as render_code
from .render import file_contents as render_file_contents
from .render import repositories as render_repositories

SERVER_NAME = "gh-mcp"
TOKEN_ENV_VARS = ("GITHUB_PAT", "GITHUB_TOKEN")
"""Checked in this order. Without a token, repository search still works (unauthenticated
rate limits apply) but code search does not: GitHub requires a token for it."""

INSTRUCTIONS = """Searches public GitHub data only. Build `query` with GitHub search qualifier
syntax (`language:`, `stars:>N`, `topic:`, `repo:`, `user:`, `org:`, `in:path`, and so on);
natural-language words are also accepted. Sort is relevance unless you ask otherwise.
After a search, `get_file_contents` opens what you found: a file by path, a directory
listing, or (with `recursive`) a repository's whole tree. All three serve public
repositories only."""

Limit = Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Maximum number of results to return (1–50).")]
Page = Annotated[int, Field(ge=1, description="1-based page of results; start at 1.")]
Owner = Annotated[str, Field(min_length=1, max_length=100, description="Repository owner: a user or organization name.")]
Repo = Annotated[str, Field(min_length=1, max_length=100, description="Repository name.")]
Path = Annotated[str, Field(max_length=1000, description="File or directory path. Empty lists the repository root.")]
Ref = Annotated[str | None, Field(max_length=200, description="Branch, tag or commit SHA. Defaults to the default branch.")]
StartLine = Annotated[int, Field(ge=1, description="First line to return (1-based). Ignored for directories.")]
EndLine = Annotated[
    int | None,
    Field(description=f"Last line to return, inclusive. At most {MAX_LINES} lines are returned per call. Ignored for directories."),
]

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

NO_TOKEN_FOR_CODE = (
    "Code search needs a GitHub token: GitHub returns 401 without one. "
    f"Set one of the environment variables {', '.join(TOKEN_ENV_VARS)} on the machine running this server, "
    "then restart the MCP client. Repository search works without a token."
)


@dataclass
class AppContext:
    client: GitHubClient
    has_token: bool


def token_from_env(env: Mapping[str, str]) -> str | None:
    for name in TOKEN_ENV_VARS:
        value = env.get(name, "").strip()
        if value:
            return value
    return None


async def search_repositories(
    app: AppContext,
    query: str,
    *,
    sort: RepoSort = "best",
    order: Order = "desc",
    limit: int = DEFAULT_LIMIT,
    page: int = 1,
) -> str:
    """Logic behind the ``search_repositories`` tool. Returns markdown text."""
    try:
        result = await app.client.search_repositories(query, sort=sort, order=order, limit=limit, page=page)
    except GitHubError as exc:
        raise ToolError(str(exc)) from exc
    return render_repositories(result, sort=sort, order=order, page=page, limit=limit)


async def search_code(app: AppContext, query: str, *, limit: int = DEFAULT_LIMIT, page: int = 1) -> str:
    """Logic behind the ``search_code`` tool. Returns markdown text."""
    if not app.has_token:
        raise ToolError(NO_TOKEN_FOR_CODE)
    try:
        result = await app.client.search_code(query, limit=limit, page=page)
    except GitHubError as exc:
        raise ToolError(str(exc)) from exc
    return render_code(result, page=page, limit=limit)


def _app(ctx: Context) -> AppContext:
    app = ctx.request_context.lifespan_context
    if not isinstance(app, AppContext):  # pragma: no cover - defensive
        raise ToolError("Server context is not initialised.")
    return app


async def get_file_contents(
    app: AppContext,
    owner: str,
    repo: str,
    *,
    path: str = "",
    ref: str | None = None,
    recursive: bool = False,
    start_line: int = 1,
    end_line: int | None = None,
) -> str:
    """Logic behind the ``get_file_contents`` tool. Returns markdown text."""
    try:
        result = await app.client.get_file_contents(
            owner, repo, path=path, ref=ref, recursive=recursive, start_line=start_line, end_line=end_line
        )
    except GitHubError as exc:
        raise ToolError(str(exc)) from exc
    return render_file_contents(result)


def create_server(env: Mapping[str, str] | None = None) -> MCPServer:
    """Build the server. ``env`` defaults to ``os.environ``; tests pass a mapping."""
    environ = os.environ if env is None else env

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
        token = token_from_env(environ)
        client = GitHubClient(token)
        try:
            yield AppContext(client=client, has_token=bool(token))
        finally:
            await client.aclose()

    server = MCPServer(name=SERVER_NAME, version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan)

    @server.tool(name="search_repositories", annotations=READ_ONLY, structured_output=False)
    async def _tool_search_repositories(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=MAX_QUERY_LENGTH,
                description="Search text with optional GitHub qualifiers, e.g. `mcp server stars:>100 language:python`.",
            ),
        ],
        ctx: Context,
        sort: RepoSort = "best",
        order: Order = "desc",
        limit: Limit = DEFAULT_LIMIT,
        page: Page = 1,
    ) -> str:
        """Search public GitHub repositories by keyword and qualifiers. Results are always restricted to public repositories."""
        return await search_repositories(
            _app(ctx), query, sort=sort, order=order, limit=limit, page=page
        )

    @server.tool(name="search_code", annotations=READ_ONLY, structured_output=False)
    async def _tool_search_code(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=MAX_QUERY_LENGTH,
                description="Code search text with optional qualifiers, e.g. `O_NOFOLLOW language:python`.",
            ),
        ],
        ctx: Context,
        limit: Limit = DEFAULT_LIMIT,
        page: Page = 1,
    ) -> str:
        """Search code in public GitHub repositories. Results from private repositories are never returned."""
        return await search_code(_app(ctx), query, limit=limit, page=page)

    @server.tool(name="get_file_contents", annotations=READ_ONLY, structured_output=False)
    async def _tool_get_file_contents(
        owner: Owner,
        repo: Repo,
        ctx: Context,
        path: Path = "",
        ref: Ref = None,
        recursive: Annotated[
            bool, Field(description="For a directory: show its whole subtree instead of a single level.")
        ] = False,
        start_line: StartLine = 1,
        end_line: EndLine = None,
    ) -> str:
        """Read a text file, list a directory, or show a repository's tree, in a public GitHub repository. Use it after a search to open what you found."""
        return await get_file_contents(
            _app(ctx), owner, repo, path=path, ref=ref, recursive=recursive, start_line=start_line, end_line=end_line
        )

    return server


def main(argv: list[str] | None = None) -> None:
    """Entry point: run the stdio server. ``--version`` prints the version and exits."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-V", "--version"):
        print(__version__)
        return
    if args:
        print(f"usage: {SERVER_NAME} [--version]  (speaks MCP over stdio)", file=sys.stderr)
        raise SystemExit(2)
    # httpx logs every request at INFO; that only clutters the client's log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    create_server().run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
