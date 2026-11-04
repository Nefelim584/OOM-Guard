"""Tests for the Decision Model dataset, train/holdout split, weight fit, and report (Block 4)."""

import ast
import json
import math

import pytest
from acc_mcp.decision import DecisionModel
from acc_mcp.evaluate_decision import (
    HOLDOUT_FRACTION,
    TIERS,
    CalibrationResult,
    DecisionEvaluator,
    EvaluationMetrics,
    stratified_split,
)


@pytest.fixture(scope="module")
def evaluator() -> DecisionEvaluator:
    return DecisionEvaluator()


@pytest.fixture(scope="module")
def dataset(evaluator: DecisionEvaluator) -> list[dict]:
    return evaluator.load_dataset()


@pytest.fixture(scope="module")
def raw_dataset(evaluator: DecisionEvaluator) -> list[dict]:
    return json.loads(evaluator.dataset_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def calibration(evaluator: DecisionEvaluator, dataset: list[dict]) -> CalibrationResult:
    return evaluator.calibrate(dataset)


# ---------------------------------------------------------------------------
# 1. Dataset Integrity & Schema Validation (Step 21.1)
# ---------------------------------------------------------------------------


def test_dataset_has_about_100_tasks_across_all_tiers(dataset: list[dict]):
    """Verify the dataset was expanded to ~100 tasks with every tier well represented."""
    assert len(dataset) >= 90
    for tier in TIERS:
        assert sum(1 for t in dataset if t["ground_truth_tier"] == tier) >= 25


def test_dataset_task_schema_and_code_syntax(dataset: list[dict]):
    """Verify all task entries have valid schema and syntactically valid Python code snippets."""
    required_keys = {
        "id",
        "task_intent",
        "target_symbol",
        "category",
        "requires_full_body",
        "ground_truth_tier",
        "description",
        "code_snippet",
    }

    seen_ids = set()
    for task in dataset:
        for key in required_keys:
            assert key in task, f"Task {task.get('id')} missing key '{key}'"

        task_id = task["id"]
        assert task_id not in seen_ids, f"Duplicate task ID: {task_id}"
        seen_ids.add(task_id)

        assert task["ground_truth_tier"] in set(TIERS)
        # requires_full_body is the Tier 3 flag
        assert task["requires_full_body"] == (task["ground_truth_tier"] == 3), task_id

        snippet = task["code_snippet"]
        assert isinstance(snippet, str) and len(snippet) > 0
        assert isinstance(ast.parse(snippet), ast.Module)


def test_snippet_refs_point_to_tasks_with_code(raw_dataset: list[dict], dataset: list[dict]):
    """Verify every snippet_ref names a task that has its own code, and loading copies that code."""
    by_id = {t["id"]: t for t in raw_dataset}
    refs = [t for t in raw_dataset if "snippet_ref" in t]
    assert refs, "expanded dataset should reuse snippets"

    resolved = {t["id"]: t for t in dataset}
    for task in refs:
        assert "code_snippet" not in task, task["id"]
        source = by_id[task["snippet_ref"]]
        assert "code_snippet" in source, f"{task['id']} refers to a task without code"
        assert resolved[task["id"]]["code_snippet"] == source["code_snippet"]


def test_bad_snippet_ref_is_rejected(tmp_path):
    """Verify a task with neither code nor a valid snippet_ref fails loudly."""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"id": "t1", "task_intent": "x", "snippet_ref": "missing"}]))
    with pytest.raises(ValueError, match="snippet_ref"):
        DecisionEvaluator(dataset_path=path).load_dataset()


def test_dataset_includes_real_ledger_intents(dataset: list[dict]):
    """Verify real task_intent strings recorded by the server are part of the dataset."""
    assert any(t.get("source") == "ledger" for t in dataset)


# ---------------------------------------------------------------------------
# 2. Stratified Train / Holdout Split (Step 21.2)
# ---------------------------------------------------------------------------


def test_split_is_disjoint_and_complete(dataset: list[dict]):
    """Verify every task lands on exactly one side of the split."""
    train, holdout = stratified_split(dataset)
    train_ids = {t["id"] for t in train}
    holdout_ids = {t["id"] for t in holdout}

    assert not train_ids & holdout_ids
    assert train_ids | holdout_ids == {t["id"] for t in dataset}


def test_split_is_stratified_by_tier(dataset: list[dict]):
    """Verify each tier sends ~30% of its tasks to holdout, so every tier is on both sides."""
    train, holdout = stratified_split(dataset)
    for tier in TIERS:
        total = sum(1 for t in dataset if t["ground_truth_tier"] == tier)
        in_holdout = sum(1 for t in holdout if t["ground_truth_tier"] == tier)
        in_train = sum(1 for t in train if t["ground_truth_tier"] == tier)
        assert in_holdout == round(total * HOLDOUT_FRACTION)
        assert in_holdout > 0 and in_train > 0


def test_split_is_deterministic_and_order_independent(dataset: list[dict]):
    """Verify the split depends only on ids and seed, not on the order of the file."""
    _, holdout = stratified_split(dataset)
    _, holdout_again = stratified_split(dataset)
    _, holdout_reversed = stratified_split(list(reversed(dataset)))

    ids = {t["id"] for t in holdout}
    assert ids == {t["id"] for t in holdout_again}
    assert ids == {t["id"] for t in holdout_reversed}


# ---------------------------------------------------------------------------
# 3. Fitted Weights (Step 21.3)
# ---------------------------------------------------------------------------


