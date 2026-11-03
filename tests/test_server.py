"""Integration tests for ACC-MCP Server and Protocol Tools (Block 2)."""

import ast
import json
from pathlib import Path
import pytest
from acc_mcp.guard import SecurityViolationError
from acc_mcp.server import create_server


@pytest.fixture
def temp_ledger(tmp_path: Path) -> Path:
    return tmp_path / "server_test_budget.json"


@pytest.fixture
def sample_source_file(tmp_path: Path) -> Path:
    file_path = tmp_path / "sample_service.py"
    content = '''"""Sample microservice module for MCP server testing."""

from typing import List, Optional

class UserManager:
    """Manages user authentication and caching."""

    def __init__(self, cache_ttl: int = 300) -> None:
        """Initialize user manager with cache TTL."""
        self.cache_ttl = cache_ttl
        self._cache = {}

    def authenticate(self, username: str, token: str) -> bool:
        """Authenticate user with bearer token."""
        if not username or not token:
            return False
        self._cache[username] = token
        return True

    def get_token(self, username: str) -> Optional[str]:
        """Fetch token from cache."""
        return self._cache.get(username)


def calculate_hash(data: str) -> str:
    """Calculate deterministic string hash."""
    return str(hash(data))
'''
    file_path.write_text(content, encoding="utf-8")
    return file_path


# ---------------------------------------------------------------------------
# 1. MCP Tool Discovery Tests (Step 7.1 & Step 7.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_tool_discovery(temp_ledger: Path):
    """Verify that the MCP server correctly advertises and discovers all required tools."""
    server = create_server(ledger_path=temp_ledger)
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]

    assert "get_adaptive_context" in tool_names
    assert "get_token_ledger" in tool_names
    assert len(tools) >= 2

    # Check tool schemas
    context_tool = next(t for t in tools if t.name == "get_adaptive_context")
    schema_str = str(context_tool.input_schema)
    assert "task_intent" in schema_str
    assert "file_path" in schema_str


# ---------------------------------------------------------------------------
# 2. get_adaptive_context Tool Execution Tests (Step 7.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_get_adaptive_context_tier1(sample_source_file: Path, temp_ledger: Path):
    """Verify get_adaptive_context serves Tier 1 interface context for inspection intent."""
    server = create_server(ledger_path=temp_ledger)

    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Inspect the UserManager authenticate signature and arguments",
            "symbol_name": "UserManager.authenticate",
        },
    )

    assert result.is_error is False
    assert len(result.content) > 0
    text = result.content[0].text

    # Target method interface is present with stubbed body
    assert "def authenticate(self, username: str, token: str) -> bool:" in text
    assert "..." in text
    assert "self._cache[username] = token" not in text

    # Valid Python syntax
    parsed = ast.parse(text)
    assert isinstance(parsed, ast.Module)


@pytest.mark.asyncio
async def test_tool_get_adaptive_context_tier2(sample_source_file: Path, temp_ledger: Path):
    """Verify get_adaptive_context serves Tier 2 call-graph skeleton for refactor intent."""
    server = create_server(ledger_path=temp_ledger)

    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Refactor UserManager interface and trace callers",
            "symbol_name": "UserManager",
        },
    )

    assert result.is_error is False
    text = result.content[0].text
    assert "Tier 2 Context" in text
    assert "Cyclomatic Complexity" in text
    assert "UserManager" in text


@pytest.mark.asyncio
async def test_tool_get_adaptive_context_tier3(sample_source_file: Path, temp_ledger: Path):
    """Verify get_adaptive_context serves Tier 3 full implementation body for rewrite intent."""
    server = create_server(ledger_path=temp_ledger)

    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Rewrite and fix authentication bug in UserManager.authenticate",
            "symbol_name": "UserManager.authenticate",
        },
    )

    assert result.is_error is False
    text = result.content[0].text

    # Full implementation body is present
    assert "if not username or not token:" in text
    assert "self._cache[username] = token" in text
    assert "return True" in text

    # Valid Python syntax
    parsed = ast.parse(text)
    assert isinstance(parsed, ast.Module)


