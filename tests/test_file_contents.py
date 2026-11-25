"""get_file_contents against a fake GitHub: directory listings, trees, file reads. No network."""

from __future__ import annotations

import httpx
import pytest
from fake_github import FakeGitHub, entry, error, file_entry, routes, tree

from gh_search_mcp.github import MAX_CHARS, MAX_LINES, MAX_TREE_ENTRIES, GitHubError
from gh_search_mcp.render import human_size
from gh_search_mcp.server import AppContext, get_file_contents
from mcp.server.mcpserver.exceptions import ToolError

REPO = "/repos/o/r"
CONTENTS = REPO + "/contents/"
TREES = REPO + "/git/trees/"
PUBLIC = {"private": False, "default_branch": "main"}


def public(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=PUBLIC)


def static(payload: object) -> object:
    return lambda request: httpx.Response(200, json=payload)


async def read(handler, *args, **kwargs) -> str:
    """Run the tool logic against a fake GitHub and return the markdown it renders."""
    async with FakeGitHub(handler).client() as client:
        return await get_file_contents(AppContext(client=client, has_token=True), "o", "r", *args, **kwargs)


async def fetch(handler, *args, **kwargs) -> dict:
    async with FakeGitHub(handler).client() as client:
        return await client.get_file_contents("o", "r", *args, **kwargs)


# ---------- directory listing ----------


@pytest.mark.anyio
async def test_directory_listing_checks_visibility_first_then_lists_one_level():
    listing = [entry("b.py", kind="file", size=1024), entry("nested"), entry("a.py", kind="file", size=12)]
    fake_hub = FakeGitHub(routes({REPO: public, CONTENTS: static(listing)}))
    async with fake_hub.client() as client:
        result = await client.get_file_contents("o", "r")

    assert [request.url.path for request in fake_hub.requests] == [REPO, CONTENTS]
    assert result["kind"] == "directory" and result["ref"] == "main"
    assert [item["name"] for item in result["entries"]] == ["b.py", "nested", "a.py"]

    text = await read(routes({REPO: public, CONTENTS: static(listing)}))
    assert text == (
        "`o/r` at `main` — directory `/` (3 entries: 1 directory, 2 files)\n"
        "DIR      nested/\n"
        "FILE     a.py (12 B)\n"
        "FILE     b.py (1.0 KB)\n"
        "Open an entry with get_file_contents and its path.\n"
    )


@pytest.mark.anyio
async def test_subdirectory_listing_labels_and_empty_directory():
    listing = [
        entry("link", kind="symlink", target="../real/file"),
        entry("vendor", kind="submodule"),
        entry("only.py", kind="file", size=0),
    ]
    text = await read(routes({REPO: public, CONTENTS + "src/lib": static(listing)}), path="/src/lib/")
    assert text.splitlines()[0] == "`o/r` at `main` — directory `src/lib/` (3 entries: 0 directories, 3 files)"
    assert "FILE     only.py" in text
    assert "LINK     link -> ../real/file" in text
    assert "SUBMODULE vendor" in text

    empty = await read(routes({REPO: public, CONTENTS: static([])}))
    assert "(0 entries: 0 directories, 0 files)" in empty and "The directory is empty." in empty


@pytest.mark.anyio
async def test_path_is_normalised_and_percent_encoded():
    fake = FakeGitHub(routes({REPO: public, CONTENTS + "a b/c": static([])}))
    async with fake.client() as client:
        await client.get_file_contents("o", "r", path="/a b//c/")
    assert fake.requests[1].url.raw_path == b"/repos/o/r/contents/a%20b/c"


@pytest.mark.anyio
async def test_default_branch_is_shown_unless_a_ref_is_given():
    develop = {"private": False, "default_branch": "develop"}
    fake = FakeGitHub(routes({REPO: static(develop), CONTENTS: static([])}))
    async with fake.client() as client:
        assert (await client.get_file_contents("o", "r"))["ref"] == "develop"
        assert (await client.get_file_contents("o", "r", ref="v1.2"))["ref"] == "v1.2"
    assert "ref=" not in str(fake.requests[1].url)
    assert "ref=v1.2" in str(fake.requests[3].url)


# ---------- recursive tree ----------


@pytest.mark.anyio
async def test_tree_of_the_whole_repository_is_one_request_after_the_visibility_check():
    paths = ["README.md", "lib/", "lib/deep/", "lib/deep/x.py", "lib/a.py", "z.txt"]
    fake = FakeGitHub(routes({REPO: public, TREES + "main": lambda request: tree(*paths)}))
    async with fake.client() as client:
        result = await client.get_file_contents("o", "r", recursive=True)

    assert [request.url.path for request in fake.requests] == [REPO, TREES + "main"]
    assert "recursive=1" in str(fake.requests[1].url)
    assert result["kind"] == "tree" and result["total"] == len(paths)

    text = await read(routes({REPO: public, TREES + "main": lambda request: tree(*paths)}), recursive=True)
    assert text == (
        "`o/r` at `main` — tree under the repository root (6 entries)\n"
        "lib/\n"
        "  deep/\n"
        "    x.py\n"
        "  a.py\n"
        "README.md\n"
        "z.txt\n"
    )


