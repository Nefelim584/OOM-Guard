"""Tests for the import-aware, cross-file call graph and its use in Tier 2 and x3 (Phase 18)."""

from pathlib import Path

import pytest

from acc_mcp.budget import ContextBudgetTracker
from acc_mcp.decision import DecisionModel
from acc_mcp.guard import GuardRail
from acc_mcp.index import WorkspaceIndex
from acc_mcp.parser import ASTParser, CallerRef
from acc_mcp.server import create_server

BILLING_PY = (
    "class Invoice:\n"                      # 1
    "    def total(self) -> int:\n"         # 2
    "        return self.subtotal() + 1\n"  # 3
    "\n"                                    # 4
    "    def subtotal(self) -> int:\n"      # 5
    "        return 10\n"                   # 6
    "\n"                                    # 7
    "\n"                                    # 8
    "class Cart:\n"                         # 9
    "    def total(self) -> int:\n"         # 10
    "        return 0\n"                    # 11
    "\n"                                    # 12
    "    def checkout(self) -> int:\n"      # 13
    "        return self.total()\n"         # 14
)
UTILS_PY = (
    "def calculate_hash(data: str) -> str:\n"  # 1
    '    """Hash a string."""\n'               # 2
    "    return str(hash(data))\n"             # 3
)
SERVICE_PY = (
    "from pkg.utils import calculate_hash\n"   # 1
    "from . import billing\n"                  # 2
    "from .billing import Invoice\n"           # 3
    "\n"                                       # 4
    "\n"                                       # 5
    "def sign(payload: str) -> str:\n"         # 6
    "    return calculate_hash(payload)\n"     # 7
    "\n"                                       # 8
    "\n"                                       # 9
    "def invoice_total(invoice: Invoice) -> int:\n"  # 10
    "    return invoice.total()\n"             # 11
    "\n"                                       # 12
    "\n"                                       # 13
    "def legacy_total(thing) -> int:\n"        # 14
    "    return thing.total()\n"               # 15
    "\n"                                       # 16
    "\n"                                       # 17
    "def build() -> int:\n"                    # 18
    "    return billing.Cart().checkout()\n"   # 19
)
APP_PY = (
    "import pkg.utils\n"                       # 1
    "import pkg.utils as hashing\n"            # 2
    "\n"                                       # 3
    "\n"                                       # 4
    "def run(data: str) -> str:\n"             # 5
    "    return pkg.utils.calculate_hash(data) + hashing.calculate_hash(data)\n"  # 6
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "billing.py").write_text(BILLING_PY, encoding="utf-8")
    (root / "pkg" / "utils.py").write_text(UTILS_PY, encoding="utf-8")
    (root / "pkg" / "service.py").write_text(SERVICE_PY, encoding="utf-8")
    (root / "app.py").write_text(APP_PY, encoding="utf-8")
    return root


@pytest.fixture
def index(workspace: Path, tmp_path: Path) -> WorkspaceIndex:
    workspace_index = WorkspaceIndex(
        guard=GuardRail(workspace_root=workspace),
        tracker=ContextBudgetTracker(ledger_path=tmp_path / "ledger.json"),
    )
    workspace_index.refresh()
    return workspace_index


def refs(callers: list[CallerRef]) -> list[tuple[str, str, int]]:
    return sorted((c.file_path, c.symbol, c.line) for c in callers)


# ---------------------------------------------------------------------------
# 1. In-File Resolution: self., super(), typed receivers, unknown receivers (Step 18.2)
# ---------------------------------------------------------------------------


def test_self_call_resolves_to_own_class():
    """Verify `self.total()` in Cart links to Cart.total, never to the same-named Invoice.total."""
    symbols = ASTParser(BILLING_PY).get_symbols()

    assert symbols["Cart.checkout"].callees == {"Cart.total"}
    assert symbols["Cart.total"].callers == {"Cart.checkout"}
    assert symbols["Invoice.total"].callers == set()
    assert symbols["Invoice.subtotal"].callers == {"Invoice.total"}


