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
    workspace_root: str | Path | None = None,
) -> MCPServer:
    """Create and configure an ACC-MCP server instance exposing context tools.

    Only `.py` files inside `workspace_root` are served. When it is not given, the
    `ACC_MCP_ROOT` env var is used, then the current working directory.
    """
    server = MCPServer(
        name="acc-mcp",
        version="0.1.0",
        instructions=(
            "ACC-MCP server provides intelligent, adaptive context pruning for coding agents, "
            "reducing token waste while strictly obeying security path rules and token ceilings."
        ),
    )

    guard = GuardRail(
        max_tokens=max_tokens,
        token_counter=token_counter,
        workspace_root=workspace_root,
    )
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
        min_tier: int | None = None,
    ) -> str:
        """Retrieve an adaptively compressed code slice for a target file and query.

        The decision model picks the optimal tier from your task_intent:
        - Tier 1 (signatures/docstrings): use verbs like "check", "inspect", "view", "list"
        - Tier 2 (call-graph skeleton): use verbs like "trace", "refactor", "rename", "deprecate"
        - Tier 3 (full implementation): use verbs like "fix", "debug", "rewrite", "optimize"

        If the returned slice is insufficient, retry with min_tier=2 or min_tier=3 to
        escalate without changing your task_intent.  The model's own choice is never
        lowered below min_tier, but min_tier cannot lower it either.

        Every slice starts with `# <file> lines a-b: <symbol>` lines giving the real
        line ranges. Tier 3 is copied verbatim from the file, so it can be used for
        exact search-and-replace edits. If a slice is cut to fit the token ceiling, a
        `# [ACC-MCP] lines a-b omitted` note says what is missing and how to get it.

        Args:
            file_path: Path to a `.py` file inside the workspace root (relative paths
                       are taken from the root).
            task_intent: Short phrase describing what you need right now
                         (e.g. "check signature", "trace callers", "rewrite body").
            symbol_name: Optional target symbol within the file.
            min_tier: Optional floor (1, 2, or 3) for the served tier.
                      The served tier is max(model_tier, min_tier).
        """
        # 1. Enforce Protected Path Rule and resolve to the real file inside the workspace
        path_obj = guard.validate_path(file_path)

        # 2. Check the file exists
        if not path_obj.is_file():
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

        # 5. Apply min_tier escalation (never lower the model's choice)
        effective_min_tier = max(1, min(3, min_tier)) if min_tier is not None else None
        served_tier = decision.tier
        if effective_min_tier is not None and effective_min_tier > served_tier:
            served_tier = effective_min_tier

        # 6. Extract structural context slice at served tier
        if served_tier == 1:
            raw_context = parser.get_tier1_context(symbol_name=symbol_name)
        elif served_tier == 2:
            raw_context = parser.get_tier2_context(symbol_name=symbol_name)
        else:
            raw_context = parser.get_tier3_context(
                symbol_name=symbol_name,
                max_tokens=guard.max_tokens,
                token_counter=guard.token_counter,
            )

        # 7. Enforce deterministic token ceiling
        final_context = guard.enforce_ceiling(raw_context)

        # 8. Record multi-turn token accounting with escalation metadata
        budget_tracker.record_retrieval(
            file_path=str(path_obj),
            task_intent=task_intent,
            served_tier=served_tier,
            baseline_content=file_content,
            served_content=final_context,
            symbol_name=symbol_name,
            requested_min_tier=effective_min_tier,
            model_tier=decision.tier,
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
