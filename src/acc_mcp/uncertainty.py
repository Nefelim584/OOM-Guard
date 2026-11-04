"""Uncertainty on the holdout results (Block 8).

The Block 4 and Block 7 numbers come from one 70/30 split with 30 holdout tasks, so a single
accuracy says little on its own. This module adds, with no new dependencies:

- a Wilson 95% interval and a seeded bootstrap 95% interval (resampling holdout tasks) for
  accuracy, miss rate and false alarm rate;
- paired comparisons on the same holdout tasks: the exact McNemar test (binomial on the
  discordant pairs) and a paired bootstrap interval for the accuracy difference, for fitted vs
  hand-picked weights (Block 4) and Naive Bayes vs the keyword rule (Block 7);
- split sensitivity: the split and both fits repeated for 50 seeds, so the seed-21 split is
  shown as one draw.

Run `python3 src/acc_mcp/uncertainty.py` to print the report (about a minute: 50 weight fits).
"""

from __future__ import annotations

import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# Ensure src is on sys.path for direct CLI execution
src_dir = Path(__file__).resolve().parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from acc_mcp.decision import ContextFeatures, DecisionModel, fit_weights
from acc_mcp.evaluate_decision import (
    HAND_PICKED_WEIGHTS,
    SPLIT_SEED,
    DecisionEvaluator,
    _tier_of,
    metrics_from_evaluations,
    stratified_split,
)
from acc_mcp.intent_model import compare_intent_models

Z_95 = 1.959963984540054  # standard normal quantile for a two-sided 95% interval
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 8
SENSITIVITY_SEEDS = range(50)
METRICS = ("accuracy", "miss_rate", "false_alarm_rate")
SWEEP_MODELS = {
    "logistic": "Logistic decision model (Block 4, weights refitted per split)",
    "naive_bayes": "Naive Bayes intent model (Block 7, refitted per split)",
    "keyword_rule": "Keyword rule (no fitting)",
}


