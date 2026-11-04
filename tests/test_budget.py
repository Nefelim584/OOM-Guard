"""Unit tests for ContextBudgetTracker, persistent ledger, and restart recovery (Block 5)."""

import json
from pathlib import Path
import pytest
from acc_mcp.budget import ContextBudgetTracker, RetrievalRecord, ServedRange
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


def test_single_retrieval_recording_and_upper_bound(tracker: ContextBudgetTracker):
    """Verify a retrieval records served tokens and the whole-file upper-bound saving."""
    whole_file = (
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
        whole_file_content=whole_file,
        served_content=served,
        query_id="query_101",
    )

    assert record.query_id == "query_101"
    assert record.served_tier == 1
    assert record.whole_file_tokens > record.served_tokens
    assert record.upper_bound_savings_pct == round(
        (record.whole_file_tokens - record.served_tokens) / record.whole_file_tokens * 100.0, 2
    )
    assert record.upper_bound_savings_pct > 50.0

    # Summary counters
    assert tracker.total_requests == 1
    assert tracker.whole_file_tokens == record.whole_file_tokens
    assert tracker.cumulative_served_tokens == record.served_tokens
    assert tracker.upper_bound_savings_pct == record.upper_bound_savings_pct


def test_multi_turn_retrieval_accounting(tracker: ContextBudgetTracker):
    """Verify multi-turn session tracking aggregates cumulative tokens and tier distribution."""
    # Query 1: Tier 1
    tracker.record_retrieval(
        file_path="src/db.py",
        symbol_name="connect",
        task_intent="Inspect signature",
        served_tier=1,
        whole_file_content="class DB:\n    def connect(self):\n        pass\n" * 10,
        served_content="class DB:\n    def connect(self):\n        ...\n",
    )

    # Query 2: Tier 2
    tracker.record_retrieval(
        file_path="src/auth.py",
        symbol_name="validate_token",
        task_intent="Trace callers",
        served_tier=2,
        whole_file_content="def validate_token(t):\n    return True\n" * 15,
        served_content="def validate_token(t):\n    ...\n# Callers: ['handler']\n",
    )

    # Query 3: Tier 3
    full_code = "def process():\n    x = 1\n    return x\n"
    tracker.record_retrieval(
        file_path="src/core.py",
        symbol_name="process",
        task_intent="Rewrite logic",
        served_tier=3,
        whole_file_content=full_code,
        served_content=full_code,
    )

    assert tracker.total_requests == 3
    assert tracker.whole_file_tokens > tracker.cumulative_served_tokens
    assert tracker.upper_bound_savings_pct > 0.0

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
        whole_file_content="def log_event(name, val): pass\n" * 8,
        served_content="def log_event(name, val): ...\n",
    )

    assert temp_ledger_path.exists()

    with open(temp_ledger_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["version"] == "1.1"  # 1.1: whole_file_tokens, line_ranges, per-tier and per-tool totals (Phase 23)
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
        whole_file_content=complex_parser.source_code,
        served_content="def validate_user(user_id: int, roles: list[str]) -> bool: ...",
    )
    session1_tracker.record_retrieval(
        file_path="complex.py",
        symbol_name="PaymentProcessor.process_transaction",
        task_intent="Trace caller dependencies",
        served_tier=2,
        whole_file_content=complex_parser.source_code,
        served_content=complex_parser.get_tier2_context(symbol_name="PaymentProcessor.process_transaction"),
    )

    summary_before_restart = session1_tracker.get_summary()
    assert summary_before_restart["total_requests"] == 2
    assert summary_before_restart["upper_bound_savings_pct"] > 0
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
        summary_after_restart["whole_file_tokens"]
        == summary_before_restart["whole_file_tokens"]
    )
    assert (
        summary_after_restart["cumulative_served_tokens"]
        == summary_before_restart["cumulative_served_tokens"]
    )
    assert (
        summary_after_restart["upper_bound_savings_pct"]
        == summary_before_restart["upper_bound_savings_pct"]
    )
    for key in ("served_tokens_by_tier", "served_tokens_by_tool"):
        assert summary_after_restart[key] == summary_before_restart[key]
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
            whole_file_content="def f(x):\n    return x\n" * (10 + i),
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
        "whole_file_tokens": sum(r.whole_file_tokens for r in records),
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
            whole_file_content="def f(x):\n    return x\n" * 10,
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


def test_ledger_path_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify ACC_MCP_LEDGER picks the ledger file when no path is passed, and an explicit path wins."""
    env_ledger = tmp_path / "runs" / "task_01.json"
    env_ledger.parent.mkdir()
    monkeypatch.setenv("ACC_MCP_LEDGER", str(env_ledger))

    tracker = ContextBudgetTracker()
    assert tracker.ledger_path == env_ledger
    tracker.save_ledger()
    assert env_ledger.exists()

    explicit = tmp_path / "explicit.json"
    assert ContextBudgetTracker(ledger_path=explicit).ledger_path == explicit


def test_ledger_path_defaults_to_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify the ledger falls back to ./context_budget.json when ACC_MCP_LEDGER is unset."""
    monkeypatch.delenv("ACC_MCP_LEDGER", raising=False)
    monkeypatch.chdir(tmp_path)
    assert ContextBudgetTracker(auto_save=False).ledger_path == Path("context_budget.json")


# ---------------------------------------------------------------------------
# 5. Honest Ledger Accounting (Steps 23.1 - 23.4)
# ---------------------------------------------------------------------------


