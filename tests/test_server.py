"""Integration tests for ACC-MCP Server and Protocol Tools (Block 2)."""

import ast
import json
from pathlib import Path
import pytest
from acc_mcp.guard import SecurityViolationError
from acc_mcp.server import create_server


@pytest.fixture(autouse=True)
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ACC_MCP_ROOT", str(tmp_path))
    monkeypatch.delenv("ACC_MCP_SESSION_BUDGET", raising=False)
    return tmp_path


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
    assert "find_symbol" in tool_names
    assert "reset_session" in tool_names
    assert len(tools) >= 4

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
async def test_tool_rejects_py_symlink_to_env(workspace_root: Path, temp_ledger: Path):
    """Verify a `.py` symlink pointing at `.env` never serves the secrets, even at Tier 3."""
    (workspace_root / ".env").write_text("API_KEY = 'sk-live-secret'\n", encoding="utf-8")
    (workspace_root / "notes.py").symlink_to(workspace_root / ".env")
    server = create_server(ledger_path=temp_ledger)

    with pytest.raises(Exception) as exc_info:
        await server.call_tool(
            "get_adaptive_context",
            {"file_path": "notes.py", "task_intent": "Rewrite the module", "min_tier": 3},
        )

    cause = exc_info.value.__cause__ or exc_info.value
    assert isinstance(cause, SecurityViolationError)
    assert "sk-live-secret" not in str(cause)


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



# ---------------------------------------------------------------------------
# 6. min_tier Escalation Tests (Step 12.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_min_tier_escalates_inspection_to_tier3(sample_source_file: Path, temp_ledger: Path):
    """Verify min_tier=3 forces Tier 3 even when intent is pure inspection (normally Tier 1)."""
    server = create_server(ledger_path=temp_ledger)

    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Inspect the UserManager authenticate signature",
            "symbol_name": "UserManager.authenticate",
            "min_tier": 3,
        },
    )

    assert result.is_error is False
    text = result.content[0].text

    # Full implementation body must be present (Tier 3), not stubbed
    assert "if not username or not token:" in text
    assert "self._cache[username] = token" in text

    # Valid Python syntax
    parsed = ast.parse(text)
    assert isinstance(parsed, ast.Module)


@pytest.mark.asyncio
async def test_min_tier_cannot_lower_model_choice(sample_source_file: Path, temp_ledger: Path):
    """Verify that min_tier below the model's own tier never lowers the served tier."""
    server = create_server(ledger_path=temp_ledger)

    # Rewrite intent naturally selects Tier 3
    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Rewrite authentication logic in UserManager.authenticate",
            "symbol_name": "UserManager.authenticate",
            "min_tier": 1,
        },
    )

    assert result.is_error is False
    text = result.content[0].text

    # Full implementation body must still be present (model chose Tier 3, min_tier=1 can't lower it)
    assert "if not username or not token:" in text
    assert "self._cache[username] = token" in text


@pytest.mark.asyncio
async def test_min_tier_ledger_records_escalation_metadata(sample_source_file: Path, temp_ledger: Path):
    """Verify ledger records requested_min_tier and model_tier for escalation tracking."""
    server = create_server(ledger_path=temp_ledger)

    # Inspection intent (model will pick Tier 1) with min_tier=3 override
    await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Check calculate_hash signature",
            "symbol_name": "calculate_hash",
            "min_tier": 3,
        },
    )

    ledger_res = await server.call_tool("get_token_ledger", {})
    data = json.loads(ledger_res.content[0].text)

    assert len(data["history"]) == 1
    record = data["history"][0]

    # Served tier must be escalated to 3
    assert record["served_tier"] == 3
    # Model's own choice should be recorded (Tier 1 for inspection)
    assert record["model_tier"] == 1
    # Requested min_tier should be recorded
    assert record["requested_min_tier"] == 3


