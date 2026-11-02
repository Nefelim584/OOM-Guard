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

    @property
    def in_degree(self) -> int:
        """In-degree: number of callers dependent on this symbol."""
        return len(self.callers)


class CyclomaticComplexityVisitor(ast.NodeVisitor):
    """Calculates Cyclomatic Complexity by counting branching decision points."""

    def __init__(self) -> None:
        self.complexity: int = 1

    def visit_If(self, node: ast.If) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.complexity += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.complexity += len(node.cases)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Do not recurse into nested function bodies for outer complexity
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass


class SymbolDependencyVisitor(ast.NodeVisitor):
    """Collects direct callees and raised exceptions within a function or method body."""

    def __init__(self) -> None:
        self.callees: set[str] = set()
        self.raised_exceptions: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            self.callees.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.callees.add(node.func.attr)
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is not None:
            if isinstance(node.exc, ast.Name):
                self.raised_exceptions.add(node.exc.id)
            elif isinstance(node.exc, ast.Call):
                if isinstance(node.exc.func, ast.Name):
                    self.raised_exceptions.add(node.exc.func.id)
                elif isinstance(node.exc.func, ast.Attribute):
                    self.raised_exceptions.add(node.exc.func.attr)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass


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
        docstring = ast.get_docstring(node)
        retained_body: list[ast.stmt] = []

        if docstring:
            retained_body.append(ast.Expr(value=ast.Constant(value=docstring)))

        target_base = self.target_symbol.split(".")[-1] if self.target_symbol else None

        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str) and stmt.value.value == docstring:
                continue

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