def test_served_tokens_by_tier_and_tool(temp_ledger_path: Path):
    """Verify served tokens are split per tier and per tool, and find_symbol stays out of history and the session."""
    tracker = ContextBudgetTracker(ledger_path=temp_ledger_path, session_budget=5000)
    records = record_n(tracker, 4)  # tiers 1, 2, 3, 1
    session_before = tracker.session_served_tokens

    lookup_tokens = tracker.record_tool_output("find_symbol", '{"query": "f", "matches": []}')

    by_tier = {tier: sum(r.served_tokens for r in records if r.served_tier == tier) for tier in (1, 2, 3)}
    slice_tokens = sum(r.served_tokens for r in records)
    summary = tracker.get_summary()
    assert summary["served_tokens_by_tier"] == by_tier
    assert summary["served_tokens_by_tool"] == {
        "get_adaptive_context": {"requests": 4, "served_tokens": slice_tokens},
        "find_symbol": {"requests": 1, "served_tokens": lookup_tokens},
    }
    assert lookup_tokens > 0
    assert summary["total_requests"] == 4
    assert summary["cumulative_served_tokens"] == slice_tokens
    assert len(tracker.history) == 4
    assert tracker.session_served_tokens == session_before

    restarted = ContextBudgetTracker(ledger_path=temp_ledger_path)
    for key in ("served_tokens_by_tier", "served_tokens_by_tool", "whole_file_tokens", "upper_bound_savings_pct"):
        assert restarted.get_summary()[key] == summary[key]


def test_summary_and_ledger_use_upper_bound_names(tracker: ContextBudgetTracker, temp_ledger_path: Path):
    """Verify the old savings names are gone from the summary, the history and the saved totals."""
    record_n(tracker, 1)
    data = json.loads(temp_ledger_path.read_text(encoding="utf-8"))

    for old in ("cumulative_baseline_tokens", "cumulative_tokens_saved", "overall_savings_percentage"):
        assert old not in data["summary"]
    for old in ("baseline_tokens", "tokens_saved", "savings_percentage"):
        assert old not in data["history"][0]
    assert "baseline_tokens" not in data["totals"]
    assert data["summary"]["whole_file_tokens"] == data["history"][0]["whole_file_tokens"] > 0
    assert data["history"][0]["upper_bound_savings_pct"] == data["summary"]["upper_bound_savings_pct"]


def old_format_record(i: int, tier: int, baseline: int, served: int) -> dict:
    """A history record as written before Phase 23."""
    return {
        "query_id": f"q_{i}",
        "timestamp": "2026-10-01T10:00:00+00:00",
        "file_path": f"src/mod_{i}.py",
        "symbol_name": "f",
        "task_intent": "Inspect signature",
        "served_tier": tier,
        "baseline_tokens": baseline,
        "served_tokens": served,
        "tokens_saved": baseline - served,
        "savings_percentage": round((baseline - served) / baseline * 100.0, 2),
        "requested_min_tier": None,
        "model_tier": tier,
        "budget_limited": False,
    }


def test_old_format_ledger_loads(temp_ledger_path: Path):
    """Verify a pre-Phase 23 ledger (baseline_tokens / savings_percentage, no tier or tool totals) loads and is rewritten in the new format."""
    history = [old_format_record(1, 1, 400, 40), old_format_record(2, 3, 300, 200)]
    temp_ledger_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "summary": {},
                "totals": {
                    # One older request (tier 2, 100 served of 500) was already trimmed from history
                    "requests": 3,
                    "baseline_tokens": 1200,
                    "served_tokens": 340,
                    "tier_counts": {"1": 1, "2": 1, "3": 1},
                    "escalations": 0,
                },
                "history": history,
                "symbol_cache": {},
            }
        ),
        encoding="utf-8",
    )

    loaded = ContextBudgetTracker(ledger_path=temp_ledger_path)

    assert loaded.whole_file_tokens == 1200
    assert loaded.cumulative_served_tokens == 340
    assert loaded.upper_bound_savings_pct == round(860 / 1200 * 100.0, 2)
    assert [r.whole_file_tokens for r in loaded.history] == [400, 300]
    assert [r.upper_bound_savings_pct for r in loaded.history] == [90.0, 33.33]
    assert all(r.line_ranges == [] for r in loaded.history)
    # Per-tier tokens were never stored: recounted from the history that is left
    assert loaded.served_tokens_by_tier == {1: 40, 2: 0, 3: 200}
    assert loaded.served_tokens_by_tool == {
        "get_adaptive_context": {"requests": 3, "served_tokens": 340},
        "find_symbol": {"requests": 0, "served_tokens": 0},
    }

    loaded.save_ledger()
    data = json.loads(temp_ledger_path.read_text(encoding="utf-8"))
    assert data["totals"]["whole_file_tokens"] == 1200
    assert "baseline_tokens" not in data["history"][0]


def test_line_ranges_survive_restart(temp_ledger_path: Path):
    """Verify the served line ranges are stored per request and restored after a restart."""
    ranges = [
        ServedRange(file="/ws/pkg/mod.py", symbol="imports", start=1, end=3),
        ServedRange(file="/ws/pkg/mod.py", symbol="Engine.run", start=40, end=55),
    ]
    tracker = ContextBudgetTracker(ledger_path=temp_ledger_path)
    tracker.record_retrieval(
        file_path="/ws/pkg/mod.py",
        task_intent="Fix run",
        served_tier=3,
        whole_file_content="x = 1\n" * 100,
        served_content="x = 1\n" * 10,
        line_ranges=ranges,
    )

    data = json.loads(temp_ledger_path.read_text(encoding="utf-8"))
    assert data["history"][0]["line_ranges"][1] == {
        "file": "/ws/pkg/mod.py", "symbol": "Engine.run", "start": 40, "end": 55
    }
    assert ContextBudgetTracker(ledger_path=temp_ledger_path).history[0].line_ranges == ranges
