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


def _words(*forms: str) -> str:
    """Regex matching exactly one of the given word forms, so `add` does not match `address`."""
    return r"\b(?:" + "|".join(forms) + r")\b"


class TaskIntentClassifier:
    """Classifies developer task intent into task depth x_1 in {0, 1, 2}."""

    # Level 2: Full algorithmic rewrite, debugging deep logic, optimization, fixing implementations
    # Focuses on operation verbs requiring the function body, avoiding subject nouns like 'error' or 'bug'
    DEPTH_2_PATTERNS = [
        _words("fix", "fixes", "fixed", "fixing"),
        _words("debug", "debugs", "debugged", "debugging"),
        _words("rewrite", "rewrites", "rewrote", "rewritten", "rewriting"),
        _words("reimplement", "reimplements", "reimplemented", "reimplementing", "reimplementation"),
        _words(
            "optimize", "optimizes", "optimized", "optimizing", "optimization",
            "optimise", "optimises", "optimised", "optimising", "optimisation",
        ),
        _words("implement", "implements", "implemented", "implementing", "implementation", "implementations"),
        _words("overhaul", "overhauls", "overhauled", "overhauling"),
    ]

    # Level 1: Refactoring, patching, call-graph analysis, caller/callee tracing, modifying signatures
    DEPTH_1_PATTERNS = [
        _words("refactor", "refactors", "refactored", "refactoring"),
        _words("rename", "renames", "renamed", "renaming"),
        _words("patch", "patches", "patched", "patching"),
        _words("deprecate", "deprecates", "deprecated", "deprecating", "deprecation"),
        _words("trace", "traces", "traced", "tracing"),
        _words("caller", "callers", "callee", "callees"),
        r"\bcall\s*sites?\b",
        r"\bcall\s+graphs?\b",
        _words(
            "depend", "depends", "depended", "depending",
            "dependency", "dependencies", "dependent", "dependents",
        ),
        _words("wrapper", "wrappers"),
        _words("wire", "wires", "wired", "wiring"),
        _words("migrate", "migrates", "migrated", "migrating", "migration", "migrations"),
        _words("modify", "modifies", "modified", "modifying", "modification", "modifications"),
        _words("change", "changes", "changed", "changing"),
        _words("extend", "extends", "extended", "extending"),
        _words("add", "adds", "added", "adding"),
    ]

    # Level 0: Pure inspection, verification, checking signatures, docstrings, type hints
    DEPTH_0_PATTERNS = [
        _words("inspect", "inspects", "inspected", "inspecting", "inspection"),
        _words("check", "checks", "checked", "checking"),
        _words("view", "views", "viewed", "viewing"),
        _words("show", "shows", "showed", "shown", "showing"),
        r"\blook\s+at\b",
        _words("find", "finds", "finding"),
        r"\bwhat\s+is\b",
        r"\bhow\s+to\s+call\b",
        r"\bhow\s+to\s+use\b",
        r"\bcall(?:s|ing)?(?!\s*(?:site|graph))\b",
        _words("invoke", "invokes", "invoked", "invoking"),
        _words("usage", "usages"),
        _words("signature", "signatures"),
        _words("type", "types"),
        _words("docstring", "docstrings"),
        _words("argument", "arguments"),
        _words("parameter", "parameters"),
        _words("interface", "interfaces"),
        # `list[int]` is a type annotation, not a request to list something
        _words("list", "lists", "listed", "listing") + r"(?!\s*\[)",
    ]

    def __init__(self) -> None:
        self._compiled_depth2 = [re.compile(p, re.IGNORECASE) for p in self.DEPTH_2_PATTERNS]
        self._compiled_depth1 = [re.compile(p, re.IGNORECASE) for p in self.DEPTH_1_PATTERNS]
        self._compiled_depth0 = [re.compile(p, re.IGNORECASE) for p in self.DEPTH_0_PATTERNS]

    def classify(self, task_intent: str) -> int:
        """Extract task intent depth x_1 in {0, 1, 2}.

        Counts pattern matches for each depth level and selects the depth with the most hits.
        On a tie, selects the deeper level (giving too little context is worse than giving too much).
        Default fallback when no patterns match: 1 (safe intermediate tier).
        """
        text = task_intent.strip()
        if not text:
            return 0

        hits_2 = sum(1 for regex in self._compiled_depth2 if regex.search(text))
        hits_1 = sum(1 for regex in self._compiled_depth1 if regex.search(text))
        hits_0 = sum(1 for regex in self._compiled_depth0 if regex.search(text))

        max_hits = max(hits_2, hits_1, hits_0)
        if max_hits == 0:
            return 1

        # Tie-breaker: pick deeper depth
        if hits_2 == max_hits:
            return 2
        if hits_1 == max_hits:
            return 1
        return 0


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
        elif guard is not None:
            raw_context = parser.get_tier3_context(
                symbol_name=symbol_name,
                max_tokens=guard.max_tokens,
                token_counter=guard.token_counter,
            )
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
