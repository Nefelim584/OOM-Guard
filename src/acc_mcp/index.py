"""Workspace Symbol Index with a hash-checked, restart-safe cache for ACC-MCP (Block 5)."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from acc_mcp.budget import ContextBudgetTracker, content_hash
from acc_mcp.guard import GuardRail
from acc_mcp.parser import ASTParser, CallerRef, SymbolInfo

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

    @property
    def changed(self) -> bool:
        """True when the symbol cache was modified and the ledger needs saving."""
        return bool(self.files_parsed or self.files_removed)


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
        # Derived from the cache; cleared whenever refresh() sees a change
        self._modules: dict[str, Path] | None = None
        self._symbol_maps: dict[str, dict[str, SymbolInfo]] = {}
        self._callers: dict[tuple[str, str], dict[tuple[str, str], CallerRef]] | None = None
        self._possible_callers: dict[str, list[CallerRef]] | None = None

    @property
    def root(self) -> Path:
        return self.guard.workspace_root

    def module_name(self, path: Path) -> str:
        """Dotted module name of a file from the workspace root: `pkg/utils.py` -> `pkg.utils`."""
        parts = list(path.relative_to(self.root).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def package_name(self, path: Path) -> str:
        """Package that relative imports in this file start from: `pkg/utils.py` -> `pkg`."""
        if path.name == "__init__.py":
            return self.module_name(path)
        return self.module_name(path).rpartition(".")[0]

    def relative_path(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

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

    def refresh(self, save: bool = True) -> IndexStats:
        """Re-parse new and changed files, reuse unchanged ones, drop deleted ones.

        Pass save=False when the caller saves the ledger itself later in the same request.
        """
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
                parser = ASTParser(text, file_path=key, package=self.package_name(path))
                symbols = parser.get_symbols()
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

        if stats.changed and save and self.tracker.auto_save:
            self.tracker.save_ledger()

        if stats.changed or files != self._files:
            self._modules = None
            self._symbol_maps.clear()
            self._callers = None
            self._possible_callers = None
        self._files = files
        return stats

    def _symbols_of(self, path: Path) -> dict[str, SymbolInfo]:
        """Cached symbol map of an indexed file, deserialized once per change."""
        key = str(path)
        if key not in self._symbol_maps:
            self._symbol_maps[key] = self.tracker.get_cached_symbols(key) or {}
        return self._symbol_maps[key]

    def _unique_symbols(self, path: Path) -> list[SymbolInfo]:
        """Symbols of a file without the bare-name aliases of methods."""
        return [info for name, info in self._symbols_of(path).items() if info.name == name]

    def _module_paths(self, module: str) -> list[Path]:
        """Indexed files for an imported module; `acc_mcp.guard` also matches `src/acc_mcp/guard.py`."""
        if self._modules is None:
            self._modules = {self.module_name(path): path for path in self._files}
        if module in self._modules:
            return [self._modules[module]]
        return [path for name, path in self._modules.items() if name.endswith(f".{module}")]

    def resolve_reference(self, dotted: str) -> tuple[Path, str] | None:
        """File and qualified symbol a dotted call target refers to, e.g.
        `pkg.billing.Invoice.total` -> (pkg/billing.py, "Invoice.total"). None if not in the workspace."""
        parts = dotted.split(".")
        for split in range(len(parts) - 1, 0, -1):
            module, qualified = ".".join(parts[:split]), ".".join(parts[split:])
            for path in self._module_paths(module):
                info = self._symbols_of(path).get(qualified)
                if info is not None and info.name == qualified:
                    return path, qualified
        return None

    def _build_caller_maps(self) -> None:
        """Invert every file's external and possible calls into per-target caller lists."""
        callers: dict[tuple[str, str], dict[tuple[str, str], CallerRef]] = {}
        possible: dict[str, list[CallerRef]] = {}
        for path in self._files:
            relative = self.relative_path(path)
            for info in self._unique_symbols(path):
                for dotted, line in info.external_calls.items():
                    target = self.resolve_reference(dotted)
                    if target is None or target[0] == path:
                        continue
                    key = (str(target[0]), target[1])
                    callers.setdefault(key, {}).setdefault(
                        (relative, info.name), CallerRef(relative, info.name, line)
                    )
                for name, line in info.possible_calls.items():
                    possible.setdefault(name, []).append(CallerRef(relative, info.name, line))
        self._callers = callers
        self._possible_callers = possible

    def callers_of(self, path: Path, qualified: str) -> tuple[list[CallerRef], list[CallerRef]]:
        """Callers of a symbol in other files: (resolved, possible).

        Possible callers are `obj.method()` calls with an unknown receiver type; they are only
        reported for methods and never include a caller that is already resolved.
        """
        if self._callers is None or self._possible_callers is None:
            self._build_caller_maps()
        assert self._callers is not None and self._possible_callers is not None

        resolved = list(self._callers.get((str(path), qualified), {}).values())
        if "." not in qualified:
            return resolved, []
        relative = self.relative_path(path)
        known = {(c.file_path, c.symbol) for c in resolved}
        possible = [
            c for c in self._possible_callers.get(qualified.split(".")[-1], [])
            if c.file_path != relative and (c.file_path, c.symbol) not in known
        ]
        return resolved, possible

    def attach_cross_file_callers(self, path: Path, parser: ASTParser) -> None:
        """Fill cross_file_callers / possible_cross_file_callers on every symbol of a parsed file."""
        for name, info in parser.get_symbols().items():
            if info.name != name:
                continue
            info.cross_file_callers, info.possible_cross_file_callers = self.callers_of(path, name)

    def find(self, name: str) -> list[SymbolLocation]:
        """Definitions matching a bare name (`authenticate`) or qualified name (`User.authenticate`).

        Uses the state of the last refresh().
        """
        matches: list[SymbolLocation] = []
        for path in self._files:
            relative = self.relative_path(path)
            for info in self._unique_symbols(path):
                if info.name == name or info.name.split(".")[-1] == name:
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
