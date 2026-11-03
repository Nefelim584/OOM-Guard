# Adaptive Context Compressor MCP (ACC-MCP) - Implementation Plan

## Phase 1: Core AST Extractor & Tier 1 Compression
*Feature: AST analysis and Tier 1 interface extraction (signatures, docstrings, type hints, stubbed bodies).*
- [x] Step 1.1: Set up project structure, python dependencies (`mcp`, `pytest`, `tiktoken`), and package layout.
- [x] Step 1.2: Implement `ASTParser` in `src/acc_mcp/parser.py` to extract classes, functions, docstrings, and type annotations with `...` body stubs.
- [x] Step 1.3: Implement pluggable token counter in `src/acc_mcp/tokens.py` supporting remote `/tokenize` endpoints (`llama-server`, `Ollama`) and fast offline fallback (`tiktoken` / heuristic).
- [x] Step 1.4: Write unit tests in `tests/test_parser_tier1.py` validating Tier 1 generation and token counter implementations.
- [x] Step 1.5: Run test suite, verify Tier 1 extraction passes, and suggest commit name: `feat(parser): implement AST extractor and Tier 1 interface compressor`.

## Phase 2: Call-Graph Slicing & AST Metrics (Tier 2 Compression)
*Feature: Call-graph analysis, caller/callee tracing, cyclomatic complexity, and Tier 2 skeleton slice generation.*
- [x] Step 2.1: Implement AST visitor to trace in-degree (callers), callees, and raised exceptions for a target symbol.
- [x] Step 2.2: Implement cyclomatic complexity ($CC$) calculator in `src/acc_mcp/parser.py`.
- [x] Step 2.3: Implement Tier 2 skeleton slice generator combining Tier 1 interface with callers, callees, and exceptions.
- [x] Step 2.4: Write unit tests in `tests/test_parser_tier2.py` verifying Tier 2 slices, in-degree counts, and cyclomatic complexity.
- [x] Step 2.5: Run test suite, verify Tier 2 functionality passes, and suggest commit name: `feat(parser): add call-graph slicing, complexity metrics, and Tier 2 context`.

## Phase 3: Full Body Extraction & Deterministic Guardrails (Block 3)
*Feature: Tier 3 context extraction with hard security path checks and deterministic AST token ceiling enforcement.*
- [x] Step 3.1: Implement Tier 3 full symbol implementation body extraction in `src/acc_mcp/parser.py`.
- [x] Step 3.2: Implement `GuardRail` in `src/acc_mcp/guard.py` enforcing the Protected Path Rule (raising `SecurityViolationError` for `.env`, `.git/`, `credentials.json`, `alembic/migrations/`).
- [x] Step 3.3: Implement deterministic token ceiling truncation ($C_{\max} = 2,000$ tokens) via AST node slicing using the pluggable token counter.
- [x] Step 3.4: Write unit tests in `tests/test_guard.py` verifying security rejections and deterministic ceiling truncation.
- [x] Step 3.5: Run test suite, verify guardrail behavior passes, and suggest commit name: `feat(guard): implement path security guardrails and AST token ceiling truncation`.

## Phase 4: Decision Model & Intent Feature Extractor (Block 4)
*Feature: Query intent analysis, AST feature fusion, and logistic scoring for dynamic tier selection.*
- [x] Step 4.1: Implement task intent classifier in `src/acc_mcp/decision.py` extracting task depth $x_1 \in \{0, 1, 2\}$ (inspect vs refactor vs rewrite).
- [x] Step 4.2: Implement feature vector compilation $(x_1, x_2, x_3, x_4)$ fusing task intent and AST metrics.
- [x] Step 4.3: Implement logistic scoring model ($z = w_0 + w_1 x_1 + w_2 x_2 + w_3 x_3 - w_4 x_4$), sigmoid probability $P(\text{Full})$, and threshold tier policy ($\theta_{\text{low}} = 0.30$, $\theta_{\text{high}} = 0.65$).
- [x] Step 4.4: Write unit tests in `tests/test_decision.py` verifying scoring, probability, and tier boundary decisions.
- [x] Step 4.5: Run test suite, verify decision engine passes, and suggest commit name: `feat(decision): implement logistic scoring model and adaptive tier selection policy`.