def test_unknown_receiver_is_possible_caller_and_not_counted():
    """Verify `thing.total()` with an untyped receiver is a possible caller, excluded from in-degree."""
    code = (
        "class Invoice:\n"
        "    def total(self) -> int:\n"
        "        return 1\n"
        "\n"
        "\n"
        "class Cart:\n"
        "    def total(self) -> int:\n"
        "        return 0\n"
        "\n"
        "\n"
        "def legacy_total(thing) -> int:\n"
        "    return thing.total()\n"
        "\n"
        "\n"
        "def typed_total(cart: Cart) -> int:\n"
        "    return cart.total()\n"
        "\n"
        "\n"
        "def built_total() -> int:\n"
        "    invoice = Invoice()\n"
        "    return invoice.total()\n"
    )
    parser = ASTParser(code)
    symbols = parser.get_symbols()

    assert symbols["Invoice.total"].callers == {"built_total"}
    assert symbols["Cart.total"].callers == {"typed_total"}
    assert symbols["Invoice.total"].possible_callers == {"legacy_total"}
    assert symbols["Cart.total"].possible_callers == {"legacy_total"}
    assert symbols["Invoice.total"].in_degree == 1
    assert symbols["Invoice"].callers == {"built_total"}
    assert symbols["legacy_total"].possible_calls == {"total": 12}
    assert symbols["legacy_total"].callees == set()

    tier2 = parser.get_tier2_context(symbol_name="Invoice.total")
    assert "# In-degree (Callers count): 1" in tier2
    assert "# Possible callers (receiver type unknown, not counted in in-degree):" in tier2
    assert "#   legacy_total (this file)" in tier2


def test_super_and_inherited_self_calls_resolve_to_base_class():
    """Verify `super().save()` and an inherited `self.log()` resolve to the base class methods."""
    code = (
        "class Base:\n"
        "    def save(self) -> None:\n"
        "        pass\n"
        "\n"
        "    def log(self) -> None:\n"
        "        pass\n"
        "\n"
        "\n"
        "class Child(Base):\n"
        "    def save(self) -> None:\n"
        "        super().save()\n"
        "        self.log()\n"
    )
    symbols = ASTParser(code).get_symbols()

    assert symbols["Child.save"].callees == {"Base.save", "Base.log"}
    assert symbols["Base.save"].callers == {"Child.save"}
    assert symbols["Base.log"].callers == {"Child.save"}


def test_builtin_calls_are_not_recorded():
    """Verify calls to builtins such as `str()` and `hash()` produce no call-graph edges."""
    info = ASTParser(UTILS_PY).get_symbols()["calculate_hash"]

    assert info.callees == set()
    assert info.external_calls == {}
    assert info.possible_calls == {}


# ---------------------------------------------------------------------------
# 2. Import Resolution (Step 18.1)
# ---------------------------------------------------------------------------


def test_imports_become_dotted_external_calls():
    """Verify absolute, relative, and module-alias imports turn calls into dotted targets with lines."""
    symbols = ASTParser(SERVICE_PY, package="pkg").get_symbols()

    assert symbols["sign"].external_calls == {"pkg.utils.calculate_hash": 7}
    assert symbols["invoice_total"].external_calls == {"pkg.billing.Invoice.total": 11}
    assert symbols["build"].external_calls == {"pkg.billing.Cart": 19}
    assert symbols["build"].possible_calls == {"checkout": 19}

    app = ASTParser(APP_PY, package="").get_symbols()
    assert app["run"].external_calls == {"pkg.utils.calculate_hash": 6}


def test_relative_import_without_package_is_not_resolved():
    """Verify relative imports are skipped when the parser does not know the file's package."""
    symbols = ASTParser(SERVICE_PY).get_symbols()

    assert symbols["sign"].external_calls == {"pkg.utils.calculate_hash": 7}
    assert symbols["invoice_total"].external_calls == {}
    assert symbols["invoice_total"].possible_calls == {"total": 11}


# ---------------------------------------------------------------------------
# 3. Cross-File Callers via the Workspace Index (Steps 18.1 & 18.3)
# ---------------------------------------------------------------------------


def test_cross_file_callers_follow_imports(index: WorkspaceIndex, workspace: Path):
    """Verify callers in other modules are found through `from x import y`, `import x`, and `import x as y`."""
    resolved, possible = index.callers_of((workspace / "pkg" / "utils.py").resolve(), "calculate_hash")

    assert refs(resolved) == [("app.py", "run", 6), ("pkg/service.py", "sign", 7)]
    assert possible == []


