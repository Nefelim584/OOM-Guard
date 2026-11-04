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

## Phase 14: Pi Coding Agent Integration and Benchmark (Block 9)
*Feature: Run ACC-MCP inside a real agent (Pi + local Qwen via `llama-server`) and compare plain Pi with Pi + ACC-MCP on real code, measuring both task success and context used. SWE-bench was ruled out (Docker images, ~3 GB each); the benchmark injects bugs into pinned releases of two pure-Python libraries and grades with their own tests (SWE-smith style).*
- [x] Step 14.1: Workspace root (`ACC_MCP_ROOT`, added in Phase 16) plus an `ACC_MCP_LEDGER` env var for the ledger path, so every run writes its own ledger outside the agent's workspace.
- [x] Step 14.2: Connect ACC-MCP to Pi (1.0.2 has built-in MCP, so no adapter extension is needed): `eval/pi_bench.py prepare` writes an isolated `PI_CODING_AGENT_DIR` per run (local model, real context window, compaction settings for a ~30K window, and in acc mode only ACC-MCP with `direct` tool exposure) and a `start.sh` that opens interactive Pi; `collect` measures the run from Pi's session file and checks the fix with the repo's tests.
- [ ] Step 14.3: Task set and protocol: 10 bug-injection tasks (5 in more-itertools v11.2.1, 5 in click 8.5.0) in `eval/tasks.json`, method in `eval/PROTOCOL.md`; confirm with `python3 eval/pi_bench.py validate` (every mutation breaks exactly its target tests) and `doctor` (Pi starts ACC-MCP over stdio and sees its 4 tools).
- [ ] Step 14.4: Do all 10 tasks by hand in interactive Pi, plain then acc (`prepare` → `start.sh` → `collect --notes`), following the operator rules in `eval/PROTOCOL.md`. *(Moved to Phase 26: the protocol changes in Phases 23-25 first.)*
- [ ] Step 14.5: Write `eval/results.md` (`pi_bench.py report`), put the numbers in README Block 9 whatever they are, run the full test suite (includes the `ACC_MCP_LEDGER` tests), and suggest commit name: `feat(eval): add hand-run Pi benchmark with per-run ledger and bug-injection tasks`.

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
- [x] Step 18.1: Use the index from Phase 17 to find callers in other files, following `import x` and `from x import y` to the right definition.
- [x] Step 18.2: Stop linking calls by bare name only: `self.method()` points to the same class; `obj.method()` with an unknown type is shown as "possible caller" and is not counted in the in-degree $x_3$.
- [x] Step 18.3: Show cross-file callers in Tier 2 with file path and line number, and compute $x_3$ from the cross-file caller count.
- [x] Step 18.4: Write tests with a small multi-file fixture: a caller in another module is found, a same-named method in an unrelated class is not counted, `self.` calls resolve to the right class.
- [ ] Step 18.5: Run the full test suite and suggest commit name: `feat(parser): add cross-file call graph with import resolution`.

## Phase 19: Session Budget That Actually Limits Context (Block 5)
*Feature: Make the budget control what is served, not only log it. Today the 2,000-token ceiling applies to one slice, but nothing stops ten slices from filling an 8k window.*
- [x] Step 19.1: Track the running total of tokens served in the current session against a configurable session budget (e.g. 6,000 tokens for ACC-MCP output inside an 8k window). The server only sees its own output, so this is a share of the window, not the whole window.
- [x] Step 19.2: Pass the remaining budget into tier selection: if the chosen tier does not fit, step down one tier and say so in the slice header; the ceiling becomes `min(C_max, remaining)`. If `min_tier` asks for more than fits, return a clear message instead of silently cutting.
- [x] Step 19.3: Add a `reset_session` tool so a new Pi task starts with a fresh budget, and save the ledger once per request instead of twice (now both `cache_symbols` and `record_retrieval` rewrite the whole file); keep only the last N history records while keeping the totals.
- [x] Step 19.4: Write tests in `tests/test_budget.py` and `tests/test_server.py` for: tier steps down when the budget is low, `reset_session` restores the budget, one save per request, history cap keeps correct totals.
- [ ] Step 19.5: Run the full test suite and suggest commit name: `feat(budget): make session budget drive tier selection`.

