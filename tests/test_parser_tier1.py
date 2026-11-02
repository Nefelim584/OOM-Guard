"""Unit tests for ASTParser Tier 1 compression and TokenCounter using pytest fixtures."""

import ast
import pytest
from acc_mcp.parser import ASTParser, SymbolInfo
from acc_mcp.tokens import HeuristicTokenCounter, EndpointTokenCounter, get_token_counter


def test_symbol_indexing(tier1_parser: ASTParser):
    symbols = tier1_parser.get_symbols()

    assert "DatabaseManager" in symbols
    assert symbols["DatabaseManager"].kind == "class"
    assert "DatabaseManager.connect" in symbols
    assert symbols["DatabaseManager.connect"].kind == "method"
    assert symbols["DatabaseManager.connect"].has_type_hints is True
    assert "compute_metrics" in symbols
    assert symbols["compute_metrics"].kind == "function"
    assert symbols["compute_metrics"].has_type_hints is True


def test_tier1_whole_file_compression(tier1_parser: ASTParser):
    tier1_output = tier1_parser.get_tier1_context()

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


def test_tier1_single_symbol_isolation(tier1_parser: ASTParser):
    tier1_isolated = tier1_parser.get_tier1_context(symbol_name="compute_metrics")

    assert "def compute_metrics(data: List[float], scale: float" in tier1_isolated
    assert "Compute normalized metric score." in tier1_isolated
    assert "DatabaseManager" not in tier1_isolated

    # Valid syntax
    parsed = ast.parse(tier1_isolated)
    assert isinstance(parsed, ast.Module)


def test_tier1_class_symbol_isolation(tier1_parser: ASTParser):
    tier1_method = tier1_parser.get_tier1_context(symbol_name="execute_query")
    assert "class DatabaseManager" in tier1_method
    assert "async def execute_query" in tier1_method
    assert "compute_metrics" not in tier1_method


def test_heuristic_token_counter(heuristic_counter: HeuristicTokenCounter):
    assert heuristic_counter.count_tokens("") == 0

    tokens = heuristic_counter.count_tokens("def hello() -> str:\n    return 'world'")
    assert tokens > 0
    assert tokens < 20


def test_endpoint_token_counter_fallback(sample_tier1_code: str):
    counter = EndpointTokenCounter(endpoint_url="http://127.0.0.1:9999/tokenize")
    tokens = counter.count_tokens(sample_tier1_code)
    assert tokens > 50

    factory_counter = get_token_counter()
    assert isinstance(factory_counter, HeuristicTokenCounter)
