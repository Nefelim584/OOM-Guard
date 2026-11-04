# AI-Use Log & Prompt Refinement Transcript (R3)

**Project:** Adaptive Context Compressor MCP (ACC-MCP / OOM-Guard)  
**Course:** Coding for AI & Data Science — Final Project (2026–2027)  
**Target Model:** Qwen3.5-4B (GGUF via `llama-server`); the recorded trace used `Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q6_K.gguf`  
**Building Blocks Implemented:** Block 2, Block 3, Block 4, Block 5, Block 7, Block 8 (Pi benchmark exploratory)  
**Requirements Addressed:** R1 (It Runs), R2 (It Is Checked), R3 (AI-Use Log), R4 (Individual Defence)  

---

## 1. Overview and Chronology of AI Collaboration

This document fulfills course requirement **R3 (AI-Use Log)** by recording work sessions, prompt refinements, design decisions, manual code corrections, and debugging interventions. The plan lives in `todo.md` (30 phases). Exported session transcripts are in this folder: `todo-generation-and-project-planning--8027412e.md` and `continuing-phase-3-project--85952ca6.md` (planning and Phases 1–3), and `2026-11-04_phases-23-29.txt` (Phases 23–29, Claude Code).

### Phases 1–10: First build

| Phase | Core Objective | Key AI Contributions | Human Inquiries & Prompt Refinements |
| :--- | :--- | :--- | :--- |
| **Phase 1** | AST Parser & Tier 1 Compression | Implemented `ASTParser` and `Tier1Transformer` stubbing bodies with `...`. | Inquired about token standard; decoupled tokenizer into pluggable `TokenCounter`. |
| **Phase 2** | Call-Graph & Complexity Metrics (Tier 2) | Built `CyclomaticComplexityVisitor` and `SymbolDependencyVisitor`. | Directed in-degree caller tracing and formatting of Tier 2 call-graph skeleton. |
| **Phase 3** | Full Body & Guardrails (Block 3) | Implemented `Tier3Transformer`, `GuardRail.validate_path`, and AST ceiling. | Instructed deterministic statement-level AST slicing instead of LLM prompting. |
| **Phase 4** | Decision Model (Block 4) | Built `TaskIntentClassifier`, feature fusion, and logistic scoring function. | Refined intent patterns to distinguish action verbs (inspect vs refactor vs rewrite). |
| **Phase 5** | Decision Model Evaluation | Generated 20-task benchmark dataset, confusion matrix runner, and calibration. | Asked for zero critical misses and minimal false alarms. *(Later found to be measured on the same tasks used to pick the weights; replaced in Phase 21.)* |
| **Phase 6** | Budget Tracker & Persistent Ledger (Block 5) | Built `ContextBudgetTracker`, JSON persistence, and symbol map cache. | Verified cold restart recovery preserving repository symbol maps without re-parsing. |
| **Phase 7** | Stdio MCP Server Integration (Block 2) | Created stdio MCP server exposing `get_adaptive_context` and `get_token_ledger`. | Accommodated MCP 2.x migration (`MCPServer` vs `FastMCP`). |
| **Phase 8** | Agent Harness & Model Integration (R1) | Developed OpenAI-compatible agent harness supporting local `llama-server` / `Ollama`. | Reconciled pre-flight estimates with ground-truth `usage.prompt_tokens`. *(Harness removed in Phase 13.)* |
| **Phase 9** | Benchmark Suite (Block 9) | Created 30-run comparison runner `compare_context.py` and overflow failure trace. | Formulated oversized codebase to document baseline overflow crash vs gated success. *(Benchmark removed in Phase 13.)* |
| **Phase 10** | Documentation & Verification (R1–R4) | Authored `README.md`, ran 110 automated tests, clean git state. | Final audit against all 5 building blocks and R1–R4 course requirements. *(Many claims later corrected in Phases 20–22.)* |

### Phases 11–22: Review and rework

