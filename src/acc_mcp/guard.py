"""Deterministic Guardrails and Security Boundary Enforcement for ACC-MCP (Block 3)."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

from acc_mcp.tokens import HeuristicTokenCounter, TokenCounter


class SecurityViolationError(PermissionError):
    """Raised when an operation targets a protected or forbidden path."""

    pass


def _slice_function_node(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    budget_tokens: int,
    counter: TokenCounter,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Slice a function body to fit within budget_tokens, appending an Ellipsis stub."""
    docstring = ast.get_docstring(node)
    stub_body: list[ast.stmt] = []
    if docstring:
        stub_body.append(ast.Expr(value=ast.Constant(value=docstring)))
    stub_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))

    node_cls = type(node)
    candidate = ast.copy_location(
        node_cls(
            name=node.name,
            args=node.args,
            body=stub_body,
            decorator_list=node.decorator_list,
            returns=node.returns,
            type_comment=getattr(node, "type_comment", None),
        ),
        node,
    )
    ast.fix_missing_locations(candidate)

    if counter.count_tokens(ast.unparse(candidate)) > budget_tokens:
        return None

    # Filter out original docstring statement from original body statements
    orig_stmts = [
        s
        for s in node.body
        if not (
            isinstance(s, ast.Expr)
            and isinstance(s.value, ast.Constant)
            and s.value.value == docstring
        )
    ]

    best_candidate = candidate
    current_body = list(stub_body)

    for stmt in orig_stmts:
        test_body = current_body[:-1] + [stmt, current_body[-1]]
        test_candidate = ast.copy_location(
            node_cls(
                name=node.name,
                args=node.args,
                body=test_body,
                decorator_list=node.decorator_list,
                returns=node.returns,
                type_comment=getattr(node, "type_comment", None),
            ),
            node,
        )
        ast.fix_missing_locations(test_candidate)
        if counter.count_tokens(ast.unparse(test_candidate)) <= budget_tokens:
            current_body = test_body
            best_candidate = test_candidate
        else:
            break

    return best_candidate


def _slice_class_node(
    node: ast.ClassDef,
    budget_tokens: int,
    counter: TokenCounter,
) -> ast.ClassDef | None:
    """Slice a class body to fit within budget_tokens, retaining attributes and stubs."""
    docstring = ast.get_docstring(node)
    stub_body: list[ast.stmt] = []
    if docstring:
        stub_body.append(ast.Expr(value=ast.Constant(value=docstring)))
    stub_body.append(ast.Expr(value=ast.Constant(value=Ellipsis)))

    candidate = ast.copy_location(
        ast.ClassDef(
            name=node.name,
            bases=node.bases,
            keywords=node.keywords,
            body=stub_body,
            decorator_list=node.decorator_list,
        ),
        node,
    )
    ast.fix_missing_locations(candidate)

    if counter.count_tokens(ast.unparse(candidate)) > budget_tokens:
        return None

    orig_stmts = [
        s
        for s in node.body
        if not (
            isinstance(s, ast.Expr)
            and isinstance(s.value, ast.Constant)
            and s.value.value == docstring
        )
    ]

    best_candidate = candidate
    current_body = list(stub_body)

    for stmt in orig_stmts:
        test_body = current_body[:-1] + [stmt]
        test_candidate = ast.copy_location(
            ast.ClassDef(
                name=node.name,
                bases=node.bases,
                keywords=node.keywords,
                body=test_body,
                decorator_list=node.decorator_list,
            ),
            node,
        )
        ast.fix_missing_locations(test_candidate)
        if counter.count_tokens(ast.unparse(test_candidate)) <= budget_tokens:
            current_body = test_body
            best_candidate = test_candidate
        else:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sub_budget = budget_tokens - counter.count_tokens(ast.unparse(best_candidate))
                sliced_method = _slice_function_node(stmt, sub_budget, counter)
                if sliced_method:
                    test_body = current_body + [sliced_method]
                    test_candidate = ast.copy_location(
                        ast.ClassDef(
                            name=node.name,
                            bases=node.bases,
                            keywords=node.keywords,
                            body=test_body,
                            decorator_list=node.decorator_list,
                        ),
                        node,
                    )
                    ast.fix_missing_locations(test_candidate)
                    if counter.count_tokens(ast.unparse(test_candidate)) <= budget_tokens:
                        best_candidate = test_candidate
            break

    return best_candidate