@pytest.mark.anyio
async def test_tree_of_a_subdirectory_keeps_paths_relative_to_it():
    paths = ["src/", "src/lib/", "src/lib/x.py", "src/main.py", "README.md"]
    text = await read(routes({REPO: public, TREES + "main": lambda request: tree(*paths)}), path="src", recursive=True)
    assert text == (
        "`o/r` at `main` — tree under `src/` (3 entries)\n"
        "lib/\n"
        "  x.py\n"
        "main.py\n"
    )


@pytest.mark.anyio
async def test_tree_ref_is_encoded_as_one_path_segment():
    fake = FakeGitHub(routes({REPO: public, TREES + "feature/x": lambda request: tree("a.py")}))
    async with fake.client() as client:
        result = await client.get_file_contents("o", "r", ref="feature/x", recursive=True)
    assert fake.requests[1].url.raw_path == b"/repos/o/r/git/trees/feature%2Fx?recursive=1"
    assert result["ref"] == "feature/x"


@pytest.mark.anyio
async def test_tree_entry_cap_and_github_truncation_are_announced():
    many = [f"f{n:04}" for n in range(MAX_TREE_ENTRIES + 50)]
    text = await read(routes({REPO: public, TREES + "main": lambda request: tree(*many)}), recursive=True)
    assert f"({MAX_TREE_ENTRIES + 50} entries)" in text
    assert f"Showing the first {MAX_TREE_ENTRIES} of {MAX_TREE_ENTRIES + 50} entries. Narrow with path=<directory>." in text

    text = await read(routes({REPO: public, TREES + "main": lambda request: tree("a.py", truncated=True)}), recursive=True)
    assert "GitHub truncated this tree" in text


@pytest.mark.anyio
async def test_missing_directory_in_tree():
    table = {REPO: public, TREES + "main": lambda request: tree("a.py")}
    with pytest.raises(GitHubError, match="No directory `src` in o/r at `main`"):
        await fetch(routes(table), path="src", recursive=True)

    truncated = {REPO: public, TREES + "main": lambda request: tree("a.py", truncated=True)}
    with pytest.raises(GitHubError, match="the tree is truncated"):
        await fetch(routes(truncated), path="src", recursive=True)

    empty = await read(routes({REPO: public, TREES + "main": lambda request: tree()}), recursive=True)
    assert "(0 entries)" in empty and "The tree is empty." in empty


# ---------- file reads ----------


@pytest.mark.anyio
async def test_file_is_decoded_and_shown_in_full():
    text = await read(routes({REPO: public, CONTENTS + "src/a.py": static(file_entry("src/a.py", "a\nb\nc\n", sha="0123456789ab"))}),
                      path="src/a.py")
    assert text == (
        "`o/r` at `main` — file `src/a.py`: 3 lines, 6 B, blob 0123456789ab\n"
        "Lines 1–3 of 3.\n"
        "\n"
        "a\nb\nc\n"
    )


@pytest.mark.anyio
async def test_crlf_and_trailing_newline_are_handled():
    result = await fetch(routes({REPO: public, CONTENTS + "x": static(file_entry("x", "a\r\nb\r\n"))}), path="x")
    assert result["lines"] == ["a", "b"] and result["total_lines"] == 2


@pytest.mark.anyio
async def test_line_window():
    body = "".join(f"line {n}\n" for n in range(1, 11))
    table = {REPO: public, CONTENTS + "x": static(file_entry("x", body))}
    result = await fetch(routes(table), path="x", start_line=4, end_line=6)
    assert result["lines"] == ["line 4", "line 5", "line 6"]
    assert "Lines 4–6 of 10. Continue with start_line=7." in await read(routes(table), path="x", start_line=4, end_line=6)
    assert "Lines 8–10 of 10." in await read(routes(table), path="x", start_line=8)


@pytest.mark.anyio
async def test_line_cap():
    table = {REPO: public, CONTENTS + "x": static(file_entry("x", "".join(f"l{n}\n" for n in range(1000))))}
    result = await fetch(routes(table), path="x")
    assert len(result["lines"]) == MAX_LINES and result["stop_reason"] == "lines"
    assert f"Lines 1–{MAX_LINES} of 1000. Continue with start_line={MAX_LINES + 1}." in await read(routes(table), path="x")


@pytest.mark.anyio
async def test_character_cap():
    line_len = 1000
    fits = MAX_CHARS // line_len
    body = ("x" * (line_len - 1) + "\n") * 200
    table = {REPO: public, CONTENTS + "x": static(file_entry("x", body))}
    result = await fetch(routes(table), path="x")
    assert len(result["lines"]) == fits and result["stop_reason"] == "chars"
    assert f"Lines 1–{fits} of 200; stopped because the output limit was reached. Continue with start_line={fits + 1}." in (
        await read(routes(table), path="x")
    )


