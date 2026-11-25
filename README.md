# gh-search-mcp

A read-only [MCP](https://modelcontextprotocol.io) server, over stdio, that lets an agent search **public** GitHub repositories and code. It exposes exactly two tools and nothing that can write.

```text
search_repositories   keyword + qualifiers  →  public repositories
search_code           keyword + qualifiers  →  code in public repositories
```

## Why

GitHub's own remote MCP server is the right tool if you want all of GitHub in your agent. If you only want "find public repos and code", it brings 40+ tools (creating repositories, pushing files, merging pull requests) into every conversation. This server is the narrow version:

- **Two read-only tools.** Both are annotated `readOnlyHint`; there is no code path that writes to GitHub.
- **Public only.** Repository queries always carry `is:public`, so GitHub filters them server-side. Code results that come from private repositories are dropped before the model sees them.
- **Small output.** Results are rendered as compact markdown instead of raw API JSON.
- **Async, standard stack.** Built on the official Python SDK (`mcp`) and `httpx.AsyncClient`.

## Install

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv tool install git+https://github.com/raystyle/gh-search-mcp
gh-search-mcp --version
```

Or run it without installing:

```bash
uvx --from git+https://github.com/raystyle/gh-search-mcp gh-search-mcp --version
```

The server itself takes no arguments and speaks MCP on stdin/stdout.

## Token

The token is read from the environment when the server starts: `GITHUB_PAT`, or `GITHUB_TOKEN` as a fallback.

| Tool                  | Without a token                    | With a token |
| --------------------- | ---------------------------------- | ------------ |
| `search_repositories` | Works (lower GitHub rate limits)   | Works        |
| `search_code`         | Refused with an explanatory error  | Works        |

GitHub requires authentication for code search, which is why that tool cannot run anonymously. The token decides what is reachable: a token limited to public repositories never sees private matches, while a broader token's private matches are removed by this server.

## Use with an MCP client

Claude Code:

```bash
claude mcp add-json gh-search '{"command":"gh-search-mcp","env":{"GITHUB_PAT":"<your token>"}}'
```

Any client that takes a stdio server command:

```json
{
  "mcpServers": {
    "gh-search": {
      "command": "gh-search-mcp",
      "env": { "GITHUB_PAT": "<your token>" }
    }
  }
}
```

If the token is already in the server process's environment, the `env` block can be omitted.

## Tools

### `search_repositories`

Search public repositories. `query` takes GitHub's repository search syntax, for example `mcp server stars:>100 language:python`, `topic:openwrt`, or plain words.

| Parameter | Type   | Default | Notes                                              |
| --------- | ------ | ------- | -------------------------------------------------- |
| `query`   | string | —       | Required, at most 256 characters                   |
| `sort`    | string | `best`  | `best`, `stars`, `forks`, `help-wanted-issues`, `updated` |
| `order`   | string | `desc`  | Only used when `sort` is not `best`                |
| `limit`   | int    | 20      | 1–50                                               |
| `page`   | int    | 1       | 1-based                                             |

### `search_code`

Search code in public repositories. `query` takes GitHub's code search syntax, for example `O_NOFOLLOW language:python` or `create_connected_server_and_client_remote_streams repo:modelcontextprotocol/python-sdk`. Up to two matching text fragments are returned per result.

| Parameter | Type   | Default | Notes                            |
| --------- | ------ | ------- | -------------------------------- |
| `query`   | string | —       | Required, at most 256 characters |
| `limit`   | int    | 20      | 1–50                             |
| `page`    | int    | 1       | 1-based                           |

Both tools return markdown: a header with the result window and total, one entry per result, and a note when more pages exist or when private matches were hidden.

## Limits

GitHub rate limits search separately from the rest of the API: 30 requests per minute for repository search and 10 per minute for code search with a token, and 10 per minute without one. When the limit is hit, the tool returns an error that says how long to wait. Each call to either tool is one GitHub request.

## Development

```bash
uv sync --group dev
uv run pytest
uv run python -m gh_search_mcp   # run the server from the working tree
```

The tests use an in-memory fake of the GitHub Search API and need no network or token. `src/gh_search_mcp/github.py` is the API client, `render.py` turns results into markdown, and `server.py` wires them to MCP.

## License

MIT