After Phase 10 the project was reviewed and `todo.md` was extended with Phases 11–22 to fix what the review found. For Phases 11–20 this log records only what `todo.md` and the commit history show; transcripts of those sessions are not in this folder yet.

| Phase | Core Objective | What Was Done (from `todo.md` and commits) | Commit |
| :--- | :--- | :--- | :--- |
| **Phase 11** | Intent depth comes from the verb | Classifier counts operation verbs, not subject nouns like "error" or "bug". | `8b1e2b3` |
| **Phase 12** | Tier escalation | Added `min_tier` to `get_adaptive_context` and tier guidance in the tool description. | `550c1b6` |
| **Phase 13** | Remove synthetic benchmark | Deleted `compare_context.py`, `benchmark/`, and the in-process harness, because the benchmark marked every gated run as a success and one generated file produced most of its saving. | `f00e381` |
| **Phase 14** | Pi integration & manual evaluation | Partly done: per-run ledger (`ACC_MCP_LEDGER`), Pi setup through `eval/pi_bench.py`, `eval/PROTOCOL.md`, and one pilot session. The counted runs and `eval/results.md` were moved to Phase 26. (`ACC_MCP_ROOT` itself was added in Phase 16.) | — |
| **Phase 15** | Edit-ready output | Tier 3 copied verbatim from the file, `# file lines a-b: symbol` headers, explicit "omitted lines" notes. | `be3b350` |
| **Phase 16** | Path security | Only `.py` files inside the workspace root; resolved (symlink) paths checked. | `53bb6b4` |
| **Phase 17** | Symbol index | Workspace index with SHA-256 hash-checked cache and the `find_symbol` tool. | `b5b36e2` |
| **Phase 18** | Cross-file call graph | Callers found through imports; typed receivers resolved; unknown receivers listed as possible callers. | `96bde66` |
| **Phase 19** | Session budget | Budget drives tier selection (step down, refuse forced tiers that do not fit), `reset_session`, one ledger save per request, history cap. | `17b713b` |
| **Phase 20** | Decision model quick fixes | 3x3 confusion matrix, exact word-form keywords, `escalation_rate` in the ledger, README states the weights were hand-picked. | `048dcbf` |
| **Phase 21** | Honest calibration | 100-task dataset, stratified 70/30 split, weights fitted by gradient descent, train and holdout reported separately. See Refinement 4. | `41b6c93` |
| **Phase 22** | Documentation cleanup | README, `Description.md`, and this log aligned with the code; unsupported claims removed; `LICENSE` added. See Refinement 5. | `f5f0c02` (with 23-24) |

### Phases 23–27: Measuring against what the agent really does

These phases were planned after the first Pi session (the pilot, plain mode, task `mi_difference`). It showed that the agent does not read whole files: it greps and reads small line windows. The plan changed from "saving against whole files" to "navigation cost against the agent's own strategy". The work was done with Claude Code in this repository; the human ran every command that executes code (tests, the benchmark helper) and made the commits.

| Phase | Core Objective | What Was Done | Commit |
| :--- | :--- | :--- | :--- |
| **Phase 23** | Honest ledger accounting (Block 5) | Whole-file comparison renamed to an upper bound (`whole_file_tokens`, `upper_bound_savings_pct`); old ledgers still load; served line ranges stored per request; served tokens per tier and per tool (`find_symbol` output now counted). See Refinement 6, Correction 5. | `f5f0c02` |
| **Phase 24** | Navigation metrics from Pi sessions (Block 9) | `pi_bench.py collect` classifies every tool call (code reading, tests, edits, ACC-MCP, other) and counts output tokens with one counter; navigation calls before the first edit of the target function, wasted and large reads; silent escalations and the online miss rate. Tests use a trimmed copy of the pilot session, checked by hand. | `f5f0c02` |
| **Phase 25** | Protocol fixes | Click baseline fixed (Correction 6); new hypotheses H1-H3 in `eval/PROTOCOL.md`; one context window (30,208) everywhere with the session budget at 20% of it; 4 of the 10 tasks turned into call-graph tasks and all reports made easier (Refinement 7). | `3bcc93c` |
| **Phase 26** | Benchmark runs and results | Tooling done: `freeze` records the setup and blocks runs that no longer match it; `report` gives summaries per mode and task kind, ACC-MCP use and the online miss rate, and a paired per-task table. **The counted runs have not been done yet.** | `d52cf62` |
| **Phase 27** | Documentation for the new framing | README and `Description.md` now describe ACC-MCP as symbol-level navigation measured against `grep` + windowed `read`; whole-file saving only as an upper bound; the online miss rate described next to the holdout result. Numbers that need the Phase 26 runs are marked as not measured. | `8ecc7cc` |

