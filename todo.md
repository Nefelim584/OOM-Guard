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
