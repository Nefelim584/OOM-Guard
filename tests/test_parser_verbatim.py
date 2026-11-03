"""Tests for edit-ready slices: verbatim Tier 3 source, line-range headers, and cut reporting (Phase 15)."""

import ast
import re

import pytest
from acc_mcp.parser import ASTParser
from acc_mcp.tokens import HeuristicTokenCounter

SOURCE = '''"""Payment service module."""

import os
from typing import Optional


class PaymentService:
    """Validates and charges payments."""

    retries: int = 3

    def __init__(self, gateway: str) -> None:
        self.gateway = gateway  # keep the raw gateway name

    @staticmethod
    def validate(amount: float, currency: str = "EUR") -> bool:
        # Reject non-positive amounts early
        if amount <= 0:
            return False
        return currency in ("EUR", "USD")

    def charge(self, amount: float) -> Optional[str]:
        """Charge the card."""
        if not self.validate(amount):
            return None
        return f"{self.gateway}:{amount}"


def helper(x):
    return x * 2   # double it
'''

HEADER_RE = re.compile(r"^# (\S+) lines (\d+)-(\d+): (.+)$", re.MULTILINE)


@pytest.fixture
def parser() -> ASTParser:
    return ASTParser(SOURCE, file_path="src/service.py")


def _line_of(fragment: str, source: str = SOURCE) -> int:
    for number, line in enumerate(source.splitlines(), 1):
        if fragment in line:
            return number
    raise AssertionError(f"{fragment!r} not in source")


def _chunk(start: int, end: int, source: str = SOURCE) -> str:
    return "".join(source.splitlines(keepends=True)[start - 1:end])


# ---------------------------------------------------------------------------
# 1. Tier 3 is copied verbatim from the file (Step 15.1)
# ---------------------------------------------------------------------------


def test_tier3_method_is_exact_substring_of_source(parser: ASTParser):
    out = parser.get_tier3_context(symbol_name="PaymentService.validate")

    start = _line_of("@staticmethod")
    end = _line_of('return currency in ("EUR", "USD")')
    chunk = _chunk(start, end)

    assert chunk in SOURCE
    assert f"# service.py lines {start}-{end}: PaymentService.validate\n{chunk}" in out


def test_tier3_keeps_comments_and_formatting(parser: ASTParser):
    method_out = parser.get_tier3_context(symbol_name="PaymentService.validate")
    assert "        # Reject non-positive amounts early\n" in method_out
    assert 'currency: str = "EUR"' in method_out
    assert '("EUR", "USD")' in method_out

    module_out = parser.get_tier3_context(symbol_name=None)
    assert "    return x * 2   # double it\n" in module_out
    assert "self.gateway = gateway  # keep the raw gateway name" in module_out


def test_tier3_whole_module_is_the_file(parser: ASTParser):
    total = len(SOURCE.splitlines())
    out = parser.get_tier3_context(symbol_name=None)
    assert out == f"# service.py lines 1-{total}: entire module\n{SOURCE}"


def test_tier3_method_slice_has_class_header_and_member_list(parser: ASTParser):
    out = parser.get_tier3_context(symbol_name="PaymentService.validate")

    cls_start = _line_of("class PaymentService:")
    header_end = _line_of("retries: int = 3")
    assert f"# service.py lines {cls_start}-{header_end}: PaymentService (class header)\n" in out
    assert _chunk(cls_start, header_end) in out

    init_span = parser.get_symbol_span("PaymentService.__init__")
    assert init_span is not None
    assert f"#   lines {init_span[0]}-{init_span[1]}: def __init__(self, gateway: str) -> None" in out
    assert "self.gateway = gateway" not in out

    # Imports are served verbatim with their own range
    assert f"# service.py lines {_line_of('import os')}-{_line_of('from typing')}: imports\n" in out

    # Class header + method body is still valid Python
    assert isinstance(ast.parse(out), ast.Module)


