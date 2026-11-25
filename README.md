# gh-mcp

A read-only [MCP](https://modelcontextprotocol.io) server, over stdio, that lets an agent search **public** GitHub repositories and code, and read what it finds. It exposes three read-only tools and nothing that can write.

```text
search_repositories   keyword + qualifiers  →  public repositories
search_code           keyword + qualifiers  →  code in public repositories
get_file_contents     owner/repo + path     →  a file, a directory listing, or a repository tree
```

## Why

GitHub's own remote MCP server is the right tool if you want all of GitHub in your agent. If you only want "find public repos and code, then read them", it brings 40+ tools (creating repositories, pushing files, merging pull requests) into every conversation. This server is the narrow version:

- **Three read-only tools.** All are annotated `readOnlyHint`; there is no code path that writes to GitHub.
- **Public only.** Repository queries always carry `is:public`, so GitHub filters them server-side. Code results that come from private repositories are dropped before the model sees them, and `get_file_contents` refuses any repository that is not public before it asks for content.
- **Small output.** Results are rendered as compact markdown instead of raw API JSON.
- **Async, standard stack.** Built on the official Python SDK (`mcp`) and `httpx.AsyncClient`.

## Install

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv tool install git+https://github.com/raystyle/gh-mcp
gh-mcp --version
```

Or run it without installing:

```bash
uvx --from git+https://github.com/raystyle/gh-mcp gh-mcp --version
```

The server itself takes no arguments and speaks MCP on stdin/stdout.

## Token

The token is read from the environment when the server starts: `GITHUB_PAT`, or `GITHUB_TOKEN` as a fallback.

| Tool                  | Without a token                    | With a token |
| --------------------- | ---------------------------------- | ------------ |
| `search_repositories` | Works (lower GitHub rate limits)   | Works        |
| `search_code`         | Refused with an explanatory error  | Works        |
| `get_file_contents`   | Works (lower GitHub rate limits)   | Works        |

GitHub requires authentication for code search, which is why that tool cannot run anonymously. The token decides what is reachable: a token limited to public repositories never sees private matches, while a broader token's private matches are removed by this server.

## Use with an MCP client

Claude Code:

```bash
claude mcp add-json gh-search '{"command":"gh-mcp","env":{"GITHUB_PAT":"<your token>"}}'
```

Any client that takes a stdio server command:

```json
{
  "mcpServers": {
    "gh-search": {
      "command": "gh-mcp",
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

The search tools return markdown: a header with the result window and total, one entry per result, and a note when more pages exist or when private matches were hidden.

### `get_file_contents`

Read a text file, list a directory, or show a repository tree in a public repository. What you get depends on `path`:

| `path` points to               | With `recursive` | Result                                         |
| ------------------------------ | ---------------- | ---------------------------------------------- |
| a file                         | —                | The file's lines, at most 400 per call        |
| a directory                    | `false` (default) | The entries directly inside it                |
| a directory, or empty for root | `true`           | The whole subtree, as an indented tree        |

| Parameter    | Type          | Default | Notes                                                       |
| ------------ | ------------- | ------- | ----------------------------------------------------------- |
| `owner`      | string        | —       | Required                                                    |
| `repo`       | string        | —       | Required                                                    |
| `path`       | string        | `""`    | Empty means the repository root                             |
| `ref`        | string or null | `null` | Branch, tag or commit SHA; the default branch when omitted |
| `recursive`  | bool          | `false` | Show a directory's whole subtree instead of one level       |
| `start_line` | int           | 1       | First line to return; ignored for directories               |
| `end_line`   | int or null   | `null`  | Last line to return; ignored for directories                |

Large files come back in windows: the header says which lines are shown and which `start_line` continues. Binary files and files over 1 MB (GitHub's limit for this endpoint) are refused with a message that says so. A recursive tree shows at most 400 entries; narrow it with `path`.

## Limits

GitHub rate limits search separately from the rest of the API: 30 requests per minute for repository search and 10 per minute for code search with a token, and 10 per minute without one. The file and tree endpoints use GitHub's general limit instead: 5,000 requests per hour with a token and 60 per hour without one. When a limit is hit, the tool returns an error that says how long to wait. Each search call is one GitHub request; each `get_file_contents` call is two (a visibility check, then the content), because the content endpoint does not say whether a repository is private.

## Development

```bash
uv sync --group dev
uv run pytest
uv run python -m gh_mcp   # run the server from the working tree
```

The tests use an in-memory fake of the GitHub API and need no network or token. `src/gh_mcp/github.py` is the API client, `render.py` turns results into markdown, and `server.py` wires them to MCP.

## License

MIT