## Phase 5: Decision Model Ground-Truth Benchmark & Evaluation (Block 4 Evaluation)
*Feature: Ground-truth validation dataset, confusion matrix computation, and decision model calibration.*
- [x] Step 5.1: Create ground-truth dataset in `data/decision_dataset.json` containing 20 distinct coding tasks (10 interface-only, 10 deep logic rewrites).
- [x] Step 5.2: Implement evaluation script `src/acc_mcp/evaluate_decision.py` to calculate confusion matrix, false alarm rate, and miss rate.
- [x] Step 5.3: Calibrate weights and decision thresholds to ensure minimal false alarms and zero critical misses.
- [x] Step 5.4: Write automated tests in `tests/test_decision_eval.py` verifying benchmark accuracy and evaluation metrics.
- [x] Step 5.5: Run test suite, verify benchmark evaluation passes, and suggest commit name: `feat(decision): add 20-task benchmark dataset, evaluation runner, and model calibration`.

## Phase 6: Session Context Budget Tracker & Persistent Ledger (Block 5)
*Feature: Multi-turn token accounting, savings calculation, and persistent JSON ledger with restart recovery.*
- [x] Step 6.1: Implement `ContextBudgetTracker` in `src/acc_mcp/budget.py` tracking pre-flight estimates and reconciling ground-truth token usage from harness responses.
- [x] Step 6.2: Implement persistent session caching and ledger export to `context_budget.json`.
- [x] Step 6.3: Implement repository symbol map caching to retain AST statistics across agent restarts without re-parsing files.
- [x] Step 6.4: Write unit tests in `tests/test_budget.py` verifying budget calculations, JSON persistence, and restart state recovery.
- [x] Step 6.5: Run test suite, verify budget ledger and restart behavior pass, and suggest commit name: `feat(budget): add persistent context budget tracker and restart recovery ledger`.

## Phase 7: Model Context Protocol (MCP) Server Integration (Block 2)
*Feature: Stdio MCP server exposing adaptive context slicing and token audit ledger tools.*
- [x] Step 7.1: Implement MCP server using Python `mcp` SDK via stdio transport in `src/acc_mcp/server.py`.
- [x] Step 7.2: Implement and expose MCP tool `get_adaptive_context(file_path: str, task_intent: str, symbol_name: str | None)`.
- [x] Step 7.3: Implement and expose MCP tool `get_token_ledger()`.
- [x] Step 7.4: Write integration tests in `tests/test_server.py` verifying MCP client connection, tool discovery, and tool execution.
- [x] Step 7.5: Run test suite, verify MCP protocol integration passes, and suggest commit name: `feat(server): expose get_adaptive_context and get_token_ledger via MCP stdio server`.

## Phase 8: Agent Harness & Local LLM Integration (R1)
*Feature: Model-agnostic agent harness connecting ACC-MCP with standard OpenAI-compatible endpoints.*
- [x] Step 8.1: Implement model-agnostic agent harness client in `src/acc_mcp/harness.py` supporting standard OpenAI-compatible endpoints (extracting `usage.prompt_tokens`) with configurable backends (`llama-server`, `Ollama`, Cerebras).
- [x] Step 8.2: Implement agent workflow routing context retrieval requests through ACC-MCP before prompt dispatch.
- [x] Step 8.3: Implement trace logger capturing MCP handshake, context compression payload, and agent code generation response with token metrics.
- [x] Step 8.4: Write integration tests in `tests/test_harness.py` validating end-to-end agent query flow across configured backends.
- [x] Step 8.5: Run test suite, verify harness workflow passes, and suggest commit name: `feat(harness): integrate model-agnostic agent harness with ACC-MCP`.

## Phase 9: Benchmark Suite: Baseline vs. Gated Comparison (Block 9)
*Feature: Automated evaluation running 10 programming tasks x 3 runs comparing raw retrieval vs ACC-MCP.*
- [x] Step 9.1: Construct benchmark task suite in `benchmark/tasks.json` with 10 standard programming tasks across small and large codebases.
- [x] Step 9.2: Implement benchmark runner `compare_context.py` executing 30 runs (10 tasks x 3 runs) extracting ground-truth `usage` tokens to compare Baseline (raw full file) vs Gated (ACC-MCP).
- [x] Step 9.3: Compute comparative metrics: mean token consumption, variance/spread, and task completion success rate.
- [x] Step 9.4: Capture and document a trace analysis of a Baseline context overflow failure vs Gated success.
- [x] Step 9.5: Run benchmark suite, verify comparative analysis output, and suggest commit name: `feat(benchmark): add 30-run comparison benchmark suite and overflow failure trace`.

