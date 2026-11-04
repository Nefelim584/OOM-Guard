"""Learned intent classifier for ACC-MCP (Block 7).

A multinomial Naive Bayes model that predicts the context tier (1/2/3) from the words of
`task_intent` alone. It targets the main error source found in Block 4: most decision errors
come from the keyword rule that turns the intent into x_1, not from the logistic weights.

It is trained on the same stratified train split as the Block 4 weights (same seed) and
compared on the same 30 holdout tasks with two baselines: always the most common train tier,
and the current keyword rule (`TaskIntentClassifier` depth 0/1/2 read as Tier 1/2/3).
It is evaluated offline only and is not used by the server.

Run `python3 src/acc_mcp/intent_model.py` to train it and print the report.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Ensure src is on sys.path for direct CLI execution
src_dir = Path(__file__).resolve().parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from acc_mcp.decision import TaskIntentClassifier
from acc_mcp.evaluate_decision import (
    SPLIT_SEED,
    TIERS,
    DecisionEvaluator,
    EvaluationMetrics,
    _tier_of,
    _verdict,
    metrics_from_evaluations,
    stratified_split,
)

TOKEN = re.compile(r"[a-z0-9]+")
MODEL_NAMES = {
    "naive_bayes": "Naive Bayes (words of task_intent)",
    "keyword_rule": "Keyword rule (TaskIntentClassifier depth + 1)",
    "majority": "Most common train tier",
}


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens; `snake_case` names split into their words."""
    return TOKEN.findall(text.lower())


