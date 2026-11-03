"""Workspace Symbol Index with a hash-checked, restart-safe cache for ACC-MCP (Block 5)."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from acc_mcp.budget import ContextBudgetTracker, content_hash
from acc_mcp.guard import GuardRail
from acc_mcp.parser import ASTParser

logger = logging.getLogger(__name__)


@dataclass
class SymbolLocation:
    """Where a symbol is defined: file relative to the workspace root and its line range."""

    file_path: str
    symbol: str
    kind: str
    lineno: int
    end_lineno: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IndexStats:
    """Outcome of one refresh() pass over the workspace."""

    files_seen: int = 0
    files_parsed: int = 0
    files_reused: int = 0
    files_removed: int = 0


class WorkspaceIndex:
    """Symbol map of every servable `.py` file under the workspace root.

    The map is stored in the budget tracker's symbol cache, so it survives restarts. A file
    is parsed again only when the SHA-256 of its text differs from the cached `file_hash`.
    """

    SKIP_DIRS: frozenset[str] = frozenset({".venv", "venv", ".git", "__pycache__", "node_modules"})

    def __init__(self, guard: GuardRail, tracker: ContextBudgetTracker) -> None:
        self.guard = guard
        self.tracker = tracker
        self._files: list[Path] = []

    @property
    def root(self) -> Path:
        return self.guard.workspace_root

    def iter_source_files(self) -> list[Path]:
        """Real paths of all `.py` files under the root that the guard would serve."""
        found: dict[Path, None] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in self.SKIP_DIRS)
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                # Relative path, so the guard checks it against its own (unresolved) root too
                relative = (Path(dirpath) / filename).relative_to(self.root)
                if self.guard.is_protected_path(relative):
                    continue
                found.setdefault(self.guard.resolve_path(relative), None)
        return list(found)

    def refresh(self) -> IndexStats:
        """Re-parse new and changed files, reuse unchanged ones, drop deleted ones."""
        stats = IndexStats()
        files = self.iter_source_files()
        seen = {str(path) for path in files}

        for path in files:
            key = str(path)
            stats.files_seen += 1
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("Skipping unreadable file %s: %s", key, exc)
                continue

            if self.tracker.get_cached_hash(key) == content_hash(text):
                stats.files_reused += 1
                continue

            try:
                symbols = ASTParser(text, file_path=key).get_symbols()
            except (SyntaxError, ValueError) as exc:
                # Cached with no symbols, so a broken file is not re-parsed until it changes
                logger.warning("Indexing %s without symbols, it does not parse: %s", key, exc)
                symbols = {}
            self.tracker.cache_symbols(key, symbols, text, save=False)
            stats.files_parsed += 1

        for key in self.tracker.cached_files():
            if key not in seen and Path(key).is_relative_to(self.root):
                self.tracker.forget_symbols(key, save=False)
                stats.files_removed += 1

        if (stats.files_parsed or stats.files_removed) and self.tracker.auto_save:
            self.tracker.save_ledger()

        self._files = files
        return stats

    def find(self, name: str) -> list[SymbolLocation]:
        """Definitions matching a bare name (`authenticate`) or qualified name (`User.authenticate`).

        Uses the state of the last refresh().
        """
        matches: list[SymbolLocation] = []
        for path in self._files:
            symbols = self.tracker.get_cached_symbols(str(path)) or {}
            relative = path.relative_to(self.root).as_posix()
            # A method is cached under both `Class.method` and `method`; report it once
            reported: set[str] = set()
            for info in symbols.values():
                if info.name in reported:
                    continue
                if info.name == name or info.name.split(".")[-1] == name:
                    reported.add(info.name)
                    matches.append(
                        SymbolLocation(
                            file_path=relative,
                            symbol=info.name,
                            kind=info.kind,
                            lineno=info.lineno,
                            end_lineno=info.end_lineno,
                        )
                    )
        matches.sort(key=lambda m: (m.file_path, m.lineno))
        return matches
