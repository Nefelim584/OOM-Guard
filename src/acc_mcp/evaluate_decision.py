"""Decision Model Evaluation and Benchmark Runner for ACC-MCP (Block 4 Evaluation)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Ensure src is on sys.path for direct CLI execution
src_dir = Path(__file__).resolve().parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from acc_mcp.decision import DecisionModel, DecisionResult
from acc_mcp.parser import ASTParser


TIERS = (1, 2, 3)


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


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _verdict(actual: int, predicted: int) -> str:
    if predicted < actual:
        return f"Miss (needed Tier {actual})"
    if predicted > actual:
        return f"False Alarm (Tier {actual} sufficed)"
    return "Correct"


class DecisionEvaluator:
    """Evaluates DecisionModel against ground-truth benchmark dataset."""

    DEFAULT_DATASET_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "decision_dataset.json"

    def __init__(
        self,
        dataset_path: str | Path | None = None,
        model: DecisionModel | None = None,
    ) -> None:
        self.dataset_path = Path(dataset_path or self.DEFAULT_DATASET_PATH)
        self.model = model or DecisionModel()

    def load_dataset(self) -> list[dict[str, Any]]:
        """Load benchmark dataset from JSON."""
        if not self.dataset_path.exists():
            raise FileNotFoundError(f"Dataset not found at {self.dataset_path}")

        with open(self.dataset_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("Dataset JSON must contain an array of task objects.")

        return data

    def evaluate(self, dataset: list[dict[str, Any]] | None = None) -> EvaluationMetrics:
        """Run evaluation over dataset tasks and compute performance metrics."""
        tasks = dataset if dataset is not None else self.load_dataset()

        tier_cm: dict[int, dict[int, int]] = {a: {p: 0 for p in TIERS} for a in TIERS}
        evaluations: list[dict[str, Any]] = []

        for task in tasks:
            task_id = task.get("id", "unknown")
            intent = task["task_intent"]
            target_symbol = task.get("target_symbol")
            code_snippet = task.get("code_snippet", "")
            ground_truth_tier = int(
                task.get("ground_truth_tier", 3 if task.get("requires_full_body") else 1)
            )
            if ground_truth_tier not in TIERS:
                raise ValueError(f"Task {task_id} has invalid ground_truth_tier {ground_truth_tier}")

            # Parse snippet to extract AST metrics
            parser = ASTParser(code_snippet, file_path=f"synthetic_{task_id}.py")
            decision: DecisionResult = self.model.select_tier(
                task_intent=intent,
                parser=parser,
                symbol_name=target_symbol,
            )

            tier_cm[ground_truth_tier][decision.tier] += 1

            evaluations.append(
                {
                    "id": task_id,
                    "task_intent": intent,
                    "target_symbol": target_symbol,
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

    def generate_markdown_report(self, metrics: EvaluationMetrics) -> str:
        """Format metrics into a comprehensive Markdown evaluation report."""
        tcm = metrics.tier_confusion_matrix

        lines = [
            "# Decision Model Benchmark Evaluation Report (Block 4)",
            "",
            "> The weights and thresholds were picked by hand on these same tasks, so these",
            "> numbers are not a measurement on unseen data.",
            "",
            "## Summary Performance Metrics",
            f"- **Total Benchmark Tasks:** {metrics.total_samples}",
            f"- **Tier Accuracy (1/2/3):** {metrics.accuracy * 100:.2f}%",
            f"- **Miss Rate:** {metrics.miss_rate * 100:.2f}% ({metrics.misses} tasks served a lower tier than needed)",
            f"- **False Alarm Rate:** {metrics.false_alarm_rate * 100:.2f}% ({metrics.false_alarms} tasks served a higher tier than needed)",
            "",
            "## 3x3 Tier Confusion Matrix",
            "| Actual \\ Predicted | Predicted Tier 1 | Predicted Tier 2 | Predicted Tier 3 |",
            "| :--- | :---: | :---: | :---: |",
            *(
                f"| **Actual Tier {a}** | {tcm[a][1]} | {tcm[a][2]} | {tcm[a][3]} |"
                for a in TIERS
            ),
            "",
            "Cells below the diagonal are misses, cells above it are false alarms.",
            "",
            "## Per-Tier Precision and Recall",
            "| Tier | Precision | Recall | Support |",
            "| :--- | :---: | :---: | :---: |",
            *(
                f"| Tier {t} | {m['precision'] * 100:.2f}% | {m['recall'] * 100:.2f}% | {m['support']} |"
                for t, m in metrics.per_tier.items()
            ),
            "",
            "## Benchmark Task Breakdown",
            "| Task ID | Intent | True Tier | Pred Tier | P(Full) | Verdict |",
            "| :--- | :--- | :---: | :---: | :---: | :---: |",
        ]

        for item in metrics.task_evaluations:
            verdict = "PASS" if item["correct_tier"] else f"FAIL ({item['classification']})"
            lines.append(
                f"| `{item['id']}` | {item['task_intent'][:40]}... | {item['ground_truth_tier']} | "
                f"{item['predicted_tier']} | {item['probability']:.3f} | {verdict} |"
            )

        return "\n".join(lines)


def main() -> None:
    """Run evaluation and print report to console."""
    evaluator = DecisionEvaluator()
    metrics = evaluator.evaluate()
    report = evaluator.generate_markdown_report(metrics)
    print(report)


if __name__ == "__main__":
    main()
