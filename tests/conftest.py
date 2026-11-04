"""Shared pytest fixtures for ACC-MCP test suite."""

from pathlib import Path

import pytest
from acc_mcp.decision import DecisionModel
from acc_mcp.guard import GuardRail
from acc_mcp.parser import ASTParser
from acc_mcp.tokens import HeuristicTokenCounter


@pytest.fixture
def sample_tier1_code() -> str:
    """Sample Python code covering classes, async methods, and typed functions."""
    return '''"""Module docstring for testing."""

import os
from typing import List, Optional

CONSTANT_VAL = 42

class DatabaseManager:
    """Manages database connection and queries."""

    connection_timeout: int = 30

    def __init__(self, host: str, port: int = 5432) -> None:
        """Initialize connection parameters."""
        self.host = host
        self.port = port
        self.is_connected = False

    def connect(self) -> bool:
        """Establish the database connection."""
        print(f"Connecting to {self.host}:{self.port}")
        self.is_connected = True
        return True

    async def execute_query(self, query: str, params: Optional[dict] = None) -> List[dict]:
        """Execute SQL query asynchronously."""
        if not self.is_connected:
            raise RuntimeError("Database not connected")
        return [{"id": 1, "query": query}]


def compute_metrics(data: List[float], scale: float = 1.0) -> float:
    """Compute normalized metric score."""
    total = sum(data)
    result = total * scale
    return result


def _internal_helper() -> None:
    pass
'''


@pytest.fixture
def complex_branching_code() -> str:
    """Sample Python code with high cyclomatic complexity, exceptions, and call chains."""
    return '''"""Module demonstrating branching and cross-function dependencies."""

class ServiceError(Exception):
    """Custom service exception."""
    pass


def log_audit(action: str, user_id: int) -> None:
    """Log an audit event."""
    print(f"Audit: {action} by {user_id}")


def validate_user(user_id: int, roles: list[str]) -> bool:
    """Validate user credentials and authorization."""
    if user_id <= 0:
        raise ValueError("Invalid user ID")

    if not roles or "admin" not in roles:
        raise PermissionError("User lacks admin role")

    return True


class PaymentProcessor:
    """Processes financial transactions with retry and validation."""

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries

    def process_transaction(self, user_id: int, amount: float, roles: list[str]) -> bool:
        """Process transaction with validation, branching, and logging."""
        log_audit("start_transaction", user_id)

        if amount <= 0:
            raise ValueError("Amount must be positive")

        is_valid = validate_user(user_id, roles)
        if not is_valid:
            raise ServiceError("Validation failed")

        attempts = 0
        while attempts < self.max_retries:
            try:
                if amount > 1000 and "vip" not in roles:
                    attempts += 1
                    continue
                log_audit("success_transaction", user_id)
                return True
            except Exception as exc:
                attempts += 1

        raise ServiceError("Transaction failed after max retries")
'''


@pytest.fixture
def tier1_parser(sample_tier1_code: str) -> ASTParser:
    return ASTParser(sample_tier1_code, file_path="sample_tier1.py")


@pytest.fixture
def complex_parser(complex_branching_code: str) -> ASTParser:
    return ASTParser(complex_branching_code, file_path="complex.py")


@pytest.fixture
def heuristic_counter() -> HeuristicTokenCounter:
    return HeuristicTokenCounter()


@pytest.fixture
def default_guard(tmp_path: Path) -> GuardRail:
    return GuardRail(workspace_root=tmp_path)


POLICY_WEIGHTS = {"w0": -2.0, "w1": 2.0, "w2": 0.2, "w3": 0.3, "w4": 1.0}


@pytest.fixture
def policy_server_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `create_server` use fixed weights (the pre-Phase 21 hand-picked ones).

    Tests that check what a Tier 2 slice contains use this, because the fitted default
    weights send most "trace callers" intents to Tier 3 (P(Full) just above 0.65).
    """
    monkeypatch.setattr("acc_mcp.server.DecisionModel", lambda: DecisionModel(**POLICY_WEIGHTS))
