"""Tests for the learned intent classifier and its baselines (Block 7)."""

import math

import pytest
from acc_mcp import intent_model
from acc_mcp.decision import TaskIntentClassifier
from acc_mcp.evaluate_decision import TIERS, DecisionEvaluator, metrics_from_evaluations, stratified_split
from acc_mcp.intent_model import (
    NaiveBayesIntentModel,
    compare_intent_models,
    generate_report,
    keyword_rule_tier,
    majority_tier,
    tokenize,
)

# A tiny hand-made training set: each tier has its own verbs, "the function" is shared.
TINY_INTENTS = [
    "show the signature",
    "show the docstring",
    "who calls the function",
    "list the callers",
    "fix the function body",
    "rewrite the loop",
]
TINY_TIERS = [1, 1, 2, 2, 3, 3]


@pytest.fixture(scope="module")
def dataset() -> list[dict]:
    return DecisionEvaluator().load_dataset()


@pytest.fixture(scope="module")
def comparison(dataset: list[dict]):
    return compare_intent_models(dataset)


# ---------------------------------------------------------------------------
# 1. The model on a hand-made set (Step 28.1)
# ---------------------------------------------------------------------------


def test_tokenize_lowercases_and_splits_names():
    assert tokenize("Fix calculate_hash, NOW!") == ["fix", "calculate", "hash", "now"]


def test_fit_predict_on_tiny_set():
    """Verify each tier's own words decide the prediction."""
    model = NaiveBayesIntentModel().fit(TINY_INTENTS, TINY_TIERS)

    assert model.predict("show me the signature") == 1
    assert model.predict("find the callers") == 2
    assert model.predict("rewrite this body") == 3
    assert model.tiers == [1, 2, 3]
    assert model.documents == {1: 2, 2: 2, 3: 2}


def test_laplace_smoothing_counted_by_hand():
    """Verify P(w | tier) = (count + 1) / (words in tier + |V|) and the priors."""
    model = NaiveBayesIntentModel().fit(TINY_INTENTS, TINY_TIERS)
    vocabulary = len(model.vocabulary)
    # Tier 1 has 6 words: show, the, signature, show, the, docstring
    assert model.log_likelihoods[1]["show"] == pytest.approx(math.log((2 + 1) / (6 + vocabulary)))
    assert model.log_likelihoods[1]["loop"] == pytest.approx(math.log((0 + 1) / (6 + vocabulary)))
    assert model.log_priors[2] == pytest.approx(math.log(2 / 6))
    for tier in model.tiers:
        assert sum(math.exp(v) for v in model.log_likelihoods[tier].values()) == pytest.approx(1.0)


def test_unknown_words_fall_back_to_priors_and_ties_go_up():
    """Verify an intent with no known word is decided by the priors, and equal scores give the higher tier."""
    model = NaiveBayesIntentModel().fit(TINY_INTENTS, TINY_TIERS)
    assert model.scores("zzz qqq") == model.log_priors
    assert model.predict("zzz qqq") == 3  # equal priors -> higher tier

    skewed = NaiveBayesIntentModel().fit(["a", "b", "c"], [1, 1, 2])
    assert skewed.predict("unknown") == 1


def test_fit_and_predict_reject_bad_input():
    with pytest.raises(ValueError):
        NaiveBayesIntentModel().fit(["a"], [1, 2])
    with pytest.raises(ValueError):
        NaiveBayesIntentModel().fit([], [])
    with pytest.raises(ValueError):
        NaiveBayesIntentModel().predict("show")


def test_explanation_words():
    """Verify the defence tables: likely words include the shared ones, distinctive words do not."""
    model = NaiveBayesIntentModel().fit(TINY_INTENTS, TINY_TIERS)
    assert model.top_words(1, 2)[0][0] in {"show", "the"}
    distinctive = [w for w, _ in model.distinctive_words(1, 3)]
    assert distinctive[0] == "show"
    assert "the" not in distinctive
    assert NaiveBayesIntentModel().fit(["a"], [1]).distinctive_words(1) == []


def test_baselines():
    assert majority_tier([1, 3, 3, 2]) == 3
    assert majority_tier([1, 1, 2, 2]) == 2  # tie -> higher tier
    classifier = TaskIntentClassifier()
    assert keyword_rule_tier(classifier, "Check the signature") == 1
    assert keyword_rule_tier(classifier, "Rewrite the parser") == 3


# ---------------------------------------------------------------------------
# 2. Evaluation on the Block 4 split (Steps 28.2 - 28.3)
# ---------------------------------------------------------------------------