## Phase 20: Decision Model Quick Fixes and Honest Reporting (Block 4)
*Feature: Fix the cheap, obvious problems in the classifier and its evaluation before the heavier refit in Phase 21.*
- [x] Step 20.1: Replace the binary 2x2 confusion matrix with a 3x3 one (Tier 1 / 2 / 3), because mistakes between Tier 1 and Tier 2 are not measured today.
- [x] Step 20.2: Fix wrong keyword hits: `\badd\w*` matches "address", `\blist\w*` matches "list[int]", `\btype\w*` matches "typed dict"; use exact word forms, and add test cases for each false hit.
- [x] Step 20.3: Add `escalation_rate` (how often the agent had to raise `min_tier`) to the `get_token_ledger` summary, so the Pi runs from Phase 14 give a real miss-rate number.
- [x] Step 20.4: State plainly in `README.md` that the weights are hand-picked, that the 20-task result is measured on the same tasks used to pick them, and that today the tier is decided almost only by the verb in `task_intent` ($x_1 = 1$ can never reach Tier 1, $x_1 = 2$ is always Tier 3).
- [ ] Step 20.5: Run the full test suite and suggest commit name: `fix(decision): 3-tier confusion matrix, keyword fixes, and escalation rate`.

## Phase 21: Honest Calibration of the Decision Model
*Feature: Turn Block 4 from hand-picked weights measured on their own dataset into a model that is fitted on training data and reported on data it has never seen.*
- [x] Step 21.1: Expand `data/decision_dataset.json` to roughly 100 short task intents labeled with a ground-truth tier, since the classifier reads text and 20 examples over 4 features cannot support a 100% accuracy claim. Include real `task_intent` strings collected from the Pi runs in Phase 14. *(Done with 100 tasks; Phase 14 Pi runs do not exist yet, so only the 2 real intents from the server ledger are included. Add Pi intents when those runs exist.)*
- [x] Step 21.2: Add a 70/30 train/holdout split, stratified by ground-truth tier, so every tier is represented on both sides.
- [x] Step 21.3: Fit the four weights ($w_0 \dots w_4$) with plain gradient descent on the train split instead of setting them by hand (about 20 lines, no new dependencies), and check whether $x_2$, $x_3$, and $x_4$ now influence the outcome at all.
- [x] Step 21.4: Report accuracy, the 3x3 confusion matrix from Phase 20, miss rate, and false alarm rate separately for train and holdout; update the assertions in `tests/test_decision.py` (which pin tiers to the old weights) and `tests/test_decision_eval.py` (which assume the 20-task dataset).
- [ ] Step 21.5: Run the full test suite, put the holdout numbers in `README.md` whatever they turn out to be, and suggest commit name: `feat(decision): fit logistic weights on held-out split with expanded intent dataset`.

## Phase 22: Documentation Cleanup and Final Verification (R1-R4)
*Feature: Make the README match the code, and remove claims the project cannot back up.*
- [x] Step 22.1: Fix factual mismatches in `README.md`: the target model says Qwen3.5-4B but the download command fetches Qwen2.5-Coder-3B; the MIT badge points to a `LICENSE` file that does not exist (add it); the test-count badge is out of date.
- [x] Step 22.2: Replace all `file:///Users/...` links with relative links so they work on GitHub, and update the architecture diagram, tool list (`find_symbol`, `reset_session`), and repository layout to the current code.
- [x] Step 22.3: Update `Description.md` so Block 9 describes the manual Pi evaluation and points to `eval/results.md`, and add the new work sessions to `ai_log/AI_USE_LOG.md`.
- [x] Step 22.4: Re-check every requirement (Blocks 2, 3, 4, 5, 9 and R1-R4) against the code and the Pi results, and remove any README sentence that no longer has evidence behind it. *(No Pi results exist yet (Phase 14), so Block 9 and R1 are marked as not done rather than claimed.)*
- [ ] Step 22.5: Run the full test suite, ensure a clean git state, and suggest commit name: `docs: align README with code and manual Pi evaluation results`.