## Phase 10: AI-Use Logs, Documentation & Final Verification (R1-R4)
*Feature: Transcripts in ai_log/, complete README.md, and comprehensive verification of all requirements.*
- [x] Step 10.1: Record and structure AI-use session transcripts in `ai_log/` with prompt refinements and manual code corrections.
- [x] Step 10.2: Write comprehensive `README.md` with architecture diagrams, installation guide, local model runner setup (`llama-server`, `Ollama`), and benchmark results.
- [x] Step 10.3: Run full automated pytest suite covering all modules (parser, guard, decision, budget, server, harness).
- [x] Step 10.4: Perform final verification of all course requirements (Block 2, Block 3, Block 4, Block 5, Block 9, R1–R4).
- [x] Step 10.5: Verify full test suite passes, ensure clean git state, and suggest final commit name: `chore(release): complete documentation, ai_logs, and full R1-R4 verification`.

## Phase 11: Intent Classifier Fix - Depth Comes From the Verb
*Feature: Stop the classifier from sending almost every real query to Tier 3, by deciding depth from the action the developer asks for instead of the topic they ask about.*
- [x] Step 11.1: Remove subject-matter words (`bug`, `crash`, `error`, `exception`, `failed`, `performance`, `race condition`, `memory leak`, `algorithm`, `logic`) from the depth-2 list, because a question about errors can be shallow or deep; keep the action verbs (`fix`, `debug`, `rewrite`, `optimize`, `implement`, `overhaul`), because those really do need the function body.
- [x] Step 11.2: Add word-stem matching to every pattern (`\bfix\w*`, `\brewrit\w*`, `\btrac\w*`), so that common forms like "fixing" and "rewriting" are no longer missed and silently fall back to the default depth.
- [x] Step 11.3: Replace the current "first list that matches wins" rule with counting how many patterns of each depth matched and picking the depth with the most hits; on a tie, pick the deeper one, since giving too little context is worse than giving too much.
- [x] Step 11.4: Re-check that all 20 task intents in `data/decision_dataset.json` still land on their ground-truth tier, and run `tests/test_decision.py` and `tests/test_decision_eval.py` (all 21 parametrized intent cases are expected to still pass).
- [x] Step 11.5: Run the full test suite, confirm no regressions, and suggest commit name: `fix(decision): classify intent depth by operation verb, not subject matter`.

## Phase 12: Tier Escalation and Clearer Tool Instructions
*Feature: Let the agent ask for a deeper tier when the slice it got was not enough, and tell the agent what to write in `task_intent` so the classifier sees useful input.*
- [ ] Step 12.1: Add an optional `min_tier` argument to `get_adaptive_context` and serve `max(decision.tier, min_tier)`, so the decision model still picks the default tier but the agent can raise it. No server-side session state is needed.
- [ ] Step 12.2: Rewrite the tool docstring to tell the agent what `task_intent` should contain (a few words about what it needs right now, e.g. "check signature", "trace callers", "rewrite body") and how to retry with a higher `min_tier` if the previous slice was insufficient.
- [ ] Step 12.3: Record both the requested `min_tier` and the tier actually served in the budget ledger, so how often the agent escalates becomes a measurable, real-world miss-rate signal.
- [ ] Step 12.4: Write tests in `tests/test_server.py` verifying that `min_tier=3` raises an inspection query to Tier 3, that a `min_tier` below the model's own choice never lowers the tier, and that the ledger stores both values.
- [ ] Step 12.5: Run the full test suite, verify escalation behavior passes, and suggest commit name: `feat(server): add min_tier escalation parameter and intent guidance`.

## Phase 13: Honest Calibration of the Decision Model
*Feature: Turn Block 4 from hand-picked weights measured on their own dataset into a model that is fitted on training data and reported on data it has never seen.*
- [ ] Step 13.1: Expand `data/decision_dataset.json` to roughly 100 short task intents labeled with a ground-truth tier, since the classifier reads text and 20 examples over 4 features cannot support a 100% accuracy claim.
- [ ] Step 13.2: Add a 70/30 train/holdout split, stratified by ground-truth tier, so every tier is represented on both sides.
- [ ] Step 13.3: Fit the four weights ($w_0 \dots w_4$) with plain gradient descent on the train split instead of setting them by hand (about 20 lines, no new dependencies), and check whether $x_2$, $x_3$, and $x_4$ now influence the outcome at all.
- [ ] Step 13.4: Report accuracy, confusion matrix, miss rate, and false alarm rate separately for train and holdout; update the assertions in `tests/test_decision.py` (lines 161-175 pin tiers to the old weights) and `tests/test_decision_eval.py` (lines 126-149 and the literal `TN = 10` / `TP = 10` checks assume the 20-task dataset).
- [ ] Step 13.5: Run the full test suite, put the holdout numbers in `README.md` whatever they turn out to be, and suggest commit name: `feat(decision): fit logistic weights on held-out split with expanded intent dataset`.
