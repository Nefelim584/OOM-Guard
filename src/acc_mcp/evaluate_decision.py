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


@dataclass
class EvaluationMetrics:
    """Evaluation metrics for the Decision Model."""

    total_samples: int
    tp: int
    tn: int
    fp: int
    fn: int
    accuracy: float
    precision: float
    recall: float
    false_alarm_rate: float
    miss_rate: float
    tier_accuracy: float
    confusion_matrix_2x2: dict[str, int]
    tier_confusion_matrix: dict[int, dict[int, int]]
    task_evaluations: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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

        tp = 0
        tn = 0
        fp = 0
        fn = 0
        tier_matches = 0

        # Tier confusion matrix: tier_cm[actual_tier][predicted_tier]
        tier_cm: dict[int, dict[int, int]] = {
            1: {1: 0, 2: 0, 3: 0},
            2: {1: 0, 2: 0, 3: 0},
            3: {1: 0, 2: 0, 3: 0},
        }

        evaluations: list[dict[str, Any]] = []

        for task in tasks:
            task_id = task.get("id", "unknown")
            intent = task["task_intent"]
            target_symbol = task.get("target_symbol")
            code_snippet = task.get("code_snippet", "")
            requires_full_body = bool(task["requires_full_body"])
            ground_truth_tier = int(task.get("ground_truth_tier", 3 if requires_full_body else 1))

            # Parse snippet to extract AST metrics
            parser = ASTParser(code_snippet, file_path=f"synthetic_{task_id}.py")
            decision: DecisionResult = self.model.select_tier(
                task_intent=intent,
                parser=parser,
                symbol_name=target_symbol,
            )

            predicted_full_body = decision.tier == 3

            # 2x2 Binary Classification (Full Body vs Interface)
            if requires_full_body and predicted_full_body:
                tp += 1
                classification = "TP"
            elif not requires_full_body and not predicted_full_body:
                tn += 1
                classification = "TN"
            elif not requires_full_body and predicted_full_body:
                fp += 1
                classification = "FP (False Alarm)"
            else:
                fn += 1
                classification = "FN (Critical Miss)"

            # 3-Tier Multi-class Match
            if decision.tier == ground_truth_tier:
                tier_matches += 1

            if ground_truth_tier in tier_cm and decision.tier in tier_cm[ground_truth_tier]:
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
                    "classification": classification,
                    "correct_tier": decision.tier == ground_truth_tier,
                }
            )

        total = len(tasks)
        accuracy = (tp + tn) / total if total > 0 else 0.0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        false_alarm_rate = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        miss_rate = fn / (fn + tp) if (fn + tp) > 0 else 0.0
        tier_accuracy = tier_matches / total if total > 0 else 0.0

        return EvaluationMetrics(
            total_samples=total,
            tp=tp,
            tn=tn,
            fp=fp,
            fn=fn,
            accuracy=accuracy,
            precision=precision,
            recall=recall,
            false_alarm_rate=false_alarm_rate,
            miss_rate=miss_rate,
            tier_accuracy=tier_accuracy,
            confusion_matrix_2x2={"TP": tp, "TN": tn, "FP": fp, "FN": fn},
            tier_confusion_matrix=tier_cm,
            task_evaluations=evaluations,
        )

    def generate_markdown_report(self, metrics: EvaluationMetrics) -> str:
        """Format metrics into a comprehensive Markdown evaluation report."""
        cm = metrics.confusion_matrix_2x2
        tcm = metrics.tier_confusion_matrix

        lines = [
            "# Decision Model Benchmark Evaluation Report (Block 4)",
            "",
            "## Summary Performance Metrics",
            f"- **Total Benchmark Tasks:** {metrics.total_samples}",
            f"- **Accuracy (Full vs Interface):** {metrics.accuracy * 100:.2f}%",
            f"- **Tier Selection Accuracy (1/2/3):** {metrics.tier_accuracy * 100:.2f}%",
            f"- **Precision:** {metrics.precision * 100:.2f}%",
            f"- **Recall:** {metrics.recall * 100:.2f}%",
            f"- **False Alarm Rate (Type I Error):** {metrics.false_alarm_rate * 100:.2f}% (Tokens wasted when signatures sufficed)",
            f"- **Miss Rate (Type II Error):** {metrics.miss_rate * 100:.2f}% (Critical misses where agent lacked required logic)",
            "",
            "## 2x2 Binary Confusion Matrix (Tier 3 vs Tiers 1 & 2)",
            "| | Predicted Interface (Tier 1/2) | Predicted Full Body (Tier 3) |",
            "| :--- | :---: | :---: |",
            f"| **Actual Interface** | **TN = {cm['TN']}** | **FP = {cm['FP']}** |",
            f"| **Actual Full Body** | **FN = {cm['FN']}** | **TP = {cm['TP']}** |",
            "",
            "## 3x3 Tier Selection Matrix",
            "| Actual \\ Predicted | Predicted Tier 1 | Predicted Tier 2 | Predicted Tier 3 |",
            "| :--- | :---: | :---: | :---: |",
            f"| **Actual Tier 1** | {tcm[1][1]} | {tcm[1][2]} | {tcm[1][3]} |",
            f"| **Actual Tier 2** | {tcm[2][1]} | {tcm[2][2]} | {tcm[2][3]} |",
            f"| **Actual Tier 3** | {tcm[3][1]} | {tcm[3][2]} | {tcm[3][3]} |",
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