def test_no_holdout_task_is_used_in_fitting(dataset: list[dict], monkeypatch: pytest.MonkeyPatch):
    """Verify Naive Bayes is fitted on exactly the Block 4 train split, never on a holdout intent."""
    seen: list[list[str]] = []
    real_fit = NaiveBayesIntentModel.fit

    def recording_fit(self, intents, tiers):
        seen.append(list(intents))
        return real_fit(self, intents, tiers)

    monkeypatch.setattr(NaiveBayesIntentModel, "fit", recording_fit)
    result = compare_intent_models(dataset)
    train, holdout = stratified_split(dataset)

    assert seen == [[t["task_intent"] for t in train]]
    assert set(result.train_ids).isdisjoint(result.holdout_ids)
    assert result.holdout_ids == [t["id"] for t in holdout]
    assert sum(result.model.documents.values()) == len(train)


def test_baselines_use_the_same_holdout_tasks(comparison):
    """Verify all three models are scored on the same holdout (and train) ids, in the same order."""
    for split, ids in (("holdout", comparison.holdout_ids), ("train", comparison.train_ids)):
        for name in ("naive_bayes", "keyword_rule", "majority"):
            assert [e["id"] for e in comparison.results[name][split].task_evaluations] == ids
    assert len(comparison.holdout_ids) == 30


def test_majority_baseline_always_predicts_the_train_majority(comparison, dataset: list[dict]):
    train, _ = stratified_split(dataset)
    assert comparison.majority == majority_tier([t["ground_truth_tier"] for t in train])
    for split in ("train", "holdout"):
        assert {e["predicted_tier"] for e in comparison.results["majority"][split].task_evaluations} == {comparison.majority}


@pytest.mark.parametrize("name", ["naive_bayes", "keyword_rule", "majority"])
@pytest.mark.parametrize("split", ["train", "holdout"])
def test_metric_arithmetic_matches_the_confusion_matrix(comparison, name: str, split: str):
    """Verify accuracy, misses and false alarms are read off the 3x3 matrix correctly."""
    m = comparison.results[name][split]
    cm = m.tier_confusion_matrix
    total = sum(cm[a][p] for a in TIERS for p in TIERS)
    assert total == m.total_samples
    assert m.accuracy == pytest.approx(sum(cm[t][t] for t in TIERS) / total)
    assert m.misses == sum(cm[a][p] for a in TIERS for p in TIERS if p < a)
    assert m.false_alarms == sum(cm[a][p] for a in TIERS for p in TIERS if p > a)
    can_miss = sum(sum(cm[a].values()) for a in (2, 3))
    assert m.miss_rate == pytest.approx(m.misses / can_miss)
    for item in m.task_evaluations:
        assert item["correct_tier"] == (item["predicted_tier"] == item["ground_truth_tier"])


def test_metrics_from_evaluations_counted_by_hand():
    """Verify the shared metric code on 4 hand-made predictions."""
    m = metrics_from_evaluations([
        {"ground_truth_tier": 1, "predicted_tier": 1},
        {"ground_truth_tier": 2, "predicted_tier": 3},  # false alarm
        {"ground_truth_tier": 3, "predicted_tier": 1},  # miss
        {"ground_truth_tier": 3, "predicted_tier": 3},
    ])
    assert (m.accuracy, m.misses, m.false_alarms) == (0.5, 1, 1)
    assert m.miss_rate == pytest.approx(1 / 3)  # tiers 2 and 3 can be missed: 3 tasks
    assert m.false_alarm_rate == pytest.approx(1 / 2)  # tiers 1 and 2 can be over-served: 2 tasks


def test_report_is_reproducible_and_complete(dataset: list[dict], comparison):
    """Verify two runs give the same report, with holdout before train and the explanation tables."""
    first = generate_report(comparison)
    second = generate_report(compare_intent_models(dataset))
    assert first == second
    assert first.index("## Holdout (30 tasks)") < first.index("## Train (70 tasks")
    for label in intent_model.MODEL_NAMES.values():
        assert first.count(label) >= 3  # holdout row, train row, confusion matrix section
    assert "## What Naive Bayes Learned" in first
    assert "| Tier 3 |" in first


def test_block4_evaluation_unchanged_by_the_shared_metric_code(dataset: list[dict]):
    """Verify DecisionEvaluator.evaluate still reports the Block 4 per-task fields."""
    item = DecisionEvaluator().evaluate(dataset[:3]).task_evaluations[0]
    assert {"probability", "logit", "features", "classification", "correct_tier"} <= set(item)
