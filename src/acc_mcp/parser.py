"""AST Code Extractor and Context Compressor for ACC-MCP."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from acc_mcp.tokens import HeuristicTokenCounter, TokenCounter


def _node_start(node: ast.stmt) -> int:
    """First source line of a statement, including its decorators."""
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decorators])


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """One-line signature of a function, e.g. `def f(x: int) -> str`."""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    sig = f"{prefix} {node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        sig += f" -> {ast.unparse(node.returns)}"
    return sig


@dataclass
class SourceSegment:
    """A verbatim line range of the source file served in a Tier 3 slice."""

    start: int
    end: int
    label: str
    trailer: list[str] = field(default_factory=list)


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


class ASTParser:
    """Parses Python source code and generates structural context slices."""

    def __init__(self, source_code: str, file_path: str = "") -> None:
        self.source_code = source_code
        self.file_path = file_path
        self._tree: ast.Module | None = None
        self._symbols: dict[str, SymbolInfo] = {}
        # Keyed by qualified symbol name: source line span (decorators included) and AST node
        self._spans: dict[str, tuple[int, int]] = {}
        self._nodes: dict[str, ast.stmt] = {}
        self._source_lines: list[str] | None = None
        self._stmt_ends: set[int] | None = None
        self._units: list[tuple[str, int, int]] | None = None

    @property
    def display_name(self) -> str:
        """File name shown in slice headers."""
        return Path(self.file_path).name if self.file_path else "<source>"

    @property
    def source_lines(self) -> list[str]:
        """Source split on '\\n' with line endings kept, so index i is AST line i + 1."""
        if self._source_lines is None:
            parts = self.source_code.split("\n")
            lines = [p + "\n" for p in parts[:-1]]
            if parts[-1]:
                lines.append(parts[-1])
            self._source_lines = lines
        return self._source_lines

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
        self._spans.clear()
        self._nodes.clear()
        # First pass: catalog all symbols, complexity, callees, and raised exceptions
        for stmt in self._tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._nodes[stmt.name] = stmt
                self._spans[stmt.name] = (_node_start(stmt), stmt.end_lineno or stmt.lineno)

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
                        self._nodes[full_name] = item
                        self._spans[full_name] = (_node_start(item), item.end_lineno or item.lineno)
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

    def get_symbol_span(self, symbol_name: str) -> tuple[int, int] | None:
        """Return the (first, last) source line of a symbol, decorators included."""
        info = self.get_symbol_info(symbol_name)
        return self._spans.get(info.name) if info else None

    def _primary_symbol_names(self) -> list[str]:
        """Qualified names of all classes, functions and methods, in file order."""
        _ = self.tree
        names = [
            name
            for name, info in self._symbols.items()
            if not (info.parent_class and "." not in name)
        ]
        return sorted(names, key=lambda n: self._spans[n])

    def _shown_symbols(self, info: SymbolInfo) -> list[str]:
        """Symbols whose interfaces appear in a Tier 1 slice isolated on `info`."""
        if info.kind == "class":
            members = [
                n for n in self._primary_symbol_names()
                if self._symbols[n].parent_class == info.name
            ]
            return [info.name] + members
        if info.parent_class:
            return [info.parent_class, info.name]
        return [info.name]

    def _location_line(self, name: str, label: str | None = None) -> str:
        start, end = self._spans[name]
        return f"# {self.display_name} lines {start}-{end}: {label or name}"

    def _with_header(self, names: list[str], code: str) -> str:
        """Prefix code with one location line per symbol, in file order."""
        ordered = sorted(dict.fromkeys(names), key=lambda n: self._spans[n])
        header = "\n".join(self._location_line(n) for n in ordered)
        if not header:
            return code
        return f"{header}\n{code}" if code else header

    def get_tier1_context(self, symbol_name: str | None = None) -> str:
        """Generate Tier 1 context: module interfaces, signatures, docstrings, type hints.

        Bodies of all functions and methods are replaced with `...`.
        If symbol_name is specified, only that symbol (or its enclosing class) is isolated.
        The slice starts with `# <file> lines a-b: <symbol>` lines for every symbol shown.
        """
        code, shown = self._tier1_code(symbol_name)
        return self._with_header(shown, code)

    def _tier1_code(self, symbol_name: str | None = None) -> tuple[str, list[str]]:
        """Tier 1 code without header, plus the qualified names of the symbols it shows."""
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
            return ast.unparse(tree_copy), self._primary_symbol_names()

        target_info = self.get_symbol_info(symbol_name)
        if not target_info:
            return self._tier1_code(symbol_name=None)

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
        return ast.unparse(tree_copy), self._shown_symbols(target_info)

    def get_tier2_context(self, symbol_name: str | None = None) -> str:
        """Generate Tier 2 context: Skeleton & Call-Graph Slice.

        Combines Tier 1 interface with:
        - Callers (in-degree dependencies)
        - Callees (out-degree dependencies)
        - Raised exceptions
        - Slices of adjacent dependency signatures
        The slice starts with `# <file> lines a-b: <symbol>` lines for every symbol shown.
        """
        if symbol_name is None:
            # Whole file Tier 2: Tier 1 annotated with dependency metadata
            base_tier1, shown = self._tier1_code(symbol_name=None)
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
            return self._with_header(shown, "\n".join(lines))

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

        shown: list[str] = []
        dep_contexts: list[str] = []
        for dep in sorted(dep_symbols):
            info = self.get_symbol_info(dep)
            if info:
                dep_code, dep_shown = self._tier1_code(symbol_name=dep)
                if dep_code:
                    dep_contexts.append(dep_code)
                    shown.extend(dep_shown)

        if dep_contexts:
            lines.append("# --- Direct Dependencies (Callers & Callees) Interfaces ---")
            lines.extend(dep_contexts)
            lines.append("")

        target_code, target_shown = self._tier1_code(symbol_name=target_info.name)
        shown.extend(target_shown)
        lines.append("# --- Target Symbol Interface ---")
        lines.append(target_code)

        return self._with_header(shown, "\n".join(lines))

    def get_tier3_context(
        self,
        symbol_name: str | None = None,
        max_tokens: int | None = None,
        token_counter: TokenCounter | None = None,
    ) -> str:
        """Generate Tier 3 context: full implementation, copied verbatim from the file.

        Serves the requested symbol (plus top-level imports and, for a method, the
        enclosing class header) or the entire module if symbol_name is None. Every
        part is preceded by `# <file> lines a-b: <label>`, so the text can be used
        directly for search-and-replace edits. Sibling methods are listed with their
        signatures and line ranges instead of being shown.

        If max_tokens is given and the slice does not fit, it is cut at a statement
        boundary and each omitted line range is reported with how to retrieve it.
        """
        segments, target = self._tier3_segments(symbol_name)
        full = "".join(self._render_segment(seg) for seg in segments)
        if max_tokens is None:
            return full

        counter = token_counter or HeuristicTokenCounter()
        if counter.count_tokens(full) <= max_tokens:
            return full
        return self._fit_tier3(segments, target, max_tokens, counter)

    def _tier3_segments(self, symbol_name: str | None) -> tuple[list[SourceSegment], str | None]:
        """Line ranges making up a Tier 3 slice, and the qualified target name."""
        _ = self.tree
        total = len(self.source_lines)
        if total == 0:
            return [], None

        target = self.get_symbol_info(symbol_name) if symbol_name else None
        if target is None:
            return [SourceSegment(1, total, "entire module")], None

        segments = self._import_segments()
        t_start, t_end = self._spans[target.name]
        cls_node = self._nodes.get(target.parent_class) if target.parent_class else None
        header_end = self._class_header_end(cls_node) if isinstance(cls_node, ast.ClassDef) else None

        if isinstance(cls_node, ast.ClassDef) and header_end is not None:
            cls_start = self._spans[cls_node.name][0]
            segments.append(
                SourceSegment(
                    cls_start,
                    header_end,
                    f"{cls_node.name} (class header)",
                    trailer=self._member_listing(cls_node, exclude=target.name),
                )
            )
            segments.append(SourceSegment(t_start, t_end, target.name))
        elif isinstance(cls_node, ast.ClassDef):
            c_start, c_end = self._spans[cls_node.name]
            segments.append(SourceSegment(c_start, c_end, cls_node.name))
        else:
            segments.append(SourceSegment(t_start, t_end, target.name))

        segments.sort(key=lambda seg: seg.start)
        return segments, target.name

    def _import_segments(self) -> list[SourceSegment]:
        """Runs of consecutive top-level import statements."""
        segments: list[SourceSegment] = []
        group: list[int] | None = None
        for stmt in self.tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                end = stmt.end_lineno or stmt.lineno
                if group is None:
                    group = [stmt.lineno, end]
                else:
                    group[1] = end
            elif group is not None:
                segments.append(SourceSegment(group[0], group[1], "imports"))
                group = None
        if group is not None:
            segments.append(SourceSegment(group[0], group[1], "imports"))
        return segments

    def _class_header_end(self, cls: ast.ClassDef) -> int | None:
        """Last line of the class header: the class line, docstring and leading attributes."""
        start = _node_start(cls)
        first_def = next(
            (s for s in cls.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))),
            None,
        )
        end = (cls.end_lineno or cls.lineno) if first_def is None else _node_start(first_def) - 1
        while end >= start and not self.source_lines[end - 1].strip():
            end -= 1
        return end if end >= start else None

    def _member_listing(self, cls: ast.ClassDef, exclude: str) -> list[str]:
        """Comment lines listing the class members that the slice does not show."""
        lines = [f"# other members of {cls.name} (not shown):"]
        for stmt in cls.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if f"{cls.name}.{stmt.name}" == exclude:
                    continue
                lines.append(f"#   lines {_node_start(stmt)}-{stmt.end_lineno}: {_signature(stmt)}")
            elif isinstance(stmt, ast.ClassDef):
                lines.append(f"#   lines {_node_start(stmt)}-{stmt.end_lineno}: class {stmt.name}")
        return lines if len(lines) > 1 else []

    def _render_segment(self, seg: SourceSegment, end: int | None = None) -> str:
        """Location line plus the verbatim source lines seg.start..end."""
        stop = seg.end if end is None else end
        text = "".join(self.source_lines[seg.start - 1:stop])
        if text and not text.endswith("\n"):
            text += "\n"
        header = f"# {self.display_name} lines {seg.start}-{stop}: {seg.label}\n"
        trailer = "".join(f"{line}\n" for line in seg.trailer) if stop == seg.end else ""
        return header + text + trailer

    def _unit_spans(self) -> list[tuple[str, int, int]]:
        """Spans of functions and methods: the units a cut must not split silently."""
        if self._units is None:
            self._units = [
                (name, *self._spans[name])
                for name in self._primary_symbol_names()
                if self._symbols[name].kind != "class"
            ]
        return self._units

    def _unit_containing(self, line: int) -> str | None:
        """Function or method that continues past `line`, if any."""
        for name, start, end in self._unit_spans():
            if start <= line < end:
                return name
        return None

    def _cut_points(self, start: int, end: int) -> tuple[list[int], list[int]]:
        """Lines in [start, end) after which a statement ends, largest first.

        Returns cuts that keep every function whole, then cuts inside a function.
        """
        if self._stmt_ends is None:
            self._stmt_ends = {
                node.end_lineno
                for node in ast.walk(self.tree)
                if isinstance(node, ast.stmt) and node.end_lineno is not None
            }
        ends = sorted((c for c in self._stmt_ends if start <= c < end), reverse=True)
        whole = [c for c in ends if self._unit_containing(c) is None]
        split = [c for c in ends if self._unit_containing(c) is not None]
        return whole, split

    def _omission_notes(
        self,
        ranges: list[tuple[int, int]],
        target: str | None,
        limit: int,
        cut_after: int | None = None,
    ) -> str:
        """Comment lines naming what a cut slice left out and how to get it."""
        notes: list[str] = []
        if cut_after is not None:
            unit = self._unit_containing(cut_after)
            if unit is not None:
                u_start, u_end = self._spans[unit]
                notes.append(
                    f"# [ACC-MCP] {unit} is incomplete: it spans lines {u_start}-{u_end} "
                    f"but only lines up to {cut_after} are shown; read the rest from the file before editing it"
                )

        for a, b in ranges:
            if a > b:
                continue
            names = [
                n for n in self._primary_symbol_names()
                if n != target and a <= self._spans[n][0] <= b
            ]
            # Suggest a class rather than each of its methods when the whole class is omitted
            names = [
                n for n in names
                if not (self._symbols[n].parent_class and self._symbols[n].parent_class in names)
            ]
            if names:
                listed = ", ".join(f'"{n}"' for n in names[:3])
                more = f" (+{len(names) - 3} more)" if len(names) > 3 else ""
                hint = f"request symbol_name={listed}{more} to see them"
            else:
                hint = "read these lines from the file to see them"
            notes.append(f"# [ACC-MCP] lines {a}-{b} omitted to fit the {limit}-token ceiling; {hint}")
        return "".join(f"{note}\n" for note in notes)

    def _fit_tier3(
        self,
        segments: list[SourceSegment],
        target: str | None,
        limit: int,
        counter: TokenCounter,
    ) -> str:
        """Keep whole segments while they fit, then cut the first one that does not."""
        kept = ""
        for i, seg in enumerate(segments):
            rest = [(s.start, s.end) for s in segments[i + 1:]]
            whole = self._render_segment(seg)
            reserve = self._omission_notes(rest, target, limit)
            if counter.count_tokens(kept + whole + reserve) <= limit:
                kept += whole
                continue

            def build(cut: int) -> str:
                notes = self._omission_notes([(cut + 1, seg.end)] + rest, target, limit, cut_after=cut)
                return kept + self._render_segment(seg, end=cut) + notes

            for cuts in self._cut_points(seg.start, seg.end):
                candidate = self._largest_fitting(cuts, build, limit, counter)
                if candidate is not None:
                    return candidate
            return kept + self._omission_notes([(seg.start, seg.end)] + rest, target, limit)
        return kept

    @staticmethod
    def _largest_fitting(
        cuts: list[int],
        build: Callable[[int], str],
        limit: int,
        counter: TokenCounter,
    ) -> str | None:
        """Binary search over descending cuts for the largest one whose text fits."""
        lo, hi = 0, len(cuts)
        best: str | None = None
        while lo < hi:
            mid = (lo + hi) // 2
            candidate = build(cuts[mid])
            if counter.count_tokens(candidate) <= limit:
                best = candidate
                hi = mid
            else:
                lo = mid + 1
        return best