@pytest.mark.asyncio
async def test_min_tier_none_behaves_as_before(sample_source_file: Path, temp_ledger: Path):
    """Verify that omitting min_tier preserves original behavior and records None in ledger."""
    server = create_server(ledger_path=temp_ledger)

    await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Check calculate_hash signature",
            "symbol_name": "calculate_hash",
        },
    )

    ledger_res = await server.call_tool("get_token_ledger", {})
    data = json.loads(ledger_res.content[0].text)

    record = data["history"][0]
    assert record["served_tier"] == 1
    assert record["model_tier"] == 1
    assert record["requested_min_tier"] is None


@pytest.mark.asyncio
async def test_tier3_cut_reports_omitted_lines(tmp_path: Path, temp_ledger: Path):
    """Verify an oversized Tier 3 slice is cut verbatim and names the omitted line range."""
    big_file = tmp_path / "big_module.py"
    big_file.write_text(
        "\n".join(
            f"def step_{i}(values: list[int]) -> int:\n"
            f"    kept = [v for v in values if v > {i}]  # filter step {i}\n"
            f"    return sum(kept)\n"
            for i in range(40)
        ),
        encoding="utf-8",
    )
    server = create_server(ledger_path=temp_ledger, max_tokens=250)

    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(big_file),
            "task_intent": "Rewrite the module",
            "min_tier": 3,
        },
    )

    text = result.content[0].text
    assert text.startswith("# big_module.py lines 1-")
    assert "# filter step 0" in text
    assert "omitted to fit the 250-token ceiling; request symbol_name=" in text
    assert "Context truncated" not in text


@pytest.mark.asyncio
async def test_min_tier_clamped_to_valid_range(sample_source_file: Path, temp_ledger: Path):
    """Verify out-of-range min_tier values are clamped to [1, 3]."""
    server = create_server(ledger_path=temp_ledger)

    # min_tier=5 should be clamped to 3
    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(sample_source_file),
            "task_intent": "Check calculate_hash signature",
            "symbol_name": "calculate_hash",
            "min_tier": 5,
        },
    )
    assert result.is_error is False

    ledger_res = await server.call_tool("get_token_ledger", {})
    data = json.loads(ledger_res.content[0].text)
    record = data["history"][0]
    assert record["served_tier"] == 3
    assert record["requested_min_tier"] == 3


# ---------------------------------------------------------------------------
# 7. Session Budget Drives Tier Selection (Steps 19.2 - 19.4)
# ---------------------------------------------------------------------------


@pytest.fixture
def long_function_file(tmp_path: Path) -> Path:
    """A function whose Tier 3 body is far larger than its Tier 2 skeleton."""
    file_path = tmp_path / "long_routine.py"
    steps = "".join(f"    total += values[{i}] * {i}  # step {i}\n" for i in range(60))
    file_path.write_text(
        "def long_routine(values: list[int]) -> int:\n"
        '    """Combine values through many weighted steps."""\n'
        "    total = 0\n"
        f"{steps}"
        "    return total\n",
        encoding="utf-8",
    )
    return file_path


REWRITE_ARGS = {"task_intent": "Rewrite the body of long_routine", "symbol_name": "long_routine"}


async def serve_full_tier3(server, file_path: Path) -> tuple[str, int]:
    """Serve the Tier 3 slice with a fresh budget; return its text and token count."""
    result = await server.call_tool(
        "get_adaptive_context", {"file_path": str(file_path), **REWRITE_ARGS}
    )
    record = server.budget_tracker.history[-1]
    assert record.served_tier == 3
    server.budget_tracker.reset_session()
    return result.content[0].text, record.served_tokens


@pytest.mark.asyncio
async def test_tier_steps_down_when_session_budget_is_low(long_function_file: Path, temp_ledger: Path):
    """Verify a Tier 3 slice that does not fit the remaining budget is served as Tier 2 with a note."""
    server = create_server(ledger_path=temp_ledger)
    tracker = server.budget_tracker
    _, full_tokens = await serve_full_tier3(server, long_function_file)

    remaining = full_tokens - 1
    tracker.session_served_tokens = tracker.session_budget - remaining
    result = await server.call_tool(
        "get_adaptive_context", {"file_path": str(long_function_file), **REWRITE_ARGS}
    )
    text = result.content[0].text

    assert text.startswith(
        f"# [ACC-MCP] Served Tier 2 instead of Tier 3: only {remaining} of the "
        f"6000-token session budget is left; call reset_session"
    )
    assert "Tier 2 Context" in text
    assert "# step 0" not in text

    record = tracker.history[-1]
    assert (record.model_tier, record.served_tier, record.budget_limited) == (3, 2, True)
    assert record.served_tokens <= remaining
    assert tracker.remaining_session_tokens == remaining - record.served_tokens