@pytest.mark.anyio
async def test_single_line_over_the_cap_is_cut():
    table = {REPO: public, CONTENTS + "min.js": static(file_entry("min.js", "y" * (MAX_CHARS * 2)))}
    result = await fetch(routes(table), path="min.js")
    assert result["stop_reason"] == "long_line" and len(result["lines"][0]) == MAX_CHARS
    assert f"Lines 1–1 of 1; line 1 is longer than the output limit, so only its first {MAX_CHARS:,} characters are shown." in (
        await read(routes(table), path="min.js")
    )


@pytest.mark.anyio
async def test_empty_file():
    table = {REPO: public, CONTENTS + "x": static(file_entry("x", ""))}
    result = await fetch(routes(table), path="x")
    assert result["total_lines"] == 0 and result["lines"] == []
    assert "The file is empty." in await read(routes(table), path="x")


@pytest.mark.anyio
async def test_start_line_past_the_end_is_an_error():
    table = {REPO: public, CONTENTS + "x": static(file_entry("x", "a\nb\n"))}
    with pytest.raises(GitHubError, match="start_line is 5 but `x` in o/r at `main` has only 2 lines."):
        await fetch(routes(table), path="x", start_line=5)


@pytest.mark.parametrize(
    "payload",
    [
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
        "hi".encode("utf-16"),
        "a\x00b",
    ],
)
@pytest.mark.anyio
async def test_binary_content_is_refused(payload: bytes):
    table = {REPO: public, CONTENTS + "blob": static(file_entry("blob", payload))}
    with pytest.raises(GitHubError, match=f"is a binary file \\({human_size(len(payload))}\\); only text files are shown"):
        await fetch(routes(table), path="blob")


@pytest.mark.anyio
async def test_oversized_and_malformed_content():
    huge = {REPO: public, CONTENTS + "big": static(file_entry("big", "", size=5_000_000, encoding=None))}
    with pytest.raises(GitHubError, match="too large to read"):
        await fetch(routes(huge), path="big")

    broken = {"type": "file", "content": "not base64!!", "encoding": "base64", "size": 11}
    with pytest.raises(GitHubError, match="not valid base64"):
        await fetch(routes({REPO: public, CONTENTS + "x": static(broken)}), path="x")


@pytest.mark.anyio
async def test_symlink_at_the_path_is_not_a_file():
    table = {REPO: public, CONTENTS + "link": static({"type": "symlink", "name": "link", "target": "elsewhere"})}
    with pytest.raises(GitHubError, match="`link` in o/r at `main` is a symlink"):
        await fetch(routes(table), path="link")


# ---------- public-only and not-found handling ----------


@pytest.mark.anyio
async def test_private_repositories_are_refused_before_any_content_is_requested():
    for info in ({"private": True, "default_branch": "main"}, {"default_branch": "main"}):
        fake = FakeGitHub(routes({REPO: static(info)}))
        async with fake.client() as client:
            with pytest.raises(GitHubError, match="not a public repository"):
                await client.get_file_contents("o", "r", path="secret.txt")
            with pytest.raises(GitHubError, match="not a public repository"):
                await client.get_file_contents("o", "r", recursive=True)
        assert [request.url.path for request in fake.requests] == [REPO, REPO]


@pytest.mark.anyio
async def test_not_found_messages_distinguish_repository_path_and_ref():
    with pytest.raises(GitHubError, match="Repository o/r was not found. It may not exist, or it may be private"):
        await fetch(routes({REPO: lambda request: error(404, "Not Found")}))

    table = {REPO: public, CONTENTS + "nope": lambda request: error(404, "Not Found")}
    with pytest.raises(GitHubError, match="Nothing found at `nope` in o/r at `main`. Check the path and ref."):
        await fetch(routes(table), path="nope")

    missing_tree = {REPO: public, TREES + "nope": lambda request: error(404, "Not Found")}
    with pytest.raises(GitHubError, match="No tree at `nope` in o/r. Check the ref."):
        await fetch(routes(missing_tree), ref="nope", recursive=True)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"owner": " "}, "owner must be a single GitHub owner name"),
        ({"repo": "a/b"}, "repo must be a single GitHub repo name"),
        ({"owner": "has space"}, "owner must be a single GitHub owner name"),
        ({"start_line": 0}, "start_line must be 1 or greater"),
        ({"start_line": 10, "end_line": 9}, "end_line must not be before start_line"),
    ],
)
@pytest.mark.anyio
async def test_invalid_arguments_are_rejected_before_any_request(kwargs: dict, message: str):
    arguments = {"owner": "o", "repo": "r", **kwargs}
    fake = FakeGitHub(routes({REPO: public}))
    async with fake.client() as client:
        with pytest.raises(GitHubError, match=message):
            await client.get_file_contents(arguments.pop("owner"), arguments.pop("repo"), **arguments)
    assert fake.requests == []


@pytest.mark.anyio
async def test_tool_logic_maps_github_errors_to_tool_errors():
    table = {REPO: public, CONTENTS: lambda request: error(500, "boom")}
    with pytest.raises(ToolError, match="HTTP 500"):
        await read(routes(table))