### Phases 28–30: Blocks 7 and 8, and the Pi benchmark made exploratory

After the first trace runs the human changed the plan: the counted Pi benchmark (about 60 hand runs) is not done. Instead the project claims Blocks 7 and 8, built on the Block 4 dataset, and keeps the Pi runs as the real-agent trace for R1 and Block 2.

| Phase | Core Objective | What Was Done | Commit |
| :--- | :--- | :--- | :--- |
| **Phase 28** | Learned intent classifier vs baselines (Block 7) | Naive Bayes over the words of `task_intent`, trained on the Block 4 train split and compared on the same holdout with the majority tier and the keyword rule. The metric code was moved into `metrics_from_evaluations`, shared with Block 4. See Refinement 8. | `8ecc7cc` |
| **Phase 29** | Uncertainty on the holdout (Block 8) | Wilson and bootstrap intervals, exact McNemar and paired bootstrap, and a 50-split sensitivity sweep. See Refinement 8. | `8ecc7cc` |
| **Phase 30** | Claimed blocks and the exploratory benchmark | Documents now claim Blocks 2, 3, 4, 5, 7, 8. Block 9 became an "exploratory, not claimed" note. R1 is verified by the quoted acc trace run, and Block 5 gives its restart numbers from the tests. See Refinement 9. | pending |

---

## 2. Key Prompt Refinements & Architectural Evolutions

### Refinement 1: Model-Agnostic Token Accounting Standard
- **Initial Idea:** Hardcode token counting specifically for Qwen using a static tokenizer or fixed multiplier.
- **Human Guidance:** *"Does harness usually provide amount of spending tokens? Any existing standard? Why asking - yes, we are working with mainly one model - Qwen, but it will be better if we will be able to retrieve amount of tokens from the harness and/or from any model and not be tied to the only one selected."*
- **Refined Engineering Solution:**
  1. Implemented a pluggable pre-flight `TokenCounter` interface (`tokens.py`):
     - `EndpointTokenCounter`: Queries local runner `/tokenize` endpoint (`llama-server` `POST /tokenize` or `Ollama` `POST /api/tokenize`).
     - `HeuristicTokenCounter`: Fast offline fallback using `tiktoken` with character-ratio fallback.
  2. Implemented post-flight ground-truth token accounting in the agent harness (`harness.py`), extracting `response["usage"]["prompt_tokens"]` and reconciling it with `ContextBudgetTracker.reconcile_usage` in `src/acc_mcp/budget.py`. *(Both the harness and `reconcile_usage` were removed in Phase 13; the pluggable `TokenCounter` remains.)*

### Refinement 2: Deterministic AST Node Slicing (No LLM Prompting)
- **Initial Idea:** Use an LLM prompt to summarize oversized context when $C_{\max}$ is exceeded.
- **Requirement Rule (Block 3):** Under no circumstances can context exceed $C_{\max} = 2,000$ tokens, and truncation must be enforced deterministically in code via AST node slicing, not by LLM prompting.
- **Refined Engineering Solution:**
  - Implemented `GuardRail.enforce_ceiling` in `guard.py` using AST statement-level slicing.
  - Slices functions at statement boundaries, appending an `Ellipsis` (`...`) stub while preserving valid Python syntax.
  - Slices classes by retaining class attributes and stubbing remaining methods.
  - Identical input always produces identical output.
  - *(Since Phase 15, Tier 3 is first cut by the parser on the original file text with an "omitted lines" note; `enforce_ceiling` remains as the final check.)*