@pytest.mark.asyncio
async def test_forced_min_tier_that_does_not_fit_is_refused(long_function_file: Path, temp_ledger: Path):
    """Verify min_tier=3 over budget returns a clear message instead of a silently cut slice."""
    server = create_server(ledger_path=temp_ledger)
    tracker = server.budget_tracker
    _, full_tokens = await serve_full_tier3(server, long_function_file)

    remaining = full_tokens - 1
    tracker.session_served_tokens = tracker.session_budget - remaining
    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": str(long_function_file),
            "task_intent": "Check long_routine signature",
            "symbol_name": "long_routine",
            "min_tier": 3,
        },
    )
    text = result.content[0].text

    assert result.is_error is False
    assert text.startswith(
        f"# [ACC-MCP] Not served: the Tier 3 slice needs about {full_tokens} tokens, "
        f"but only {remaining} of the 6000-token session budget is left."
    )
    assert "total +=" not in text
    assert tracker.total_requests == 1
    assert tracker.remaining_session_tokens == remaining


@pytest.mark.asyncio
async def test_reset_session_restores_budget(long_function_file: Path, temp_ledger: Path):
    """Verify an exhausted budget refuses slices and reset_session brings back the full Tier 3."""
    server = create_server(ledger_path=temp_ledger)
    tracker = server.budget_tracker
    full_text, _ = await serve_full_tier3(server, long_function_file)

    tracker.session_served_tokens = tracker.session_budget
    exhausted = await server.call_tool(
        "get_adaptive_context", {"file_path": str(long_function_file), **REWRITE_ARGS}
    )
    assert exhausted.content[0].text.startswith(
        "# [ACC-MCP] Not served: the 6000-token session budget is used up (0 tokens left)."
    )

    reset = json.loads((await server.call_tool("reset_session", {})).content[0].text)
    assert reset["previous_session_served_tokens"] == 6000
    assert reset["session_remaining_tokens"] == 6000

    again = await server.call_tool(
        "get_adaptive_context", {"file_path": str(long_function_file), **REWRITE_ARGS}
    )
    assert again.content[0].text == full_text
    assert tracker.history[-1].budget_limited is False

    ledger = json.loads((await server.call_tool("get_token_ledger", {})).content[0].text)
    assert ledger["summary"]["total_requests"] == 2
    assert ledger["summary"]["session_requests"] == 1


@pytest.mark.asyncio
async def test_ledger_saved_once_per_request(
    sample_source_file: Path, temp_ledger: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify a request writes the ledger once, also when it is the one that indexes the workspace."""
    server = create_server(ledger_path=temp_ledger)
    tracker = server.budget_tracker
    saves: list[Path | None] = []
    real_save = tracker.save_ledger

    def counting_save(target_path=None):
        saves.append(target_path)
        real_save(target_path)

    monkeypatch.setattr(tracker, "save_ledger", counting_save)
    args = {
        "file_path": str(sample_source_file),
        "task_intent": "Check calculate_hash signature",
        "symbol_name": "calculate_hash",
    }

    await server.call_tool("get_adaptive_context", args)
    assert len(saves) == 1
    assert tracker.is_file_cached(str(sample_source_file.resolve()))

    await server.call_tool("get_adaptive_context", args)
    assert len(saves) == 2

    restarted = create_server(ledger_path=temp_ledger)
    assert restarted.budget_tracker.total_requests == 2
    assert restarted.budget_tracker.is_file_cached(str(sample_source_file.resolve()))
