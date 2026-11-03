"""Unit tests for GuardRail security boundaries, deterministic AST ceiling, and Tier 3 context (Block 3)."""

import ast
from pathlib import Path

import pytest
from acc_mcp.guard import GuardRail, SecurityViolationError
from acc_mcp.parser import ASTParser
from acc_mcp.tokens import HeuristicTokenCounter


# ---------------------------------------------------------------------------
# 1. Protected Path Rule Tests (Step 3.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_path",
    [
        ".env",
        ".env.local",
        ".env.production",
        ".env.staging",
        "nested/path/.env",
        "nested/path/.env.local",
        "credentials.json",
        "secrets/credentials.json",
        "secrets.json",
        ".git",
        ".git/config",
        ".git/HEAD",
        ".git/hooks/pre-commit",
        "repo/.git/refs/heads/main",
        "alembic/migrations/env.py",
        "alembic/migrations/versions/1234_create_tables.py",
        "project/alembic/migrations/script.py.mako",
        "/etc/passwd",
        "/etc/shadow",
        "../../.env",
        "foo/bar/../../../credentials.json",
        "app/../../.git/config",
    ],
)
def test_guardrail_rejects_protected_paths(default_guard: GuardRail, forbidden_path: str):
    """Verify that any access to protected or sensitive paths raises SecurityViolationError."""
    with pytest.raises(SecurityViolationError) as exc_info:
        default_guard.validate_path(forbidden_path)

    assert "denied by GuardRail" in str(exc_info.value)


@pytest.mark.parametrize(
    "allowed_path",
    [
        "src/acc_mcp/parser.py",
        "src/acc_mcp/tokens.py",
        "src/acc_mcp/guard.py",
        "tests/test_guard.py",
        "app/models/user.py",
        "services/payment_service.py",
        "utils/helpers.py",
        "db/migration_helper.py",  # Not inside alembic/migrations
    ],
)
def test_guardrail_accepts_valid_paths(default_guard: GuardRail, allowed_path: str):
    """Verify that legitimate application paths pass validation cleanly."""
    # Should not raise any exception
    default_guard.validate_path(allowed_path)


# ---------------------------------------------------------------------------
# 1b. Workspace Root, Resolved Paths, and `.py`-Only Rule (Phase 16)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret_path",
    [
        ".ssh/id_rsa",
        ".ssh/config.py",
        "home/.aws/credentials",
        ".aws/boto_helper.py",
        "certs/server.pem",
        "keys/private.key",
        "id_rsa",
        "id_ed25519",
        ".npmrc",
        ".pypirc",
    ],
)
def test_guardrail_rejects_common_secret_locations(default_guard: GuardRail, secret_path: str):
    """Verify SSH/AWS folders, key files, and package-registry tokens are denied."""
    with pytest.raises(SecurityViolationError):
        default_guard.validate_path(secret_path)


@pytest.mark.parametrize(
    "non_python_path",
    ["README.md", "config.yaml", "notes.txt", "settings.toml", "Makefile", "src/app.pyc"],
)
def test_guardrail_rejects_non_python_files(default_guard: GuardRail, non_python_path: str):
    """Verify only `.py` files are served."""
    with pytest.raises(SecurityViolationError):
        default_guard.validate_path(non_python_path)


def test_guardrail_rejects_absolute_path_outside_root(
    default_guard: GuardRail, tmp_path_factory: pytest.TempPathFactory
):
    """Verify a real `.py` file in another folder is denied, even though nothing in its name is protected."""
    outside_file = tmp_path_factory.mktemp("outside") / "service.py"
    outside_file.write_text("def run() -> None:\n    pass\n", encoding="utf-8")

    with pytest.raises(SecurityViolationError):
        default_guard.validate_path(str(outside_file))


@pytest.mark.parametrize("escape_path", ["../service.py", "src/../../service.py", "/tmp/service.py"])
def test_guardrail_rejects_traversal_outside_root(default_guard: GuardRail, escape_path: str):
    """Verify `../` tricks and absolute paths cannot leave the workspace root."""
    with pytest.raises(SecurityViolationError):
        default_guard.validate_path(escape_path)


