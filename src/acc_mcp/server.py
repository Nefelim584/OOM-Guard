"""Model Context Protocol (MCP) Server for Adaptive Context Compression (Block 2)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
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

from acc_mcp.budget import LOOKUP_TOOL, ContextBudgetTracker, ServedRange
from acc_mcp.decision import DecisionModel
from acc_mcp.guard import GuardRail, SecurityViolationError
from acc_mcp.index import WorkspaceIndex
from acc_mcp.parser import ASTParser
from acc_mcp.tokens import TokenCounter

logger = logging.getLogger(__name__)

FIND_SYMBOL_LIMIT = 50


@dataclass
class BudgetedSlice:
    """A slice fitted into the session budget; tier is None when nothing was served."""

    text: str
    tier: int | None
    budget_limited: bool = False


def _render_tier(
    parser: ASTParser,
    guard: GuardRail,
    tier: int,
    symbol_name: str | None,
    ceiling: int,
) -> str:
    """Slice at the given tier, cut to the token ceiling."""
    if tier == 1:
        raw = parser.get_tier1_context(symbol_name=symbol_name)
    elif tier == 2:
        raw = parser.get_tier2_context(symbol_name=symbol_name)
    else:
        raw = parser.get_tier3_context(
            symbol_name=symbol_name,
            max_tokens=ceiling,
            token_counter=guard.token_counter,
        )
    return guard.enforce_ceiling(raw, max_tokens=ceiling)


def _budget_note(tier: int, wanted_tier: int, remaining: int, budget: int) -> str:
    """Header line saying the session budget lowered or cut this slice."""
    if tier < wanted_tier:
        what = f"Served Tier {tier} instead of Tier {wanted_tier}"
    else:
        what = f"Tier {tier} slice cut to fit"
    return (
        f"# [ACC-MCP] {what}: only {remaining} of the {budget}-token session budget is left; "
        f"call reset_session when you start a new task\n"
    )


def fit_session_budget(
    parser: ASTParser,
    guard: GuardRail,
    tracker: ContextBudgetTracker,
    wanted_tier: int,
    min_tier: int | None,
    symbol_name: str | None,
) -> BudgetedSlice:
    """Serve wanted_tier if it fits the remaining session budget, otherwise one tier lower.

    The ceiling for a slice is min(C_max, remaining). The lower tier is cut to that ceiling
    if needed, but a tier the agent asked for with min_tier is never cut: a message saying
    what does not fit is returned instead.
    """
    count = guard.token_counter.count_tokens
    remaining = tracker.remaining_session_tokens
    budget = tracker.session_budget

    sliced = _render_tier(parser, guard, wanted_tier, symbol_name, guard.max_tokens)
    if count(sliced) <= remaining:
        return BudgetedSlice(sliced, wanted_tier)

    tier = wanted_tier - 1 if wanted_tier > (min_tier or 1) else wanted_tier
    note = _budget_note(tier, wanted_tier, remaining, budget)
    if tier < wanted_tier:
        sliced = _render_tier(parser, guard, tier, symbol_name, guard.max_tokens)
        if count(note + sliced) <= remaining:
            return BudgetedSlice(note + sliced, tier, budget_limited=True)

    if min_tier is not None and min_tier > 1 and tier == min_tier:
        return BudgetedSlice(
            f"# [ACC-MCP] Not served: the Tier {tier} slice needs about {count(sliced)} tokens, "
            f"but only {remaining} of the {budget}-token session budget is left. Retry with a "
            f"lower min_tier or a narrower symbol_name, or call reset_session if you are "
            f"starting a new task.\n",
            None,
        )

    limit = min(guard.max_tokens, remaining - count(note))
    if limit > 0:
        cut = note + _render_tier(parser, guard, tier, symbol_name, limit)
        if count(cut) <= remaining:
            return BudgetedSlice(cut, tier, budget_limited=True)

    return BudgetedSlice(
        f"# [ACC-MCP] Not served: the {budget}-token session budget is used up "
        f"({remaining} tokens left). Work with the slices you already have, or call "
        f"reset_session if you are starting a new task.\n",
        None,
    )


def create_server(
    ledger_path: str | Path | None = None,
    max_tokens: int = GuardRail.DEFAULT_MAX_TOKENS,
    token_counter: TokenCounter | None = None,
    workspace_root: str | Path | None = None,
    session_budget: int | None = None,
    history_limit: int = ContextBudgetTracker.DEFAULT_HISTORY_LIMIT,
) -> MCPServer:
    """Create and configure an ACC-MCP server instance exposing context tools.

    Only `.py` files inside `workspace_root` are served. When it is not given, the
    `ACC_MCP_ROOT` env var is used, then the current working directory.

    `session_budget` caps the tokens served per session (default: `ACC_MCP_SESSION_BUDGET`
    env var, then 6,000). It is a share of the agent's window, since the server only sees
    its own output.
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
        session_budget=session_budget,
        history_limit=history_limit,
    )
    workspace_index = WorkspaceIndex(guard=guard, tracker=budget_tracker)

    # Attach components to server for programmatic inspection
    server.guard = guard  # type: ignore[attr-defined]
    server.decision_model = decision_model  # type: ignore[attr-defined]
    server.budget_tracker = budget_tracker  # type: ignore[attr-defined]
    server.workspace_index = workspace_index  # type: ignore[attr-defined]

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
        - Tier 2 (call-graph skeleton, with callers from other workspace files and their
          line numbers): use verbs like "trace", "refactor", "rename", "deprecate"
        - Tier 3 (full implementation): use verbs like "fix", "debug", "rewrite", "optimize"

        If the returned slice is insufficient, retry with min_tier=2 or min_tier=3 to
        escalate without changing your task_intent.  The model's own choice is never
        lowered below min_tier, but min_tier cannot lower it either.

        Every slice starts with `# <file> lines a-b: <symbol>` lines giving the real
        line ranges. Tier 3 is copied verbatim from the file, so it can be used for
        exact search-and-replace edits. If a slice is cut to fit the token ceiling, a
        `# [ACC-MCP] lines a-b omitted` note says what is missing and how to get it.

        All slices in a session share a fixed token budget (see get_token_ledger). When
        too little is left, the slice is served one tier lower with a `# [ACC-MCP] Served
        Tier n instead of Tier m` note, and a tier forced with min_tier that does not fit
        is refused with a `# [ACC-MCP] Not served` message. Call reset_session at the start
        of every new task.

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

        # 3. Bring the workspace symbol cache up to date, parse the file, and add callers from other files
        index_stats = workspace_index.refresh(save=False)
        parser = ASTParser(
            file_content,
            file_path=str(path_obj),
            package=workspace_index.package_name(path_obj),
        )
        workspace_index.attach_cross_file_callers(path_obj, parser)

        # 4. Predict optimal context tier
        decision = decision_model.select_tier(
            task_intent=task_intent,
            parser=parser,
            symbol_name=symbol_name,
        )

        # 5. Apply min_tier escalation (never lower the model's choice)
        effective_min_tier = max(1, min(3, min_tier)) if min_tier is not None else None
        wanted_tier = max(decision.tier, effective_min_tier or 1)

        # 6. Extract the slice under min(C_max, remaining session budget), stepping down a tier if needed
        served = fit_session_budget(
            parser=parser,
            guard=guard,
            tracker=budget_tracker,
            wanted_tier=wanted_tier,
            min_tier=effective_min_tier,
            symbol_name=symbol_name,
        )

        # 7. Save the ledger once per request: record_retrieval saves it, a refusal only if the index changed
        if served.tier is None:
            if index_stats.changed and budget_tracker.auto_save:
                budget_tracker.save_ledger()
            return served.text

        line_ranges = [
            ServedRange(file=str(path_obj), symbol=label, start=start, end=end)
            for label, start, end in parser.served_line_ranges(served.text)
        ]
        budget_tracker.record_retrieval(
            file_path=str(path_obj),
            task_intent=task_intent,
            served_tier=served.tier,
            whole_file_content=file_content,
            served_content=served.text,
            symbol_name=symbol_name,
            requested_min_tier=effective_min_tier,
            model_tier=decision.tier,
            budget_limited=served.budget_limited,
            line_ranges=line_ranges,
        )

        return served.text

    @server.tool()
    async def find_symbol(name: str) -> str:
        """Find where a class, function, or method is defined anywhere in the workspace.

        Use this when you know a symbol's name but not its file. Each match gives a
        `file_path` relative to the workspace root (pass it straight to
        get_adaptive_context), the qualified `symbol` name, its `kind`, and
        `lineno`-`end_lineno` (the `def`/`class` line through the end of its body;
        decorators sit just above `lineno`).

        Args:
            name: Bare name ("authenticate") or qualified name ("UserManager.authenticate").
        """
        workspace_index.refresh(save=False)
        matches = workspace_index.find(name)
        payload = {
            "query": name,
            "matches": [m.to_dict() for m in matches[:FIND_SYMBOL_LIMIT]],
            "total_matches": len(matches),
            "truncated": len(matches) > FIND_SYMBOL_LIMIT,
        }
        text = json.dumps(payload, indent=2)
        # Saves the ledger (and the refreshed index) once for this request
        budget_tracker.record_tool_output(LOOKUP_TOOL, text)
        return text

    @server.tool()
    async def get_token_ledger() -> str:
        """Retrieve the token ledger: what ACC-MCP served, per tier and per tool.

        `summary` has all-time totals: `cumulative_served_tokens` (slices),
        `served_tokens_by_tier`, `served_tokens_by_tool` (get_adaptive_context and
        find_symbol), `escalation_rate` (share of requests where min_tier was raised above
        the model's tier), and the current session budget (`session_budget`,
        `session_served_tokens`, `session_remaining_tokens`).

        `whole_file_tokens` and `upper_bound_savings_pct` compare the slices with reading
        each whole file. That is an upper bound, NOT the saving against what the agent
        would really have read: agents usually grep and read small line windows instead.

        `history` holds only the most recent `history_limit` requests; each record lists
        the `line_ranges` (file, symbol, start, end) shown in its slice.
        """
        payload = {
            "summary": budget_tracker.get_summary(),
            "history": [r.to_dict() for r in budget_tracker.history],
        }
        return json.dumps(payload, indent=2)

    @server.tool()
    async def reset_session() -> str:
        """Start a fresh session token budget. Call this once at the start of each new task.

        Slices served earlier no longer count against the budget. All-time totals and the
        history in get_token_ledger are kept.
        """
        payload = {
            "previous_session_served_tokens": budget_tracker.session_served_tokens,
            "previous_session_requests": budget_tracker.session_requests,
        }
        budget_tracker.reset_session()
        payload["session_budget"] = budget_tracker.session_budget
        payload["session_remaining_tokens"] = budget_tracker.remaining_session_tokens
        return json.dumps(payload, indent=2)

    return server


def run_server() -> None:
    """Start ACC-MCP server with stdio transport."""
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    run_server()
