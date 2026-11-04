"""Decision Model calibration and evaluation for ACC-MCP (Block 4 Evaluation).

The weights are fitted on a stratified train split of `data/decision_dataset.json` and
measured separately on the train split and on a holdout split the fit never sees.
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Ensure src is on sys.path for direct CLI execution
src_dir = Path(__file__).resolve().parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from acc_mcp.decision import ContextFeatures, DecisionModel, DecisionResult, Weights, fit_weights
from acc_mcp.parser import ASTParser


TIERS = (1, 2, 3)
HOLDOUT_FRACTION = 0.3
SPLIT_SEED = 21
# The weights used before Phase 21, set by hand while looking at the original 20 tasks.
HAND_PICKED_WEIGHTS: Weights = (-2.0, 2.0, 0.2, 0.3, 1.0)
# Index into DecisionModel.weights for each AST feature.
AST_FEATURE_WEIGHTS = {"x2_cyclomatic_complexity": 2, "x3_in_degree": 3, "x4_has_annotations": 4}


@dataclass
class EvaluationMetrics:
    """Evaluation metrics for the Decision Model, read off the 3x3 tier confusion matrix.

    A miss is a predicted tier below the ground-truth tier (the agent gets too little
    context); a false alarm is a predicted tier above it (tokens are wasted). The miss rate
    is taken over tasks that can be missed (true tier 2 or 3), the false alarm rate over
    tasks that can be over-served (true tier 1 or 2).
    """

    total_samples: int
    accuracy: float
    misses: int
    false_alarms: int
    miss_rate: float
    false_alarm_rate: float
    # tier -> {"precision": float, "recall": float, "support": int}
    per_tier: dict[int, dict[str, float]]
    # tier_confusion_matrix[actual_tier][predicted_tier]
    tier_confusion_matrix: dict[int, dict[int, int]]
    task_evaluations: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CalibrationResult:
    """Fitted weights plus separate train and holdout metrics."""

    weights: Weights
    train: EvaluationMetrics
    holdout: EvaluationMetrics
    # feature -> {"weight": float, "train_flips": int, "holdout_flips": int}
    feature_influence: dict[str, dict[str, float]]


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _verdict(actual: int, predicted: int) -> str:
    if predicted < actual:
        return f"Miss (needed Tier {actual})"
    if predicted > actual:
        return f"False Alarm (Tier {actual} sufficed)"
    return "Correct"


def _tier_of(task: dict[str, Any]) -> int:
    tier = int(task.get("ground_truth_tier", 3 if task.get("requires_full_body") else 1))
    if tier not in TIERS:
        raise ValueError(f"Task {task.get('id', 'unknown')} has invalid ground_truth_tier {tier}")
    return tier


def stratified_split(
    tasks: list[dict[str, Any]],
    holdout_fraction: float = HOLDOUT_FRACTION,
    seed: int = SPLIT_SEED,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split tasks into (train, holdout), taking `holdout_fraction` of each tier for holdout.

    Every tier is represented on both sides. The split depends only on the task ids and the
    seed, so it does not change when the dataset file is reordered. Both lists keep the
    dataset order.
    """
    rng = random.Random(seed)
    holdout_ids: set[str] = set()
    for tier in TIERS:
        ids = sorted(t["id"] for t in tasks if _tier_of(t) == tier)
        rng.shuffle(ids)
        holdout_ids.update(ids[: round(len(ids) * holdout_fraction)])
    train = [t for t in tasks if t["id"] not in holdout_ids]
    holdout = [t for t in tasks if t["id"] in holdout_ids]
    return train, holdout


