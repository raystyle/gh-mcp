# gh-mcp

[English](README.md) | [中文](README.zh-CN.md)

一个只读的 [MCP](https://modelcontextprotocol.io) 服务器（stdio），让 agent 搜索**公开**的 GitHub 仓库、代码、issue 和 pull request，并读取搜索结果。共七个只读工具，没有任何写操作。

```text
search_repositories   关键词 + 限定符     →  公开仓库
search_code           关键词 + 限定符     →  公开仓库中的代码
search_issues         关键词 + 限定符     →  公开仓库中的 issue
search_pull_requests  关键词 + 限定符     →  公开仓库中的 pull request
get_file_contents     owner/repo + 路径  →  文件、目录列表或仓库树
get_issue             owner/repo + 编号  →  单个 issue 及其描述
get_pull_request      owner/repo + 编号  →  单个 pull request 及其描述
```

## 为什么做这个

如果你想让 agent 拥有 GitHub 的全部能力，GitHub 官方的远程 MCP 服务器是正确选择。如果你只想要“搜索公开仓库、代码、issue 和 PR，然后读取它们”，官方服务器会把 40 多个工具（建仓库、推文件、合并 PR 等）带进每一次对话。本服务器是窄版本：

- **七个只读工具。** 全部标注 `readOnlyHint`，没有任何写入 GitHub 的代码路径。
- **只服务公开数据。** 仓库、issue 和 PR 查询总是携带 `is:public`，由 GitHub 在服务端过滤；代码搜索中来自私有仓库的结果会在模型看到之前被丢弃；`get_file_contents`、`get_issue` 和 `get_pull_request` 在请求内容之前会先确认仓库是公开的，否则拒绝。
- **输出精简。** 结果渲染成紧凑的 markdown 而不是原始 API JSON；过长的描述会被截断。
- **异步、标准技术栈。** 基于官方 Python SDK（`mcp`）和 `httpx.AsyncClient`。

## 安装

需要 [uv](https://docs.astral.sh/uv/)。

```bash
uv tool install git+https://github.com/raystyle/gh-mcp
gh-mcp --version
```

也可以免安装直接运行：

```bash
uvx --from git+https://github.com/raystyle/gh-mcp gh-mcp --version
```

服务器本身不接受参数，在 stdin/stdout 上讲 MCP。

## 令牌

服务器启动时从环境变量读取令牌：优先 `GITHUB_PAT`，其次是 `GITHUB_TOKEN`。

| 工具                  | 无令牌                            | 有令牌 |
| --------------------- | --------------------------------- | ------ |
| `search_repositories` | 可用（速率限制更低）              | 可用   |
| `search_code`         | 拒绝并给出说明性错误              | 可用   |
| `search_issues`       | 可用（速率限制更低）              | 可用   |
| `search_pull_requests`| 可用（速率限制更低）              | 可用   |
| `get_file_contents`   | 可用（速率限制更低）              | 可用   |
| `get_issue`           | 可用（速率限制更低）              | 可用   |
| `get_pull_request`    | 可用（速率限制更低）              | 可用   |

GitHub 要求代码搜索必须认证，所以该工具无法匿名运行。令牌决定可见范围：仅限公开仓库的令牌永远看不到私有匹配；权限更宽的令牌产生的私有匹配，在 API 允许的范围内由本服务器剔除（仓库和代码搜索结果带有可见性标志；issue 搜索结果不带，因此过滤依赖 `is:public` 限定符，由 GitHub 在服务端生效）。

## 在 MCP 客户端中使用

Claude Code：

```bash
claude mcp add-json gh-mcp '{"command":"gh-mcp","env":{"GITHUB_PAT":"<你的令牌>"}}'
```

任何接受 stdio 服务器命令的客户端：

```json
{
  "mcpServers": {
    "gh-mcp": {
      "command": "gh-mcp",
      "env": { "GITHUB_PAT": "<你的令牌>" }
    }
  }
}
```

如果令牌已在服务器进程的环境中，可省略 `env` 块。

## 工具

### `search_repositories`

搜索公开仓库。`query` 接受 GitHub 仓库搜索语法，例如 `mcp server stars:>100 language:python`、`topic:openwrt`，或普通关键词。

| 参数    | 类型   | 默认值  | 说明                                              |
| ------- | ------ | ------- | ------------------------------------------------- |
| `query` | string | —       | 必填，最多 256 字符                               |
| `sort`  | string | `best`  | `best`、`stars`、`forks`、`help-wanted-issues`、`updated` |
| `order` | string | `desc`  | 仅当 `sort` 不是 `best` 时生效                    |
| `limit` | int    | 20      | 1–50                                              |
| `page`  | int    | 1       | 从 1 开始                                          |

### `search_code`

搜索公开仓库中的代码。`query` 接受 GitHub 代码搜索语法，例如 `O_NOFOLLOW language:python` 或 `create_connected_server_and_client_remote_streams repo:modelcontextprotocol/python-sdk`。每条结果最多返回两段匹配文本。

| 参数    | 类型   | 默认值 | 说明              |
| ------- | ------ | ------ | ----------------- |
| `query` | string | —      | 必填，最多 256 字符 |
| `limit` | int    | 20     | 1–50              |
| `page`  | int    | 1      | 从 1 开始          |

### `search_issues` / `search_pull_requests`

搜索公开仓库中的 issue（或 pull request）。`query` 接受 GitHub 的 issue 搜索语法：`memory leak repo:python/cpython state:open`、`docker label:good-first-issue`、`fuzz repo:rust-lang/rust is:merged` 等。工具会自动加上 `is:issue`（或 `is:pr`）和 `is:public`；每条结果显示状态、标签、评论数和一行描述摘要。

| 参数    | 类型   | 默认值  | 说明                                       |
| ------- | ------ | ------- | ------------------------------------------ |
| `query` | string | —       | 必填，最多 256 字符                        |
| `sort`  | string | `best`  | `best`、`comments`、`reactions`、`created`、`updated` |
| `order` | string | `desc`  | 仅当 `sort` 不是 `best` 时生效             |
| `limit` | int    | 20      | 1–50                                       |
| `page`  | int    | 1       | 从 1 开始                                   |

### `get_issue` / `get_pull_request`

读取公开仓库中的单个 issue 或 pull request：标题、状态、标签、作者、评论数，以及最多 100 行描述。

| 参数                        | 类型   | 默认值 | 说明                                    |
| --------------------------- | ------ | ------ | --------------------------------------- |
| `owner`                     | string | —      | 必填                                    |
| `repo`                      | string | —      | 必填                                    |
| `issue_number` / `pull_number` | int | —      | 必填；issue/PR URL 中的编号             |

### `get_file_contents`

读取公开仓库中的文本文件、列目录或查看仓库树。返回内容取决于 `path`：

| `path` 指向      | `recursive`        | 结果                               |
| ---------------- | ------------------ | ---------------------------------- |
| 文件             | —                  | 文件内容，每次最多 400 行          |
| 目录             | `false`（默认）    | 目录下一层的条目                   |
| 目录或空（根）   | `true`             | 整棵子树，缩进树形展示             |

| 参数         | 类型           | 默认值  | 说明                                        |
| ------------ | -------------- | ------- | ------------------------------------------- |
| `owner`      | string         | —       | 必填                                        |
| `repo`       | string         | —       | 必填                                        |
| `path`       | string         | `""`    | 空表示仓库根目录                            |
| `ref`        | string 或 null | `null`  | 分支、标签或 commit SHA；缺省为默认分支     |
| `recursive`  | bool           | `false` | 显示目录整棵子树而不是一层                  |
| `start_line` | int            | 1       | 起始行；目录时忽略                          |
| `end_line`   | int 或 null    | `null`  | 结束行；目录时忽略                          |

搜索类工具返回 markdown：头部是结果窗口和总数，每条结果一段，结尾在有更多页或隐藏了私有匹配时给出提示。大文件按窗口返回：头部说明当前显示的行以及续读所需的 `start_line`。二进制文件和超过 1 MB 的文件（GitHub 该端点的上限）会被拒绝并说明原因。递归树最多显示 400 个条目；可用 `path` 收窄。issue 或 pull request 的描述最多显示前 100 行，其余部分可按提示访问 URL 阅读。

## 速率限制

GitHub 对搜索类 API 与一般 API 分别限速：有令牌时仓库搜索每分钟 30 次、issue/PR 搜索每分钟 30 次、代码搜索每分钟 10 次（无令牌均为每分钟 10 次）。文件、目录树、issue 和 PR 端点走一般限速：有令牌每小时 5,000 次，无令牌每小时 60 次。触发限速时，工具返回的错误会说明还需等待多久。每次搜索是一个 GitHub 请求；每次 `get_file_contents`、`get_issue` 或 `get_pull_request` 是两个请求（先查可见性再取内容），因为这些端点不告知仓库是否私有。

## 开发

```bash
uv sync --group dev
uv run pytest
uv run python -m gh_mcp   # 从工作树运行服务器
```

测试使用内存中的 GitHub API 假实现，无需网络和令牌。`src/gh_mcp/github.py` 是 API 客户端，`render.py` 把结果渲染成 markdown，`server.py` 负责接入 MCP。

## 许可证

MIT
