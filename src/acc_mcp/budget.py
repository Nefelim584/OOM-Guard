"""Context Budget Tracker & Persistent Ledger for ACC-MCP (Block 5)."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from acc_mcp.parser import SymbolInfo
from acc_mcp.tokens import HeuristicTokenCounter, TokenCounter

logger = logging.getLogger(__name__)


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
    )


class ContextBudgetTracker:
    """Multi-turn token consumption and savings tracker with persistent ledger."""

    DEFAULT_LEDGER_FILE: str = "context_budget.json"

    def __init__(
        self,
        ledger_path: str | Path | None = None,
        auto_save: bool = True,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.ledger_path: Path = Path(ledger_path or self.DEFAULT_LEDGER_FILE)
        self.auto_save = auto_save
        self.token_counter = token_counter or HeuristicTokenCounter()

        self.history: list[RetrievalRecord] = []
        # symbol_cache: file_path -> {"file_hash": str, "symbols": {name: SymbolInfo}}
        self._symbol_cache: dict[str, dict[str, Any]] = {}

        # If existing ledger file exists, load state
        if self.ledger_path.exists():
            self.load_ledger()

    @property
    def total_requests(self) -> int:
        return len(self.history)

    @property
    def cumulative_baseline_tokens(self) -> int:
        return sum(r.baseline_tokens for r in self.history)

    @property
    def cumulative_served_tokens(self) -> int:
        return sum(r.served_tokens for r in self.history)

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
        dist = {1: 0, 2: 0, 3: 0}
        for r in self.history:
            dist[r.served_tier] = dist.get(r.served_tier, 0) + 1
        return dist

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
    ) -> RetrievalRecord:
        """Record context retrieval event, computing tokens and savings percentage."""
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
        )

        self.history.append(record)

        if self.auto_save:
            self.save_ledger()

        return record

    def cache_symbols(
        self,
        file_path: str,
        symbols: dict[str, SymbolInfo],
        file_content: str | None = None,
    ) -> None:
        """Cache cataloged symbols and AST statistics for file_path."""
        content_hash = ""
        if file_content is not None:
            content_hash = hashlib.sha256(file_content.encode("utf-8")).hexdigest()

        serialized_symbols = {
            name: _symbol_info_to_dict(info) for name, info in symbols.items()
        }

        self._symbol_cache[file_path] = {
            "file_hash": content_hash,
            "cached_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "symbols": serialized_symbols,
        }

        if self.auto_save:
            self.save_ledger()

    def is_file_cached(self, file_path: str) -> bool:
        """Check if symbol map exists in cache for file_path."""
        return file_path in self._symbol_cache

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
        }

    def save_ledger(self, target_path: str | Path | None = None) -> None:
        """Persist session token accounting ledger and symbol cache to JSON."""
        dest = Path(target_path or self.ledger_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "version": "1.0",
            "summary": self.get_summary(),
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
            return True
        except Exception as exc:
            logger.warning("Failed to load ledger from %s: %s", src, exc)
            return False

    def reset(self) -> None:
        """Reset in-memory counters, history, and cache."""
        self.history.clear()
        self._symbol_cache.clear()
        if self.ledger_path.exists():
            try:
                self.ledger_path.unlink()
            except Exception:
                pass