def test_same_named_method_in_unrelated_class_is_not_counted(index: WorkspaceIndex, workspace: Path):
    """Verify a typed `invoice.total()` counts only for Invoice.total; untyped `thing.total()` is only possible."""
    billing = (workspace / "pkg" / "billing.py").resolve()

    invoice_resolved, invoice_possible = index.callers_of(billing, "Invoice.total")
    assert refs(invoice_resolved) == [("pkg/service.py", "invoice_total", 11)]
    assert refs(invoice_possible) == [("pkg/service.py", "legacy_total", 15)]

    cart_resolved, cart_possible = index.callers_of(billing, "Cart.total")
    assert cart_resolved == []
    assert refs(cart_possible) == [("pkg/service.py", "legacy_total", 15)]


def test_constructor_call_through_module_alias(index: WorkspaceIndex, workspace: Path):
    """Verify `billing.Cart()` is a resolved caller of Cart, and the chained `.checkout()` only a possible one."""
    billing = (workspace / "pkg" / "billing.py").resolve()

    assert refs(index.callers_of(billing, "Cart")[0]) == [("pkg/service.py", "build", 19)]
    resolved, possible = index.callers_of(billing, "Cart.checkout")
    assert resolved == []
    assert refs(possible) == [("pkg/service.py", "build", 19)]


def test_x3_counts_cross_file_callers(index: WorkspaceIndex, workspace: Path):
    """Verify the decision model's in-degree x3 includes callers from other files."""
    utils = (workspace / "pkg" / "utils.py").resolve()
    parser = ASTParser(UTILS_PY, file_path=str(utils), package="pkg")
    assert parser.get_symbol_info("calculate_hash").in_degree == 0

    index.attach_cross_file_callers(utils, parser)
    features = DecisionModel().extract_features("Trace callers", parser, "calculate_hash")

    assert features.in_degree == 2


def test_edited_caller_file_updates_cross_file_callers(index: WorkspaceIndex, workspace: Path):
    """Verify removing a call in another file removes that caller after refresh."""
    (workspace / "app.py").write_text("def run(data: str) -> str:\n    return data\n", encoding="utf-8")
    index.refresh()

    resolved, _ = index.callers_of((workspace / "pkg" / "utils.py").resolve(), "calculate_hash")
    assert refs(resolved) == [("pkg/service.py", "sign", 7)]


# ---------------------------------------------------------------------------
# 4. Tier 2 Through the MCP Server (Step 18.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier2_lists_cross_file_callers_with_lines(workspace: Path, tmp_path: Path):
    """Verify Tier 2 shows callers in other files with path and line, and counts them in in-degree."""
    server = create_server(ledger_path=tmp_path / "ledger.json", workspace_root=workspace)

    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": "pkg/utils.py",
            "task_intent": "Trace callers of calculate_hash",
            "symbol_name": "calculate_hash",
        },
    )
    text = result.content[0].text

    assert "Tier 2 Context: Call-Graph Slice for 'calculate_hash'" in text
    assert "# In-degree (Callers count): 2" in text
    assert "# Callers in other files (2):" in text
    assert "#   app.py line 6: run" in text
    assert "#   pkg/service.py line 7: sign" in text


@pytest.mark.asyncio
async def test_tier2_separates_resolved_and_possible_callers(workspace: Path, tmp_path: Path):
    """Verify Tier 2 for Invoice.total counts the typed caller and lists the untyped one as possible."""
    server = create_server(ledger_path=tmp_path / "ledger.json", workspace_root=workspace)

    result = await server.call_tool(
        "get_adaptive_context",
        {
            "file_path": "pkg/billing.py",
            "task_intent": "Trace callers of Invoice.total",
            "symbol_name": "Invoice.total",
        },
    )
    text = result.content[0].text

    assert "# In-degree (Callers count): 1" in text
    assert "#   pkg/service.py line 11: invoice_total" in text
    possible_block = text.split("# Possible callers (receiver type unknown, not counted in in-degree):")[1]
    assert possible_block.startswith("\n#   pkg/service.py line 15: legacy_total")
