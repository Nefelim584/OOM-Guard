"""Unit tests for ContextBudgetTracker, persistent ledger, and restart recovery (Block 5)."""

import json
from pathlib import Path
import pytest
from acc_mcp.budget import ContextBudgetTracker, RetrievalRecord
from acc_mcp.parser import ASTParser


@pytest.fixture
def temp_ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "context_budget.json"


@pytest.fixture
def tracker(temp_ledger_path: Path) -> ContextBudgetTracker:
    return ContextBudgetTracker(ledger_path=temp_ledger_path, auto_save=True)


# ---------------------------------------------------------------------------
# 1. Retrieval Event Recording & Savings Arithmetic (Step 6.1)
# ---------------------------------------------------------------------------


def test_single_retrieval_recording_and_savings(tracker: ContextBudgetTracker):
    """Verify single retrieval records tokens and computes relative savings percentage."""
    baseline = (
        "def full_function_implementation():\n"
        "    data = [x * 2 for x in range(100)]\n"
        "    total = sum(data)\n"
        "    print(f'Processed {total} items')\n"
        "    return total\n"
    )
    served = "def full_function_implementation():\n    ...\n"

    record = tracker.record_retrieval(
        file_path="src/service.py",
        symbol_name="full_function_implementation",
        task_intent="Check function interface",
        served_tier=1,
        baseline_content=baseline,
        served_content=served,
        query_id="query_101",
    )

    assert record.query_id == "query_101"
    assert record.served_tier == 1
    assert record.baseline_tokens > record.served_tokens
    assert record.tokens_saved == record.baseline_tokens - record.served_tokens
    assert record.savings_percentage > 50.0

    # Summary counters
    assert tracker.total_requests == 1
    assert tracker.cumulative_baseline_tokens == record.baseline_tokens
    assert tracker.cumulative_served_tokens == record.served_tokens
    assert tracker.cumulative_tokens_saved == record.tokens_saved
    assert tracker.overall_savings_percentage == record.savings_percentage


def test_multi_turn_retrieval_accounting(tracker: ContextBudgetTracker):
    """Verify multi-turn session tracking aggregates cumulative tokens and tier distribution."""
    # Query 1: Tier 1
    tracker.record_retrieval(
        file_path="src/db.py",
        symbol_name="connect",
        task_intent="Inspect signature",
        served_tier=1,
        baseline_content="class DB:\n    def connect(self):\n        pass\n" * 10,
        served_content="class DB:\n    def connect(self):\n        ...\n",
    )

    # Query 2: Tier 2
    tracker.record_retrieval(
        file_path="src/auth.py",
        symbol_name="validate_token",
        task_intent="Trace callers",
        served_tier=2,
        baseline_content="def validate_token(t):\n    return True\n" * 15,
        served_content="def validate_token(t):\n    ...\n# Callers: ['handler']\n",
    )

    # Query 3: Tier 3
    full_code = "def process():\n    x = 1\n    return x\n"
    tracker.record_retrieval(
        file_path="src/core.py",
        symbol_name="process",
        task_intent="Rewrite logic",
        served_tier=3,
        baseline_content=full_code,
        served_content=full_code,
    )

    assert tracker.total_requests == 3
    assert tracker.cumulative_baseline_tokens > tracker.cumulative_served_tokens
    assert tracker.cumulative_tokens_saved > 0
    assert tracker.overall_savings_percentage > 0.0

    dist = tracker.tier_distribution
    assert dist[1] == 1
    assert dist[2] == 1
    assert dist[3] == 1


# ---------------------------------------------------------------------------
# 2. JSON Persistence & Ledger Export (Step 6.2)
# ---------------------------------------------------------------------------


