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
- [x] Step 12.1: Add an optional `min_tier` argument to `get_adaptive_context` and serve `max(decision.tier, min_tier)`, so the decision model still picks the default tier but the agent can raise it. No server-side session state is needed.
- [x] Step 12.2: Rewrite the tool docstring to tell the agent what `task_intent` should contain (a few words about what it needs right now, e.g. "check signature", "trace callers", "rewrite body") and how to retry with a higher `min_tier` if the previous slice was insufficient.
- [x] Step 12.3: Record both the requested `min_tier` and the tier actually served in the budget ledger, so how often the agent escalates becomes a measurable, real-world miss-rate signal.
- [x] Step 12.4: Write tests in `tests/test_server.py` verifying that `min_tier=3` raises an inspection query to Tier 3, that a `min_tier` below the model's own choice never lowers the tier, and that the ledger stores both values.
- [x] Step 12.5: Run the full test suite, verify escalation behavior passes, and suggest commit name: `feat(server): add min_tier escalation parameter and intent guidance`.

## Phase 13: Remove the Synthetic Benchmark and In-Process Harness
*Feature: Drop the simulated comparison, because it marks every gated run as a success, never checks the model's answer, and gets most of its 91% saving from one generated 20k-token file. Real testing moves to the Pi coding agent.*
- [x] Step 13.1: Delete `compare_context.py`, the whole `benchmark/` folder (`tasks.json`, `codebases/`, `results.json`, `REPORT.md`, `trace_overflow_analysis.md`, `temp_ledgers/`), and `tests/test_benchmark.py`.
- [x] Step 13.2: Delete `src/acc_mcp/harness.py` and `tests/test_harness.py`, because the harness calls the server in-process, always calls the tool exactly once, and never lets the model decide when to call it or escalate. Pi becomes the real harness. Remove the harness exports from `src/acc_mcp/__init__.py`.
- [x] Step 13.3: Remove `ContextBudgetTracker.reconcile_usage` and the `ground_truth_tokens` field if nothing else uses them after the harness is gone, so no dead code is left behind.
- [x] Step 13.4: Remove the Block 9 section, the benchmark numbers (91.49%, 100% success, "zero overflow"), and the benchmark run commands from `README.md` and `Description.md`; leave a short note that evaluation is done manually with Pi (see Phase 14).
- [ ] Step 13.5: Run the full test suite, confirm nothing still imports the removed modules, and suggest commit name: `chore(benchmark): remove synthetic benchmark and in-process harness`.

## Phase 14: Pi Coding Agent Integration and Manual Evaluation Protocol
*Feature: Run ACC-MCP inside a real agent (Pi + local Qwen via `llama-server`) and define a repeatable manual test on real coding tasks, which replaces the old Block 9 comparison.*
- [ ] Step 14.1: Add a workspace root setting (`ACC_MCP_ROOT` env var or `--root` CLI argument) and resolve relative `file_path` values against it instead of the server's current working directory, because the agent and the server usually run from different folders.
- [ ] Step 14.2: Add an `ACC_MCP_LEDGER` env var for the ledger path, so every manual run can write its own ledger file instead of mixing all runs into one `context_budget.json` in the current folder.
- [ ] Step 14.3: Connect ACC-MCP to the Pi coding agent over stdio (Pi has no built-in MCP support, so use its MCP adapter extension) with Qwen running on `llama-server`, and write the exact setup steps in `README.md`.
- [ ] Step 14.4: Write `eval/PROTOCOL.md`: pick about 10 Python tasks from a public coding benchmark that come with tests (for example SWE-bench Lite or the Python exercises from the Aider benchmark), run each one with plain Pi and with Pi + ACC-MCP, and record in `eval/results.md`: did the tests pass, prompt tokens used, which tiers were served, and how often the agent escalated with `min_tier`.
- [ ] Step 14.5: Add one integration test that starts the server as a real subprocess and talks to it through the `mcp` stdio client (current tests only call it in-process), run the full test suite, and suggest commit name: `feat(server): add workspace root, per-run ledger, and Pi agent setup`.

