"""Unit tests for ASTParser Tier 2 context compression, call-graph slicing, and metrics."""

import ast
import pytest
from acc_mcp.parser import ASTParser, CyclomaticComplexityVisitor, SymbolDependencyVisitor


COMPLEX_SAMPLE_CODE = '''"""Module demonstrating branching and cross-function dependencies."""

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
                # simulate processing
                if amount > 1000 and "vip" not in roles:
                    attempts += 1
                    continue
                log_audit("success_transaction", user_id)
                return True
            except Exception as exc:
                attempts += 1

        raise ServiceError("Transaction failed after max retries")
'''


def test_cyclomatic_complexity_calculation():
    parser = ASTParser(COMPLEX_SAMPLE_CODE, file_path="complex.py")
    symbols = parser.get_symbols()

    # log_audit is linear: CC = 1
    assert symbols["log_audit"].cyclomatic_complexity == 1

    # validate_user: base (1) + if (1) + if (1) + bool_op 'or' (1) = 4
    val_info = symbols["validate_user"]
    assert val_info.cyclomatic_complexity >= 3

    # PaymentProcessor.process_transaction has multiple if, while, try-except, bool_op
    tx_info = parser.get_symbol_info("PaymentProcessor.process_transaction")
    assert tx_info is not None
    assert tx_info.cyclomatic_complexity >= 6


def test_dependency_callees_and_exceptions():
    parser = ASTParser(COMPLEX_SAMPLE_CODE, file_path="complex.py")
    symbols = parser.get_symbols()

    # validate_user raises ValueError and PermissionError
    val_info = symbols["validate_user"]
    assert "ValueError" in val_info.raised_exceptions
    assert "PermissionError" in val_info.raised_exceptions

    # process_transaction calls log_audit, validate_user
    tx_info = parser.get_symbol_info("PaymentProcessor.process_transaction")
    assert tx_info is not None
    assert "log_audit" in tx_info.callees
    assert "validate_user" in tx_info.callees
    assert "ValueError" in tx_info.raised_exceptions
    assert "ServiceError" in tx_info.raised_exceptions


def test_reverse_dependency_callers_and_in_degree():
    parser = ASTParser(COMPLEX_SAMPLE_CODE, file_path="complex.py")
    symbols = parser.get_symbols()

    # validate_user is called by PaymentProcessor.process_transaction
    val_info = symbols["validate_user"]
    assert val_info.in_degree >= 1
    assert any("process_transaction" in caller for caller in val_info.callers)

    # log_audit is called by PaymentProcessor.process_transaction
    audit_info = symbols["log_audit"]
    assert audit_info.in_degree >= 1
    assert any("process_transaction" in caller for caller in audit_info.callers)


def test_tier2_context_generation():
    parser = ASTParser(COMPLEX_SAMPLE_CODE, file_path="complex.py")

    # Generate Tier 2 for validate_user
    tier2_output = parser.get_tier2_context(symbol_name="validate_user")

    # Verify Call-graph metadata header
    assert "Tier 2 Context: Call-Graph Slice for 'validate_user'" in tier2_output
    assert "Cyclomatic Complexity (CC):" in tier2_output
    assert "In-degree (Callers count): 1" in tier2_output
    assert "Raised Exceptions: ['PermissionError', 'ValueError']" in tier2_output

    # Verify dependency interface inclusion
    assert "PaymentProcessor" in tier2_output

    # Verify target interface inclusion
    assert "def validate_user(user_id: int, roles: list[str]) -> bool:" in tier2_output
    assert "..." in tier2_output


def test_tier2_whole_file_context():
    parser = ASTParser(COMPLEX_SAMPLE_CODE, file_path="complex.py")
    whole_tier2 = parser.get_tier2_context(symbol_name=None)

    assert "Tier 2 Context: Module Skeleton with Dependency Graph" in whole_tier2
    assert "Symbol: validate_user" in whole_tier2
    assert "Symbol: PaymentProcessor.process_transaction" in whole_tier2
    assert "class PaymentProcessor" in whole_tier2
