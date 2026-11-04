"""Tests for the holdout uncertainty analysis (Block 8)."""

import pytest
from acc_mcp import uncertainty
from acc_mcp.evaluate_decision import DecisionEvaluator, stratified_split
from acc_mcp.intent_model import NaiveBayesIntentModel, compare_intent_models
from acc_mcp.uncertainty import (
    bootstrap_interval,
    mcnemar_exact,
    metric_intervals,
    paired_bootstrap_difference,
    paired_comparison,
    precompute_features,
    quantile,
    split_sensitivity,
    wilson_interval,
)


@pytest.fixture(scope="module")
def dataset() -> list[dict]:
    return DecisionEvaluator().load_dataset()


@pytest.fixture(scope="module")
def features(dataset: list[dict]):
    return precompute_features(dataset)


# ---------------------------------------------------------------------------
# 1. Intervals (Step 29.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("successes", "n", "low", "high"),
    [
        (7, 10, 0.3968, 0.8922),  # textbook Wilson 95% interval for 7/10
        (0, 10, 0.0, 0.2775),
        (10, 10, 0.7225, 1.0),
        (21, 30, 0.5212, 0.8334),  # Block 4 holdout accuracy 70.0%
    ],
)
def test_wilson_interval_known_values(successes, n, low, high):
    lo, hi = wilson_interval(successes, n)
    assert lo == pytest.approx(low, abs=1e-4)
    assert hi == pytest.approx(high, abs=1e-4)


def test_wilson_interval_edge_cases():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo3, hi3 = wilson_interval(3, 10)
    lo7, hi7 = wilson_interval(7, 10)
    assert lo3 == pytest.approx(1 - hi7) and hi3 == pytest.approx(1 - lo7)  # symmetric
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_quantile_interpolates():
    assert quantile([1, 2, 3, 4, 5], 0.5) == 3
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([0, 10], 0.25) == 2.5
    assert (quantile([4], 0.0), quantile([4], 1.0)) == (4, 4)


def test_bootstrap_is_reproducible_and_contains_the_estimate():
    """Verify a fixed seed gives the same interval, and the interval contains the point estimate."""
    items = [True] * 21 + [False] * 9

    def mean(sample):
        return sum(sample) / len(sample)

    first = bootstrap_interval(items, mean, n_resamples=500, seed=3)
    assert first == bootstrap_interval(items, mean, n_resamples=500, seed=3)
    assert first[0] <= 0.7 <= first[1]
    assert 0.4 < first[0] < first[1] < 0.95
    assert bootstrap_interval([True] * 10, mean, n_resamples=50) == (1.0, 1.0)


def test_metric_intervals_on_hand_made_predictions():
    """Verify each metric's count, and that both intervals contain the value."""
    evaluations = [
        {"id": "a", "ground_truth_tier": 1, "predicted_tier": 1},
        {"id": "b", "ground_truth_tier": 2, "predicted_tier": 3},
        {"id": "c", "ground_truth_tier": 3, "predicted_tier": 1},
        {"id": "d", "ground_truth_tier": 3, "predicted_tier": 3},
    ]
    out = metric_intervals(evaluations, n_resamples=300, seed=1)
    assert (out["accuracy"]["successes"], out["accuracy"]["n"]) == (2, 4)
    assert (out["miss_rate"]["successes"], out["miss_rate"]["n"]) == (1, 3)
    assert (out["false_alarm_rate"]["successes"], out["false_alarm_rate"]["n"]) == (1, 2)
    for info in out.values():
        for low, high in (info["wilson"], info["bootstrap"]):
            assert low <= info["value"] <= high


# ---------------------------------------------------------------------------
# 2. Paired comparisons (Step 29.2)
# ---------------------------------------------------------------------------


def test_mcnemar_identical_predictions_give_p_1():
    same = [True, False, True, True]
    assert mcnemar_exact(same, same) == {"only_a": 0, "only_b": 0, "p_value": 1.0}


@pytest.mark.parametrize(
    ("only_a", "only_b", "p"),
    [
        (0, 5, 0.0625),  # 2 * (1/2)^5
        (1, 6, 0.125),  # 2 * (1 + 7) / 2^7
        (3, 3, 1.0),
        (2, 0, 0.5),  # 2 * (1/2)^2
    ],
)
def test_mcnemar_counted_by_hand(only_a, only_b, p):
    concordant = 4  # both right; must not change p
    a = [True] * only_a + [False] * only_b + [True] * concordant
    b = [False] * only_a + [True] * only_b + [True] * concordant
    result = mcnemar_exact(a, b)
    assert (result["only_a"], result["only_b"]) == (only_a, only_b)
    assert result["p_value"] == pytest.approx(p)
    assert mcnemar_exact(b, a)["p_value"] == pytest.approx(p)


