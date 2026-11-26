"""Server wiring and the real stdio protocol. No network access."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from typing import Any

import httpx
import pytest
from fake_github import FakeGitHub, code, error, ok, repo
from mcp.server.mcpserver.exceptions import ToolError

from gh_mcp import __version__
from gh_mcp.github import GitHubError
from gh_mcp.server import AppContext, create_server, search_code, search_repositories


# ---------- tool catalogue ----------


@pytest.mark.anyio
async def test_exposes_exactly_five_read_only_tools():
    tools = {tool.name: tool for tool in await create_server(env={}).list_tools()}

    assert sorted(tools) == [
        "get_file_contents", "get_issue", "get_pull_request", "search_code",
        "search_issues", "search_pull_requests", "search_repositories",
    ]
    for tool in tools.values():
        annotations = tool.annotations
        assert annotations is not None and annotations.read_only_hint is True
        assert annotations.destructive_hint is False
        assert tool.description and tool.description.strip()

    code_schema = tools["search_code"].input_schema
    assert sorted(code_schema["properties"]) == ["limit", "page", "query"]
    assert code_schema["required"] == ["query"]
    assert code_schema["properties"]["limit"]["minimum"] == 1
    assert code_schema["properties"]["limit"]["maximum"] == 50

    repo_schema = tools["search_repositories"].input_schema
    assert sorted(repo_schema["properties"]) == ["limit", "order", "page", "query", "sort"]

    issue_schema = tools["search_issues"].input_schema
    assert sorted(issue_schema["properties"]) == ["limit", "order", "page", "query", "sort"]
    pull_schema = tools["search_pull_requests"].input_schema
    assert sorted(pull_schema["properties"]) == ["limit", "order", "page", "query", "sort"]

    get_issue_schema = tools["get_issue"].input_schema
    assert sorted(get_issue_schema["properties"]) == ["issue_number", "owner", "repo"]
    assert get_issue_schema["required"] == ["owner", "repo", "issue_number"]

    get_pull_schema = tools["get_pull_request"].input_schema
    assert sorted(get_pull_schema["properties"]) == ["owner", "pull_number", "repo"]
    assert get_pull_schema["required"] == ["owner", "repo", "pull_number"]

    file_schema = tools["get_file_contents"].input_schema
    assert sorted(file_schema["properties"]) == [
        "end_line", "owner", "path", "recursive", "ref", "repo", "start_line"
    ]
    assert file_schema["required"] == ["owner", "repo"]


# ---------- tool logic ----------


@pytest.mark.anyio
async def test_code_search_without_token_fails_before_calling_github():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected without a token")

    async with FakeGitHub(handler).client(None) as client:
        with pytest.raises(ToolError, match="GITHUB_PAT"):
            await search_code(AppContext(client=client, has_token=False), "anything")


@pytest.mark.anyio
async def test_tool_logic_returns_markdown_and_maps_github_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok([code("a.py")]) if request.url.path == "/search/code" else error(500, "boom")

    async with FakeGitHub(handler).client() as client:
        app = AppContext(client=client, has_token=True)
        assert "owner/repo: a.py" in await search_code(app, "x")
        with pytest.raises(ToolError, match="HTTP 500"):
            await search_repositories(app, "x")


# ---------- stdio protocol, real process ----------


class ServerProcess:
    """The server as an MCP client sees it: a child process speaking JSON-RPC over stdio."""

    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "gh_mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=lambda: [self._lines.put(line) for line in self.proc.stdout], daemon=True).start()

    def __enter__(self) -> ServerProcess:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            raise

    def send(self, message: dict[str, Any]) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def rpc(self, request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        while True:
            message = json.loads(self._lines.get(timeout=60))
            if message.get("id") == request_id:
                assert "error" not in message, message
                return message["result"]


def token_free_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in ("GITHUB_PAT", "GITHUB_TOKEN")}


def test_stdio_handshake_tool_list_and_token_guidance():
    with ServerProcess(token_free_env()) as server:
        init = server.rpc(1, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        assert init["serverInfo"] == {"name": "gh-mcp", "version": __version__}
        assert init["protocolVersion"]
        server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        tools = {tool["name"]: tool for tool in server.rpc(2, "tools/list")["tools"]}
        assert sorted(tools) == [
            "get_file_contents", "get_issue", "get_pull_request", "search_code",
            "search_issues", "search_pull_requests", "search_repositories",
        ]
        assert tools["search_repositories"]["annotations"]["readOnlyHint"] is True

        result = server.rpc(3, "tools/call", {"name": "search_code", "arguments": {"query": "x"}})
        assert result["isError"] is True
        assert "GITHUB_PAT" in result["content"][0]["text"]

    assert server.proc.returncode == 0


def test_version_flag_prints_version_and_exits():
    completed = subprocess.run(
        [sys.executable, "-m", "gh_mcp", "--version"], capture_output=True, text=True, encoding="utf-8"
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == __version__
