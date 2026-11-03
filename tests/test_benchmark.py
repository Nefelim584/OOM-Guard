"""Automated verification tests for the 30-run Benchmark Suite (Block 9)."""

import ast
import json
from pathlib import Path
import pytest
from compare_context import BenchmarkRunner, BenchmarkSuiteSummary


@pytest.fixture
def tasks_path() -> Path:
    return Path(__file__).resolve().parent.parent / "benchmark" / "tasks.json"


@pytest.fixture
def benchmark_tasks(tasks_path: Path) -> list[dict]:
    with open(tasks_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. Benchmark Task Suite Integrity (Step 9.1)
# ---------------------------------------------------------------------------


def test_benchmark_tasks_structure(benchmark_tasks: list[dict]):
    """Verify benchmark/tasks.json contains 10 tasks across small, medium, large, and oversized categories."""
    assert len(benchmark_tasks) == 10

    categories = {t["category"] for t in benchmark_tasks}
    assert "small" in categories
    assert "medium" in categories
    assert "large" in categories
    assert "oversized" in categories

    seen_ids = set()
    for task in benchmark_tasks:
        assert "id" in task
        assert "title" in task
        assert "task_intent" in task
        assert "code_content" in task
        assert task["id"] not in seen_ids
        seen_ids.add(task["id"])

        # Code content must be valid Python
        parsed = ast.parse(task["code_content"])
        assert isinstance(parsed, ast.Module)


# ---------------------------------------------------------------------------
# 2. Benchmark Execution, Statistical Metrics & Overflow Trace (Steps 9.2 - 9.4)
# ---------------------------------------------------------------------------


def test_benchmark_runner_full_suite(tmp_path: Path):
    """Execute full 10 tasks x 3 runs benchmark and verify comparative statistics."""
    runner = BenchmarkRunner(
        output_dir=tmp_path,
        runs_per_task=3,
        use_mock_llm=True,
    )

    summary: BenchmarkSuiteSummary = runner.run_benchmark()

    # 1. Total runs verification (30 Baseline + 30 Gated = 60 total runs)
    assert summary.total_tasks == 10
    assert summary.runs_per_task == 3
    assert summary.total_runs_per_mode == 30
    assert len(summary.task_metrics) == 10

    # 2. Token savings and variance metrics
    assert summary.overall_baseline_mean_tokens > summary.overall_gated_mean_tokens
    assert summary.overall_token_savings_pct > 75.0
    assert summary.overall_gated_mean_tokens < 500.0

    # 3. Task completion success rate verification
    # Baseline must fail on task_10 (oversized) due to 8k context window overflow -> 90%
    assert summary.baseline_overall_success_rate == 90.0
    # Gated must have 100% success rate across all tasks
    assert summary.gated_overall_success_rate == 100.0

    # 4. Overflow failure trace capture
    trace = summary.overflow_failure_trace
    assert trace is not None
    assert trace["task_id"] == "task_10"
    assert trace["prompt_tokens"] > 8192
    assert trace["gated_tokens"] < 2000
    assert trace["savings_vs_overflow"] > 95.0
    assert "ContextWindowOverflowError" in trace["error"]

    # 5. File artifacts saving
    runner.save_results(summary)
    assert (tmp_path / "results.json").exists()
    assert (tmp_path / "REPORT.md").exists()
    assert (tmp_path / "trace_overflow_analysis.md").exists()

    with open(tmp_path / "results.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["total_tasks"] == 10
    assert data["overall_token_savings_pct"] > 75.0