class GuardRail:
    """Deterministic security and token budget guardrail for ACC-MCP."""

    DEFAULT_MAX_TOKENS: int = 2000

    PROTECTED_FILENAMES: set[str] = {
        ".env",
        "credentials.json",
        "secrets.json",
    }

    PROTECTED_PREFIXES: tuple[str, ...] = (
        ".env.",
    )

    PROTECTED_DIR_PARTS: set[str] = {
        ".git",
    }

    def __init__(
        self,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.token_counter = token_counter or HeuristicTokenCounter()

    def is_protected_path(self, file_path: str | Path) -> bool:
        """Check if file_path violates Protected Path Rules."""
        norm_str = os.path.normpath(str(file_path)).replace("\\", "/")
        path_obj = Path(norm_str)

        # 1. Exact forbidden filenames (.env, credentials.json, etc.)
        if path_obj.name.lower() in self.PROTECTED_FILENAMES:
            return True

        # 2. Protected prefixes (e.g. .env.local, .env.production)
        if path_obj.name.lower().startswith(self.PROTECTED_PREFIXES):
            return True

        # 3. Protected directory components (.git)
        if any(part.lower() in self.PROTECTED_DIR_PARTS for part in path_obj.parts):
            return True

        # 4. Alembic migrations protection (e.g. alembic/migrations/...)
        parts_lower = [p.lower() for p in path_obj.parts]
        if "alembic" in parts_lower and "migrations" in parts_lower:
            return True
        if "alembic/migrations" in norm_str.lower():
            return True

        # 5. System sensitive root paths (protecting /etc, /proc, /sys, /var while allowing user temp dirs)
        if norm_str.startswith(("/etc", "/proc", "/sys")):
            return True
        if norm_str.startswith("/var/") and not norm_str.startswith(("/var/folders", "/var/tmp")):
            return True

        return False

    def validate_path(self, file_path: str | Path) -> None:
        """Validate file path against Protected Path Rules.

        Raises:
            SecurityViolationError: If file_path is forbidden.
        """
        if self.is_protected_path(file_path):
            raise SecurityViolationError(
                f"Access to protected path '{file_path}' is denied by GuardRail."
            )

    @staticmethod
    def _split_comment_header(code: str) -> tuple[str, str]:
        """Split off the leading block of `#` comment lines."""
        lines = code.splitlines(keepends=True)
        count = 0
        while count < len(lines) and lines[count].startswith("#"):
            count += 1
        return "".join(lines[:count]), "".join(lines[count:])

    def _fallback_line_slice(self, text: str, limit: int) -> str:
        """Deterministic line-by-line fallback slicing for non-Python or unparseable text."""
        lines = text.splitlines(keepends=True)
        retained: list[str] = []
        banner = f"# [ACC-MCP] Context truncated to satisfy token ceiling (<= {limit} tokens)\n"
        current_tokens = self.token_counter.count_tokens(banner)
        for line in lines:
            line_tokens = self.token_counter.count_tokens(line)
            if current_tokens + line_tokens <= limit:
                retained.append(line)
                current_tokens += line_tokens
            else:
                break
        return banner + "".join(retained)

    def enforce_ceiling(self, code: str, max_tokens: int | None = None) -> str:
        """Deterministically truncate code using AST node slicing to fit within max_tokens ceiling."""
        limit = max_tokens if max_tokens is not None else self.max_tokens
        if not code:
            return ""

        if self.token_counter.count_tokens(code) <= limit:
            return code

        # Keep a leading comment header (slice location lines) intact and cut only the code after it
        header, body = self._split_comment_header(code)
        header_tokens = self.token_counter.count_tokens(header)
        if header and body and header_tokens < limit:
            kept = header + self.enforce_ceiling(body, max_tokens=limit - header_tokens)
            if self.token_counter.count_tokens(kept) <= limit:
                return kept

        # Attempt AST node slicing
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return self._fallback_line_slice(code, limit)

        banner = f"# [ACC-MCP] Context truncated to satisfy token ceiling (<= {limit} tokens)\n"
        banner_tokens = self.token_counter.count_tokens(banner)
        effective_limit = max(1, limit - banner_tokens)

        selected_stmts: list[ast.stmt] = []

        # Check if top-level module docstring exists and fits
        mod_docstring = ast.get_docstring(tree)
        if mod_docstring:
            doc_stmt = ast.Expr(value=ast.Constant(value=mod_docstring))
            test_mod = ast.Module(body=[doc_stmt], type_ignores=[])
            ast.fix_missing_locations(test_mod)
            if self.token_counter.count_tokens(ast.unparse(test_mod)) <= effective_limit:
                selected_stmts.append(doc_stmt)

        for stmt in tree.body:
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and stmt.value.value == mod_docstring
            ):
                continue

            # Try adding full stmt
            candidate_body = selected_stmts + [stmt]
            candidate_mod = ast.Module(body=candidate_body, type_ignores=[])
            ast.fix_missing_locations(candidate_mod)
            candidate_code = ast.unparse(candidate_mod)

            if self.token_counter.count_tokens(candidate_code) <= effective_limit:
                selected_stmts.append(stmt)
            else:
                # Try partial slice if statement is a function or class
                current_used = self.token_counter.count_tokens(
                    ast.unparse(ast.Module(body=selected_stmts, type_ignores=[]))
                ) if selected_stmts else 0
                remaining_budget = effective_limit - current_used

                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sliced_func = _slice_function_node(stmt, remaining_budget, self.token_counter)
                    if sliced_func:
                        selected_stmts.append(sliced_func)
                elif isinstance(stmt, ast.ClassDef):
                    sliced_class = _slice_class_node(stmt, remaining_budget, self.token_counter)
                    if sliced_class:
                        selected_stmts.append(sliced_class)
                break

        final_mod = ast.Module(body=selected_stmts, type_ignores=[])
        ast.fix_missing_locations(final_mod)
        final_code = banner + ast.unparse(final_mod) if selected_stmts else banner.strip()

        # Safety loop: ensure token ceiling is strictly obeyed
        while selected_stmts and self.token_counter.count_tokens(final_code) > limit:
            selected_stmts.pop()
            final_mod = ast.Module(body=selected_stmts, type_ignores=[])
            ast.fix_missing_locations(final_mod)
            final_code = banner + ast.unparse(final_mod) if selected_stmts else banner.strip()

        if self.token_counter.count_tokens(final_code) > limit:
            final_code = self._fallback_line_slice(code, limit)

        return final_code

    def protect(
        self,
        file_path: str | Path,
        context: str,
        max_tokens: int | None = None,
    ) -> str:
        """Validate path and enforce context token ceiling.

        Raises:
            SecurityViolationError: If file_path is forbidden.
        Returns:
            Ceiling-enforced context string.
        """
        self.validate_path(file_path)
        return self.enforce_ceiling(context, max_tokens=max_tokens)
