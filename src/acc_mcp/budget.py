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


@dataclass
class RetrievalRecord:
    """Record of a single context retrieval event."""

    query_id: str
    timestamp: str
    file_path: str
    symbol_name: str | None
    task_intent: str
    served_tier: int
    baseline_tokens: int
    served_tokens: int
    tokens_saved: int
    savings_percentage: float
    requested_min_tier: int | None = None
    model_tier: int | None = None
    budget_limited: bool = False

    @property
    def was_escalated(self) -> bool:
        """True when the agent explicitly escalated above the model's choice."""
        if self.model_tier is not None and self.requested_min_tier is not None:
            return self.served_tier > self.model_tier
        return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievalRecord:
        return cls(
            query_id=data["query_id"],
            timestamp=data["timestamp"],
            file_path=data["file_path"],
            symbol_name=data.get("symbol_name"),
            task_intent=data.get("task_intent", ""),
            served_tier=int(data["served_tier"]),
            baseline_tokens=int(data["baseline_tokens"]),
            served_tokens=int(data["served_tokens"]),
            tokens_saved=int(data["tokens_saved"]),
            savings_percentage=float(data["savings_percentage"]),
            requested_min_tier=data.get("requested_min_tier"),
            model_tier=data.get("model_tier"),
            budget_limited=bool(data.get("budget_limited", False)),
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
        self.ledger_path: Path = Path(ledger_path or self.DEFAULT_LEDGER_FILE)
        self.auto_save = auto_save
        self.token_counter = token_counter or HeuristicTokenCounter()
        self.session_budget = self._resolve_session_budget(session_budget)
        self.history_limit = max(1, history_limit)

        self.history: list[RetrievalRecord] = []
        # symbol_cache: file_path -> {"file_hash": str, "symbols": {name: SymbolInfo}}
        self._symbol_cache: dict[str, dict[str, Any]] = {}

        self._total_requests = 0
        self._total_baseline_tokens = 0
        self._total_served_tokens = 0
        self._tier_counts: dict[int, int] = {1: 0, 2: 0, 3: 0}

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
    def cumulative_baseline_tokens(self) -> int:
        return self._total_baseline_tokens

    @property
    def cumulative_served_tokens(self) -> int:
        return self._total_served_tokens

    @property
    def cumulative_tokens_saved(self) -> int:
        return self.cumulative_baseline_tokens - self.cumulative_served_tokens

    @property
    def overall_savings_percentage(self) -> float:
        if self.cumulative_baseline_tokens == 0:
            return 0.0
        return round(
            (self.cumulative_tokens_saved / self.cumulative_baseline_tokens) * 100.0,
            2,
        )

    @property
    def tier_distribution(self) -> dict[int, int]:
        return dict(self._tier_counts)

    def _clear_totals(self) -> None:
        self._total_requests = 0
        self._total_baseline_tokens = 0
        self._total_served_tokens = 0
        self._tier_counts = {1: 0, 2: 0, 3: 0}

    def _add_to_totals(self, record: RetrievalRecord) -> None:
        self._total_requests += 1
        self._total_baseline_tokens += record.baseline_tokens
        self._total_served_tokens += record.served_tokens
        self._tier_counts[record.served_tier] = self._tier_counts.get(record.served_tier, 0) + 1

    def _trim_history(self) -> None:
        if len(self.history) > self.history_limit:
            del self.history[: -self.history_limit]

    def record_retrieval(
        self,
        file_path: str,
        task_intent: str,
        served_tier: int,
        baseline_content: str,
        served_content: str,
        symbol_name: str | None = None,
        query_id: str | None = None,
        requested_min_tier: int | None = None,
        model_tier: int | None = None,
        budget_limited: bool = False,
    ) -> RetrievalRecord:
        """Record context retrieval event, computing tokens and savings percentage.

        The served tokens count against the session budget.
        """
        q_id = query_id or f"q_{uuid.uuid4().hex[:8]}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        baseline_tokens = self.token_counter.count_tokens(baseline_content)
        served_tokens = self.token_counter.count_tokens(served_content)

        tokens_saved = max(0, baseline_tokens - served_tokens)
        savings_pct = (
            round((tokens_saved / baseline_tokens) * 100.0, 2)
            if baseline_tokens > 0
            else 0.0
        )

        record = RetrievalRecord(
            query_id=q_id,
            timestamp=now,
            file_path=file_path,
            symbol_name=symbol_name,
            task_intent=task_intent,
            served_tier=served_tier,
            baseline_tokens=baseline_tokens,
            served_tokens=served_tokens,
            tokens_saved=tokens_saved,
            savings_percentage=savings_pct,
            requested_min_tier=requested_min_tier,
            model_tier=model_tier,
            budget_limited=budget_limited,
        )

        self._add_to_totals(record)
        self.history.append(record)
        self._trim_history()
        self.session_served_tokens += served_tokens
        self.session_requests += 1

        if self.auto_save:
            self.save_ledger()

        return record

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
        """Return high-level session token accounting summary."""
        return {
            "total_requests": self.total_requests,
            "cumulative_baseline_tokens": self.cumulative_baseline_tokens,
            "cumulative_served_tokens": self.cumulative_served_tokens,
            "cumulative_tokens_saved": self.cumulative_tokens_saved,
            "overall_savings_percentage": self.overall_savings_percentage,
            "tier_distribution": self.tier_distribution,
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
            "version": "1.0",
            "summary": self.get_summary(),
            "totals": {
                "requests": self._total_requests,
                "baseline_tokens": self._total_baseline_tokens,
                "served_tokens": self._total_served_tokens,
                "tier_counts": self._tier_counts,
            },
            "history": [r.to_dict() for r in self.history],
            "symbol_cache": self._symbol_cache,
        }

        # Write to temporary file first then replace for atomicity
        temp_dest = dest.with_suffix(".tmp")
        with open(temp_dest, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        temp_dest.replace(dest)

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
                self._total_requests = int(totals["requests"])
                self._total_baseline_tokens = int(totals["baseline_tokens"])
                self._total_served_tokens = int(totals["served_tokens"])
                self._tier_counts.update(
                    {int(tier): int(n) for tier, n in totals.get("tier_counts", {}).items()}
                )
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