# ---------------------------------------------------------------------------
# 3. get_token_ledger Tool Execution Tests (Step 7.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_get_token_ledger_audit_log(sample_source_file: Path, temp_ledger: Path):
    """Verify get_token_ledger returns JSON ledger with session metrics and query history."""
    server = create_server(ledger_path=temp_ledger)

    # Initial ledger should be empty
    initial_res = await server.call_tool("get_token_ledger", {})
    initial_data = json.loads(initial_res.content[0].text)
    assert initial_data["summary"]["total_requests"] == 0
    assert len(initial_data["history"]) == 0

    # Perform a retrieval query
    await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Check function signature for calculate_hash",
            "symbol_name": "calculate_hash",
        },
    )

    # Query ledger again
    updated_res = await server.call_tool("get_token_ledger", {})
    updated_data = json.loads(updated_res.content[0].text)

    summary = updated_data["summary"]
    assert summary["total_requests"] == 1
    assert summary["cumulative_baseline_tokens"] > 0
    assert summary["cumulative_served_tokens"] > 0
    assert summary["cumulative_tokens_saved"] > 0
    assert summary["overall_savings_percentage"] > 0.0

    assert len(updated_data["history"]) == 1
    record = updated_data["history"][0]
    assert record["file_path"] == str(sample_source_file)
    assert record["symbol_name"] == "calculate_hash"


# ---------------------------------------------------------------------------
# 4. Security & Error Handling Tests (Step 7.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_security_path_rejection(temp_ledger: Path):
    """Verify MCP tool immediately rejects protected paths (.env, .git, credentials)."""
    server = create_server(ledger_path=temp_ledger)

    with pytest.raises(Exception) as exc_info:
        await server.call_tool(
            "get_adaptive_context",
            {
                "file_path": ".env",
                "task_intent": "View database credentials",
            },
        )

    cause = exc_info.value.__cause__ or exc_info.value
    assert isinstance(cause, SecurityViolationError)
    assert "denied by GuardRail" in str(cause)

    with pytest.raises(Exception) as exc_info:
        await server.call_tool(
            "get_adaptive_context",
            {
                "file_path": "alembic/migrations/env.py",
                "task_intent": "Inspect migration logic",
            },
        )

    cause = exc_info.value.__cause__ or exc_info.value
    assert isinstance(cause, SecurityViolationError)


@pytest.mark.asyncio
async def test_tool_file_not_found(temp_ledger: Path):
    """Verify MCP tool raises FileNotFoundError on missing file."""
    server = create_server(ledger_path=temp_ledger)

    with pytest.raises(Exception) as exc_info:
        await server.call_tool(
            "get_adaptive_context",
            {
                "file_path": "non_existent_file_xyz123.py",
                "task_intent": "Inspect signature",
            },
        )

    cause = exc_info.value.__cause__ or exc_info.value
    assert isinstance(cause, FileNotFoundError)


# ---------------------------------------------------------------------------
# 5. Persistent Ledger File Across Server Restarts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_persistent_ledger_file(sample_source_file: Path, temp_ledger: Path):
    """Verify that ledger file persists on disk and survives server restart."""
    server1 = create_server(ledger_path=temp_ledger)

    await server1.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Check UserManager constructor",
            "symbol_name": "UserManager.__init__",
        },
    )

    assert temp_ledger.exists()

    # Cold restart: instantiate server2 with same ledger path
    server2 = create_server(ledger_path=temp_ledger)
    ledger_res = await server2.call_tool("get_token_ledger", {})
    data = json.loads(ledger_res.content[0].text)

    assert data["summary"]["total_requests"] == 1
    assert data["summary"]["cached_files_count"] >= 1
    assert len(data["history"]) == 1