def test_paired_bootstrap_difference():
    same = [True, False, True, False]
    assert paired_bootstrap_difference(same, same, n_resamples=100) == {"difference": 0.0, "low": 0.0, "high": 0.0}
    better = paired_bootstrap_difference([True] * 10, [False] * 10, n_resamples=100)
    assert better == {"difference": 1.0, "low": 1.0, "high": 1.0}
    with pytest.raises(ValueError):
        paired_bootstrap_difference([True], [True, False])


def test_paired_comparison_requires_the_same_tasks():
    a = [{"id": "x", "ground_truth_tier": 1, "predicted_tier": 1}]
    b = [{"id": "y", "ground_truth_tier": 1, "predicted_tier": 1}]
    with pytest.raises(ValueError):
        paired_comparison(a, b)


def test_block7_comparison_uses_the_holdout_pairs(dataset: list[dict]):
    """Verify the Naive Bayes vs keyword rule comparison is on the 30 shared holdout tasks."""
    intent = compare_intent_models(dataset)
    c = paired_comparison(
        intent.results["naive_bayes"]["holdout"].task_evaluations,
        intent.results["keyword_rule"]["holdout"].task_evaluations,
        n_resamples=200,
    )
    assert c["n"] == 30
    assert c["accuracy_a"] == pytest.approx(intent.results["naive_bayes"]["holdout"].accuracy)
    assert c["accuracy_b"] == pytest.approx(intent.results["keyword_rule"]["holdout"].accuracy)
    assert c["bootstrap"]["difference"] == pytest.approx(c["accuracy_a"] - c["accuracy_b"])
    assert c["bootstrap"]["low"] <= c["bootstrap"]["difference"] <= c["bootstrap"]["high"]


# ---------------------------------------------------------------------------
# 3. Split sensitivity (Step 29.3)
# ---------------------------------------------------------------------------


def test_seed_sweep_never_fits_on_its_own_holdout(dataset, features, monkeypatch: pytest.MonkeyPatch):
    """Verify each seed fits both models on that seed's train split only."""
    logistic_fits: list[set[int]] = []
    nb_fits: list[list[str]] = []
    real_fit_weights = uncertainty.fit_weights
    real_nb_fit = NaiveBayesIntentModel.fit

    def recording_fit_weights(samples, *args, **kwargs):
        logistic_fits.append({id(f) for f, _ in samples})
        return real_fit_weights(samples, *args, **kwargs)

    def recording_nb_fit(self, intents, tiers):
        nb_fits.append(list(intents))
        return real_nb_fit(self, intents, tiers)

    monkeypatch.setattr(uncertainty, "fit_weights", recording_fit_weights)
    monkeypatch.setattr(NaiveBayesIntentModel, "fit", recording_nb_fit)
    seeds = [0, 1]
    split_sensitivity(dataset, seeds, features)

    for i, seed in enumerate(seeds):
        train, holdout = stratified_split(dataset, seed=seed)
        assert logistic_fits[i] == {id(features[t["id"]]) for t in train}
        assert logistic_fits[i].isdisjoint({id(features[t["id"]]) for t in holdout})
        assert nb_fits[i] == [t["task_intent"] for t in train]


def test_seed_21_reproduces_the_block4_and_block7_holdout(dataset, features):
    """Verify the sweep's seed-21 row equals the single-split numbers quoted in Blocks 4 and 7."""
    sweep = split_sensitivity(dataset, [21], features)
    (row,) = sweep["per_seed"]
    intent = compare_intent_models(dataset)
    assert row["logistic"] == pytest.approx(DecisionEvaluator().calibrate(dataset).holdout.accuracy)
    assert row["naive_bayes"] == pytest.approx(intent.results["naive_bayes"]["holdout"].accuracy)
    assert row["keyword_rule"] == pytest.approx(intent.results["keyword_rule"]["holdout"].accuracy)
    assert sweep["summary"]["logistic"]["sd"] == 0.0


def test_sweep_summary_matches_the_rows(dataset, features):
    sweep = split_sensitivity(dataset, [3, 4], features)
    assert [row["seed"] for row in sweep["per_seed"]] == [3, 4]
    for name in uncertainty.SWEEP_MODELS:
        values = [row[name] for row in sweep["per_seed"]]
        s = sweep["summary"][name]
        assert s["min"] <= s["mean"] <= s["max"]
        assert (s["min"], s["max"]) == (min(values), max(values))
        assert s["mean"] == pytest.approx(sum(values) / 2)


def test_report_sections(dataset):
    """Verify the report has every section, with a small sweep to keep the test fast."""
    report = uncertainty.generate_report(dataset, seeds=[21, 22], n_resamples=200)
    for heading in ("## Intervals on the holdout", "## Paired comparisons", "## Split sensitivity (2 stratified",
                    "## What the data cannot conclude"):
        assert heading in report
    assert "Fitted vs hand-picked weights (Block 4)" in report
    assert "Naive Bayes vs keyword rule (Block 7)" in report
    assert report.count("| Logistic, fitted weights (Block 4) |") == 3  # accuracy, miss rate, false alarm rate