def test_ledger_json_persistence(tracker: ContextBudgetTracker, temp_ledger_path: Path):
    """Verify ledger exports to JSON with summary, history, and cache."""
    tracker.record_retrieval(
        file_path="src/metrics.py",
        symbol_name="log_event",
        task_intent="View parameters",
        served_tier=1,
        baseline_content="def log_event(name, val): pass\n" * 8,
        served_content="def log_event(name, val): ...\n",
    )

    assert temp_ledger_path.exists()

    with open(temp_ledger_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["version"] == "1.0"
    assert "summary" in data
    assert data["summary"]["total_requests"] == 1
    assert "history" in data
    assert len(data["history"]) == 1
    assert data["history"][0]["file_path"] == "src/metrics.py"
    assert "symbol_cache" in data


# ---------------------------------------------------------------------------
# 3. Repository Symbol Map Caching & Restart Test (Steps 6.3 & 6.4)
# ---------------------------------------------------------------------------


def test_restart_recovery_preserves_statistics_and_symbol_cache(
    temp_ledger_path: Path, complex_parser: ASTParser
):
    """Block 5 Restart Test: Verify cold agent restart retains symbol maps and token statistics without re-parsing."""
    # ---------------------------------------------------------
    # Session 1: Run agent interactions, cache symbols, record tokens
    # ---------------------------------------------------------
    session1_tracker = ContextBudgetTracker(ledger_path=temp_ledger_path, auto_save=True)

    # 1. Cache symbols for complex.py
    symbols_to_cache = complex_parser.get_symbols()
    session1_tracker.cache_symbols(
        file_path="complex.py",
        symbols=symbols_to_cache,
        file_content=complex_parser.source_code,
    )

    # 2. Record multi-turn retrievals
    session1_tracker.record_retrieval(
        file_path="complex.py",
        symbol_name="validate_user",
        task_intent="Inspect arguments",
        served_tier=1,
        baseline_content=complex_parser.source_code,
        served_content="def validate_user(user_id: int, roles: list[str]) -> bool: ...",
    )
    session1_tracker.record_retrieval(
        file_path="complex.py",
        symbol_name="PaymentProcessor.process_transaction",
        task_intent="Trace caller dependencies",
        served_tier=2,
        baseline_content=complex_parser.source_code,
        served_content=complex_parser.get_tier2_context(symbol_name="PaymentProcessor.process_transaction"),
    )

    summary_before_restart = session1_tracker.get_summary()
    assert summary_before_restart["total_requests"] == 2
    assert summary_before_restart["cumulative_tokens_saved"] > 0
    assert summary_before_restart["cached_files_count"] == 1

    # Ensure ledger is flushed to disk
    session1_tracker.save_ledger()

    # ---------------------------------------------------------
    # Cold Restart: Create brand new tracker instance from disk
    # ---------------------------------------------------------
    session2_tracker = ContextBudgetTracker(ledger_path=temp_ledger_path, auto_save=True)

    # 1. Verify token statistics fully recovered
    summary_after_restart = session2_tracker.get_summary()
    assert summary_after_restart["total_requests"] == summary_before_restart["total_requests"]
    assert (
        summary_after_restart["cumulative_baseline_tokens"]
        == summary_before_restart["cumulative_baseline_tokens"]
    )
    assert (
        summary_after_restart["cumulative_served_tokens"]
        == summary_before_restart["cumulative_served_tokens"]
    )
    assert (
        summary_after_restart["cumulative_tokens_saved"]
        == summary_before_restart["cumulative_tokens_saved"]
    )
    assert (
        summary_after_restart["overall_savings_percentage"]
        == summary_before_restart["overall_savings_percentage"]
    )
    assert summary_after_restart["tier_distribution"] == summary_before_restart["tier_distribution"]

    # 2. Verify repository symbol map fully recovered without re-parsing files
    assert session2_tracker.is_file_cached("complex.py") is True
    cached_map = session2_tracker.get_cached_symbols("complex.py")
    assert cached_map is not None
    assert "validate_user" in cached_map
    assert "PaymentProcessor.process_transaction" in cached_map
    assert "log_audit" in cached_map

    # 3. Verify AST metrics (cyclomatic complexity, in-degree, callers) retained exactly
    val_info = session2_tracker.get_cached_symbol_info("complex.py", "validate_user")
    assert val_info is not None
    assert val_info.name == "validate_user"
    assert val_info.cyclomatic_complexity >= 3
    assert val_info.in_degree >= 1
    assert "PermissionError" in val_info.raised_exceptions
    assert "ValueError" in val_info.raised_exceptions

    tx_info = session2_tracker.get_cached_symbol_info("complex.py", "process_transaction")
    assert tx_info is not None
    assert tx_info.cyclomatic_complexity >= 6
    assert "validate_user" in tx_info.callees