### Refinement 3: Action-Verb Intent Disambiguation
- **Initial Observation:** Query `"What is the parameter type for data?"` was classified as Depth 1 because `"parameter"` was grouped under refactoring.
- **Refinement:**
  - Separated structural domain nouns (`parameter`, `interface`, `argument`, `type`) from active mutation verbs.
  - Action verbs indicating inspection (`what is`, `check`, `show`, `view`, `inspect`) take precedence for Depth 0.
  - Action verbs indicating interface mutation (`refactor`, `rename`, `patch`, `deprecate`, `trace callers`) define Depth 1.
  - Action verbs indicating algorithmic overhaul (`rewrite`, `reimplement`, `fix bug`, `optimize`, `debug`) define Depth 2.
  - Result at the time: 100% accuracy on the 20-task dataset. *(That dataset was used to tune the patterns and weights, so this was not a measurement on unseen data. On the Phase 21 holdout split the model gets 70%.)*

### Refinement 4: Fitting the Decision Model and Choosing Thresholds (Phase 21)
- **Task:** Replace the hand-picked weights with weights fitted on a train split, and report holdout numbers whatever they turn out to be.
- **What the AI found:** The 20 original tasks gave $x_4 = 1$ for every task and $x_3 > 0$ only for Tier 2 tasks, so a fit would have learned "has callers means Tier 2" from a coincidence. The expanded dataset (100 tasks) puts called and unannotated symbols in every tier and phrases many intents without the classifier's keywords.
- **Decision put to the human:** With the fitted weights, every $x_1 = 1$ intent lands at $P \approx 0.55$–$0.72$, right on $\theta_{\text{high}} = 0.65$. The AI offered three options: keep 0.30/0.65 (holdout miss 14.3%, false alarm 33.3%), use the midpoints 0.25/0.75 (miss 38.1%, false alarm 5.6%), or also fit the thresholds.
- **Human choice:** Keep 0.30/0.65 and fit only the weights, as the plan said (fewer misses, since too little context is worse than too much).
- **Result:** Holdout accuracy 70.0% (hand-picked weights: 66.7%). Most errors come from the keyword classifier, not the weights.

### Refinement 5: Documentation Must Match the Code (Phase 22)
- **Task:** Remove README claims without evidence.
- **Human choice:** The README named Qwen3.5-4B while the commands used Qwen2.5-Coder-3B. The human confirmed Qwen3.5-4B is the model actually used (already downloaded locally), and asked the AI not to download or run the model itself but to give commands instead.
- **Removed or corrected claims:** tier token footprints (Tier 3 "1,000–3,000 tokens" contradicted the 2,000-token ceiling), "more than 70% of queries need only interfaces" and "60–85% fewer prompt tokens" (no measurement behind them), the Cerebras fallback (no code), `alembic/migrations/` as a protected path (no longer in `guard.py`), and `file:///Users/...` links (do not work on GitHub).

### Refinement 6: Measure Against What the Agent Really Does (Phases 23–25)
- **Observation:** In the pilot session the agent located `difference` with `find | xargs grep` and `grep -n` and read it in 6 windows of 10-100 lines, 2 of which missed it; it never loaded the ~45K-token `more.py`. 48 of its 65 tool calls were its own Python experiments.
- **Consequence:** The ledger's "saving against the whole file" compared ACC-MCP with something the agent never does. It is now reported only as an upper bound. The hypotheses became H1 (navigation calls and code-reading tokens against the agent's own `grep` + `read`), H2 (success not lower) and H3 (peak context, which may not change because most of the pilot's context came from the agent's experiments).
- **Design choices made by the AI and reported to the human:** `find_symbol` output is counted per tool but not against the session budget; bash `find`/`ls` and Pi's own `grep`/`find`/`ls` tools count as code reading; a read of any other file counts as wasted; a ledger without line ranges gives "unknown" silent escalations, not 0.

