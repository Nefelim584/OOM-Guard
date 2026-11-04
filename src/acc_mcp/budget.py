"""Context Budget Tracker & Persistent Ledger for ACC-MCP (Block 5)."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from acc_mcp.parser import SymbolInfo
from acc_mcp.tokens import HeuristicTokenCounter, TokenCounter

logger = logging.getLogger(__name__)


# Bump when the cached symbol fields change, so entries written by an older parser are rebuilt
SYMBOL_CACHE_SCHEMA = 2


def content_hash(file_content: str) -> str:
    """SHA-256 of file text, used to tell whether a cached symbol map is still valid."""
    return hashlib.sha256(file_content.encode("utf-8")).hexdigest()


# Tools whose output the ledger counts; only get_adaptive_context requests go into history
SLICE_TOOL = "get_adaptive_context"
LOOKUP_TOOL = "find_symbol"
TRACKED_TOOLS = (SLICE_TOOL, LOOKUP_TOOL)


@dataclass(frozen=True)
class ServedRange:
    """Lines of one symbol (or import block, class header) shown in a slice.

    For Tier 3 these are the verbatim lines served, so a slice cut by the ceiling ends
    where the cut is. For Tier 1 and 2 they are the span of each symbol whose interface
    is shown (the body itself is not).
    """

    file: str
    symbol: str
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServedRange:
        return cls(
            file=data["file"],
            symbol=data["symbol"],
            start=int(data["start"]),
            end=int(data["end"]),
        )


@dataclass
class RetrievalRecord:
    """Record of a single get_adaptive_context request.

    `whole_file_tokens` and `upper_bound_savings_pct` compare the slice with reading the
    whole file. Agents rarely do that (they grep and read windows), so this is an upper
    bound on the saving, not the saving against what the agent would really have read.
    """

    query_id: str
    timestamp: str
    file_path: str
    symbol_name: str | None
    task_intent: str
    served_tier: int
    whole_file_tokens: int
    served_tokens: int
    upper_bound_savings_pct: float
    requested_min_tier: int | None = None
    model_tier: int | None = None
    budget_limited: bool = False
    line_ranges: list[ServedRange] = field(default_factory=list)

    @property
    def was_escalated(self) -> bool:
        """True when the agent asked with min_tier for more than the model's choice."""
        if self.model_tier is not None and self.requested_min_tier is not None:
            return self.requested_min_tier > self.model_tier
        return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievalRecord:
        # Ledgers written before Phase 23 use baseline_tokens / savings_percentage
        whole_file = data.get("whole_file_tokens", data.get("baseline_tokens", 0))
        pct = data.get("upper_bound_savings_pct", data.get("savings_percentage", 0.0))
        return cls(
            query_id=data["query_id"],
            timestamp=data["timestamp"],
            file_path=data["file_path"],
            symbol_name=data.get("symbol_name"),
            task_intent=data.get("task_intent", ""),
            served_tier=int(data["served_tier"]),
            whole_file_tokens=int(whole_file),
            served_tokens=int(data["served_tokens"]),
            upper_bound_savings_pct=float(pct),
            requested_min_tier=data.get("requested_min_tier"),
            model_tier=data.get("model_tier"),
            budget_limited=bool(data.get("budget_limited", False)),
            line_ranges=[ServedRange.from_dict(r) for r in data.get("line_ranges", [])],
        )


def _symbol_info_to_dict(info: SymbolInfo) -> dict[str, Any]:
    """Serialize SymbolInfo dataclass to JSON-friendly dict."""
    return {
        "name": info.name,
        "kind": info.kind,
        "lineno": info.lineno,
        "end_lineno": info.end_lineno,
        "docstring": info.docstring,
        "has_type_hints": info.has_type_hints,
        "parent_class": info.parent_class,
        "cyclomatic_complexity": info.cyclomatic_complexity,
        "callers": sorted(list(info.callers)),
        "callees": sorted(list(info.callees)),
        "raised_exceptions": sorted(list(info.raised_exceptions)),
        "possible_callers": sorted(info.possible_callers),
        "external_calls": dict(sorted(info.external_calls.items())),
        "possible_calls": dict(sorted(info.possible_calls.items())),
        "in_degree": info.in_degree,
    }