# ---------------------------------------------------------------------------
# Intervals (Step 29.1)
# ---------------------------------------------------------------------------


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a proportion successes / n; (0, 1) when n is 0."""
    if n <= 0:
        return 0.0, 1.0
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be in [0, n], got {successes} of {n}")
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """q-quantile of already sorted values, linear interpolation between ranks."""
    if not sorted_values:
        raise ValueError("quantile of no values")
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def bootstrap_interval(
    items: Sequence[Any],
    statistic: Callable[[list[Any]], float],
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    level: float = 0.95,
) -> tuple[float, float]:
    """Percentile bootstrap interval: resample the items with replacement, seeded."""
    if not items:
        raise ValueError("bootstrap of no items")
    rng = random.Random(seed)
    n = len(items)
    values = sorted(statistic([items[rng.randrange(n)] for _ in range(n)]) for _ in range(n_resamples))
    tail = (1 - level) / 2
    return quantile(values, tail), quantile(values, 1 - tail)


def metric_intervals(
    evaluations: list[dict[str, Any]],
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, dict[str, Any]]:
    """Value, count, Wilson and bootstrap 95% intervals of accuracy, miss rate and false alarm rate."""
    m = metrics_from_evaluations(evaluations)
    cm = m.tier_confusion_matrix
    counts = {
        "accuracy": (sum(cm[t][t] for t in cm), m.total_samples),
        "miss_rate": (m.misses, sum(sum(cm[a].values()) for a in cm if a > 1)),
        "false_alarm_rate": (m.false_alarms, sum(sum(cm[a].values()) for a in cm if a < 3)),
    }
    out = {}
    for metric in METRICS:
        successes, n = counts[metric]
        out[metric] = {
            "value": getattr(m, metric),
            "successes": successes,
            "n": n,
            "wilson": wilson_interval(successes, n),
            "bootstrap": bootstrap_interval(
                evaluations, lambda sample, name=metric: getattr(metrics_from_evaluations(sample), name),
                n_resamples, seed,
            ),
        }
    return out


# ---------------------------------------------------------------------------
# Paired comparisons (Step 29.2)
# ---------------------------------------------------------------------------


def mcnemar_exact(correct_a: Sequence[bool], correct_b: Sequence[bool]) -> dict[str, Any]:
    """Exact two-sided McNemar test: binomial(n = discordant pairs, p = 1/2)."""
    if len(correct_a) != len(correct_b):
        raise ValueError("paired results must have the same length")
    only_a = sum(1 for a, b in zip(correct_a, correct_b) if a and not b)
    only_b = sum(1 for a, b in zip(correct_a, correct_b) if b and not a)
    discordant = only_a + only_b
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(discordant, i) for i in range(min(only_a, only_b) + 1)) / 2 ** discordant
        p_value = min(1.0, 2 * tail)
    return {"only_a": only_a, "only_b": only_b, "p_value": p_value}


def paired_bootstrap_difference(
    correct_a: Sequence[bool],
    correct_b: Sequence[bool],
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    level: float = 0.95,
) -> dict[str, float]:
    """Accuracy of A minus accuracy of B, with a bootstrap interval resampling task pairs."""
    if len(correct_a) != len(correct_b) or not correct_a:
        raise ValueError("paired results must have the same, non-zero length")
    pairs = list(zip(correct_a, correct_b))

    def difference(sample: list[tuple[bool, bool]]) -> float:
        return (sum(a for a, _ in sample) - sum(b for _, b in sample)) / len(sample)

    low, high = bootstrap_interval(pairs, difference, n_resamples, seed, level)
    return {"difference": difference(pairs), "low": low, "high": high}


def paired_comparison(
    evaluations_a: list[dict[str, Any]],
    evaluations_b: list[dict[str, Any]],
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """McNemar and paired bootstrap for two models scored on the same tasks, matched by id."""
    ids_a = [e["id"] for e in evaluations_a]
    if ids_a != [e["id"] for e in evaluations_b]:
        raise ValueError("both models must be scored on the same tasks in the same order")
    correct_a = [e["predicted_tier"] == e["ground_truth_tier"] for e in evaluations_a]
    correct_b = [e["predicted_tier"] == e["ground_truth_tier"] for e in evaluations_b]
    return {
        "n": len(ids_a),
        "accuracy_a": sum(correct_a) / len(ids_a),
        "accuracy_b": sum(correct_b) / len(ids_a),
        "mcnemar": mcnemar_exact(correct_a, correct_b),
        "bootstrap": paired_bootstrap_difference(correct_a, correct_b, n_resamples, seed),
    }


# ---------------------------------------------------------------------------
# Split sensitivity (Step 29.3)
# ---------------------------------------------------------------------------


def precompute_features(tasks: list[dict[str, Any]]) -> dict[str, ContextFeatures]:
    """AST and intent features per task id; they do not depend on the split, only the weights do."""
    samples = DecisionEvaluator().extract_samples(tasks)
    return {task["id"]: features for task, (features, _) in zip(tasks, samples)}


def logistic_holdout_accuracy(
    train: list[dict[str, Any]], holdout: list[dict[str, Any]], features: dict[str, ContextFeatures]
) -> float:
    """Fit the Block 4 weights on train only and return the accuracy on holdout."""
    weights = fit_weights([(features[t["id"]], _tier_of(t)) for t in train])
    model = DecisionModel(*weights)
    return sum(model.evaluate(features[t["id"]]).tier == _tier_of(t) for t in holdout) / len(holdout)


def split_sensitivity(
    tasks: list[dict[str, Any]],
    seeds: Iterable[int] = SENSITIVITY_SEEDS,
    features: dict[str, ContextFeatures] | None = None,
) -> dict[str, Any]:
    """Holdout accuracy of each model over many stratified splits: per seed, and mean/sd/min/max."""
    features = features if features is not None else precompute_features(tasks)
    per_seed = []
    for seed in seeds:
        train, holdout = stratified_split(tasks, seed=seed)
        intent = compare_intent_models(tasks, seed=seed)
        per_seed.append({
            "seed": seed,
            "logistic": logistic_holdout_accuracy(train, holdout, features),
            "naive_bayes": intent.results["naive_bayes"]["holdout"].accuracy,
            "keyword_rule": intent.results["keyword_rule"]["holdout"].accuracy,
        })
    summary = {}
    for name in SWEEP_MODELS:
        values = [row[name] for row in per_seed]
        summary[name] = {
            "mean": statistics.fmean(values),
            "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
            "min": min(values),
            "max": max(values),
        }
    return {"per_seed": per_seed, "summary": summary}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def holdout_evaluations(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Per-task holdout predictions (seed-21 split) of every model in Blocks 4 and 7."""
    evaluator = DecisionEvaluator()
    _, holdout = stratified_split(tasks)
    intent = compare_intent_models(tasks)
    return {
        "Logistic, fitted weights (Block 4)": evaluator.calibrate(tasks).holdout.task_evaluations,
        "Logistic, hand-picked weights": evaluator.evaluate(holdout, DecisionModel(*HAND_PICKED_WEIGHTS)).task_evaluations,
        "Naive Bayes (Block 7)": intent.results["naive_bayes"]["holdout"].task_evaluations,
        "Keyword rule": intent.results["keyword_rule"]["holdout"].task_evaluations,
        "Most common train tier": intent.results["majority"]["holdout"].task_evaluations,
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _interval(bounds: tuple[float, float]) -> str:
    return f"{bounds[0] * 100:.1f}–{bounds[1] * 100:.1f}%"


def generate_report(
    tasks: list[dict[str, Any]] | None = None,
    seeds: Iterable[int] = SENSITIVITY_SEEDS,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> str:
    """Markdown report: intervals, paired comparisons, split sensitivity, and the limits."""
    tasks = tasks if tasks is not None else DecisionEvaluator().load_dataset()
    models = holdout_evaluations(tasks)
    holdout_n = len(next(iter(models.values())))
    seeds = list(seeds)
    lines = [
        "# Uncertainty on the Holdout (Block 8)",
        "",
        f"> One stratified 70/30 split (seed {SPLIT_SEED}), {holdout_n} holdout tasks. Wilson and percentile "
        f"bootstrap 95% intervals ({n_resamples} resamples of the holdout tasks, seed {seed}).",
        "",
        "## Intervals on the holdout",
        "| Model | Metric | Value (count) | Wilson 95% | Bootstrap 95% |",
        "| :--- | :--- | :---: | :---: | :---: |",
    ]
    for name, evaluations in models.items():
        for metric, info in metric_intervals(evaluations, n_resamples, seed).items():
            lines.append(
                f"| {name} | {metric.replace('_', ' ')} | {_pct(info['value'])} ({info['successes']}/{info['n']}) "
                f"| {_interval(info['wilson'])} | {_interval(info['bootstrap'])} |"
            )

    pairs = [
        ("Fitted vs hand-picked weights (Block 4)", "Logistic, fitted weights (Block 4)", "Logistic, hand-picked weights"),
        ("Naive Bayes vs keyword rule (Block 7)", "Naive Bayes (Block 7)", "Keyword rule"),
    ]
    lines += [
        "",
        "## Paired comparisons on the same holdout tasks",
        "| Comparison | Accuracy A | Accuracy B | A − B | Paired bootstrap 95% | Only A right | Only B right | McNemar exact p |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for label, a, b in pairs:
        c = paired_comparison(models[a], models[b], n_resamples, seed)
        boot, mc = c["bootstrap"], c["mcnemar"]
        lines.append(
            f"| {label} | {_pct(c['accuracy_a'])} | {_pct(c['accuracy_b'])} | {boot['difference'] * 100:+.1f} pp "
            f"| {boot['low'] * 100:+.1f} to {boot['high'] * 100:+.1f} pp | {mc['only_a']} | {mc['only_b']} | {mc['p_value']:.3f} |"
        )

    sweep = split_sensitivity(tasks, seeds)
    seed21 = next((row for row in sweep["per_seed"] if row["seed"] == SPLIT_SEED), None)
    lines += [
        "",
        f"## Split sensitivity ({len(seeds)} stratified splits, holdout accuracy)",
        "| Model | Mean | SD | Min | Max | Seed 21 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]
    for name, label in SWEEP_MODELS.items():
        s = sweep["summary"][name]
        lines.append(
            f"| {label} | {_pct(s['mean'])} | {s['sd'] * 100:.1f} pp | {_pct(s['min'])} | {_pct(s['max'])} "
            f"| {_pct(seed21[name]) if seed21 else '–'} |"
        )
    lines += [
        "",
        "## What the data cannot conclude",
        f"With {holdout_n} holdout tasks, labels written by the project author, and train and holdout tasks "
        "that share code snippets, these numbers show how the models behave on this dataset only; a difference "
        "whose paired interval includes 0 (or whose McNemar p is above 0.05) is not evidence that one model is better.",
    ]
    return "\n".join(lines)


def main() -> None:
    print(generate_report())


if __name__ == "__main__":
    main()