## Phase 15: Edit-Ready Output with Original Source and Line Numbers
*Feature: Return code the agent can actually edit. Right now `ast.unparse` drops comments, changes quotes and formatting, and gives no line numbers, so the agent's search-and-replace edits do not match the real file.*
- [x] Step 15.1: Build Tier 3 from the original file text (cut by `lineno` / `end_lineno`) instead of `ast.unparse`, so comments and formatting stay exactly as in the file.
- [x] Step 15.2: Start every served slice (all tiers) with a header that names the file and the line range of each symbol, e.g. `# service.py lines 40-72: PaymentService.validate`, so the agent can open or edit the exact lines.
- [x] Step 15.3: When the token ceiling cuts a Tier 3 slice, say clearly what was left out (e.g. `# lines 120-310 omitted, request symbol_name="Router.dispatch" to see them`) instead of only printing a generic banner, so the agent does not "fix" a function it only saw half of.
- [x] Step 15.4: Write tests checking that Tier 3 output is an exact substring of the source file, that comments survive, that line numbers in the header match the file, and that a cut slice reports the omitted range.
- [ ] Step 15.5: Run the full test suite and suggest commit name: `feat(parser): serve verbatim source with line ranges for edit-ready slices`.

## Phase 16: Hardened Path Security (Block 3)
*Feature: Move from "block a few known bad names" to "allow only Python files inside the workspace", and check the real file after symlinks are followed.*
- [x] Step 16.1: Reject any path that, after `resolve()`, is outside the workspace root from Phase 14 (absolute paths, `../` tricks, other users' folders).
- [x] Step 16.2: Run the protected-path checks on the resolved real path, not only on the string the agent sent, because a symlink like `notes.py -> .env` passes today and `API_KEY=...` is valid Python, so Tier 3 would return the secrets.
- [x] Step 16.3: Serve only `.py` files, and add common secret locations to the deny list (`.ssh/`, `.aws/`, `*.pem`, `*.key`, `id_rsa`, `.npmrc`, `.pypirc`). Remove the `/var/folders` and `/var/tmp` exception, which exists only so tests can use temp folders; let tests set a temp workspace root instead.
- [x] Step 16.4: Write tests in `tests/test_guard.py` for: symlink to `.env` rejected, path outside the root rejected, non-`.py` file rejected, normal file inside the root still allowed.
- [ ] Step 16.5: Run the full test suite and suggest commit name: `fix(guard): enforce workspace root and check resolved paths`.

## Phase 17: Repository Symbol Index with a Working Cache (Block 5)
*Feature: Index the whole workspace once and reuse it after restarts. Today `get_cached_symbols` is never called and the server re-parses the file on every request, so the "restart without re-parsing" claim is not true in practice.*
- [x] Step 17.1: Add a workspace indexer that walks all `.py` files under the root (skipping `.venv`, `.git`, `__pycache__`, `node_modules`) and stores a symbol map per file.
- [x] Step 17.2: Before parsing a file, compare its SHA-256 with the cached `file_hash`; reuse the cached symbols when it matches and re-parse only changed files.
- [x] Step 17.3: Expose a new MCP tool `find_symbol(name)` that returns file path, kind, and line range, so the agent can find code without knowing the file first.
- [x] Step 17.4: Write tests checking that a restarted server does not re-parse unchanged files (count parser calls), that an edited file is re-indexed, and that `find_symbol` returns the right location.
- [ ] Step 17.5: Run the full test suite and suggest commit name: `feat(index): add repository symbol index with hash-checked cache and find_symbol tool`.

## Phase 18: Cross-File and More Accurate Call Graph
*Feature: Make "trace callers", "rename", and "deprecate" useful. Today callers are found only inside one file, and calls are matched by bare name, so any `.get(...)` is linked to any `get` method.*
- [ ] Step 18.1: Use the index from Phase 17 to find callers in other files, following `import x` and `from x import y` to the right definition.
- [ ] Step 18.2: Stop linking calls by bare name only: `self.method()` points to the same class; `obj.method()` with an unknown type is shown as "possible caller" and is not counted in the in-degree $x_3$.
- [ ] Step 18.3: Show cross-file callers in Tier 2 with file path and line number, and compute $x_3$ from the cross-file caller count.
- [ ] Step 18.4: Write tests with a small multi-file fixture: a caller in another module is found, a same-named method in an unrelated class is not counted, `self.` calls resolve to the right class.
- [ ] Step 18.5: Run the full test suite and suggest commit name: `feat(parser): add cross-file call graph with import resolution`.

## Phase 19: Session Budget That Actually Limits Context (Block 5)
*Feature: Make the budget control what is served, not only log it. Today the 2,000-token ceiling applies to one slice, but nothing stops ten slices from filling an 8k window.*
- [ ] Step 19.1: Track the running total of tokens served in the current session against a configurable session budget (e.g. 6,000 tokens for ACC-MCP output inside an 8k window). The server only sees its own output, so this is a share of the window, not the whole window.
- [ ] Step 19.2: Pass the remaining budget into tier selection: if the chosen tier does not fit, step down one tier and say so in the slice header; the ceiling becomes `min(C_max, remaining)`. If `min_tier` asks for more than fits, return a clear message instead of silently cutting.
- [ ] Step 19.3: Add a `reset_session` tool so a new Pi task starts with a fresh budget, and save the ledger once per request instead of twice (now both `cache_symbols` and `record_retrieval` rewrite the whole file); keep only the last N history records while keeping the totals.
- [ ] Step 19.4: Write tests in `tests/test_budget.py` and `tests/test_server.py` for: tier steps down when the budget is low, `reset_session` restores the budget, one save per request, history cap keeps correct totals.
- [ ] Step 19.5: Run the full test suite and suggest commit name: `feat(budget): make session budget drive tier selection`.

## Phase 20: Decision Model Quick Fixes and Honest Reporting (Block 4)
*Feature: Fix the cheap, obvious problems in the classifier and its evaluation before the heavier refit in Phase 21.*
- [ ] Step 20.1: Replace the binary 2x2 confusion matrix with a 3x3 one (Tier 1 / 2 / 3), because mistakes between Tier 1 and Tier 2 are not measured today.
- [ ] Step 20.2: Fix wrong keyword hits: `\badd\w*` matches "address", `\blist\w*` matches "list[int]", `\btype\w*` matches "typed dict"; use exact word forms, and add test cases for each false hit.
- [ ] Step 20.3: Add `escalation_rate` (how often the agent had to raise `min_tier`) to the `get_token_ledger` summary, so the Pi runs from Phase 14 give a real miss-rate number.
- [ ] Step 20.4: State plainly in `README.md` that the weights are hand-picked, that the 20-task result is measured on the same tasks used to pick them, and that today the tier is decided almost only by the verb in `task_intent` ($x_1 = 1$ can never reach Tier 1, $x_1 = 2$ is always Tier 3).
- [ ] Step 20.5: Run the full test suite and suggest commit name: `fix(decision): 3-tier confusion matrix, keyword fixes, and escalation rate`.

## Phase 21: Honest Calibration of the Decision Model
*Feature: Turn Block 4 from hand-picked weights measured on their own dataset into a model that is fitted on training data and reported on data it has never seen.*
- [ ] Step 21.1: Expand `data/decision_dataset.json` to roughly 100 short task intents labeled with a ground-truth tier, since the classifier reads text and 20 examples over 4 features cannot support a 100% accuracy claim. Include real `task_intent` strings collected from the Pi runs in Phase 14.
- [ ] Step 21.2: Add a 70/30 train/holdout split, stratified by ground-truth tier, so every tier is represented on both sides.
- [ ] Step 21.3: Fit the four weights ($w_0 \dots w_4$) with plain gradient descent on the train split instead of setting them by hand (about 20 lines, no new dependencies), and check whether $x_2$, $x_3$, and $x_4$ now influence the outcome at all.
- [ ] Step 21.4: Report accuracy, the 3x3 confusion matrix from Phase 20, miss rate, and false alarm rate separately for train and holdout; update the assertions in `tests/test_decision.py` (which pin tiers to the old weights) and `tests/test_decision_eval.py` (which assume the 20-task dataset).
- [ ] Step 21.5: Run the full test suite, put the holdout numbers in `README.md` whatever they turn out to be, and suggest commit name: `feat(decision): fit logistic weights on held-out split with expanded intent dataset`.

## Phase 22: Documentation Cleanup and Final Verification (R1-R4)
*Feature: Make the README match the code, and remove claims the project cannot back up.*
- [ ] Step 22.1: Fix factual mismatches in `README.md`: the target model says Qwen3.5-4B but the download command fetches Qwen2.5-Coder-3B; the MIT badge points to a `LICENSE` file that does not exist (add it); the test-count badge is out of date.
- [ ] Step 22.2: Replace all `file:///Users/...` links with relative links so they work on GitHub, and update the architecture diagram, tool list (`find_symbol`, `reset_session`), and repository layout to the current code.
- [ ] Step 22.3: Update `Description.md` so Block 9 describes the manual Pi evaluation and points to `eval/results.md`, and add the new work sessions to `ai_log/AI_USE_LOG.md`.
- [ ] Step 22.4: Re-check every requirement (Blocks 2, 3, 4, 5, 9 and R1-R4) against the code and the Pi results, and remove any README sentence that no longer has evidence behind it.
- [ ] Step 22.5: Run the full test suite, ensure a clean git state, and suggest commit name: `docs: align README with code and manual Pi evaluation results`.
