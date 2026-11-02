"""Unit tests for ASTParser Tier 2 context compression, call-graph slicing, and metrics using pytest."""

import ast
import pytest
from acc_mcp.parser import ASTParser


def test_cyclomatic_complexity_calculation(complex_parser: ASTParser):
    symbols = complex_parser.get_symbols()

    # log_audit is linear: CC = 1
    assert symbols["log_audit"].cyclomatic_complexity == 1

    # validate_user: base (1) + if (1) + if (1) + bool_op 'or' (1) = 4
    val_info = symbols["validate_user"]
    assert val_info.cyclomatic_complexity >= 3

    # PaymentProcessor.process_transaction has multiple if, while, try-except, bool_op
    tx_info = complex_parser.get_symbol_info("PaymentProcessor.process_transaction")
    assert tx_info is not None
    assert tx_info.cyclomatic_complexity >= 6


def test_dependency_callees_and_exceptions(complex_parser: ASTParser):
    symbols = complex_parser.get_symbols()

    # validate_user raises ValueError and PermissionError
    val_info = symbols["validate_user"]
    assert "ValueError" in val_info.raised_exceptions
    assert "PermissionError" in val_info.raised_exceptions

    # process_transaction calls log_audit, validate_user
    tx_info = complex_parser.get_symbol_info("PaymentProcessor.process_transaction")
    assert tx_info is not None
    assert "log_audit" in tx_info.callees
    assert "validate_user" in tx_info.callees
    assert "ValueError" in tx_info.raised_exceptions
    assert "ServiceError" in tx_info.raised_exceptions


def test_reverse_dependency_callers_and_in_degree(complex_parser: ASTParser):
    symbols = complex_parser.get_symbols()

    # validate_user is called by PaymentProcessor.process_transaction
    val_info = symbols["validate_user"]
    assert val_info.in_degree >= 1
    assert any("process_transaction" in caller for caller in val_info.callers)

    # log_audit is called by PaymentProcessor.process_transaction
    audit_info = symbols["log_audit"]
    assert audit_info.in_degree >= 1
    assert any("process_transaction" in caller for caller in audit_info.callers)


def test_tier2_context_generation(complex_parser: ASTParser):
    # Generate Tier 2 for validate_user
    tier2_output = complex_parser.get_tier2_context(symbol_name="validate_user")

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


def test_tier2_whole_file_context(complex_parser: ASTParser):
    whole_tier2 = complex_parser.get_tier2_context(symbol_name=None)

    assert "Tier 2 Context: Module Skeleton with Dependency Graph" in whole_tier2
    assert "Symbol: validate_user" in whole_tier2
    assert "Symbol: PaymentProcessor.process_transaction" in whole_tier2
    assert "class PaymentProcessor" in whole_tier2