def test_guardrail_rejects_py_symlink_to_env(tmp_path: Path, default_guard: GuardRail):
    """Verify `notes.py -> .env` is denied: the real file is checked, not only the name sent."""
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY = 'sk-live-secret'\n", encoding="utf-8")
    (tmp_path / "notes.py").symlink_to(env_file)

    with pytest.raises(SecurityViolationError):
        default_guard.validate_path("notes.py")


def test_guardrail_rejects_symlink_leaving_root(
    tmp_path: Path, default_guard: GuardRail, tmp_path_factory: pytest.TempPathFactory
):
    """Verify a symlink inside the root that points to a `.py` file outside it is denied."""
    outside_file = tmp_path_factory.mktemp("outside") / "helpers.py"
    outside_file.write_text("def helper() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / "helpers.py").symlink_to(outside_file)

    with pytest.raises(SecurityViolationError):
        default_guard.validate_path("helpers.py")


def test_guardrail_rejects_symlinked_dir_into_git(tmp_path: Path, default_guard: GuardRail):
    """Verify a harmless-looking folder symlinked to `.git` is denied."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "hook.py").write_text("print('hook')\n", encoding="utf-8")
    (tmp_path / "scripts").symlink_to(git_dir, target_is_directory=True)

    with pytest.raises(SecurityViolationError):
        default_guard.validate_path("scripts/hook.py")


def test_guardrail_allows_file_inside_root_and_returns_real_path(tmp_path: Path, default_guard: GuardRail):
    """Verify a normal file inside the root passes, by relative or absolute path, and resolves to itself."""
    source = tmp_path / "app" / "service.py"
    source.parent.mkdir()
    source.write_text("def run() -> None:\n    pass\n", encoding="utf-8")

    assert default_guard.validate_path("app/service.py") == source.resolve()
    assert default_guard.validate_path(str(source)) == source.resolve()


def test_guardrail_allows_symlink_to_py_inside_root(tmp_path: Path, default_guard: GuardRail):
    """Verify a symlink between two `.py` files inside the root is allowed and resolves to the target."""
    real = tmp_path / "real_module.py"
    real.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "alias.py").symlink_to(real)

    assert default_guard.validate_path("alias.py") == real.resolve()


def test_guardrail_reads_root_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify `ACC_MCP_ROOT` sets the workspace root when no root is passed."""
    monkeypatch.setenv("ACC_MCP_ROOT", str(tmp_path))
    guard = GuardRail()

    assert guard.workspace_root == tmp_path.resolve()
    assert guard.validate_path("pkg/module.py") == (tmp_path / "pkg" / "module.py").resolve()


# ---------------------------------------------------------------------------
# 2. Tier 3 Full Symbol Implementation Extraction Tests (Step 3.1)
# ---------------------------------------------------------------------------


def test_tier3_whole_file_extraction(tier1_parser: ASTParser):
    """Verify Tier 3 returns full uncompressed source with bodies intact when symbol is None."""
    tier3_output = tier1_parser.get_tier3_context(symbol_name=None)

    # Full implementation bodies must be intact
    assert "Connecting to" in tier3_output
    assert "self.is_connected = True" in tier3_output
    assert "total = sum(data)" in tier3_output
    assert "result = total * scale" in tier3_output

    # Valid Python syntax
    parsed = ast.parse(tier3_output)
    assert isinstance(parsed, ast.Module)


def test_tier3_single_function_extraction(tier1_parser: ASTParser):
    """Verify Tier 3 isolates target top-level function with its full implementation."""
    tier3_output = tier1_parser.get_tier3_context(symbol_name="compute_metrics")

    # Target function has full implementation body
    assert "def compute_metrics(data: List[float], scale: float" in tier3_output
    assert "total = sum(data)" in tier3_output
    assert "result = total * scale" in tier3_output
    assert "return result" in tier3_output

    # Unrelated class is excluded
    assert "DatabaseManager" not in tier3_output

    # Imports are preserved
    assert "import os" in tier3_output
    assert "from typing import List, Optional" in tier3_output

    # Valid Python syntax
    parsed = ast.parse(tier3_output)
    assert isinstance(parsed, ast.Module)


def test_tier3_method_isolation_lists_siblings(tier1_parser: ASTParser):
    """Verify Tier 3 method extraction keeps full target body and lists sibling methods by line range."""
    tier3_output = tier1_parser.get_tier3_context(symbol_name="DatabaseManager.connect")

    # Enclosing class header is present
    assert "class DatabaseManager:" in tier3_output
    assert "connection_timeout: int = 30" in tier3_output

    # Target method has FULL body
    assert "def connect(self) -> bool:" in tier3_output
    assert "Connecting to" in tier3_output
    assert "self.is_connected = True" in tier3_output

    # Sibling method is listed by signature and line range, its body is not shown
    span = tier1_parser.get_symbol_span("DatabaseManager.execute_query")
    assert span is not None
    assert (
        f"#   lines {span[0]}-{span[1]}: async def execute_query"
        "(self, query: str, params: Optional[dict]=None) -> List[dict]"
    ) in tier3_output
    assert "Database not connected" not in tier3_output

    # Unrelated top-level function is excluded
    assert "def compute_metrics" not in tier3_output

    # Valid Python syntax
    parsed = ast.parse(tier3_output)
    assert isinstance(parsed, ast.Module)


def test_tier3_entire_class_extraction(tier1_parser: ASTParser):
    """Verify Tier 3 extracts entire class with all methods retaining their full bodies."""
    tier3_output = tier1_parser.get_tier3_context(symbol_name="DatabaseManager")

    assert "class DatabaseManager:" in tier3_output
    assert "self.is_connected = False" in tier3_output
    assert "Connecting to" in tier3_output
    assert "Database not connected" in tier3_output

    # Top-level compute_metrics is excluded
    assert "def compute_metrics" not in tier3_output

    # Valid Python syntax
    parsed = ast.parse(tier3_output)
    assert isinstance(parsed, ast.Module)


# ---------------------------------------------------------------------------
# 3. Deterministic AST Token Ceiling Truncation Tests (Step 3.3)
# ---------------------------------------------------------------------------


def test_ceiling_under_limit_unchanged(default_guard: GuardRail, sample_tier1_code: str):
    """Verify that code well below max_tokens (e.g. 2,000) is returned untouched."""
    truncated = default_guard.enforce_ceiling(sample_tier1_code, max_tokens=2000)
    assert truncated == sample_tier1_code
    assert "# [ACC-MCP] Context truncated" not in truncated


def test_deterministic_ast_ceiling_truncation(default_guard: GuardRail, heuristic_counter: HeuristicTokenCounter):
    """Verify that oversized code is deterministically truncated under the ceiling via AST slicing."""
    # Build a module containing 40 distinct functions (~3,500 tokens)
    large_code_parts = [
        '"""Large benchmark module for token ceiling truncation."""',
        "import os",
        "import sys",
    ]
    for i in range(40):
        large_code_parts.append(
            f"def process_batch_item_{i}(data: list[int], factor: float = {i}.0) -> dict:\n"
            f'    """Process batch item number {i} with mathematical transformations."""\n'
            f"    filtered = [x * factor for x in data if x > {i}]\n"
            f"    aggregated = sum(filtered)\n"
            f"    return {{'batch_id': {i}, 'total': aggregated, 'valid': True}}\n"
        )
    large_code = "\n".join(large_code_parts)

    original_tokens = heuristic_counter.count_tokens(large_code)
    assert original_tokens > 2000

    # Enforce standard C_max = 2,000 ceiling
    truncated = default_guard.enforce_ceiling(large_code, max_tokens=2000)

    # 1. Output must strictly respect the ceiling
    truncated_tokens = heuristic_counter.count_tokens(truncated)
    assert truncated_tokens <= 2000

    # 2. Output must include truncation notice banner
    assert "# [ACC-MCP] Context truncated to satisfy token ceiling (<= 2000 tokens)" in truncated

    # 3. Output must remain valid Python AST syntax
    parsed = ast.parse(truncated)
    assert isinstance(parsed, ast.Module)

    # 4. Truncation must be 100% deterministic (repeated calls yield identical output)
    truncated_again = default_guard.enforce_ceiling(large_code, max_tokens=2000)
    assert truncated == truncated_again


def test_ceiling_keeps_leading_location_header(default_guard: GuardRail, heuristic_counter: HeuristicTokenCounter):
    """Verify AST truncation keeps the slice's leading `# file lines a-b` header lines."""
    header = "# service.py lines 1-200: entire module\n# service.py lines 5-9: process_batch_item_0\n"
    body = "\n".join(
        f"def process_batch_item_{i}(data: list[int]) -> int:\n"
        f"    return sum(x * {i} for x in data)\n"
        for i in range(80)
    )

    truncated = default_guard.enforce_ceiling(header + body, max_tokens=300)

    assert truncated.startswith(header)
    assert heuristic_counter.count_tokens(truncated) <= 300
    assert "# [ACC-MCP] Context truncated" in truncated
    assert isinstance(ast.parse(truncated), ast.Module)


def test_ceiling_single_large_function_body_slicing(default_guard: GuardRail, heuristic_counter: HeuristicTokenCounter):
    """Verify that a single huge function is sliced at the statement level and ended with Ellipsis."""
    # Build a single function with 50 statements (~600 tokens)
    func_lines = [
        "def massive_pipeline_runner(x: int) -> int:",
        '    """Execute massive sequential pipeline."""',
    ]
    for i in range(50):
        func_lines.append(f"    x = (x + {i}) * 2")
    func_lines.append("    return x")
    single_func_code = "\n".join(func_lines)

    func_tokens = heuristic_counter.count_tokens(single_func_code)
    assert func_tokens > 250

    # Truncate with ceiling of 150 tokens
    truncated = default_guard.enforce_ceiling(single_func_code, max_tokens=150)

    tokens = heuristic_counter.count_tokens(truncated)
    assert tokens <= 150
    assert "def massive_pipeline_runner" in truncated
    assert "..." in truncated

    # Valid syntax
    parsed = ast.parse(truncated)
    assert isinstance(parsed, ast.Module)


def test_ceiling_fallback_on_syntax_error(default_guard: GuardRail, heuristic_counter: HeuristicTokenCounter):
    """Verify that non-Python or syntax-invalid input triggers deterministic line fallback."""
    raw_invalid_code = "This is not python code\n" * 100
    assert heuristic_counter.count_tokens(raw_invalid_code) > 300

    truncated = default_guard.enforce_ceiling(raw_invalid_code, max_tokens=100)
    tokens = heuristic_counter.count_tokens(truncated)
    assert tokens <= 100
    assert "# [ACC-MCP] Context truncated" in truncated


# ---------------------------------------------------------------------------
# 4. GuardRail protect() Full Pipeline Integration Tests
# ---------------------------------------------------------------------------


def test_guardrail_protect_pipeline(default_guard: GuardRail, sample_tier1_code: str):
    """Verify protect() enforces both security checks and ceiling limits."""
    # 1. Security rejection on protected file
    with pytest.raises(SecurityViolationError):
        default_guard.protect(".env", sample_tier1_code)

    with pytest.raises(SecurityViolationError):
        default_guard.protect("alembic/migrations/versions/001_init.py", sample_tier1_code)

    # 2. Allowed path with normal code
    result = default_guard.protect("src/services/db.py", sample_tier1_code, max_tokens=2000)
    assert result == sample_tier1_code

    # 3. Allowed path with tight ceiling truncation
    tight_result = default_guard.protect("src/services/db.py", sample_tier1_code, max_tokens=60)
    assert default_guard.token_counter.count_tokens(tight_result) <= 60
    assert "# [ACC-MCP] Context truncated" in tight_result
