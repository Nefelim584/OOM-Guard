"""Unit tests for TaskIntentClassifier, DecisionModel, and Adaptive Tier Selection (Block 4)."""

import math
import pytest
from acc_mcp.decision import (
    ContextFeatures,
    DecisionModel,
    DecisionResult,
    TaskIntentClassifier,
    fit_weights,
)
from acc_mcp.guard import GuardRail, SecurityViolationError
from acc_mcp.parser import ASTParser


# ---------------------------------------------------------------------------
# 1. Task Intent Classification Tests (Step 4.1)
# ---------------------------------------------------------------------------


@pytest.fixture
def classifier() -> TaskIntentClassifier:
    return TaskIntentClassifier()


@pytest.fixture
def decision_model() -> DecisionModel:
    return DecisionModel()


@pytest.fixture
def policy_model() -> DecisionModel:
    """Model with fixed weights (the pre-Phase 21 hand-picked ones).

    Threshold-policy and tier-dispatch tests use it so they check the policy and dispatch
    code, not whatever weights the current fit produces.
    """
    return DecisionModel(w0=-2.0, w1=2.0, w2=0.2, w3=0.3, w4=1.0)


@pytest.mark.parametrize(
    "query",
    [
        "Inspect the arguments of the connect function",
        "Check docstring and return type of execute_query",
        "View signatures in DatabaseManager",
        "What is the parameter type for data?",
        "How to call compute_metrics from another service?",
        "Show method interface and type hints",
        "List all methods in class",
    ],
)
def test_intent_depth_0_inspection(classifier: TaskIntentClassifier, query: str):
    """Verify inspection and interface queries resolve to depth x_1 = 0."""
    assert classifier.classify(query) == 0


@pytest.mark.parametrize(
    "query",
    [
        "Refactor PaymentProcessor method parameters",
        "Trace all callers and callees of validate_user",
        "Rename the database connection method across callers",
        "Deprecate legacy interface and add new parameter",
        "Analyze call graph dependencies of this module",
        "Patch method signature to support optional timeout",
        "Wrap the database client with dependency injection",
    ],
)
def test_intent_depth_1_refactor_dependencies(classifier: TaskIntentClassifier, query: str):
    """Verify refactoring, patching, and call-graph queries resolve to depth x_1 = 1."""
    assert classifier.classify(query) == 1


@pytest.mark.parametrize(
    "query",
    [
        "Rewrite the transaction retry algorithm to prevent lockups",
        "Fix critical bug in process_transaction when amount is negative",
        "Debug race condition and memory leak in message loop",
        "Reimplement the payment validation logic from scratch",
        "Optimize hot loop algorithm for performance",
        "Fix crash caused by unhandled ServiceError exception",
        "Complete overhaul of execution logic",
    ],
)
def test_intent_depth_2_rewrite_debug(classifier: TaskIntentClassifier, query: str):
    """Verify deep algorithmic rewrites and bug fixes resolve to depth x_1 = 2."""
    assert classifier.classify(query) == 2


@pytest.mark.parametrize(
    "query,expected_depth",
    [
        ("Fixing the edge case in calculate_discount", 2),
        ("Rewriting the queue scheduler from scratch", 2),
        ("Debugging worker thread deadlock", 2),
        ("Optimizing the lookup tree implementation", 2),
        ("Implementing cache eviction algorithm", 2),
        ("Tracing all callers of handle_request", 1),
        ("Inspecting function parameters and return type", 0),
        ("Checking docstrings and signatures", 0),
    ],
)
def test_intent_word_stem_matching(classifier: TaskIntentClassifier, query: str, expected_depth: int):
    """Verify word stems (e.g., fixing, rewriting, tracing) correctly resolve to their intent depths."""
    assert classifier.classify(query) == expected_depth