# ---------------------------------------------------------------------------
# 2. Every tier starts with line-range headers that match the file (Step 15.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tier, symbol_name",
    [
        (1, None),
        (1, "validate"),
        (2, None),
        (2, "PaymentService.charge"),
        (3, "PaymentService.charge"),
        (3, "helper"),
    ],
)
def test_headers_match_file_line_numbers(parser: ASTParser, tier: int, symbol_name: str | None):
    getter = {1: parser.get_tier1_context, 2: parser.get_tier2_context, 3: parser.get_tier3_context}[tier]
    out = getter(symbol_name=symbol_name)

    assert HEADER_RE.match(out.splitlines()[0]), f"slice does not start with a header: {out[:80]!r}"

    lines = SOURCE.splitlines()
    symbols = parser.get_symbols()
    headers = HEADER_RE.findall(out)
    assert headers
    for file_name, start, end, label in headers:
        assert file_name == "service.py"
        start, end = int(start), int(end)
        if label in symbols:
            assert parser.get_symbol_span(label) == (start, end)
            assert lines[start - 1].lstrip().startswith(("@", "def ", "async def ", "class "))
            assert lines[end - 1].strip()
        elif label.endswith(" (class header)"):
            cls_start, cls_end = parser.get_symbol_span(label.removesuffix(" (class header)"))
            assert start == cls_start and end < cls_end
            assert lines[start - 1].lstrip().startswith(("@", "class "))


def test_tier1_whole_file_header_lists_every_symbol(parser: ASTParser):
    out = parser.get_tier1_context(symbol_name=None)
    labels = {label for _, _, _, label in HEADER_RE.findall(out)}
    assert labels == {
        "PaymentService",
        "PaymentService.__init__",
        "PaymentService.validate",
        "PaymentService.charge",
        "helper",
    }
    assert isinstance(ast.parse(out), ast.Module)


# ---------------------------------------------------------------------------
# 3. A cut slice says exactly what was left out (Step 15.3)
# ---------------------------------------------------------------------------


def _many_functions_source(count: int = 30) -> str:
    parts = ['"""Module with many small functions."""', ""]
    for i in range(count):
        parts.append(
            f"def step_{i}(values: list[int]) -> int:\n"
            f"    # step {i} keeps only large values\n"
            f"    kept = [v for v in values if v > {i}]\n"
            f"    return sum(kept)\n"
        )
    return "\n".join(parts)


def test_cut_module_reports_omitted_range_and_symbol():
    source = _many_functions_source()
    big = ASTParser(source, file_path="big.py")
    counter = HeuristicTokenCounter()

    out = big.get_tier3_context(symbol_name=None, max_tokens=250, token_counter=counter)

    assert counter.count_tokens(out) <= 250
    match = re.search(
        r'# \[ACC-MCP\] lines (\d+)-(\d+) omitted to fit the 250-token ceiling; request symbol_name="(\w+)"',
        out,
    )
    assert match, out
    omitted_start, omitted_end, suggested = int(match.group(1)), int(match.group(2)), match.group(3)

    assert omitted_end == len(source.splitlines())
    span = big.get_symbol_span(suggested)
    assert span is not None and omitted_start <= span[0] <= omitted_end

    # What is shown is the verbatim start of the file, cut after a whole function
    kept = f"# big.py lines 1-{omitted_start - 1}: entire module\n" + _chunk(1, omitted_start - 1, source)
    assert out.startswith(kept)
    assert "is incomplete" not in out


def test_cut_inside_single_function_flags_it_as_incomplete():
    source = "def long_pipeline(x: int) -> int:\n"
    source += "".join(f"    x = x + {i}\n" for i in range(80))
    source += "    return x\n"
    total = len(source.splitlines())
    long_parser = ASTParser(source, file_path="pipeline.py")
    counter = HeuristicTokenCounter()

    out = long_parser.get_tier3_context(symbol_name="long_pipeline", max_tokens=200, token_counter=counter)

    assert counter.count_tokens(out) <= 200
    match = re.search(
        rf"# \[ACC-MCP\] long_pipeline is incomplete: it spans lines 1-{total} "
        r"but only lines up to (\d+) are shown; read the rest from the file before editing it",
        out,
    )
    assert match, out
    shown_end = int(match.group(1))
    assert f"# pipeline.py lines 1-{shown_end}: long_pipeline\n" + _chunk(1, shown_end, source) in out
    assert (
        f"# [ACC-MCP] lines {shown_end + 1}-{total} omitted to fit the 200-token ceiling; "
        "read these lines from the file to see them"
    ) in out


def test_cut_is_deterministic():
    big = ASTParser(_many_functions_source(), file_path="big.py")
    counter = HeuristicTokenCounter()
    first = big.get_tier3_context(symbol_name=None, max_tokens=300, token_counter=counter)
    second = big.get_tier3_context(symbol_name=None, max_tokens=300, token_counter=counter)
    assert first == second