@dataclass
class NaiveBayesIntentModel:
    """Multinomial Naive Bayes over intent words, with Laplace (add-alpha) smoothing.

    score(tier) = log P(tier) + sum over known words w of log P(w | tier), where
    P(w | tier) = (count(w, tier) + alpha) / (words in tier + alpha * |vocabulary|).
    Words never seen in training are ignored. Ties go to the higher tier, because too
    little context is worse than too much (the Block 4 threshold choice, Refinement 4).
    """

    alpha: float = 1.0
    tiers: list[int] = field(default_factory=list)
    vocabulary: set[str] = field(default_factory=set)
    log_priors: dict[int, float] = field(default_factory=dict)
    log_likelihoods: dict[int, dict[str, float]] = field(default_factory=dict)
    documents: dict[int, int] = field(default_factory=dict)

    def fit(self, intents: list[str], tiers: list[int]) -> NaiveBayesIntentModel:
        if not intents or len(intents) != len(tiers):
            raise ValueError("fit needs the same, non-zero number of intents and tiers")
        if self.alpha <= 0:
            raise ValueError("alpha must be positive")
        self.tiers = sorted(set(tiers))
        self.documents = dict(Counter(tiers))
        word_counts: dict[int, Counter[str]] = {tier: Counter() for tier in self.tiers}
        for intent, tier in zip(intents, tiers):
            word_counts[tier].update(tokenize(intent))
        self.vocabulary = set().union(*word_counts.values())

        size = len(self.vocabulary)
        for tier in self.tiers:
            denominator = sum(word_counts[tier].values()) + self.alpha * size
            self.log_priors[tier] = math.log(self.documents[tier] / len(intents))
            self.log_likelihoods[tier] = {
                word: math.log((word_counts[tier][word] + self.alpha) / denominator)
                for word in self.vocabulary
            }
        return self

    def scores(self, intent: str) -> dict[int, float]:
        """Log-score per tier (log prior + log likelihood of the known words)."""
        if not self.tiers:
            raise ValueError("model is not fitted")
        words = [w for w in tokenize(intent) if w in self.vocabulary]
        return {
            tier: self.log_priors[tier] + sum(self.log_likelihoods[tier][w] for w in words)
            for tier in self.tiers
        }

    def predict(self, intent: str) -> int:
        scores = self.scores(intent)
        return max(self.tiers, key=lambda tier: (scores[tier], tier))

    def top_words(self, tier: int, n: int = 8) -> list[tuple[str, float]]:
        """Words with the largest log P(w | tier) (frequent words, shared by tiers too)."""
        ranked = sorted(self.log_likelihoods[tier].items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:n]

    def distinctive_words(self, tier: int, n: int = 8) -> list[tuple[str, float]]:
        """Words that point most to this tier: log P(w | tier) minus the largest other tier's."""
        others = [t for t in self.tiers if t != tier]
        if not others:
            return []
        margins = {
            word: ll - max(self.log_likelihoods[t][word] for t in others)
            for word, ll in self.log_likelihoods[tier].items()
        }
        return sorted(margins.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def majority_tier(tiers: list[int]) -> int:
    """Most common tier; a tie goes to the higher tier."""
    counts = Counter(tiers)
    return max(counts, key=lambda tier: (counts[tier], tier))


def keyword_rule_tier(classifier: TaskIntentClassifier, intent: str) -> int:
    """The current Block 4 keyword rule read directly as a tier: depth 0/1/2 -> Tier 1/2/3."""
    return classifier.classify(intent) + 1


def evaluate_predictor(tasks: list[dict[str, Any]], predict: Callable[[str], int]) -> EvaluationMetrics:
    """Run `predict(task_intent)` over tasks and score it with the Block 4 metric code."""
    evaluations = []
    for task in tasks:
        actual = _tier_of(task)
        predicted = predict(task["task_intent"])
        evaluations.append({
            "id": task["id"],
            "task_intent": task["task_intent"],
            "ground_truth_tier": actual,
            "predicted_tier": predicted,
            "classification": _verdict(actual, predicted),
            "correct_tier": predicted == actual,
        })
    return metrics_from_evaluations(evaluations)


@dataclass
class IntentComparison:
    """Naive Bayes and both baselines, each measured on train and holdout separately."""

    model: NaiveBayesIntentModel
    majority: int
    train_ids: list[str]
    holdout_ids: list[str]
    # model name -> {"train": metrics, "holdout": metrics}
    results: dict[str, dict[str, EvaluationMetrics]]


def compare_intent_models(tasks: list[dict[str, Any]] | None = None, seed: int = SPLIT_SEED) -> IntentComparison:
    """Fit Naive Bayes on the Block 4 train split and score it and the baselines on both splits."""
    tasks = tasks if tasks is not None else DecisionEvaluator().load_dataset()
    train, holdout = stratified_split(tasks, seed=seed)
    train_tiers = [_tier_of(t) for t in train]
    model = NaiveBayesIntentModel().fit([t["task_intent"] for t in train], train_tiers)
    majority = majority_tier(train_tiers)
    keywords = TaskIntentClassifier()

    predictors: dict[str, Callable[[str], int]] = {
        "naive_bayes": model.predict,
        "keyword_rule": lambda intent: keyword_rule_tier(keywords, intent),
        "majority": lambda intent: majority,
    }
    results = {
        name: {"train": evaluate_predictor(train, predict), "holdout": evaluate_predictor(holdout, predict)}
        for name, predict in predictors.items()
    }
    return IntentComparison(
        model=model,
        majority=majority,
        train_ids=[t["id"] for t in train],
        holdout_ids=[t["id"] for t in holdout],
        results=results,
    )


def _summary_rows(comparison: IntentComparison, split: str) -> list[str]:
    rows = [
        "| Model | Accuracy | Miss rate | False alarm rate |",
        "| :--- | :---: | :---: | :---: |",
    ]
    for name, label in MODEL_NAMES.items():
        m = comparison.results[name][split]
        rows.append(
            f"| {label} | {m.accuracy * 100:.1f}% | {m.miss_rate * 100:.1f}% ({m.misses}) "
            f"| {m.false_alarm_rate * 100:.1f}% ({m.false_alarms}) |"
        )
    return rows + [""]


def generate_report(comparison: IntentComparison) -> str:
    """Markdown report: holdout first, train apart, confusion matrices, and the model's words."""
    holdout_n, train_n = len(comparison.holdout_ids), len(comparison.train_ids)
    lines = [
        "# Learned Intent Classifier vs Baselines (Block 7)",
        "",
        f"> Naive Bayes trained on the Block 4 train split ({train_n} tasks, seed {SPLIT_SEED}).",
        f"> The holdout split ({holdout_n} tasks) was never used for training; its numbers are the ones to quote.",
        f"> Most common train tier: Tier {comparison.majority}.",
        "",
        f"## Holdout ({holdout_n} tasks)",
        *_summary_rows(comparison, "holdout"),
        f"## Train ({train_n} tasks, used for training Naive Bayes)",
        *_summary_rows(comparison, "train"),
    ]
    for name, label in MODEL_NAMES.items():
        lines += DecisionEvaluator._metrics_section(comparison.results[name]["holdout"], f"{label}: holdout")
    lines += ["## What Naive Bayes Learned", "", "| Tier | Most likely words | Most distinctive words |",
              "| :--- | :--- | :--- |"]
    for tier in comparison.model.tiers:
        likely = ", ".join(w for w, _ in comparison.model.top_words(tier))
        distinct = ", ".join(f"{w} ({margin:+.2f})" for w, margin in comparison.model.distinctive_words(tier))
        lines.append(f"| Tier {tier} | {likely} | {distinct} |")
    lines += [
        "",
        "Most likely = largest log P(word | tier). Most distinctive = log P(word | tier) minus the "
        "largest log P(word | other tier).",
        "",
        "## Holdout Errors of Naive Bayes",
        "| Task ID | Intent | True Tier | NB | Keyword rule |",
        "| :--- | :--- | :---: | :---: | :---: |",
    ]
    keyword_by_id = {e["id"]: e["predicted_tier"] for e in comparison.results["keyword_rule"]["holdout"].task_evaluations}
    for item in comparison.results["naive_bayes"]["holdout"].task_evaluations:
        if not item["correct_tier"]:
            lines.append(
                f"| `{item['id']}` | {item['task_intent'][:60]} | {item['ground_truth_tier']} "
                f"| {item['predicted_tier']} | {keyword_by_id[item['id']]} |"
            )
    return "\n".join(lines)


def main() -> None:
    """Train Naive Bayes on the train split and print the comparison report."""
    print(generate_report(compare_intent_models()))


if __name__ == "__main__":
    main()
