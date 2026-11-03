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


# ---------------------------------------------------------------------------
# 4. Session Budget and History Cap (Steps 19.1, 19.3 & 19.4)
# ---------------------------------------------------------------------------


def record_n(tracker: ContextBudgetTracker, n: int) -> list[RetrievalRecord]:
    """Record n retrievals of growing size, cycling through tiers 1, 2, 3."""
    return [
        tracker.record_retrieval(
            file_path=f"src/mod_{i}.py",
            task_intent="Inspect signature",
            served_tier=i % 3 + 1,
            baseline_content="def f(x):\n    return x\n" * (10 + i),
            served_content="def f(x): ...\n" * (i + 1),
            query_id=f"q_{i}",
        )
        for i in range(n)
    ]


def test_session_budget_counts_served_tokens_and_resets(temp_ledger_path: Path):
    """Verify served tokens count against the session budget and reset_session restores it."""
    tracker = ContextBudgetTracker(ledger_path=temp_ledger_path, session_budget=1000)
    records = record_n(tracker, 2)
    served = sum(r.served_tokens for r in records)

    assert tracker.session_served_tokens == served
    assert tracker.remaining_session_tokens == 1000 - served
    assert tracker.get_summary()["session_remaining_tokens"] == 1000 - served

    tracker.reset_session()

    assert tracker.remaining_session_tokens == 1000
    assert tracker.session_requests == 0
    assert tracker.total_requests == 2


def test_session_is_not_restored_after_restart(temp_ledger_path: Path):
    """Verify a restarted tracker keeps the totals but starts a fresh session."""
    record_n(ContextBudgetTracker(ledger_path=temp_ledger_path, session_budget=1000), 2)

    restarted = ContextBudgetTracker(ledger_path=temp_ledger_path, session_budget=1000)

    assert restarted.total_requests == 2
    assert restarted.session_served_tokens == 0
    assert restarted.remaining_session_tokens == 1000


def test_session_budget_from_env(temp_ledger_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify ACC_MCP_SESSION_BUDGET sets the budget, the argument wins, and bad values fail loudly."""
    monkeypatch.setenv("ACC_MCP_SESSION_BUDGET", "1234")
    assert ContextBudgetTracker(ledger_path=temp_ledger_path).session_budget == 1234
    assert ContextBudgetTracker(ledger_path=temp_ledger_path, session_budget=500).session_budget == 500

    monkeypatch.setenv("ACC_MCP_SESSION_BUDGET", "lots")
    with pytest.raises(ValueError, match="ACC_MCP_SESSION_BUDGET"):
        ContextBudgetTracker(ledger_path=temp_ledger_path)

    monkeypatch.delenv("ACC_MCP_SESSION_BUDGET")
    assert ContextBudgetTracker(ledger_path=temp_ledger_path).session_budget == 6000


def test_history_cap_keeps_correct_totals(temp_ledger_path: Path):
    """Verify only the last N records are kept while totals cover every request, also after restart."""
    tracker = ContextBudgetTracker(ledger_path=temp_ledger_path, history_limit=3)
    records = record_n(tracker, 5)

    expected = {
        "total_requests": 5,
        "cumulative_baseline_tokens": sum(r.baseline_tokens for r in records),
        "cumulative_served_tokens": sum(r.served_tokens for r in records),
        "tier_distribution": {1: 2, 2: 2, 3: 1},
    }

    assert [r.query_id for r in tracker.history] == ["q_2", "q_3", "q_4"]
    for key, value in expected.items():
        assert tracker.get_summary()[key] == value

    with open(temp_ledger_path, "r", encoding="utf-8") as f:
        assert len(json.load(f)["history"]) == 3

    restarted = ContextBudgetTracker(ledger_path=temp_ledger_path, history_limit=3)
    assert len(restarted.history) == 3
    for key, value in expected.items():
        assert restarted.get_summary()[key] == value


def test_escalation_rate_counts_min_tier_raises_across_history_cap(temp_ledger_path: Path):
    """Verify escalation_rate counts only min_tier above the model's tier and survives the cap and a restart."""
    tracker = ContextBudgetTracker(ledger_path=temp_ledger_path, history_limit=2)
    cases = [
        (1, None, 1),  # model's choice, no min_tier
        (1, 3, 3),  # escalated: agent asked for more than the model gave
        (3, 1, 3),  # min_tier below the model's choice is not an escalation
        (2, 2, 2),  # min_tier equal to the model's choice is not an escalation
    ]
    for model_tier, min_tier, served_tier in cases:
        tracker.record_retrieval(
            file_path="src/service.py",
            task_intent="Inspect signature",
            served_tier=served_tier,
            baseline_content="def f(x):\n    return x\n" * 10,
            served_content="def f(x): ...\n",
            requested_min_tier=min_tier,
            model_tier=model_tier,
        )

    assert [r.was_escalated for r in tracker.history] == [False, False]
    assert tracker.total_escalations == 1
    assert tracker.get_summary()["escalation_rate"] == 0.25

    restarted = ContextBudgetTracker(ledger_path=temp_ledger_path, history_limit=2)
    assert restarted.get_summary()["escalations"] == 1
    assert restarted.get_summary()["escalation_rate"] == 0.25


def test_ledger_without_totals_is_recounted_from_history(temp_ledger_path: Path):
    """Verify a ledger written before totals were stored still loads with correct totals."""
    records = record_n(ContextBudgetTracker(ledger_path=temp_ledger_path), 4)
    with open(temp_ledger_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    del data["totals"]
    with open(temp_ledger_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    loaded = ContextBudgetTracker(ledger_path=temp_ledger_path, history_limit=2)

    assert loaded.total_requests == 4
    assert loaded.cumulative_served_tokens == sum(r.served_tokens for r in records)
    assert loaded.tier_distribution == {1: 2, 2: 1, 3: 1}
    assert [r.query_id for r in loaded.history] == ["q_2", "q_3"]
