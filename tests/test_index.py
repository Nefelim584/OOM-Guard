"""Tests for the workspace symbol index, its hash-checked cache, and the find_symbol tool (Block 5)."""

import json
from pathlib import Path

import pytest

import acc_mcp.index as index_module
from acc_mcp.budget import ContextBudgetTracker
from acc_mcp.guard import GuardRail
from acc_mcp.index import WorkspaceIndex
from acc_mcp.server import create_server

SERVICE_PY = (
    '"""User service."""\n'
    "\n"
    "\n"
    "class UserManager:\n"
    '    """Manages users."""\n'
    "\n"
    "    def authenticate(self, username: str) -> bool:\n"
    "        return bool(username)\n"
)
ADMIN_PY = (
    "class AdminManager:\n"
    "    def authenticate(self) -> bool:\n"
    "        return True\n"
)
UTILS_PY = (
    "def calculate_hash(data: str) -> str:\n"
    "    return str(hash(data))\n"
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "service.py").write_text(SERVICE_PY, encoding="utf-8")
    (root / "pkg" / "admin.py").write_text(ADMIN_PY, encoding="utf-8")
    (root / "pkg" / "utils.py").write_text(UTILS_PY, encoding="utf-8")

    # Must never be indexed
    for vendored in (".venv/lib/vendored.py", "node_modules/tool/build.py", "__pycache__/stale.py"):
        (root / vendored).parent.mkdir(parents=True, exist_ok=True)
        (root / vendored).write_text("def calculate_hash() -> None:\n    pass\n", encoding="utf-8")
    (root / ".aws").mkdir()
    (root / ".aws" / "helper.py").write_text("def calculate_hash() -> None:\n    pass\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY = 'secret'\n", encoding="utf-8")
    (root / "notes.txt").write_text("def calculate_hash(): pass\n", encoding="utf-8")
    return root


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger.json"