class Tier3Transformer(ast.NodeTransformer):
    """AST Transformer that isolates a target symbol with its full implementation body,
    stubbing non-target sibling methods within the same class with `...`."""

    def __init__(self, target_symbol: str) -> None:
        super().__init__()
        self.target_symbol = target_symbol
        self.target_base = target_symbol.split(".")[-1]

    def _stub_body(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
        """Produce a stubbed body containing only the docstring (if present) and Ellipsis."""
        docstring = ast.get_docstring(node)
        new_body: list[ast.stmt] = []

        if docstring:
            new_body.append(ast.Expr(value=ast.Constant(value=docstring)))

        new_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))
        return new_body

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST | None:
        # If the class itself is the target symbol, retain all methods with full bodies
        if node.name == self.target_symbol or node.name == self.target_base:
            return node

        docstring = ast.get_docstring(node)
        retained_body: list[ast.stmt] = []

        if docstring:
            retained_body.append(ast.Expr(value=ast.Constant(value=docstring)))

        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str) and stmt.value.value == docstring:
                continue

            if isinstance(stmt, (ast.AnnAssign, ast.Assign)):
                retained_body.append(stmt)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if stmt.name == self.target_base:
                    # Target method inside class retains its full implementation body
                    retained_body.append(stmt)
                else:
                    # Sibling methods are stubbed with ...
                    stubbed = ast.copy_location(
                        type(stmt)(
                            name=stmt.name,
                            args=stmt.args,
                            body=self._stub_body(stmt),
                            decorator_list=stmt.decorator_list,
                            returns=stmt.returns,
                            type_comment=getattr(stmt, "type_comment", None),
                        ),
                        stmt,
                    )
                    retained_body.append(stubbed)
            elif isinstance(stmt, ast.ClassDef):
                retained_body.append(stmt)

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

    def _compute_metrics_and_deps(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> tuple[int, set[str], set[str]]:
        """Compute cyclomatic complexity, callees, and raised exceptions for a function node."""
        cc_visitor = CyclomaticComplexityVisitor()
        for b_stmt in node.body:
            cc_visitor.visit(b_stmt)

        dep_visitor = SymbolDependencyVisitor()
        for b_stmt in node.body:
            dep_visitor.visit(b_stmt)

        return cc_visitor.complexity, dep_visitor.callees, dep_visitor.raised_exceptions

    def _index_symbols(self) -> None:
        """Scan AST and catalog all classes, functions, and methods with complexity and dependencies."""
        if self._tree is None:
            return

        self._symbols.clear()
        # First pass: catalog all symbols, complexity, callees, and raised exceptions
        for stmt in self._tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "async_function" if isinstance(stmt, ast.AsyncFunctionDef) else "function"
                has_hints = bool(stmt.returns or any(arg.annotation for arg in stmt.args.args))
                docstring = ast.get_docstring(stmt)
                cc, callees, excs = self._compute_metrics_and_deps(stmt)

                self._symbols[stmt.name] = SymbolInfo(
                    name=stmt.name,
                    kind=kind,
                    lineno=stmt.lineno,
                    end_lineno=getattr(stmt, "end_lineno", stmt.lineno),
                    docstring=docstring,
                    has_type_hints=has_hints,
                    cyclomatic_complexity=cc,
                    callees=callees,
                    raised_exceptions=excs,
                )
            elif isinstance(stmt, ast.ClassDef):
                docstring = ast.get_docstring(stmt)
                class_info = SymbolInfo(
                    name=stmt.name,
                    kind="class",
                    lineno=stmt.lineno,
                    end_lineno=getattr(stmt, "end_lineno", stmt.lineno),
                    docstring=docstring,
                    has_type_hints=True,
                )
                self._symbols[stmt.name] = class_info

                total_cc = 1
                for item in stmt.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_kind = "method"
                        has_hints = bool(item.returns or any(arg.annotation for arg in item.args.args))
                        m_docstring = ast.get_docstring(item)
                        cc, callees, excs = self._compute_metrics_and_deps(item)
                        total_cc += cc

                        full_name = f"{stmt.name}.{item.name}"
                        info = SymbolInfo(
                            name=full_name,
                            kind=method_kind,
                            lineno=item.lineno,
                            end_lineno=getattr(item, "end_lineno", item.lineno),
                            docstring=m_docstring,
                            has_type_hints=has_hints,
                            parent_class=stmt.name,
                            cyclomatic_complexity=cc,
                            callees=callees,
                            raised_exceptions=excs,
                        )
                        self._symbols[full_name] = info
                        if item.name not in self._symbols:
                            self._symbols[item.name] = info
                class_info.cyclomatic_complexity = total_cc

        # Second pass: invert dependency graph to populate callers (in-degree)
        for caller_name, caller_info in list(self._symbols.items()):
            # Only count primary qualified names to prevent duplicate counting
            if caller_info.parent_class and "." not in caller_name:
                continue

            for callee in caller_info.callees:
                target = self.get_symbol_info(callee)
                if target and target.name != caller_info.name:
                    target.callers.add(caller_info.name)

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
            filtered_body: list[ast.stmt] = []
            docstring = ast.get_docstring(tree_copy)
            if docstring:
                filtered_body.append(ast.Expr(value=ast.Constant(value=docstring)))

            for stmt in tree_copy.body:
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

        target_info = self.get_symbol_info(symbol_name)
        if not target_info:
            return self.get_tier1_context(symbol_name=None)

        isolated_body: list[ast.stmt] = []
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

    def get_tier2_context(self, symbol_name: str | None = None) -> str:
        """Generate Tier 2 context: Skeleton & Call-Graph Slice.

        Combines Tier 1 interface with:
        - Callers (in-degree dependencies)
        - Callees (out-degree dependencies)
        - Raised exceptions
        - Slices of adjacent dependency signatures
        """
        if symbol_name is None:
            # Whole file Tier 2: Tier 1 annotated with dependency metadata
            base_tier1 = self.get_tier1_context(symbol_name=None)
            lines = [
                "# ====================================================================",
                "# Tier 2 Context: Module Skeleton with Dependency Graph",
                "# ====================================================================",
            ]
            for s_name, s_info in sorted(self._symbols.items()):
                # Only list unique primary symbols
                if s_info.parent_class and "." not in s_name:
                    continue
                lines.append(
                    f"# Symbol: {s_name} | CC: {s_info.cyclomatic_complexity} | "
                    f"Callers ({s_info.in_degree}): {sorted(s_info.callers)} | "
                    f"Callees: {sorted(s_info.callees)} | "
                    f"Raises: {sorted(s_info.raised_exceptions)}"
                )
            lines.append("")
            lines.append(base_tier1)
            return "\n".join(lines)

        target_info = self.get_symbol_info(symbol_name)
        if not target_info:
            return self.get_tier2_context(symbol_name=None)

        lines = [
            f"# ====================================================================",
            f"# Tier 2 Context: Call-Graph Slice for '{target_info.name}'",
            f"# Cyclomatic Complexity (CC): {target_info.cyclomatic_complexity}",
            f"# In-degree (Callers count): {target_info.in_degree}",
            f"# Callers: {sorted(target_info.callers)}",
            f"# Callees: {sorted(target_info.callees)}",
            f"# Raised Exceptions: {sorted(target_info.raised_exceptions)}",
            f"# ====================================================================",
            "",
        ]

        # Collect direct dependency signatures (callers & callees available in this module)
        dep_symbols: set[str] = set()
        for caller in target_info.callers:
            dep_symbols.add(caller)
        for callee in target_info.callees:
            dep_symbols.add(callee)

        # Remove the target symbol itself from adjacent deps
        dep_symbols.discard(target_info.name)
        if target_info.parent_class:
            dep_symbols.discard(target_info.parent_class)
            dep_symbols.discard(target_info.name.split(".")[-1])

        dep_contexts: list[str] = []
        for dep in sorted(dep_symbols):
            info = self.get_symbol_info(dep)
            if info:
                dep_code = self.get_tier1_context(symbol_name=dep)
                if dep_code:
                    dep_contexts.append(dep_code)

        if dep_contexts:
            lines.append("# --- Direct Dependencies (Callers & Callees) Interfaces ---")
            lines.extend(dep_contexts)
            lines.append("")

        lines.append("# --- Target Symbol Interface ---")
        lines.append(self.get_tier1_context(symbol_name=target_info.name))

        return "\n".join(lines)

    def get_tier3_context(self, symbol_name: str | None = None) -> str:
        """Generate Tier 3 context: Full implementation body.

        Exports uncompressed source code for the requested symbol (and its immediate block)
        or the entire module if symbol_name is None.
        """
        import copy

        if symbol_name is None:
            # Full module uncompressed source code
            return ast.unparse(self.tree)

        target_info = self.get_symbol_info(symbol_name)
        if not target_info:
            return self.get_tier3_context(symbol_name=None)

        tree_copy = copy.deepcopy(self.tree)
        transformer = Tier3Transformer(target_symbol=target_info.name)

        isolated_body: list[ast.stmt] = []

        # Preserve top-level module docstring if present
        docstring = ast.get_docstring(tree_copy)
        if docstring:
            isolated_body.append(ast.Expr(value=ast.Constant(value=docstring)))

        # Preserve top-level imports and type aliases
        for stmt in tree_copy.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                isolated_body.append(stmt)
            elif hasattr(ast, "TypeAlias") and isinstance(stmt, getattr(ast, "TypeAlias")):
                isolated_body.append(stmt)

        # Isolate target symbol and its immediate enclosing block
        for stmt in tree_copy.body:
            if isinstance(stmt, ast.ClassDef):
                if stmt.name == target_info.name or stmt.name == target_info.parent_class:
                    transformed = transformer.visit(stmt)
                    if transformed is not None:
                        isolated_body.append(transformed)  # type: ignore
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if stmt.name == target_info.name:
                    isolated_body.append(stmt)

        tree_copy.body = isolated_body
        return ast.unparse(tree_copy)
