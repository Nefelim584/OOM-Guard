"""AST Code Extractor and Context Compressor for ACC-MCP."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SymbolInfo:
    """Metadata describing a code symbol (function, method, class)."""

    name: str
    kind: str  # "function", "async_function", "class", "method"
    lineno: int
    end_lineno: int
    docstring: str | None = None
    has_type_hints: bool = False
    parent_class: str | None = None
    cyclomatic_complexity: int = 1
    callers: set[str] = field(default_factory=set)
    callees: set[str] = field(default_factory=set)
    raised_exceptions: set[str] = field(default_factory=set)


class Tier1Transformer(ast.NodeTransformer):
    """AST Transformer that strips function and method bodies, replacing them with `...`."""

    def __init__(self, target_symbol: str | None = None) -> None:
        super().__init__()
        self.target_symbol = target_symbol

    def _stub_body(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
        """Produce a stubbed body containing only the docstring (if present) and Ellipsis."""
        docstring = ast.get_docstring(node)
        new_body: list[ast.stmt] = []

        if docstring:
            new_body.append(
                ast.Expr(value=ast.Constant(value=docstring))
            )

        new_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
        return new_body

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST | None:
        """Stub function body with Ellipsis."""
        node_copy = ast.copy_location(
            ast.FunctionDef(
                name=node.name,
                args=node.args,
                body=self._stub_body(node),
                decorator_list=node.decorator_list,
                returns=node.returns,
                type_comment=node.type_comment,
            ),
            node,
        )
        return node_copy

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST | None:
        """Stub async function body with Ellipsis."""
        node_copy = ast.copy_location(
            ast.AsyncFunctionDef(
                name=node.name,
                args=node.args,
                body=self._stub_body(node),
                decorator_list=node.decorator_list,
                returns=node.returns,
                type_comment=node.type_comment,
            ),
            node,
        )
        return node_copy

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST | None:
        """Process class body to retain attributes, docstring, and stubbed methods."""
        docstring = ast.get_docstring(node)
        retained_body: list[ast.stmt] = []

        if docstring:
            retained_body.append(ast.Expr(value=ast.Constant(value=docstring)))

        target_base = self.target_symbol.split(".")[-1] if self.target_symbol else None

        for stmt in node.body:
            # Skip initial docstring statement as we handled it
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str) and stmt.value.value == docstring:
                continue

            # Retain class-level type annotations / assignments
            if isinstance(stmt, (ast.AnnAssign, ast.Assign)):
                retained_body.append(stmt)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if target_base is None or stmt.name == target_base or self.target_symbol == node.name:
                    transformed = self.visit(stmt)
                    if transformed is not None:
                        retained_body.append(transformed)  # type: ignore
            elif isinstance(stmt, ast.ClassDef):
                nested = self.visit(stmt)
                if nested is not None:
                    retained_body.append(nested)  # type: ignore

        if not retained_body:
            retained_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))

        return ast.copy_location(
            ast.ClassDef(
                name=node.name,
                bases=node.bases,
                keywords=node.keywords,
                body=retained_body,
                decorator_list=node.decorator_list,
            ),
            node,
        )


class ASTParser:
    """Parses Python source code and generates structural context slices."""

    def __init__(self, source_code: str, file_path: str = "") -> None:
        self.source_code = source_code
        self.file_path = file_path
        self._tree: ast.Module | None = None
        self._symbols: dict[str, SymbolInfo] = {}

    @property
    def tree(self) -> ast.Module:
        """Lazily parse and cache the AST tree."""
        if self._tree is None:
            self._tree = ast.parse(self.source_code, filename=self.file_path)
            self._index_symbols()
        return self._tree

    def _index_symbols(self) -> None:
        """Scan AST and catalog all classes, functions, and methods hierarchically."""
        if self._tree is None:
            return

        self._symbols.clear()
        for stmt in self._tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async_function" if isinstance(stmt, ast.AsyncFunctionDef) else "function"
                has_hints = bool(stmt.returns or any(arg.annotation for arg in stmt.args.args))
                docstring = ast.get_docstring(stmt)
                self._symbols[stmt.name] = SymbolInfo(
                    name=stmt.name,
                    kind=kind,
                    lineno=stmt.lineno,
                    end_lineno=getattr(stmt, "end_lineno", stmt.lineno),
                    docstring=docstring,
                    has_type_hints=has_hints,
                )
            elif isinstance(stmt, ast.ClassDef):
                docstring = ast.get_docstring(stmt)
                self._symbols[stmt.name] = SymbolInfo(
                    name=stmt.name,
                    kind="class",
                    lineno=stmt.lineno,
                    end_lineno=getattr(stmt, "end_lineno", stmt.lineno),
                    docstring=docstring,
                    has_type_hints=True,
                )
                for item in stmt.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_kind = "method"
                        has_hints = bool(item.returns or any(arg.annotation for arg in item.args.args))
                        m_docstring = ast.get_docstring(item)
                        full_name = f"{stmt.name}.{item.name}"
                        # Register both Qualified name and simple name if not conflicting
                        info = SymbolInfo(
                            name=full_name,
                            kind=method_kind,
                            lineno=item.lineno,
                            end_lineno=getattr(item, "end_lineno", item.lineno),
                            docstring=m_docstring,
                            has_type_hints=has_hints,
                            parent_class=stmt.name,
                        )
                        self._symbols[full_name] = info
                        if item.name not in self._symbols:
                            self._symbols[item.name] = info

    def get_symbols(self) -> dict[str, SymbolInfo]:
        """Return all cataloged symbols with metadata."""
        _ = self.tree
        return self._symbols

    def get_symbol_info(self, symbol_name: str) -> SymbolInfo | None:
        """Lookup metadata for a specific symbol by name or Qualified.Name."""
        _ = self.tree
        if symbol_name in self._symbols:
            return self._symbols[symbol_name]
        for name, info in self._symbols.items():
            if name.split(".")[-1] == symbol_name:
                return info
        return None

    def get_tier1_context(self, symbol_name: str | None = None) -> str:
        """Generate Tier 1 context: module interfaces, signatures, docstrings, type hints.

        Bodies of all functions and methods are replaced with `...`.
        If symbol_name is specified, only that symbol (or its enclosing class) is isolated.
        """
        import copy

        tree_copy = copy.deepcopy(self.tree)
        transformer = Tier1Transformer(target_symbol=symbol_name)

        if symbol_name is None:
            # Whole file interface compression
            filtered_body: list[ast.stmt] = []
            docstring = ast.get_docstring(tree_copy)
            if docstring:
                filtered_body.append(ast.Expr(value=ast.Constant(value=docstring)))

            for stmt in tree_copy.body:
                # Keep imports and type aliases for signature integrity
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    filtered_body.append(stmt)
                elif hasattr(ast, "TypeAlias") and isinstance(stmt, getattr(ast, "TypeAlias")):
                    filtered_body.append(stmt)
                elif isinstance(stmt, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    transformed = transformer.visit(stmt)
                    if transformed is not None:
                        filtered_body.append(transformed)  # type: ignore

            tree_copy.body = filtered_body
            return ast.unparse(tree_copy)

        # Isolated symbol Tier 1
        target_info = self.get_symbol_info(symbol_name)
        if not target_info:
            return self.get_tier1_context(symbol_name=None)

        isolated_body: list[ast.stmt] = []
        # Include imports
        for stmt in tree_copy.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                isolated_body.append(stmt)

        for stmt in tree_copy.body:
            if isinstance(stmt, ast.ClassDef):
                if stmt.name == target_info.name or stmt.name == target_info.parent_class:
                    transformed = transformer.visit(stmt)
                    if transformed is not None:
                        isolated_body.append(transformed)  # type: ignore
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if stmt.name == target_info.name:
                    transformed = transformer.visit(stmt)
                    if transformed is not None:
                        isolated_body.append(transformed)  # type: ignore

        tree_copy.body = isolated_body
        return ast.unparse(tree_copy)