### Refinement 7: Easier Tasks That Exercise the Call Graph (Phase 25)
- **Question put to the human:** All 10 tasks were bug fixes, where the decision model almost always picks Tier 3. The plan offered to add 2-3 caller-navigation tasks (for example, change a function's signature and update every call site).
- **Human choice:** *"Not add but change some of them to the caller-navigating tasks. Also make them a bit easier, cause it tests 4b model even in q6 struggled to solve them."* The human also chose 30,208 as the one context window (the real `/props` value) over the 8,192 in the old documents.
- **Result:** 4 `call_graph` tasks, where the report names an entry function and the bug is in a helper it calls (never named in the report). `mi_difference`, where the pilot model got lost in `tee` logic, was replaced by `mi_sliding_window` (bug in `_sliding_window_deque`); the `--help` alignment bug moved from `write_dl` into its helper `measure_table`; the two other click call-graph tasks kept their bug but got reports that name the entry function. Every click report now names its module. A test checks that no call-graph report names the buggy helper.

---

### Refinement 8: Report the Learned Model Even When It Loses (Phases 28–29)
- **Plan:** The human's plan said to put the Naive Bayes holdout numbers in the README "whatever they are (even if Naive Bayes does not beat the keyword rule)".
- **Result:** Naive Bayes reached 46.7% on the holdout against 70.0% for the keyword rule, and 98.6% on its own train tasks, so it overfits 70 short intents. The keyword rule stays in the server.
- **What Block 8 changed in the Block 4 story:** the 70.0% of the fitted weights has a 95% interval of 52.1–83.3%. It is not significantly better than the hand-picked weights (McNemar p = 1.0, 7 vs 6 discordant tasks). Over 50 splits it averages 63.7%, below the keyword rule read directly as a tier (69.2%). The README now says so in Block 4 itself, not only in Block 8.
- **A mixed signal, reported as such:** for Naive Bayes vs the keyword rule, the paired bootstrap interval excludes 0 but the exact McNemar test gives p = 0.065. The documents call this weak evidence and do not pick the more convenient test.
- **AI mistakes noticed by the AI:** two test counts it reported were wrong (20 and 24 tests instead of 19 and 20), and one expected Wilson bound in a test (0.8337 instead of 0.8334) was corrected before the human ran it.

### Refinement 9: Claim Only What Was Measured (Phase 30)
- **Human decision:** do not run the 60 hand runs. Claim Blocks 2, 3, 4, 5, 7, 8, and keep the Pi benchmark as exploratory.
- **AI choice in quoting the trace:** the acc trace of `mi_split_at` is quoted as R1 evidence (Pi called `find_symbol` and `get_adaptive_context` and resolved the bug). The documents also state that this run needed 98 model calls against 11 in the plain trace, because of a wrong first fix, so the quote is not read as a navigation result.

## 3. Manual Code Corrections & Debugging Annotations

### Correction 1: Python 3.12 AST Unparse Quote Normalization
- **Issue:** In `tests/test_guard.py` and `tests/test_harness.py`, assertions checked for double-quoted f-strings (e.g. `assert 'print(f"Connecting to...")' in output`). Python 3.12's `ast.unparse` formats single quotes by default (`print(f'Connecting to...')`), causing test failures.
- **Correction:** Normalized assertions to substring checks and quote-agnostic pattern matching (`assert "Connecting to" in output`).

### Correction 2: macOS `/var/folders` Temporary Path Handling
- **Issue:** `GuardRail.validate_path` initially blocked all paths starting with `"/var"`. On macOS, `tempfile.TemporaryDirectory()` allocates paths under `/var/folders/...` (which maps to `/private/var/folders/`), causing temporary files in test runners to trigger `SecurityViolationError`.
- **Correction:** Refined system root filtering in `guard.py`:
  ```python
  if norm_str.startswith(("/etc", "/proc", "/sys")):
      return True
  if norm_str.startswith("/var/") and not norm_str.startswith(("/var/folders", "/var/tmp")):
      return True
  ```
  *(Replaced in Phase 16 by the workspace-root rule: only `.py` files inside `ACC_MCP_ROOT` are served.)*

### Correction 3: MCP 2.x SDK Migration (`FastMCP` $\to$ `MCPServer`)
- **Issue:** The official `mcp` SDK migrated from version 1.x to 2.x, renaming `FastMCP` to `MCPServer` in `mcp.server.mcpserver` and changing tool schema attributes (`input_schema` instead of `parameters`).
- **Correction:** Added dual-import fallback in `src/acc_mcp/server.py` (and in the since-removed `harness.py`) supporting both MCP 1.x and 2.x, and updated tests to inspect `context_tool.input_schema`.

### Correction 4: Tests That Silently Depended on the Hand-Picked Weights (Phase 21)
- **Issue:** After the weights were fitted, the human ran the suite and reported one failure: `tests/test_callgraph.py::test_tier2_separates_resolved_and_possible_callers` (216 passed, 1 failed). The test checks the Tier 2 caller listing, but with the fitted weights "Trace callers of Invoice.total" gets $P \approx 0.68 > 0.65$, so the server served Tier 3. The AI had pinned a similar test in `test_server.py` but missed this file.
- **Correction:** Added a `policy_server_model` fixture in `tests/conftest.py` that builds the server with fixed weights, and used it in the three tests that check what a Tier 2 slice contains. Tests of the fit itself use the real defaults.

### Correction 5: Ledger Version Pinned by an Old Test (Phase 23)
- **Issue:** The human's run after Phases 23-24 reported 251 passed, 1 failed: `test_ledger_json_persistence` still expected ledger version `"1.0"`, but the new ledger format is written as `"1.1"`.
- **Correction:** The version bump was intended, so the test was updated, not the code. The AI also noted that in this session it had first edited files with shell commands, against the repository's rule that the AI only reads with the shell; it switched to the editor tools for the remaining edits.

### Correction 6: Click Baseline With 0 Passing Tests (Phase 25)
- **Issue:** `validate` stored a click baseline with 0 passed tests and one failure, `tests.test_deprecations`, so the "no new failures" check was empty for all 5 click tasks. Cause found by reading the code: `tests/test_deprecations.py` calls `importlib.metadata.version("click")` while pytest collects it; click is imported through `PYTHONPATH=src` and not installed, so the call raises and pytest aborts the whole run.
- **Correction:** `setup` writes a metadata-only `click-8.5.0.dist-info` (name and version, no code) into the test venv, so the code still comes only from the workspace. `validate` now fails when a clean suite passes 0 tests, and `freeze` refuses such a baseline. **Not yet confirmed:** `setup` and `validate` have to be run again.

---

## 4. Verification Sign-Off

### Phase 10 sign-off (superseded)
The Phase 10 sign-off reported 110 passing tests, "full coverage", and a 30-run comparative benchmark. Coverage was never measured, and the benchmark was removed in Phase 13 because it did not check the model's answers.

### Status at Phase 22 (superseded)
217 tests; holdout accuracy 70.0%; Block 9 and R1 not done.

### Current status (Phase 30)
- **Claimed blocks:** 2, 3, 4, 5, 7, 8. The Pi benchmark (`eval/`) is exploratory and not claimed.
- **Test Suite:** 307 tests in 13 files, all passing, also on a fresh clone.
- **Decision Model (Block 4):** holdout accuracy 70.0% (95% interval 52.1–83.3%), not significantly better than the hand-picked weights; 63.7% on average over 50 splits.
- **Learned intent model (Block 7):** 46.7% holdout accuracy, below the keyword rule's 70.0%; not used by the server.
- **R1:** verified by the acc trace run `bench1/acc/mi_split_at`.

### Status at Phase 27 (superseded)
268 tests counted but not run; the counted Pi runs were still planned.
