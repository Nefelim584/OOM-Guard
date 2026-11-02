"""Unit tests for ASTParser Tier 1 compression and TokenCounter."""

import ast
import pytest
from acc_mcp.parser import ASTParser, SymbolInfo
from acc_mcp.tokens import HeuristicTokenCounter, EndpointTokenCounter, get_token_counter


SAMPLE_CODE = '''"""Module docstring for testing."""

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


def test_symbol_indexing():
    parser = ASTParser(SAMPLE_CODE, file_path="sample.py")
    symbols = parser.get_symbols()

    assert "DatabaseManager" in symbols
    assert symbols["DatabaseManager"].kind == "class"
    assert "DatabaseManager.connect" in symbols
    assert symbols["DatabaseManager.connect"].kind == "method"
    assert symbols["DatabaseManager.connect"].has_type_hints is True
    assert "compute_metrics" in symbols
    assert symbols["compute_metrics"].kind == "function"
    assert symbols["compute_metrics"].has_type_hints is True


def test_tier1_whole_file_compression():
    parser = ASTParser(SAMPLE_CODE, file_path="sample.py")
    tier1_output = parser.get_tier1_context()

    # Verify docstring preserved
    assert "Module docstring for testing." in tier1_output
    assert "Manages database connection and queries." in tier1_output
    assert "Execute SQL query asynchronously." in tier1_output

    # Verify bodies are replaced with ...
    assert "..." in tier1_output
    assert 'print(f"Connecting to {self.host}:{self.port}")' not in tier1_output
    assert "total = sum(data)" not in tier1_output

    # Verify valid Python syntax
    parsed = ast.parse(tier1_output)
    assert isinstance(parsed, ast.Module)


def test_tier1_single_symbol_isolation():
    parser = ASTParser(SAMPLE_CODE, file_path="sample.py")

    # Target: compute_metrics
    tier1_isolated = parser.get_tier1_context(symbol_name="compute_metrics")

    assert "def compute_metrics(data: List[float], scale: float" in tier1_isolated
    assert "Compute normalized metric score." in tier1_isolated
    assert "DatabaseManager" not in tier1_isolated

    # Valid syntax
    parsed = ast.parse(tier1_isolated)
    assert isinstance(parsed, ast.Module)


def test_tier1_class_symbol_isolation():
    parser = ASTParser(SAMPLE_CODE, file_path="sample.py")

    # Target method inside class
    tier1_method = parser.get_tier1_context(symbol_name="execute_query")
    assert "class DatabaseManager" in tier1_method
    assert "async def execute_query" in tier1_method
    assert "compute_metrics" not in tier1_method


def test_heuristic_token_counter():
    counter = HeuristicTokenCounter()
    assert counter.count_tokens("") == 0

    tokens = counter.count_tokens("def hello() -> str:\n    return 'world'")
    assert tokens > 0
    assert tokens < 20


def test_endpoint_token_counter_fallback():
    # Points to non-existent endpoint, should gracefully fallback
    counter = EndpointTokenCounter(endpoint_url="http://127.0.0.1:9999/tokenize")
    tokens = counter.count_tokens(SAMPLE_CODE)
    assert tokens > 50

    factory_counter = get_token_counter()
    assert isinstance(factory_counter, HeuristicTokenCounter)
