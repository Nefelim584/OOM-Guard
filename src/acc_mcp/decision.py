"""Decision Model & Adaptive Context Tier Selection Engine for ACC-MCP (Block 4)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acc_mcp.guard import GuardRail
    from acc_mcp.parser import ASTParser


@dataclass
class ContextFeatures:
    """Feature vector (x1, x2, x3, x4) for context tier classification."""

    task_depth: int  # x_1 in {0, 1, 2}
    cyclomatic_complexity: int  # x_2 in N
    in_degree: int  # x_3 in N
    has_annotations: int  # x_4 in {0, 1}

    def to_tuple(self) -> tuple[int, int, int, int]:
        return (
            self.task_depth,
            self.cyclomatic_complexity,
            self.in_degree,
            self.has_annotations,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "x1_task_depth": self.task_depth,
            "x2_cyclomatic_complexity": self.cyclomatic_complexity,
            "x3_in_degree": self.in_degree,
            "x4_has_annotations": self.has_annotations,
        }


@dataclass
class DecisionResult:
    """Outcome of DecisionModel evaluation."""

    tier: int  # 1, 2, or 3
    probability: float  # P(Full Context Needed)
    logit: float  # z
    features: ContextFeatures
    reasoning: str


class TaskIntentClassifier:
    """Classifies developer task intent into task depth x_1 in {0, 1, 2}."""

    # Level 2: Full algorithmic rewrite, debugging deep logic, optimization, fixing bugs
    DEPTH_2_PATTERNS = [
        r"\brewrite\b",
        r"\breimplement\b",
        r"\bfix\b",
        r"\bbug\b",
        r"\bdebug\b",
        r"\boptimize\b",
        r"\balgorithm\b",
        r"\blogic\b",
        r"\bimplement\b",
        r"\boverhaul\b",
        r"\bcrash\b",
        r"\bperformance\b",
        r"\brace\s+condition\b",
        r"\bmemory\s+leak\b",
        r"\bexception\b",
        r"\bfailed\b",
        r"\berror\b",
    ]

    # Level 1: Refactoring, patching, call-graph analysis, caller/callee tracing, modifying signatures
    DEPTH_1_PATTERNS = [
        r"\brefactor\b",
        r"\brename\b",
        r"\bpatch\b",
        r"\bdeprecate\b",
        r"\btrace\b",
        r"\bcaller\b",
        r"\bcallee\b",
        r"\bcall\s+graph\b",
        r"\bdependenc\w*\b",
        r"\bwrapper\b",
        r"\bwire\b",
        r"\bmigrat\w*\b",
        r"\bmodify\b",
        r"\bchange\b",
        r"\bextend\b",
    ]

    # Level 0: Pure inspection, verification, checking signatures, docstrings, type hints
    DEPTH_0_PATTERNS = [
        r"\binspect\b",
        r"\bcheck\b",
        r"\bview\b",
        r"\bshow\b",
        r"\blook\s+at\b",
        r"\bfind\b",
        r"\bwhat\s+is\b",
        r"\bhow\s+to\s+call\b",
        r"\bhow\s+to\s+use\b",
        r"\bcall\b",
        r"\binvoke\b",
        r"\busage\b",
        r"\bsignature\b",
        r"\btype\b",
        r"\bdocstring\b",
        r"\bargument\b",
        r"\bparameter\b",
        r"\binterface\b",
        r"\blist\b",
    ]

    def __init__(self) -> None:
        self._compiled_depth2 = [re.compile(p, re.IGNORECASE) for p in self.DEPTH_2_PATTERNS]
        self._compiled_depth1 = [re.compile(p, re.IGNORECASE) for p in self.DEPTH_1_PATTERNS]
        self._compiled_depth0 = [re.compile(p, re.IGNORECASE) for p in self.DEPTH_0_PATTERNS]

    def classify(self, task_intent: str) -> int:
        """Extract task intent depth x_1 in {0, 1, 2}.

        Precedence:
        1. Explicit rewrite/bug fix/logic overhaul -> 2
        2. Interface refactoring/caller tracing/patching -> 1
        3. Inspection/signature check/calling -> 0
        Default fallback: 1 (safe intermediate tier)
        """
        text = task_intent.strip()
        if not text:
            return 0

        # Check Depth 2 first (highest need for implementation code)
        if any(regex.search(text) for regex in self._compiled_depth2):
            return 2

        # Check Depth 1 (need for dependencies and call graph)
        if any(regex.search(text) for regex in self._compiled_depth1):
            return 1

        # Check Depth 0 (pure interface observation)
        if any(regex.search(text) for regex in self._compiled_depth0):
            return 0

        return 1


class DecisionModel:
    """Logistic scoring model and adaptive tier selection policy (Block 4)."""

    DEFAULT_W0: float = -2.0  # Base intercept
    DEFAULT_W1: float = 2.0  # Weight for task intent depth x1
    DEFAULT_W2: float = 0.2  # Weight for cyclomatic complexity x2
    DEFAULT_W3: float = 0.3  # Weight for in-degree x3
    DEFAULT_W4: float = 1.0  # Weight for annotations/docstrings x4

    DEFAULT_THETA_LOW: float = 0.30
    DEFAULT_THETA_HIGH: float = 0.65

    def __init__(
        self,
        w0: float = DEFAULT_W0,
        w1: float = DEFAULT_W1,
        w2: float = DEFAULT_W2,
        w3: float = DEFAULT_W3,
        w4: float = DEFAULT_W4,
        theta_low: float = DEFAULT_THETA_LOW,
        theta_high: float = DEFAULT_THETA_HIGH,
        intent_classifier: TaskIntentClassifier | None = None,
    ) -> None:
        self.w0 = w0
        self.w1 = w1
        self.w2 = w2
        self.w3 = w3
        self.w4 = w4
        self.theta_low = theta_low
        self.theta_high = theta_high
        self.intent_classifier = intent_classifier or TaskIntentClassifier()

    def extract_features(
        self,
        task_intent: str,
        parser: ASTParser,
        symbol_name: str | None = None,
    ) -> ContextFeatures:
        """Compile feature vector (x1, x2, x3, x4) from query and AST parser metrics."""
        x1 = self.intent_classifier.classify(task_intent)

        if symbol_name:
            info = parser.get_symbol_info(symbol_name)
            if info:
                x2 = info.cyclomatic_complexity
                x3 = info.in_degree
                x4 = 1 if (info.has_type_hints and bool(info.docstring)) else 0
                return ContextFeatures(
                    task_depth=x1,
                    cyclomatic_complexity=x2,
                    in_degree=x3,
                    has_annotations=x4,
                )

        # Whole module aggregate metrics
        symbols = parser.get_symbols()
        if symbols:
            x2 = max(s.cyclomatic_complexity for s in symbols.values())
            x3 = max(s.in_degree for s in symbols.values())
            annotated_count = sum(1 for s in symbols.values() if s.has_type_hints and s.docstring)
            x4 = 1 if (annotated_count >= (len(symbols) / 2)) else 0
        else:
            x2 = 1
            x3 = 0
            x4 = 0

        return ContextFeatures(
            task_depth=x1,
            cyclomatic_complexity=x2,
            in_degree=x3,
            has_annotations=x4,
        )

    def compute_logit(self, features: ContextFeatures) -> float:
        """Compute logistic score z = w0 + w1*x1 + w2*x2 + w3*x3 - w4*x4."""
        return (
            self.w0
            + self.w1 * features.task_depth
            + self.w2 * features.cyclomatic_complexity
            + self.w3 * features.in_degree
            - self.w4 * features.has_annotations
        )

    def compute_probability(self, logit: float) -> float:
        """Compute P(Full Context Needed) = 1 / (1 + e^(-z)) with numerical stability."""
        if logit >= 40:
            return 1.0
        if logit <= -40:
            return 0.0
        return 1.0 / (1.0 + math.exp(-logit))

    def evaluate(self, features: ContextFeatures) -> DecisionResult:
        """Evaluate features, determine logit, probability, and select context tier."""
        z = self.compute_logit(features)
        prob = self.compute_probability(z)

        if prob < self.theta_low:
            tier = 1
            reasoning = (
                f"P(Full)={prob:.3f} < theta_low ({self.theta_low}): "
                "Interface signatures and docstrings suffice."
            )
        elif prob < self.theta_high:
            tier = 2
            reasoning = (
                f"theta_low ({self.theta_low}) <= P(Full)={prob:.3f} < theta_high ({self.theta_high}): "
                "Call-graph skeleton and dependency slice required."
            )
        else:
            tier = 3
            reasoning = (
                f"P(Full)={prob:.3f} >= theta_high ({self.theta_high}): "
                "Full implementation body strictly required."
            )

        return DecisionResult(
            tier=tier,
            probability=prob,
            logit=z,
            features=features,
            reasoning=reasoning,
        )

    def select_tier(
        self,
        task_intent: str,
        parser: ASTParser,
        symbol_name: str | None = None,
    ) -> DecisionResult:
        """End-to-end tier selection given task intent and code parser."""
        features = self.extract_features(task_intent, parser, symbol_name)
        return self.evaluate(features)

    def get_adaptive_context(
        self,
        task_intent: str,
        parser: ASTParser,
        symbol_name: str | None = None,
        guard: GuardRail | None = None,
    ) -> tuple[str, DecisionResult]:
        """Generate optimal adaptive context slice according to predicted tier and guardrails."""
        decision = self.select_tier(task_intent, parser, symbol_name)

        if decision.tier == 1:
            raw_context = parser.get_tier1_context(symbol_name=symbol_name)
        elif decision.tier == 2:
            raw_context = parser.get_tier2_context(symbol_name=symbol_name)
        else:
            raw_context = parser.get_tier3_context(symbol_name=symbol_name)

        if guard is not None:
            final_context = guard.protect(
                file_path=parser.file_path or "context.py",
                context=raw_context,
            )
        else:
            final_context = raw_context

        return final_context, decision