def test_intent_hit_counting_and_tie_breaker(classifier: TaskIntentClassifier):
    """Verify pattern hit counting and tie-breaker preferring deeper context."""
    # Tie between depth 0 (check) and depth 1 (caller) -> deeper wins (depth 1)
    tie_0_1 = "Check caller dependencies of process_order"
    assert classifier.classify(tie_0_1) == 1

    # Tie between depth 0 (inspect) and depth 2 (fix) -> deeper wins (depth 2)
    tie_0_2 = "Inspect and fix validate_user"
    assert classifier.classify(tie_0_2) == 2

    # Plurality wins: 2 hits in depth 0 (inspect, parameter) vs 1 hit in depth 1 (caller)
    plurality_0 = "Inspect parameter types of caller function"
    assert classifier.classify(plurality_0) == 0

    # Fallback to default intermediate depth 1 when no pattern matches
    assert classifier.classify("something completely unrelated xyz123") == 1
    assert classifier.classify("") == 0


@pytest.mark.parametrize(
    "query",
    [
        "Check what exceptions parse_header raises",
        "Inspect error codes and return status",
        "View exception types in module",
        "Check docstring for error handling details",
    ],
)
def test_intent_subject_nouns_do_not_force_depth_2(classifier: TaskIntentClassifier, query: str):
    """Verify that queries with subject nouns like 'error' or 'exception' are not forced to depth 2."""
    assert classifier.classify(query) in {0, 1}
    assert classifier.classify(query) != 2


@pytest.mark.parametrize(
    "query,expected_depth",
    [
        # `add` must not match "address" (old result: tie with check -> 1)
        ("Check the address field of User", 0),
        # `list` must not match the annotation "list[int]" (old result: 2 depth-0 hits beat rename -> 0)
        ("Rename the argument of parse_ids(values: list[int])", 1),
        # `type` must not match "typed" (old result: 2 depth-0 hits beat change -> 0)
        ("Change the interface of load_config to return a typed dict", 1),
        # `fix` must not match "fixture" (old result: tie with check -> 2)
        ("Check the pytest fixture for the database", 0),
        # `trace` must not match "tracker" (old result: tie with inspect -> 1)
        ("Inspect the budget tracker class", 0),
        # `change` must not match "changelog" (old result: tie with view -> 1)
        ("View the changelog entry for release_notes", 0),
        # `optimize` must not match "optimistic" (old result: tie with check -> 2)
        ("Check optimistic locking in save_order", 0),
    ],
)
def test_intent_exact_word_forms_avoid_false_hits(
    classifier: TaskIntentClassifier, query: str, expected_depth: int
):
    """Verify keywords match whole word forms only, not longer words that share a prefix."""
    assert classifier.classify(query) == expected_depth


@pytest.mark.parametrize(
    "query,expected_depth",
    [
        ("List the methods of TokenCache", 0),
        ("Add a parameter to parse_ids", 1),
        ("Show the types of each field", 0),
        ("Check the implementation of retry", 2),
    ],
)
def test_intent_real_keyword_uses_still_match(
    classifier: TaskIntentClassifier, query: str, expected_depth: int
):
    """Verify the narrower patterns still match the intended uses of list, add, type, and implementation."""
    assert classifier.classify(query) == expected_depth


# ---------------------------------------------------------------------------
# 2. Feature Extraction & Compilation Tests (Step 4.2)
# ---------------------------------------------------------------------------


def test_feature_extraction_single_symbol(decision_model: DecisionModel, complex_parser: ASTParser):
    """Verify feature vector compilation (x1, x2, x3, x4) for a specific symbol."""
    features = decision_model.extract_features(
        task_intent="Trace callers of validate_user",
        parser=complex_parser,
        symbol_name="validate_user",
    )

    # x1: trace -> 1
    assert features.task_depth == 1
    # x2: cyclomatic complexity >= 3
    assert features.cyclomatic_complexity >= 3
    # x3: called by PaymentProcessor.process_transaction -> in_degree >= 1
    assert features.in_degree >= 1
    # x4: has type hints and docstring -> 1
    assert features.has_annotations == 1

    tup = features.to_tuple()
    assert len(tup) == 4
    assert tup == (features.task_depth, features.cyclomatic_complexity, features.in_degree, features.has_annotations)


def test_feature_extraction_module_level(decision_model: DecisionModel, tier1_parser: ASTParser):
    """Verify feature extraction aggregate fallback when symbol_name is None."""
    features = decision_model.extract_features(
        task_intent="Inspect all signatures",
        parser=tier1_parser,
        symbol_name=None,
    )

    assert features.task_depth == 0
    assert features.cyclomatic_complexity >= 1
    assert features.in_degree >= 0


