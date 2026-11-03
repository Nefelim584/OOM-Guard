"""Unit and automated verification tests for Decision Model Evaluation Benchmark (Block 4)."""

import ast
import json
from pathlib import Path
import pytest
from acc_mcp.decision import DecisionModel
from acc_mcp.evaluate_decision import DecisionEvaluator, EvaluationMetrics


@pytest.fixture
def evaluator() -> DecisionEvaluator:
    return DecisionEvaluator()


@pytest.fixture
def dataset(evaluator: DecisionEvaluator) -> list[dict]:
    return evaluator.load_dataset()


# ---------------------------------------------------------------------------
# 1. Dataset Integrity & Schema Validation (Step 5.1)
# ---------------------------------------------------------------------------


def test_dataset_file_exists_and_loads(evaluator: DecisionEvaluator):
    """Verify data/decision_dataset.json exists and loads as valid JSON array."""
    assert evaluator.dataset_path.exists()
    tasks = evaluator.load_dataset()
    assert isinstance(tasks, list)
    assert len(tasks) >= 20


def test_dataset_balanced_distribution(dataset: list[dict]):
    """Verify dataset contains at least 10 interface-only and 10 deep logic rewrite tasks."""
    interface_tasks = [t for t in dataset if not t.get("requires_full_body")]
    rewrite_tasks = [t for t in dataset if t.get("requires_full_body")]

    assert len(interface_tasks) >= 10
    assert len(rewrite_tasks) >= 10
    assert len(dataset) == len(interface_tasks) + len(rewrite_tasks)


def test_dataset_task_schema_and_code_syntax(dataset: list[dict]):
    """Verify all task entries have valid schema and syntactically valid Python code snippets."""
    required_keys = {
        "id",
        "task_intent",
        "category",
        "requires_full_body",
        "ground_truth_tier",
        "description",
        "code_snippet",
    }

    seen_ids = set()
    for task in dataset:
        # Check required fields
        for key in required_keys:
            assert key in task, f"Task {task.get('id')} missing key '{key}'"

        # Unique ID
        task_id = task["id"]
        assert task_id not in seen_ids, f"Duplicate task ID: {task_id}"
        seen_ids.add(task_id)

        # Valid tiers
        assert task["ground_truth_tier"] in {1, 2, 3}

        # Code snippet must be parseable Python
        snippet = task["code_snippet"]
        assert isinstance(snippet, str) and len(snippet) > 0
        parsed = ast.parse(snippet)
        assert isinstance(parsed, ast.Module)


# ---------------------------------------------------------------------------
# 2. Evaluation Runner & Metrics Arithmetic (Step 5.2)
# ---------------------------------------------------------------------------


def test_evaluation_metrics_arithmetic(evaluator: DecisionEvaluator, dataset: list[dict]):
    """Verify the 3x3 matrix sums to the dataset and every metric is read off it correctly."""
    metrics = evaluator.evaluate(dataset)
    tcm = metrics.tier_confusion_matrix
    tiers = (1, 2, 3)

    assert metrics.total_samples == len(dataset)
    assert sum(tcm[a][p] for a in tiers for p in tiers) == metrics.total_samples

    diagonal = sum(tcm[t][t] for t in tiers)
    assert metrics.misses == sum(tcm[a][p] for a in tiers for p in tiers if p < a)
    assert metrics.false_alarms == sum(tcm[a][p] for a in tiers for p in tiers if p > a)
    assert diagonal + metrics.misses + metrics.false_alarms == metrics.total_samples
    assert metrics.accuracy == diagonal / metrics.total_samples

    for t in tiers:
        assert metrics.per_tier[t]["support"] == sum(1 for task in dataset if task["ground_truth_tier"] == t)
        assert 0.0 <= metrics.per_tier[t]["precision"] <= 1.0
        assert 0.0 <= metrics.per_tier[t]["recall"] <= 1.0

    assert 0.0 <= metrics.miss_rate <= 1.0
    assert 0.0 <= metrics.false_alarm_rate <= 1.0


def test_tier1_tier2_confusion_is_measured(evaluator: DecisionEvaluator, dataset: list[dict]):
    """Verify serving Tier 2 for a Tier 1 task counts as a false alarm and the reverse as a miss.

    The old binary matrix lumped Tiers 1 and 2 together, so neither mistake was counted.
    """
    task = next(t for t in dataset if t["ground_truth_tier"] == 1)
    over = evaluator.evaluate([{**task, "task_intent": "Trace callers of the function"}])
    assert over.tier_confusion_matrix[1][2] == 1
    assert (over.false_alarms, over.false_alarm_rate, over.misses) == (1, 1.0, 0)
    assert over.task_evaluations[0]["classification"] == "False Alarm (Tier 1 sufficed)"

    under = evaluator.evaluate([{**task, "ground_truth_tier": 2}])
    assert under.tier_confusion_matrix[2][1] == 1
    assert (under.misses, under.miss_rate, under.false_alarms) == (1, 1.0, 0)
    assert under.task_evaluations[0]["classification"] == "Miss (needed Tier 2)"


# ---------------------------------------------------------------------------
# 3. Benchmark Verification: Zero Misses & Minimal False Alarms (Step 5.3)
# ---------------------------------------------------------------------------


def test_benchmark_zero_critical_misses(evaluator: DecisionEvaluator):
    """Verify no task is served a lower tier than its ground truth.

    A miss leaves the agent without the context it needs (e.g. signatures only for a
    deep logic rewrite). Zero misses is a mandatory course safety requirement.
    """
    metrics = evaluator.evaluate()

    assert metrics.misses == 0, f"Misses detected: {metrics.misses}"
    assert metrics.miss_rate == 0.0
    assert metrics.per_tier[3]["recall"] == 1.0


def test_benchmark_minimal_false_alarms(evaluator: DecisionEvaluator):
    """Verify no task is served a higher tier than its ground truth (no wasted tokens)."""
    metrics = evaluator.evaluate()

    assert metrics.false_alarms == 0, f"False alarms detected: {metrics.false_alarms}"
    assert metrics.false_alarm_rate == 0.0


def test_benchmark_high_accuracy(evaluator: DecisionEvaluator):
    """Verify 3-tier accuracy >= 95% on the (hand-calibrated, not held-out) dataset."""
    metrics = evaluator.evaluate()

    assert metrics.accuracy >= 0.95
    assert metrics.per_tier[1]["support"] >= 5
    assert metrics.per_tier[2]["support"] >= 5
    assert metrics.per_tier[3]["support"] >= 10


# ---------------------------------------------------------------------------
# 4. Markdown Report Formatting Tests
# ---------------------------------------------------------------------------


def test_markdown_report_generation(evaluator: DecisionEvaluator):
    """Verify generation of the human-readable Markdown evaluation report."""
    metrics = evaluator.evaluate()
    report = evaluator.generate_markdown_report(metrics)

    assert "# Decision Model Benchmark Evaluation Report (Block 4)" in report
    assert "2x2" not in report
    assert "3x3 Tier Confusion Matrix" in report
    assert "picked by hand on these same tasks" in report
    assert "False Alarm Rate" in report
    assert "Miss Rate" in report
    tcm = metrics.tier_confusion_matrix
    for a in (1, 2, 3):
        assert f"| **Actual Tier {a}** | {tcm[a][1]} | {tcm[a][2]} | {tcm[a][3]} |" in report