class DecisionEvaluator:
    """Fits and evaluates the DecisionModel against the ground-truth dataset."""

    DEFAULT_DATASET_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "decision_dataset.json"

    def __init__(
        self,
        dataset_path: str | Path | None = None,
        model: DecisionModel | None = None,
    ) -> None:
        self.dataset_path = Path(dataset_path or self.DEFAULT_DATASET_PATH)
        self.model = model or DecisionModel()

    def load_dataset(self) -> list[dict[str, Any]]:
        """Load the dataset from JSON and resolve `snippet_ref` into `code_snippet`.

        Many tasks share code: a task with `snippet_ref` uses the `code_snippet` of the task
        with that id, so one snippet can carry intents of every tier.
        """
        if not self.dataset_path.exists():
            raise FileNotFoundError(f"Dataset not found at {self.dataset_path}")

        with open(self.dataset_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("Dataset JSON must contain an array of task objects.")

        snippets = {t["id"]: t["code_snippet"] for t in data if "code_snippet" in t}
        resolved = []
        for task in data:
            if "code_snippet" not in task:
                ref = task.get("snippet_ref")
                if ref not in snippets:
                    raise ValueError(f"Task {task.get('id')} has no code_snippet and bad snippet_ref {ref!r}")
                task = {**task, "code_snippet": snippets[ref]}
            resolved.append(task)
        return resolved

    def extract_samples(self, tasks: list[dict[str, Any]]) -> list[tuple[ContextFeatures, int]]:
        """Turn tasks into (features, ground-truth tier) pairs for fitting."""
        return [
            (
                self.model.extract_features(
                    task["task_intent"],
                    ASTParser(task.get("code_snippet", ""), file_path=f"synthetic_{task['id']}.py"),
                    task.get("target_symbol"),
                ),
                _tier_of(task),
            )
            for task in tasks
        ]

    def evaluate(
        self,
        dataset: list[dict[str, Any]] | None = None,
        model: DecisionModel | None = None,
    ) -> EvaluationMetrics:
        """Run `model` (default: the evaluator's model) over tasks and compute metrics."""
        tasks = dataset if dataset is not None else self.load_dataset()
        model = model or self.model

        tier_cm: dict[int, dict[int, int]] = {a: {p: 0 for p in TIERS} for a in TIERS}
        evaluations: list[dict[str, Any]] = []

        for task in tasks:
            task_id = task.get("id", "unknown")
            ground_truth_tier = _tier_of(task)

            # Parse snippet to extract AST metrics
            parser = ASTParser(task.get("code_snippet", ""), file_path=f"synthetic_{task_id}.py")
            decision: DecisionResult = model.select_tier(
                task_intent=task["task_intent"],
                parser=parser,
                symbol_name=task.get("target_symbol"),
            )

            tier_cm[ground_truth_tier][decision.tier] += 1

            evaluations.append(
                {
                    "id": task_id,
                    "task_intent": task["task_intent"],
                    "target_symbol": task.get("target_symbol"),
                    "ground_truth_tier": ground_truth_tier,
                    "predicted_tier": decision.tier,
                    "probability": round(decision.probability, 4),
                    "logit": round(decision.logit, 4),
                    "features": decision.features.to_dict(),
                    "classification": _verdict(ground_truth_tier, decision.tier),
                    "correct_tier": decision.tier == ground_truth_tier,
                }
            )

        total = len(tasks)
        correct = sum(tier_cm[t][t] for t in TIERS)
        misses = sum(tier_cm[a][p] for a in TIERS for p in TIERS if p < a)
        false_alarms = sum(tier_cm[a][p] for a in TIERS for p in TIERS if p > a)
        can_miss = sum(sum(tier_cm[a].values()) for a in TIERS if a > 1)
        can_over_serve = sum(sum(tier_cm[a].values()) for a in TIERS if a < 3)

        per_tier: dict[int, dict[str, float]] = {}
        for t in TIERS:
            support = sum(tier_cm[t].values())
            predicted = sum(tier_cm[a][t] for a in TIERS)
            per_tier[t] = {
                "precision": _ratio(tier_cm[t][t], predicted),
                "recall": _ratio(tier_cm[t][t], support),
                "support": support,
            }

        return EvaluationMetrics(
            total_samples=total,
            accuracy=_ratio(correct, total),
            misses=misses,
            false_alarms=false_alarms,
            miss_rate=_ratio(misses, can_miss),
            false_alarm_rate=_ratio(false_alarms, can_over_serve),
            per_tier=per_tier,
            tier_confusion_matrix=tier_cm,
            task_evaluations=evaluations,
        )

    def calibrate(self, dataset: list[dict[str, Any]] | None = None) -> CalibrationResult:
        """Fit the weights on the train split and measure train and holdout separately.

        The thresholds are kept from the evaluator's model; only w0..w4 are fitted.
        """
        tasks = dataset if dataset is not None else self.load_dataset()
        train, holdout = stratified_split(tasks)
        weights = fit_weights(self.extract_samples(train))
        fitted = DecisionModel(
            *weights,
            theta_low=self.model.theta_low,
            theta_high=self.model.theta_high,
            intent_classifier=self.model.intent_classifier,
        )
        train_metrics = self.evaluate(train, fitted)
        holdout_metrics = self.evaluate(holdout, fitted)
        return CalibrationResult(
            weights=weights,
            train=train_metrics,
            holdout=holdout_metrics,
            feature_influence=self.feature_influence(fitted, train_metrics, holdout_metrics, train, holdout),
        )

    def feature_influence(
        self,
        model: DecisionModel,
        train_metrics: EvaluationMetrics,
        holdout_metrics: EvaluationMetrics,
        train: list[dict[str, Any]],
        holdout: list[dict[str, Any]],
    ) -> dict[str, dict[str, float]]:
        """For each AST feature, count tasks whose tier changes when its weight is set to 0.

        Zero flips means the feature never decides a tier on this data, whatever its weight.
        """
        influence: dict[str, dict[str, float]] = {}
        for feature, index in AST_FEATURE_WEIGHTS.items():
            ablated_weights = list(model.weights)
            ablated_weights[index] = 0.0
            ablated = DecisionModel(
                *ablated_weights,
                theta_low=model.theta_low,
                theta_high=model.theta_high,
                intent_classifier=model.intent_classifier,
            )
            flips = {}
            for name, tasks, metrics in (("train", train, train_metrics), ("holdout", holdout, holdout_metrics)):
                ablated_eval = self.evaluate(tasks, ablated).task_evaluations
                flips[name] = sum(
                    a["predicted_tier"] != b["predicted_tier"]
                    for a, b in zip(metrics.task_evaluations, ablated_eval)
                )
            influence[feature] = {
                "weight": model.weights[index],
                "train_flips": flips["train"],
                "holdout_flips": flips["holdout"],
            }
        return influence

    @staticmethod
    def _metrics_section(metrics: EvaluationMetrics, title: str) -> list[str]:
        tcm = metrics.tier_confusion_matrix
        return [
            f"## {title}",
            f"- **Tasks:** {metrics.total_samples}",
            f"- **Tier Accuracy (1/2/3):** {metrics.accuracy * 100:.2f}%",
            f"- **Miss Rate:** {metrics.miss_rate * 100:.2f}% ({metrics.misses} tasks served a lower tier than needed)",
            f"- **False Alarm Rate:** {metrics.false_alarm_rate * 100:.2f}% ({metrics.false_alarms} tasks served a higher tier than needed)",
            "",
            "### 3x3 Tier Confusion Matrix",
            "| Actual \\ Predicted | Predicted Tier 1 | Predicted Tier 2 | Predicted Tier 3 |",
            "| :--- | :---: | :---: | :---: |",
            *(f"| **Actual Tier {a}** | {tcm[a][1]} | {tcm[a][2]} | {tcm[a][3]} |" for a in TIERS),
            "",
            "### Per-Tier Precision and Recall",
            "| Tier | Precision | Recall | Support |",
            "| :--- | :---: | :---: | :---: |",
            *(
                f"| Tier {t} | {m['precision'] * 100:.2f}% | {m['recall'] * 100:.2f}% | {m['support']} |"
                for t, m in metrics.per_tier.items()
            ),
            "",
        ]

    def generate_markdown_report(self, result: CalibrationResult) -> str:
        """Format a calibration result into a Markdown report (train and holdout kept apart)."""
        names = ("w0", "w1", "w2", "w3", "w4")
        lines = [
            "# Decision Model Calibration Report (Block 4)",
            "",
            f"> Weights fitted by gradient descent on the train split ({result.train.total_samples} tasks).",
            f"> The holdout split ({result.holdout.total_samples} tasks) was never used for fitting;",
            "> its numbers are the ones to quote. The thresholds are still hand-picked.",
            "",
            "## Weights",
            "| Weight | Fitted | Hand-picked (before Phase 21) |",
            "| :--- | :---: | :---: |",
            *(
                f"| {n} | {fitted:.4f} | {old:.1f} |"
                for n, fitted, old in zip(names, result.weights, HAND_PICKED_WEIGHTS)
            ),
            "",
            "## Do the AST Features Matter?",
            "Tasks whose tier changes when the feature's weight is set to 0.",
            "",
            "| Feature | Fitted weight | Train flips | Holdout flips |",
            "| :--- | :---: | :---: | :---: |",
            *(
                f"| {feature} | {info['weight']:.4f} | {info['train_flips']} | {info['holdout_flips']} |"
                for feature, info in result.feature_influence.items()
            ),
            "",
            *self._metrics_section(result.holdout, "Holdout Split (unseen during fitting)"),
            *self._metrics_section(result.train, "Train Split (used for fitting)"),
            "## Holdout Errors",
            "| Task ID | Intent | True Tier | Pred Tier | P(Full) | Verdict |",
            "| :--- | :--- | :---: | :---: | :---: | :---: |",
        ]
        for item in result.holdout.task_evaluations:
            if not item["correct_tier"]:
                lines.append(
                    f"| `{item['id']}` | {item['task_intent'][:60]} | {item['ground_truth_tier']} | "
                    f"{item['predicted_tier']} | {item['probability']:.3f} | {item['classification']} |"
                )
        return "\n".join(lines)


def main() -> None:
    """Fit on the train split and print the train/holdout report."""
    evaluator = DecisionEvaluator()
    print(evaluator.generate_markdown_report(evaluator.calibrate()))


if __name__ == "__main__":
    main()