# ---------------------------------------------------------------------------
# 3. Logistic Scoring & Sigmoid Math Tests (Step 4.3)
# ---------------------------------------------------------------------------


def test_logit_calculation(decision_model: DecisionModel):
    """Verify exact formula z = w0 + w1*x1 + w2*x2 + w3*x3 - w4*x4."""
    # Custom weights test
    custom_model = DecisionModel(w0=-2.0, w1=2.0, w2=0.2, w3=0.3, w4=1.0)
    features = ContextFeatures(task_depth=2, cyclomatic_complexity=5, in_degree=2, has_annotations=1)

    # z = -2.0 + (2.0 * 2) + (0.2 * 5) + (0.3 * 2) - (1.0 * 1)
    # z = -2.0 + 4.0 + 1.0 + 0.6 - 1.0 = 2.6
    expected_z = -2.0 + 4.0 + 1.0 + 0.6 - 1.0
    computed_z = custom_model.compute_logit(features)

    assert math.isclose(computed_z, expected_z, abs_tol=1e-5)


def test_sigmoid_probability_bounds(decision_model: DecisionModel):
    """Verify sigmoid output properties: 0 < P < 1, monotonicity, extreme stability."""
    assert decision_model.compute_probability(0.0) == 0.5
    assert decision_model.compute_probability(50.0) == 1.0
    assert decision_model.compute_probability(-50.0) == 0.0

    p_low = decision_model.compute_probability(-2.0)
    p_mid = decision_model.compute_probability(0.0)
    p_high = decision_model.compute_probability(2.0)

    assert 0.0 < p_low < p_mid < p_high < 1.0


# ---------------------------------------------------------------------------
# 4. Decision Policy & Tier Threshold Tests (Step 4.3)
# ---------------------------------------------------------------------------


def test_tier_selection_boundaries(policy_model: DecisionModel):
    """Verify tier assignment based on thresholds theta_low=0.30, theta_high=0.65."""
    decision_model = policy_model
    # Tier 1 case: low depth, low complexity, rich docstrings -> P < 0.30
    tier1_feat = ContextFeatures(task_depth=0, cyclomatic_complexity=1, in_degree=0, has_annotations=1)
    res1 = decision_model.evaluate(tier1_feat)
    assert res1.tier == 1
    assert res1.probability < 0.30
    assert "signatures and docstrings suffice" in res1.reasoning

    # Tier 2 case: intermediate depth (refactor), moderate complexity
    tier2_feat = ContextFeatures(task_depth=1, cyclomatic_complexity=2, in_degree=2, has_annotations=1)
    res2 = decision_model.evaluate(tier2_feat)
    assert res2.tier == 2
    assert 0.30 <= res2.probability < 0.65
    assert "Call-graph skeleton" in res2.reasoning

    # Tier 3 case: full rewrite (depth=2), high complexity (CC=8)
    tier3_feat = ContextFeatures(task_depth=2, cyclomatic_complexity=8, in_degree=1, has_annotations=0)
    res3 = decision_model.evaluate(tier3_feat)
    assert res3.tier == 3
    assert res3.probability >= 0.65
    assert "Full implementation body strictly required" in res3.reasoning


def test_default_thresholds_are_unchanged():
    """Verify Phase 21 refits only the weights; the thresholds stay hand-picked at 0.30 / 0.65."""
    model = DecisionModel()
    assert (model.theta_low, model.theta_high) == (0.30, 0.65)
    assert model.weights == (
        DecisionModel.DEFAULT_W0,
        DecisionModel.DEFAULT_W1,
        DecisionModel.DEFAULT_W2,
        DecisionModel.DEFAULT_W3,
        DecisionModel.DEFAULT_W4,
    )


# ---------------------------------------------------------------------------
# 4b. Gradient-Descent Weight Fitting (Step 21.3)
# ---------------------------------------------------------------------------


def _depth_samples() -> list[tuple[ContextFeatures, int]]:
    """Samples whose tier is exactly x1 + 1, with the AST features held constant per tier."""
    return [
        (ContextFeatures(task_depth=depth, cyclomatic_complexity=cc, in_degree=1, has_annotations=1), depth + 1)
        for depth in (0, 1, 2)
        for cc in (1, 3, 5)
    ]