def _dict_to_symbol_info(data: dict[str, Any]) -> SymbolInfo:
    """Deserialize dict back into SymbolInfo dataclass."""
    return SymbolInfo(
        name=data["name"],
        kind=data["kind"],
        lineno=int(data["lineno"]),
        end_lineno=int(data["end_lineno"]),
        docstring=data.get("docstring"),
        has_type_hints=bool(data.get("has_type_hints", False)),
        parent_class=data.get("parent_class"),
        cyclomatic_complexity=int(data.get("cyclomatic_complexity", 1)),
        callers=set(data.get("callers", [])),
        callees=set(data.get("callees", [])),
        raised_exceptions=set(data.get("raised_exceptions", [])),
        possible_callers=set(data.get("possible_callers", [])),
        external_calls={k: int(v) for k, v in data.get("external_calls", {}).items()},
        possible_calls={k: int(v) for k, v in data.get("possible_calls", {}).items()},
    )


class ContextBudgetTracker:
    """Multi-turn token consumption and savings tracker with persistent ledger.

    Totals cover every request ever recorded and survive restarts; `history` keeps only the
    last `history_limit` records. The session counters cover the tokens served since the
    tracker was created or `reset_session()` was called, and are not persisted, because a
    new server process means a new agent context.
    """

    DEFAULT_LEDGER_FILE: str = "context_budget.json"
    LEDGER_ENV_VAR: str = "ACC_MCP_LEDGER"
    DEFAULT_SESSION_BUDGET: int = 6000
    SESSION_BUDGET_ENV_VAR: str = "ACC_MCP_SESSION_BUDGET"
    DEFAULT_HISTORY_LIMIT: int = 200

    def __init__(
        self,
        ledger_path: str | Path | None = None,
        auto_save: bool = True,
        token_counter: TokenCounter | None = None,
        session_budget: int | None = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> None:
        # Argument, then the env var (one ledger per evaluation run), then ./context_budget.json
        self.ledger_path: Path = Path(
            ledger_path or os.environ.get(self.LEDGER_ENV_VAR) or self.DEFAULT_LEDGER_FILE
        )
        self.auto_save = auto_save
        self.token_counter = token_counter or HeuristicTokenCounter()
        self.session_budget = self._resolve_session_budget(session_budget)
        self.history_limit = max(1, history_limit)

        self.history: list[RetrievalRecord] = []
        # symbol_cache: file_path -> {"file_hash": str, "symbols": {name: SymbolInfo}}
        self._symbol_cache: dict[str, dict[str, Any]] = {}

        self._clear_totals()

        self.session_served_tokens = 0
        self.session_requests = 0

        # If existing ledger file exists, load state
        if self.ledger_path.exists():
            self.load_ledger()

    @classmethod
    def _resolve_session_budget(cls, session_budget: int | None) -> int:
        """Session budget from the argument, then the env var, then the default."""
        raw: Any = session_budget
        if raw is None:
            raw = os.environ.get(cls.SESSION_BUDGET_ENV_VAR) or cls.DEFAULT_SESSION_BUDGET
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            raise ValueError(
                f"Session budget must be a positive integer "
                f"(argument or {cls.SESSION_BUDGET_ENV_VAR}), got {raw!r}"
            )
        return value

    @property
    def remaining_session_tokens(self) -> int:
        return max(0, self.session_budget - self.session_served_tokens)

    def reset_session(self) -> None:
        """Start a new session with the full budget; totals and history are kept."""
        self.session_served_tokens = 0
        self.session_requests = 0

    @property
    def total_requests(self) -> int:
        return self._total_requests

    @property
    def whole_file_tokens(self) -> int:
        """Tokens of the whole files the slices were cut from (upper-bound comparison only)."""
        return self._total_whole_file_tokens

    @property
    def cumulative_served_tokens(self) -> int:
        """Tokens of all get_adaptive_context slices served."""
        return self._total_served_tokens

    @property
    def upper_bound_savings_pct(self) -> float:
        """Saving if the agent would otherwise have read every whole file: an upper bound."""
        if self._total_whole_file_tokens == 0:
            return 0.0
        saved = max(0, self._total_whole_file_tokens - self._total_served_tokens)
        return round(saved / self._total_whole_file_tokens * 100.0, 2)

    @property
    def tier_distribution(self) -> dict[int, int]:
        return dict(self._tier_counts)

    @property
    def served_tokens_by_tier(self) -> dict[int, int]:
        return dict(self._tier_served_tokens)

    @property
    def served_tokens_by_tool(self) -> dict[str, dict[str, int]]:
        """Requests and output tokens per ACC-MCP tool."""
        return {tool: dict(totals) for tool, totals in self._tool_totals.items()}

    @property
    def total_escalations(self) -> int:
        return self._total_escalations

    @property
    def escalation_rate(self) -> float:
        """Share of requests where the agent raised min_tier above the model's tier.

        Each escalation follows a slice the agent found insufficient, so this is a
        real-world miss-rate signal for the decision model.
        """
        if self._total_requests == 0:
            return 0.0
        return round(self._total_escalations / self._total_requests, 4)

    def _clear_totals(self) -> None:
        self._total_requests = 0
        self._total_whole_file_tokens = 0
        self._total_served_tokens = 0
        self._tier_counts: dict[int, int] = {1: 0, 2: 0, 3: 0}
        self._tier_served_tokens: dict[int, int] = {1: 0, 2: 0, 3: 0}
        self._tool_totals: dict[str, dict[str, int]] = {
            tool: {"requests": 0, "served_tokens": 0} for tool in TRACKED_TOOLS
        }
        self._total_escalations = 0

    def _add_tool_output(self, tool: str, served_tokens: int) -> None:
        totals = self._tool_totals.setdefault(tool, {"requests": 0, "served_tokens": 0})
        totals["requests"] += 1
        totals["served_tokens"] += served_tokens

    def _add_to_totals(self, record: RetrievalRecord) -> None:
        tier = record.served_tier
        self._total_requests += 1
        self._total_whole_file_tokens += record.whole_file_tokens
        self._total_served_tokens += record.served_tokens
        self._tier_counts[tier] = self._tier_counts.get(tier, 0) + 1
        self._tier_served_tokens[tier] = self._tier_served_tokens.get(tier, 0) + record.served_tokens
        self._add_tool_output(SLICE_TOOL, record.served_tokens)
        self._total_escalations += int(record.was_escalated)

    def _trim_history(self) -> None:
        if len(self.history) > self.history_limit:
            del self.history[: -self.history_limit]

    def record_retrieval(
        self,
        file_path: str,
        task_intent: str,
        served_tier: int,
        whole_file_content: str,
        served_content: str,
        symbol_name: str | None = None,
        query_id: str | None = None,
        requested_min_tier: int | None = None,
        model_tier: int | None = None,
        budget_limited: bool = False,
        line_ranges: list[ServedRange] | None = None,
    ) -> RetrievalRecord:
        """Record a get_adaptive_context request: served tokens, line ranges, and the
        whole-file size for the upper-bound comparison.

        The served tokens count against the session budget.
        """
        q_id = query_id or f"q_{uuid.uuid4().hex[:8]}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        whole_file_tokens = self.token_counter.count_tokens(whole_file_content)
        served_tokens = self.token_counter.count_tokens(served_content)
        upper_bound_pct = (
            round(max(0, whole_file_tokens - served_tokens) / whole_file_tokens * 100.0, 2)
            if whole_file_tokens > 0
            else 0.0
        )

        record = RetrievalRecord(
            query_id=q_id,
            timestamp=now,
            file_path=file_path,
            symbol_name=symbol_name,
            task_intent=task_intent,
            served_tier=served_tier,
            whole_file_tokens=whole_file_tokens,
            served_tokens=served_tokens,
            upper_bound_savings_pct=upper_bound_pct,
            requested_min_tier=requested_min_tier,
            model_tier=model_tier,
            budget_limited=budget_limited,
            line_ranges=list(line_ranges or []),
        )

        self._add_to_totals(record)
        self.history.append(record)
        self._trim_history()
        self.session_served_tokens += served_tokens
        self.session_requests += 1

        if self.auto_save:
            self.save_ledger()

        return record

    def record_tool_output(self, tool: str, served_content: str) -> int:
        """Count the output tokens of a tool that serves no slice (e.g. find_symbol).

        It goes into the per-tool totals only: not into history, tier counts, or the
        session budget. Returns the token count.
        """
        served_tokens = self.token_counter.count_tokens(served_content)
        self._add_tool_output(tool, served_tokens)
        if self.auto_save:
            self.save_ledger()
        return served_tokens

    def cache_symbols(
        self,
        file_path: str,
        symbols: dict[str, SymbolInfo],
        file_content: str | None = None,
        save: bool = True,
    ) -> None:
        """Cache cataloged symbols and AST statistics for file_path.

        Pass save=False when caching many files in a row and call save_ledger() once at the end.
        """
        serialized_symbols = {
            name: _symbol_info_to_dict(info) for name, info in symbols.items()
        }

        self._symbol_cache[file_path] = {
            "schema": SYMBOL_CACHE_SCHEMA,
            "file_hash": content_hash(file_content) if file_content is not None else "",
            "cached_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "symbols": serialized_symbols,
        }

        if save and self.auto_save:
            self.save_ledger()

    def forget_symbols(self, file_path: str, save: bool = True) -> None:
        """Drop the cached symbol map for file_path (e.g. after the file was deleted)."""
        if self._symbol_cache.pop(file_path, None) is not None and save and self.auto_save:
            self.save_ledger()

    def is_file_cached(self, file_path: str) -> bool:
        """Check if symbol map exists in cache for file_path."""
        return file_path in self._symbol_cache

    def cached_files(self) -> list[str]:
        """File paths that currently have a cached symbol map."""
        return list(self._symbol_cache)

    def get_cached_hash(self, file_path: str) -> str | None:
        """SHA-256 the cached symbol map of file_path was built from, if any.

        Entries written with an older SYMBOL_CACHE_SCHEMA report no hash, so they get re-parsed.
        """
        entry = self._symbol_cache.get(file_path)
        if not entry or entry.get("schema") != SYMBOL_CACHE_SCHEMA:
            return None
        return entry.get("file_hash") or None

    def get_cached_symbols(self, file_path: str) -> dict[str, SymbolInfo] | None:
        """Retrieve deserialized SymbolInfo map for file_path if available."""
        entry = self._symbol_cache.get(file_path)
        if not entry:
            return None

        raw_symbols = entry.get("symbols", {})
        return {name: _dict_to_symbol_info(d) for name, d in raw_symbols.items()}

    def get_cached_symbol_info(self, file_path: str, symbol_name: str) -> SymbolInfo | None:
        """Retrieve specific SymbolInfo for symbol_name from cached file."""
        symbols = self.get_cached_symbols(file_path)
        if not symbols:
            return None
        if symbol_name in symbols:
            return symbols[symbol_name]
        for name, info in symbols.items():
            if name.split(".")[-1] == symbol_name:
                return info
        return None

    def get_summary(self) -> dict[str, Any]:
        """Return high-level token accounting summary.

        `total_requests` and `cumulative_served_tokens` count get_adaptive_context slices;
        `served_tokens_by_tool` also has find_symbol. `whole_file_tokens` and
        `upper_bound_savings_pct` are an upper bound, not a measured saving.
        """
        return {
            "total_requests": self.total_requests,
            "cumulative_served_tokens": self.cumulative_served_tokens,
            "served_tokens_by_tier": self.served_tokens_by_tier,
            "served_tokens_by_tool": self.served_tokens_by_tool,
            "whole_file_tokens": self.whole_file_tokens,
            "upper_bound_savings_pct": self.upper_bound_savings_pct,
            "tier_distribution": self.tier_distribution,
            "escalations": self.total_escalations,
            "escalation_rate": self.escalation_rate,
            "cached_files_count": len(self._symbol_cache),
            "history_records": len(self.history),
            "history_limit": self.history_limit,
            "session_budget": self.session_budget,
            "session_served_tokens": self.session_served_tokens,
            "session_remaining_tokens": self.remaining_session_tokens,
            "session_requests": self.session_requests,
        }

    def save_ledger(self, target_path: str | Path | None = None) -> None:
        """Persist session token accounting ledger and symbol cache to JSON."""
        dest = Path(target_path or self.ledger_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "version": "1.1",
            "summary": self.get_summary(),
            "totals": {
                "requests": self._total_requests,
                "whole_file_tokens": self._total_whole_file_tokens,
                "served_tokens": self._total_served_tokens,
                "tier_counts": self._tier_counts,
                "tier_served_tokens": self._tier_served_tokens,
                "tools": self._tool_totals,
                "escalations": self._total_escalations,
            },
            "history": [r.to_dict() for r in self.history],
            "symbol_cache": self._symbol_cache,
        }

        # Write to temporary file first then replace for atomicity
        temp_dest = dest.with_suffix(".tmp")
        with open(temp_dest, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        temp_dest.replace(dest)

    def _load_totals(self, totals: dict[str, Any]) -> None:
        """Restore stored totals; also reads ledgers written before Phase 23."""
        self._total_requests = int(totals["requests"])
        self._total_whole_file_tokens = int(
            totals.get("whole_file_tokens", totals.get("baseline_tokens", 0))
        )
        self._total_served_tokens = int(totals["served_tokens"])
        self._tier_counts.update(
            {int(tier): int(n) for tier, n in totals.get("tier_counts", {}).items()}
        )
        self._total_escalations = int(
            totals.get("escalations", sum(r.was_escalated for r in self.history))
        )

        if "tier_served_tokens" in totals:
            self._tier_served_tokens.update(
                {int(tier): int(n) for tier, n in totals["tier_served_tokens"].items()}
            )
        else:
            # Not stored before: recount from history (covers only the records still kept)
            for record in self.history:
                tier = record.served_tier
                self._tier_served_tokens[tier] = (
                    self._tier_served_tokens.get(tier, 0) + record.served_tokens
                )

        if "tools" in totals:
            for tool, counts in totals["tools"].items():
                self._tool_totals[tool] = {
                    "requests": int(counts.get("requests", 0)),
                    "served_tokens": int(counts.get("served_tokens", 0)),
                }
        else:
            # Older servers recorded only get_adaptive_context, and find_symbol output not at all
            self._tool_totals[SLICE_TOOL] = {
                "requests": self._total_requests,
                "served_tokens": self._total_served_tokens,
            }

    def load_ledger(self, source_path: str | Path | None = None) -> bool:
        """Load session token accounting ledger and cached symbol maps from JSON."""
        src = Path(source_path or self.ledger_path)
        if not src.exists():
            return False

        try:
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)

            raw_history = data.get("history", [])
            self.history = [RetrievalRecord.from_dict(d) for d in raw_history]
            self._symbol_cache = data.get("symbol_cache", {})

            self._clear_totals()
            totals = data.get("totals")
            if totals:
                self._load_totals(totals)
            else:
                # Ledgers written before totals were stored kept the full history
                for record in self.history:
                    self._add_to_totals(record)
            self._trim_history()
            return True
        except Exception as exc:
            logger.warning("Failed to load ledger from %s: %s", src, exc)
            return False

    def reset(self) -> None:
        """Reset in-memory counters, history, and cache."""
        self.history.clear()
        self._symbol_cache.clear()
        self._clear_totals()
        self.reset_session()
        if self.ledger_path.exists():
            try:
                self.ledger_path.unlink()
            except Exception:
                pass