def test_default_weights_match_the_fit(calibration: CalibrationResult):
    """Verify the shipped DecisionModel defaults are the weights fitted on the train split.

    If the dataset, split, or fit changes, this fails until the constants in decision.py
    are updated from `python3 src/acc_mcp/evaluate_decision.py`.
    """
    for shipped, fitted in zip(DecisionModel().weights, calibration.weights):
        assert math.isclose(shipped, fitted, abs_tol=5e-4)


def test_feature_influence_is_reported_for_each_ast_feature(calibration: CalibrationResult):
    """Verify x2, x3, x4 each get a weight and a tier-flip count on train and holdout."""
    assert set(calibration.feature_influence) == {
        "x2_cyclomatic_complexity",
        "x3_in_degree",
        "x4_has_annotations",
    }
    for info in calibration.feature_influence.values():
        assert 0 <= info["train_flips"] <= calibration.train.total_samples
        assert 0 <= info["holdout_flips"] <= calibration.holdout.total_samples


# ---------------------------------------------------------------------------
# 4. Metrics on Train and Holdout (Step 21.4)
# ---------------------------------------------------------------------------


def _check_metrics_arithmetic(metrics: EvaluationMetrics, tasks: list[dict]) -> None:
    tcm = metrics.tier_confusion_matrix

    assert metrics.total_samples == len(tasks)
    assert sum(tcm[a][p] for a in TIERS for p in TIERS) == metrics.total_samples

    diagonal = sum(tcm[t][t] for t in TIERS)
    assert metrics.misses == sum(tcm[a][p] for a in TIERS for p in TIERS if p < a)
    assert metrics.false_alarms == sum(tcm[a][p] for a in TIERS for p in TIERS if p > a)
    assert diagonal + metrics.misses + metrics.false_alarms == metrics.total_samples
    assert metrics.accuracy == diagonal / metrics.total_samples

    can_miss = sum(1 for t in tasks if t["ground_truth_tier"] > 1)
    can_over_serve = sum(1 for t in tasks if t["ground_truth_tier"] < 3)
    assert metrics.miss_rate == metrics.misses / can_miss
    assert metrics.false_alarm_rate == metrics.false_alarms / can_over_serve

    for t in TIERS:
        assert metrics.per_tier[t]["support"] == sum(1 for task in tasks if task["ground_truth_tier"] == t)
        assert 0.0 <= metrics.per_tier[t]["precision"] <= 1.0
        assert 0.0 <= metrics.per_tier[t]["recall"] <= 1.0


@pytest.mark.parametrize("split", ["train", "holdout"])
def test_metrics_are_computed_on_the_right_split(calibration: CalibrationResult, dataset: list[dict], split: str):
    """Verify train and holdout metrics each cover exactly their own tasks and add up."""
    train, holdout = stratified_split(dataset)
    tasks = train if split == "train" else holdout
    metrics = getattr(calibration, split)

    assert [e["id"] for e in metrics.task_evaluations] == [t["id"] for t in tasks]
    _check_metrics_arithmetic(metrics, tasks)


def test_holdout_beats_majority_tier_baseline(calibration: CalibrationResult):
    """Verify the model beats always predicting the most common holdout tier.

    This is a floor, not a quality claim; the README reports the real holdout numbers.
    """
    holdout = calibration.holdout
    majority = max(holdout.per_tier[t]["support"] for t in TIERS) / holdout.total_samples
    assert holdout.accuracy > majority


def test_tier1_tier2_confusion_is_measured(evaluator: DecisionEvaluator, dataset: list[dict]):
    """Verify serving Tier 2 for a Tier 1 task counts as a false alarm and the reverse as a miss."""
    model = DecisionModel(w0=-2.0, w1=2.0, w2=0.2, w3=0.3, w4=1.0)
    task = next(t for t in dataset if t["id"] == "task_01")

    over = evaluator.evaluate([{**task, "task_intent": "Trace callers of the function"}], model)
    assert over.tier_confusion_matrix[1][2] == 1
    assert (over.false_alarms, over.false_alarm_rate, over.misses) == (1, 1.0, 0)
    assert over.task_evaluations[0]["classification"] == "False Alarm (Tier 1 sufficed)"

    under = evaluator.evaluate([{**task, "ground_truth_tier": 2}], model)
    assert under.tier_confusion_matrix[2][1] == 1
    assert (under.misses, under.miss_rate, under.false_alarms) == (1, 1.0, 0)
    assert under.task_evaluations[0]["classification"] == "Miss (needed Tier 2)"


# ---------------------------------------------------------------------------
# 5. Markdown Report
# ---------------------------------------------------------------------------


def test_markdown_report_keeps_train_and_holdout_apart(evaluator: DecisionEvaluator, calibration: CalibrationResult):
    """Verify the report shows fitted weights, feature influence, and both splits separately."""
    report = evaluator.generate_markdown_report(calibration)

    assert "# Decision Model Calibration Report (Block 4)" in report
    assert "2x2" not in report
    assert "## Holdout Split (unseen during fitting)" in report
    assert "## Train Split (used for fitting)" in report
    assert report.index("## Holdout Split") < report.index("## Train Split")
    assert "thresholds are still hand-picked" in report
    assert "## Do the AST Features Matter?" in report
    assert report.count("### 3x3 Tier Confusion Matrix") == 2

    for metrics in (calibration.train, calibration.holdout):
        assert f"**Tier Accuracy (1/2/3):** {metrics.accuracy * 100:.2f}%" in report
        assert f"**Miss Rate:** {metrics.miss_rate * 100:.2f}%" in report
        assert f"**False Alarm Rate:** {metrics.false_alarm_rate * 100:.2f}%" in report

    for name, weight in zip(("w0", "w1", "w2", "w3", "w4"), calibration.weights):
        assert f"| {name} | {weight:.4f} |" in report
