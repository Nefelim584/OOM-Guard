"""Model Context Protocol (MCP) Server for Adaptive Context Compression (Block 2)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer
    except ImportError as exc:
        raise ImportError(
            "mcp SDK is required for acc_mcp.server. Install with 'pip install mcp>=1.0.0'."
        ) from exc

from acc_mcp.budget import ContextBudgetTracker
from acc_mcp.decision import DecisionModel
from acc_mcp.guard import GuardRail, SecurityViolationError
from acc_mcp.parser import ASTParser
from acc_mcp.tokens import TokenCounter

logger = logging.getLogger(__name__)


def create_server(
    ledger_path: str | Path | None = None,
    max_tokens: int = GuardRail.DEFAULT_MAX_TOKENS,
    token_counter: TokenCounter | None = None,
) -> MCPServer:
    """Create and configure an ACC-MCP server instance exposing context tools."""
    server = MCPServer(
        name="acc-mcp",
        version="0.1.0",
        instructions=(
            "ACC-MCP server provides intelligent, adaptive context pruning for coding agents, "
            "reducing token waste while strictly obeying security path rules and token ceilings."
        ),
    )

    guard = GuardRail(max_tokens=max_tokens, token_counter=token_counter)
    decision_model = DecisionModel()
    budget_tracker = ContextBudgetTracker(
        ledger_path=ledger_path,
        auto_save=True,
        token_counter=token_counter,
    )

    # Attach components to server for programmatic inspection
    server.guard = guard  # type: ignore[attr-defined]
    server.decision_model = decision_model  # type: ignore[attr-defined]
    server.budget_tracker = budget_tracker  # type: ignore[attr-defined]

    @server.tool()
    async def get_adaptive_context(
        file_path: str,
        task_intent: str,
        symbol_name: str | None = None,
    ) -> str:
        """Retrieve an adaptively compressed code slice for a target file and query.

        Extracts AST features, evaluates task depth, and returns Tier 1 (interfaces/docstrings),
        Tier 2 (call-graph/skeleton slice), or Tier 3 (full body), enforcing strict token ceilings
        and security path guardrails.
        """
        # 1. Enforce Protected Path Rule
        guard.validate_path(file_path)

        # 2. Resolve file path
        path_obj = Path(file_path)
        if not path_obj.is_absolute():
            path_obj = (Path.cwd() / path_obj).resolve()

        if not path_obj.exists() or not path_obj.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_content = path_obj.read_text(encoding="utf-8")

        # 3. Parse AST and cache symbol map
        parser = ASTParser(file_content, file_path=str(path_obj))
        budget_tracker.cache_symbols(str(path_obj), parser.get_symbols(), file_content)

        # 4. Predict optimal context tier
        decision = decision_model.select_tier(
            task_intent=task_intent,
            parser=parser,
            symbol_name=symbol_name,
        )

        # 5. Extract structural context slice
        if decision.tier == 1:
            raw_context = parser.get_tier1_context(symbol_name=symbol_name)
        elif decision.tier == 2:
            raw_context = parser.get_tier2_context(symbol_name=symbol_name)
        else:
            raw_context = parser.get_tier3_context(symbol_name=symbol_name)

        # 6. Enforce deterministic token ceiling
        final_context = guard.enforce_ceiling(raw_context)

        # 7. Record multi-turn token accounting
        budget_tracker.record_retrieval(
            file_path=str(path_obj),
            task_intent=task_intent,
            served_tier=decision.tier,
            baseline_content=file_content,
            served_content=final_context,
            symbol_name=symbol_name,
        )

        return final_context

    @server.tool()
    async def get_token_ledger() -> str:
        """Retrieve the cumulative session token audit log and savings ledger."""
        payload = {
            "summary": budget_tracker.get_summary(),
            "history": [r.to_dict() for r in budget_tracker.history],
        }
        return json.dumps(payload, indent=2)

    return server


def run_server() -> None:
    """Start ACC-MCP server with stdio transport."""
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    run_server()