## Phase 23: Honest Ledger Accounting (Block 5)
*Feature: Stop reporting savings against reading the whole file, because the agent never does that: in the pilot plain session (`eval/work/runs/run1/plain/mi_difference`) it found `difference` with `grep -n` and read it in `offset`/`limit` windows, and never loaded the ~45K-token `more.py`. The ledger reports what ACC-MCP served and keeps the whole-file number only as a labelled upper bound.*
- [x] Step 23.1: Rename the whole-file comparison in the ledger and in `get_token_ledger` to an upper bound (`whole_file_tokens`, `upper_bound_savings_pct`) and say in the tool docstring that it is not the saving against what the agent would really have read; still load old ledgers that use `baseline_tokens` / `savings_percentage`. *(`tokens_saved` / `cumulative_tokens_saved` were dropped rather than renamed; `pi_bench.py` reads both the old and the new name.)*
- [x] Step 23.2: Store the line ranges served in each request (file, symbol, start and end line of every symbol shown in the slice) in the ledger history, so `collect` can later match a slice against the agent's own `read` calls. *(Read back from the slice's `# <file> lines a-b: <label>` lines. For Tier 1/2 a range is the span of a symbol whose interface is shown, not its body.)*
- [x] Step 23.3: Report served tokens per tier and per tool (`get_adaptive_context` vs `find_symbol`) in the `get_token_ledger` summary, because these are numbers the server really knows. *(`find_symbol` output is counted per tool only: not in history, tier counts, or the session budget.)*
- [x] Step 23.4: Write tests in `tests/test_budget.py` and `tests/test_server.py` for: the renamed fields, loading an old-format ledger, line ranges stored for Tier 1 / 2 / 3 slices and for a slice cut by the ceiling.
- [ ] Step 23.5: Run the full test suite and suggest commit name: `refactor(budget): report served tokens and line ranges, whole-file saving as upper bound`.