def test_fit_weights_is_deterministic():
    """Verify the fit starts from zero with no randomness, so repeated fits match exactly."""
    samples = _depth_samples()
    assert fit_weights(samples) == fit_weights(samples)
    assert len(fit_weights(samples)) == 5


def test_fit_weights_learns_intent_depth():
    """Verify that when the tier follows x1, the fit gives w1 a large positive weight and every sample its tier."""
    samples = _depth_samples()
    weights = fit_weights(samples)
    model = DecisionModel(*weights)

    assert weights[1] > 1.0
    for features, tier in samples:
        assert model.evaluate(features).tier == tier


def test_fit_weights_x4_enters_with_minus_sign():
    """Verify w4 comes out positive when annotated symbols need less context (z uses -w4*x4)."""
    samples = [
        (ContextFeatures(task_depth=1, cyclomatic_complexity=2, in_degree=0, has_annotations=x4), tier)
        for x4, tier in ((1, 1), (0, 3))
        for _ in range(4)
    ]
    weights = fit_weights(samples)
    model = DecisionModel(*weights)

    assert weights[4] > 0
    assert model.evaluate(samples[0][0]).probability < model.evaluate(samples[-1][0]).probability


def test_fit_weights_stays_finite_on_separable_data():
    """Verify the L2 penalty keeps the weights bounded when the classes separate perfectly."""
    weights = fit_weights(_depth_samples(), epochs=20000)
    assert all(math.isfinite(w) and abs(w) < 50 for w in weights)


# ---------------------------------------------------------------------------
# 5. End-to-End Adaptive Context Retrieval Tests (Step 4.3)
#    Dispatch is tested with the fixed-weight policy model so it does not depend on the fit.
# ---------------------------------------------------------------------------


def test_adaptive_context_tier1_dispatch(
    policy_model: DecisionModel, complex_parser: ASTParser, default_guard: GuardRail
):
    """Verify inspection intent triggers Tier 1 context containing stubbed bodies."""
    context, result = policy_model.get_adaptive_context(
        task_intent="Inspect type hints and signature for log_audit",
        parser=complex_parser,
        symbol_name="log_audit",
        guard=default_guard,
    )

    assert result.tier == 1
    assert "def log_audit(action: str, user_id: int) -> None:" in context
    assert "..." in context
    assert "print(f" not in context  # Body stubbed


def test_adaptive_context_tier2_dispatch(
    policy_model: DecisionModel, complex_parser: ASTParser, default_guard: GuardRail
):
    """Verify refactor/dependency query triggers Tier 2 context containing call-graph."""
    context, result = policy_model.get_adaptive_context(
        task_intent="Trace callers and refactor dependencies of validate_user",
        parser=complex_parser,
        symbol_name="validate_user",
        guard=default_guard,
    )

    assert result.tier == 2
    assert "Tier 2 Context: Call-Graph Slice" in context
    assert "Cyclomatic Complexity" in context
    assert "PaymentProcessor" in context  # Caller interface included


def test_adaptive_context_tier3_dispatch(
    policy_model: DecisionModel, complex_parser: ASTParser, default_guard: GuardRail
):
    """Verify rewrite/bug fix query triggers Tier 3 context containing full implementation body."""
    context, result = policy_model.get_adaptive_context(
        task_intent="Rewrite and fix bug in validate_user logic",
        parser=complex_parser,
        symbol_name="validate_user",
        guard=default_guard,
    )

    assert result.tier == 3
    assert "if user_id <= 0:" in context
    assert "Invalid user ID" in context
    assert "User lacks admin role" in context


def test_adaptive_context_security_rejection(
    decision_model: DecisionModel, complex_parser: ASTParser, default_guard: GuardRail
):
    """Verify GuardRail rejects protected paths even when invoked through get_adaptive_context."""
    complex_parser.file_path = ".env"

    with pytest.raises(SecurityViolationError):
        decision_model.get_adaptive_context(
            task_intent="Inspect environment variables",
            parser=complex_parser,
            symbol_name=None,
            guard=default_guard,
        )
