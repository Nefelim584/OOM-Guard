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
    """Verify confusion matrix counts and rate formulas obey mathematical identities."""
    metrics = evaluator.evaluate(dataset)

    # Total sample balance
    assert metrics.total_samples == len(dataset)
    assert metrics.total_samples == metrics.tp + metrics.tn + metrics.fp + metrics.fn

    # Confusion matrix structure
    assert metrics.confusion_matrix_2x2 == {
        "TP": metrics.tp,
        "TN": metrics.tn,
        "FP": metrics.fp,
        "FN": metrics.fn,
    }

    # Rates bounded in [0.0, 1.0]
    assert 0.0 <= metrics.accuracy <= 1.0
    assert 0.0 <= metrics.precision <= 1.0
    assert 0.0 <= metrics.recall <= 1.0
    assert 0.0 <= metrics.false_alarm_rate <= 1.0
    assert 0.0 <= metrics.miss_rate <= 1.0
    assert 0.0 <= metrics.tier_accuracy <= 1.0

    # 3x3 Tier Matrix sums match total
    tcm = metrics.tier_confusion_matrix
    tier_total = sum(tcm[r][c] for r in (1, 2, 3) for c in (1, 2, 3))
    assert tier_total == metrics.total_samples


# ---------------------------------------------------------------------------
# 3. Benchmark Verification: Zero Misses & Minimal False Alarms (Step 5.3)
# ---------------------------------------------------------------------------


def test_benchmark_zero_critical_misses(evaluator: DecisionEvaluator):
    """Verify zero critical misses (miss_rate == 0.0, FN == 0).

    A critical miss occurs when an agent requires the full implementation body
    to perform a deep logic rewrite, but the model incorrectly serves only signatures.
    Zero critical misses is a mandatory course safety requirement.
    """
    metrics = evaluator.evaluate()

    assert metrics.fn == 0, f"Critical misses detected: FN = {metrics.fn}"
    assert metrics.miss_rate == 0.0
    assert metrics.recall == 1.0


def test_benchmark_minimal_false_alarms(evaluator: DecisionEvaluator):
    """Verify minimal false alarm rate (FP <= 1, false_alarm_rate <= 0.10).

    A false alarm wastes tokens by allocating full body context when signatures sufficed.
    """
    metrics = evaluator.evaluate()

    assert metrics.fp == 0, f"False alarms detected: FP = {metrics.fp}"
    assert metrics.false_alarm_rate <= 0.10


def test_benchmark_high_accuracy(evaluator: DecisionEvaluator):
    """Verify overall binary accuracy >= 95% and tier accuracy >= 90%."""
    metrics = evaluator.evaluate()

    assert metrics.accuracy >= 0.95
    assert metrics.tier_accuracy >= 0.90
    assert metrics.tp >= 10
    assert metrics.tn >= 10


# ---------------------------------------------------------------------------
# 4. Markdown Report Formatting Tests
# ---------------------------------------------------------------------------


def test_markdown_report_generation(evaluator: DecisionEvaluator):
    """Verify generation of the human-readable Markdown evaluation report."""
    metrics = evaluator.evaluate()
    report = evaluator.generate_markdown_report(metrics)

    assert "# Decision Model Benchmark Evaluation Report (Block 4)" in report
    assert "2x2 Binary Confusion Matrix" in report
    assert "3x3 Tier Selection Matrix" in report
    assert "False Alarm Rate" in report
    assert "Miss Rate" in report
    assert "TN = 10" in report
    assert "TP = 10" in report