## Phase 24: Navigation Metrics from Pi Sessions (Block 9)
*Feature: Measure ACC-MCP against the agent's real way of finding code (`grep` + windowed `read`), not against reading whole files. All metrics come from Pi's session file after the run, so they cost the operator nothing.*
- [x] Step 24.1: In `eval/pi_bench.py collect`, classify every tool call: code reading (`read`, and bash `grep`/`rg`/`sed`/`cat`/`head`/`tail`/`awk` on source files), test runs, other commands (e.g. Python experiments), ACC-MCP calls; count the output tokens of each class with one token counter for both modes. *(Also an `edit` class. Bash `find`/`ls`/`nl` and Pi's own `grep`/`find`/`ls` tools count as code reading; "on source files" = the program starts a pipeline, so `pip list | grep x` is not. Counter: chars/3.8, the ACC-MCP heuristic without tiktoken, so every machine counts the same.)*
- [x] Step 24.2: Compute navigation metrics from `tasks.json` (target file and function, whose line range is taken from the clean repo): navigation calls before the first edit of the target function, wasted reads (windows that do not overlap the target function), and large reads (one call returning more than 4,000 tokens of code), so the "agent reads whole files" assumption is measured instead of assumed. *(Edited lines come from Pi's edit patch. Windows: `read`, `sed -n a,bp`, `head`, `cat`; `tail`/`awk` give no window. A read of any other file counts as wasted.)*
- [x] Step 24.3: In acc mode, count silent escalations: the agent got a Tier 1 or Tier 2 slice and then read the same lines with `read` or `sed` within the next 5 calls (uses the line ranges from Phase 23); report them next to the explicit `min_tier` escalations from the ledger as the online miss rate of the decision model (Block 4). *(Online miss rate = (explicit + silent) / slices served. `head`/`cat` windows count too. A ledger without line ranges gives `None`, not 0.)*
- [x] Step 24.4: Write tests in `tests/test_pi_bench.py` using a trimmed copy of the pilot session as a fixture (`tests/fixtures/pi_session_mi_difference.jsonl`), with the expected number of reads, wasted reads and navigation calls checked by hand, plus a small synthetic acc session with one silent escalation.
- [ ] Step 24.5: Run the full test suite and suggest commit name: `feat(eval): measure navigation cost and silent escalations from Pi sessions`.

## Phase 25: Protocol Fixes and Restated Hypotheses (Block 9)
*Feature: Make the benchmark able to answer the right question before any counted run is done: a working baseline for click, hypotheses about navigation cost instead of whole-file reads, and one context window size everywhere.*
- [ ] Step 25.1: Fix the click baseline: today `eval/work/baseline/click.json` has 0 passed tests and a collection error in `tests.test_deprecations`, so the "no new failures" check is empty for all 5 click tasks; find the cause (environment, `PYTHONPATH`, missing test dependency), and make `validate` fail when a clean suite passes 0 tests.
- [ ] Step 25.2: Rewrite `eval/PROTOCOL.md` sections 1, 8 and 9: H1 = fewer navigation calls and fewer code-reading tokens than the agent's own `grep` + `read` strategy; H2 = success rate not lower; H3 = peak context, stating that it may not change because most of the pilot's context came from the agent's own Python experiments; add the Phase 24 metrics to the tables and state that the pilot session is excluded from results.
- [ ] Step 25.3: Use one context window everywhere: README and `Description.md` say 8,192 tokens, the benchmark uses 30,208 from `llama-server /props`; pick one, state it in all documents, and set the session budget as a share of it.
- [ ] Step 25.4: *(Optional, decision pending)* Add 2-3 caller-navigation tasks to `eval/tasks.json` (e.g. change a click function's signature and update every call site), because all 10 current tasks are bug fixes where the decision model almost always picks Tier 3, so Tier 1 and Tier 2 are barely exercised in Block 9.
- [ ] Step 25.5: Run `python3 eval/pi_bench.py validate` and `doctor` (all tasks OK, clean click suite passes > 0 tests), run the full test suite, and suggest commit name: `fix(eval): working click baseline and navigation-cost hypotheses`.

## Phase 26: Benchmark Runs and Results (Block 9, R1)
*Feature: Do the counted Pi runs under the frozen protocol from Phase 25 and publish the numbers whatever they are. Replaces Steps 14.4 and 14.5.*
- [ ] Step 26.1: Freeze the setup and record it in `eval/results.md`: ACC-MCP commit hash, model file and quantization, `llama-server` and Pi versions, context window, session budget.
- [ ] Step 26.2: Do every task by hand, plain then acc (`prepare` -> `start.sh` -> `collect --notes`), following the operator rules in `eval/PROTOCOL.md`.
- [ ] Step 26.3: Run `pi_bench.py report`: resolved per mode, medians of the Phase 24 metrics and of peak context, a paired per-task table, and the number of acc runs in which the model used ACC-MCP at all.
- [ ] Step 26.4: If time allows, repeat the whole set with a new `--run-id` and do the acc runs once with `--no-hint`; report every run, not only the best.
- [ ] Step 26.5: Run the full test suite and suggest commit name: `feat(eval): Pi benchmark results for plain vs ACC-MCP`.

## Phase 27: Documentation for the New Framing (R1-R4)
*Feature: Describe ACC-MCP as symbol-level code navigation for small local models, measured against the agent's real strategy, and back every sentence with Phase 26 data.*
- [ ] Step 27.1: Rewrite the problem statement in `README.md` section 1 and `Description.md` section 1: the agent finds code with `grep` and windowed reads, which costs calls and wasted reads; ACC-MCP gives exact symbol slices; drop the claim that agents read whole files unless the large-read metric shows it.
- [ ] Step 27.2: Update Block 5 (whole-file saving shown only as an upper bound, formula in `Description.md` section 4.4 changed to match) and Block 9 (new hypotheses, metrics and the Phase 26 numbers).
- [ ] Step 27.3: Update Block 4 with the online signal (explicit and silent escalations from Phase 26) next to the offline holdout result, and state that bug-fix tasks mostly exercise Tier 3; add the real Pi `task_intent` strings to `data/decision_dataset.json` as Step 21.1 planned.
- [ ] Step 27.4: Update the R1-R4 table (R1 verified only if the Pi runs exist), and add the work sessions to `ai_log/AI_USE_LOG.md`.
- [ ] Step 27.5: Run the full test suite, ensure a clean git state, and suggest commit name: `docs: reframe ACC-MCP as symbol-level navigation with measured results`.
