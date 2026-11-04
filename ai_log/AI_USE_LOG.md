# AI-Use Log & Prompt Refinement Transcript (R3)

**Project:** Adaptive Context Compressor MCP (ACC-MCP / OOM-Guard)  
**Course:** Coding for AI & Data Science — Final Project (2026–2027)  
**Target Model:** Qwen3.5-4B (Quantized Q4_K_M GGUF via `llama-server`)  
**Building Blocks Implemented:** Block 2, Block 3, Block 4, Block 5, Block 9  
**Requirements Addressed:** R1 (It Runs), R2 (Automated Tests), R3 (AI-Use Log), R4 (Defence Readiness)  

---

## 1. Overview and Chronology of AI Collaboration

This document fulfills course requirement **R3 (AI-Use Log)** by recording work sessions, prompt refinements, design decisions, manual code corrections, and debugging interventions. The plan lives in `todo.md` (22 phases). Exported session transcripts are in this folder (`*.md`).

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
| **Phase 14** | Pi integration & manual evaluation | **Not done yet.** Planned: workspace root setting, per-run ledger, Pi setup, `eval/PROTOCOL.md`, `eval/results.md`. (`ACC_MCP_ROOT` itself was added in Phase 16.) | — |
| **Phase 15** | Edit-ready output | Tier 3 copied verbatim from the file, `# file lines a-b: symbol` headers, explicit "omitted lines" notes. | `be3b350` |
| **Phase 16** | Path security | Only `.py` files inside the workspace root; resolved (symlink) paths checked. | `53bb6b4` |
| **Phase 17** | Symbol index | Workspace index with SHA-256 hash-checked cache and the `find_symbol` tool. | `b5b36e2` |
| **Phase 18** | Cross-file call graph | Callers found through imports; typed receivers resolved; unknown receivers listed as possible callers. | `96bde66` |
| **Phase 19** | Session budget | Budget drives tier selection (step down, refuse forced tiers that do not fit), `reset_session`, one ledger save per request, history cap. | `17b713b` |
| **Phase 20** | Decision model quick fixes | 3x3 confusion matrix, exact word-form keywords, `escalation_rate` in the ledger, README states the weights were hand-picked. | `048dcbf` |
| **Phase 21** | Honest calibration | 100-task dataset, stratified 70/30 split, weights fitted by gradient descent, train and holdout reported separately. See Refinement 4. | `41b6c93` |
| **Phase 22** | Documentation cleanup | README, `Description.md`, and this log aligned with the code; unsupported claims removed; `LICENSE` added. See Refinement 5. | pending |

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

---

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

---

## 4. Verification Sign-Off

### Phase 10 sign-off (superseded)
The Phase 10 sign-off reported 110 passing tests, "full coverage", and a 30-run comparative benchmark. Coverage was never measured, and the benchmark was removed in Phase 13 because it did not check the model's answers.

### Current status (Phase 22)
- **Test Suite:** 217 tests in 10 test files. The last run reported by the human was 216 passed, 1 failed (Correction 4); the fix has to be confirmed by the next full run.
- **Decision Model:** holdout accuracy 70.0%, miss rate 14.3%, false alarm rate 33.3% on 30 unseen tasks (see `README.md`).
- **Block 9 / R1:** not done yet; they need the Phase 14 Pi runs.
