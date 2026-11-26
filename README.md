# gh-mcp

[English](README.md) | [中文](README.zh-CN.md)

A read-only [MCP](https://modelcontextprotocol.io) server, over stdio, that lets an agent search **public** GitHub repositories, code, issues and pull requests, and read what it finds. It exposes seven read-only tools and nothing that can write.

```text
search_repositories   keyword + qualifiers  →  public repositories
search_code           keyword + qualifiers  →  code in public repositories
search_issues         keyword + qualifiers  →  issues in public repositories
search_pull_requests  keyword + qualifiers  →  pull requests in public repositories
get_file_contents     owner/repo + path     →  a file, a directory listing, or a repository tree
get_issue             owner/repo + number   →  one issue, with its description
get_pull_request      owner/repo + number   →  one pull request, with its description
```

## Why

GitHub's own remote MCP server is the right tool if you want all of GitHub in your agent. If you only want "find public repos, code, issues and PRs, then read them", it brings 40+ tools (creating repositories, pushing files, merging pull requests) into every conversation. This server is the narrow version:

- **Seven read-only tools.** All are annotated `readOnlyHint`; there is no code path that writes to GitHub.
- **Public only.** Repository, issue and pull request queries always carry `is:public`, so GitHub filters them server-side. Code results that come from private repositories are dropped before the model sees them, and `get_file_contents`, `get_issue` and `get_pull_request` refuse any repository that is not public before they ask for content.
- **Small output.** Results are rendered as compact markdown instead of raw API JSON; long descriptions are clipped.
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

The token is read from the environment when the server starts: `GITHUB_PAT`, or `GITHUB_TOKEN` as the fallback.

| Tool                  | Without a token                    | With a token |
| --------------------- | ---------------------------------- | ------------ |
| `search_repositories` | Works (lower GitHub rate limits)   | Works        |
| `search_code`         | Refused with an explanatory error  | Works        |
| `search_issues`       | Works (lower GitHub rate limits)   | Works        |
| `search_pull_requests`| Works (lower GitHub rate limits)   | Works        |
| `get_file_contents`   | Works (lower GitHub rate limits)   | Works        |
| `get_issue`           | Works (lower GitHub rate limits)   | Works        |
| `get_pull_request`    | Works (lower GitHub rate limits)   | Works        |

GitHub requires authentication for code search, which is why that tool cannot run anonymously. The token decides what is reachable: a token limited to public repositories never sees private matches, while a broader token's private matches are removed by this server where the API allows it (repository and code search items carry a visibility flag; issue search items do not, so filtering there relies on the `is:public` qualifier, which GitHub applies server-side).

## Use with an MCP client

Claude Code:

```bash
claude mcp add-json gh-mcp '{"command":"gh-mcp","env":{"GITHUB_PAT":"<your token>"}}'
```

Any client that takes a stdio server command:

```json
{
  "mcpServers": {
    "gh-mcp": {
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
| `page`   | int    | 1       | 1-based                           |

### `search_issues` / `search_pull_requests`

Search issues (or pull requests) in public repositories. `query` takes GitHub's issue search syntax: `memory leak repo:python/cpython state:open`, `docker label:good-first-issue`, `fuzz repo:rust-lang/rust is:merged`, and so on. The tool adds `is:issue` (or `is:pr`) and `is:public` for you; each result shows its state, labels, comment count and a one-line excerpt of the description.

| Parameter | Type   | Default | Notes                                    |
| --------- | ------ | ------- | ---------------------------------------- |
| `query`   | string | —       | Required, at most 256 characters         |
| `sort`    | string | `best`  | `best`, `comments`, `reactions`, `created`, `updated` |
| `order`   | string | `desc`  | Only used when `sort` is not `best`      |
| `limit`   | int    | 20      | 1–50                                     |
| `page`   | int    | 1       | 1-based                                   |

### `get_issue` / `get_pull_request`

Read one issue or pull request in a public repository: title, state, labels, author, comment count and up to 100 lines of the description.

| Parameter      | Type   | Default | Notes                                     |
| -------------- | ------ | ------- | ----------------------------------------- |
| `owner`        | string | —       | Required                                  |
| `repo`         | string | —       | Required                                  |
| `issue_number` / `pull_number` | int | — | Required; the number in the issue/PR URL |

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

The search tools return markdown: a header with the result window and total, one entry per result, and a note when more pages exist or when private matches were hidden. Large files come back in windows: the header says which lines are shown and which `start_line` continues. Binary files and files over 1 MB (GitHub's limit for this endpoint) are refused with a message that says so. A recursive tree shows at most 400 entries; narrow it with `path`. An issue or pull request description is clipped to its first 100 lines, with a note pointing to the URL for the rest.

## Limits

GitHub rate limits search separately from the rest of the API: 30 requests per minute for repository search, 30 for issue/PR search and 10 for code search with a token (10 per minute without one). The file, tree, issue and pull request endpoints use GitHub's general limit instead: 5,000 requests per hour with a token and 60 per hour without one. When a limit is hit, the tool returns an error that says how long to wait. Each search call is one GitHub request; each `get_file_contents`, `get_issue` or `get_pull_request` call is two (a visibility check, then the content), because those endpoints do not say whether a repository is private.

## Development

```bash
uv sync --group dev
uv run pytest
uv run python -m gh_mcp   # run the server from the working tree
```

The tests use an in-memory fake of the GitHub API and need no network or token. `src/gh_mcp/github.py` is the API client, `render.py` turns results into markdown, and `server.py` wires them to MCP.

## License

MIT