@pytest.fixture
def parse_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the file path of every ASTParser the index creates."""
    calls: list[str] = []
    real_parser = index_module.ASTParser

    class CountingParser(real_parser):
        def __init__(self, source_code: str, file_path: str = "", package: str | None = None) -> None:
            calls.append(Path(file_path).name)
            super().__init__(source_code, file_path=file_path, package=package)

    monkeypatch.setattr(index_module, "ASTParser", CountingParser)
    return calls


def make_index(workspace: Path, ledger_path: Path) -> WorkspaceIndex:
    """A fresh index as a newly started server would build it, from the ledger on disk."""
    return WorkspaceIndex(
        guard=GuardRail(workspace_root=workspace),
        tracker=ContextBudgetTracker(ledger_path=ledger_path),
    )


# ---------------------------------------------------------------------------
# 1. Workspace Walk (Step 17.1)
# ---------------------------------------------------------------------------


def test_index_skips_vendor_dirs_secrets_and_non_python(workspace: Path, ledger_path: Path):
    """Verify only servable `.py` files are indexed: no .venv, node_modules, __pycache__, .aws, .env, .txt."""
    index = make_index(workspace, ledger_path)
    stats = index.refresh()

    indexed = sorted(p.relative_to(index.root).as_posix() for p in index.iter_source_files())
    assert indexed == ["pkg/admin.py", "pkg/service.py", "pkg/utils.py"]
    assert stats.files_seen == 3
    assert stats.files_parsed == 3


def test_symlink_alias_is_indexed_once(workspace: Path, ledger_path: Path):
    """Verify a symlink to a file inside the root does not create a duplicate definition."""
    (workspace / "alias.py").symlink_to(workspace / "pkg" / "utils.py")
    index = make_index(workspace, ledger_path)
    index.refresh()

    matches = index.find("calculate_hash")
    assert [m.file_path for m in matches] == ["pkg/utils.py"]


# ---------------------------------------------------------------------------
# 2. Hash-Checked Cache Across Restarts (Step 17.2)
# ---------------------------------------------------------------------------


def test_restarted_index_does_not_reparse_unchanged_files(
    workspace: Path, ledger_path: Path, parse_calls: list[str]
):
    """Verify a second index built from the saved ledger parses nothing when no file changed."""
    make_index(workspace, ledger_path).refresh()
    assert sorted(parse_calls) == ["admin.py", "service.py", "utils.py"]

    parse_calls.clear()
    restarted = make_index(workspace, ledger_path)
    stats = restarted.refresh()

    assert parse_calls == []
    assert stats.files_reused == 3
    assert stats.files_parsed == 0
    assert restarted.find("calculate_hash")[0].file_path == "pkg/utils.py"


def test_edited_file_is_reindexed(workspace: Path, ledger_path: Path, parse_calls: list[str]):
    """Verify only the edited file is parsed again after a restart and its new symbols are found."""
    make_index(workspace, ledger_path).refresh()
    parse_calls.clear()

    (workspace / "pkg" / "utils.py").write_text(
        UTILS_PY + "\n\ndef slugify(text: str) -> str:\n    return text.lower()\n",
        encoding="utf-8",
    )
    restarted = make_index(workspace, ledger_path)
    stats = restarted.refresh()

    assert parse_calls == ["utils.py"]
    assert stats.files_parsed == 1
    assert stats.files_reused == 2
    [slugify] = restarted.find("slugify")
    assert (slugify.file_path, slugify.lineno, slugify.end_lineno) == ("pkg/utils.py", 5, 6)


def test_deleted_file_is_dropped_from_cache(workspace: Path, ledger_path: Path):
    """Verify a deleted file disappears from the cache and from find results."""
    index = make_index(workspace, ledger_path)
    index.refresh()
    (workspace / "pkg" / "utils.py").unlink()

    stats = index.refresh()

    assert stats.files_removed == 1
    assert index.find("calculate_hash") == []
    assert not index.tracker.is_file_cached(str((workspace / "pkg" / "utils.py").resolve()))


def test_unparseable_file_is_cached_and_not_reparsed(
    workspace: Path, ledger_path: Path, parse_calls: list[str]
):
    """Verify a file with a syntax error does not break indexing and is not parsed on every refresh."""
    (workspace / "pkg" / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    index = make_index(workspace, ledger_path)
    index.refresh()
    assert "broken.py" in parse_calls

    parse_calls.clear()
    index.refresh()
    assert parse_calls == []
    assert index.find("calculate_hash")[0].file_path == "pkg/utils.py"


# ---------------------------------------------------------------------------
# 3. Symbol Lookup (Step 17.3)
# ---------------------------------------------------------------------------


def test_find_by_bare_and_qualified_name(workspace: Path, ledger_path: Path):
    """Verify bare names return every definition and qualified names return exactly one."""
    index = make_index(workspace, ledger_path)
    index.refresh()

    bare = index.find("authenticate")
    assert [(m.file_path, m.symbol, m.kind) for m in bare] == [
        ("pkg/admin.py", "AdminManager.authenticate", "method"),
        ("pkg/service.py", "UserManager.authenticate", "method"),
    ]

    [qualified] = index.find("UserManager.authenticate")
    assert (qualified.file_path, qualified.lineno, qualified.end_lineno) == ("pkg/service.py", 7, 8)

    [cls] = index.find("UserManager")
    assert (cls.kind, cls.lineno, cls.end_lineno) == ("class", 4, 8)

    assert index.find("does_not_exist") == []


# ---------------------------------------------------------------------------
# 4. find_symbol MCP Tool and Restarted Server (Steps 17.3 & 17.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_symbol_tool_returns_location(workspace: Path, ledger_path: Path):
    """Verify the find_symbol tool returns file, kind, and line range usable by get_adaptive_context."""
    server = create_server(ledger_path=ledger_path, workspace_root=workspace)

    result = await server.call_tool("find_symbol", {"name": "UserManager.authenticate"})
    data = json.loads(result.content[0].text)

    assert data["query"] == "UserManager.authenticate"
    assert data["total_matches"] == 1
    assert data["truncated"] is False
    assert data["matches"] == [
        {
            "file_path": "pkg/service.py",
            "symbol": "UserManager.authenticate",
            "kind": "method",
            "lineno": 7,
            "end_lineno": 8,
        }
    ]

    context = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": data["matches"][0]["file_path"],
            "task_intent": "Rewrite the body",
            "symbol_name": data["matches"][0]["symbol"],
        },
    )
    assert "return bool(username)" in context.content[0].text


@pytest.mark.asyncio
async def test_restarted_server_does_not_reparse_unchanged_files(
    workspace: Path, ledger_path: Path, parse_calls: list[str]
):
    """Verify a cold-restarted server answers find_symbol from the ledger cache without parsing."""
    first = create_server(ledger_path=ledger_path, workspace_root=workspace)
    await first.call_tool("find_symbol", {"name": "calculate_hash"})
    assert len(parse_calls) == 3

    parse_calls.clear()
    restarted = create_server(ledger_path=ledger_path, workspace_root=workspace)
    result = await restarted.call_tool("find_symbol", {"name": "calculate_hash"})

    assert parse_calls == []
    data = json.loads(result.content[0].text)
    assert [m["file_path"] for m in data["matches"]] == ["pkg/utils.py"]
