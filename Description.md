# Adaptive Context Compressor MCP (ACC-MCP)
**Course:** Coding for AI & Data Science — Final Project (2026–2027)  
**Target Model:** Qwen3.5-4B (Quantized Q4_K_M GGUF via `llama-server`)  
**Selected Building Blocks:** Block 2, Block 3, Block 4, Block 5, Block 9  

---

## 1. Executive Summary & Motivation

Small local models such as **Qwen3.5-4B** run with a small context window on a laptop (this project uses 30,208 tokens, the per-slot window `llama-server` reports on the reference machine). When coding agents handle multi-file codebases, they often read entire files into that window. This creates two problems:
1. **Context Bloat:** a full window leaves less room for the task, and an overfull request fails outright.
2. **Token Waste:** many questions (verifying a function signature, finding call sites, checking return types) need only the interface, not the function bodies.

**Adaptive Context Compressor (ACC-MCP)** is a standalone Model Context Protocol (MCP) server that acts as an intelligent token-budget gatekeeper. Instead of returning raw source files, an embedded **Decision Model** predicts the minimum context fidelity needed for a specific task and serves a pruned AST (Abstract Syntax Tree) slice. A deterministic code layer enforces strict token ceilings and file security boundaries.

---

## 2. Architecture & Data Flow

```
   ┌────────────────────────────────────────────────────────┐
   │         Agent Harness (e.g., Pi Coding Agent)          │
   │               with Qwen3.5-4B (Local)                  │
   └───────────────────────────┬────────────────────────────┘
                               │ MCP Request:
                               │ get_adaptive_context(file, task_intent)
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        ACC-MCP Server Engine                           │
│                                                                        │
│  1. AST Code Extractor                                                 │
│     Parses target Python file into AST nodes, signatures, and deps.    │
│                                                                        │
│  2. Decision Model (Block 4)                                           │
│     Extracts task & AST features -> computes P(Full Context Needed).   │
│     Selects Tier 1, 2, or 3 based on decision threshold θ.             │
│                                                                        │
│  3. Hard Code Guardrail (Block 3)                                      │
│     - Serves only .py files inside the workspace; secrets rejected.    │
│     - Cuts output to a hard token ceiling (2,000 tokens).              │
│                                                                        │
│  4. Session Context Budget Tracker (Block 5)                           │
│     Per-session token budget, ledger, and cached symbol index.         │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │ Return compressed AST slice
                               ▼
   ┌────────────────────────────────────────────────────────┐
   │             Qwen3.5-4B Generates Solution              │
   │          (From a Compressed, Bounded Context)          │
   └────────────────────────────────────────────────────────┘
```

---

## 3. The 3-Tier Adaptive Compression Model

When context is requested, the system compiles the code into one of three structural tiers:

1. **Tier 1 (Signatures & Types):**  
   Exports module structure, class definitions, function signatures, docstrings, and type hints. Omits all function bodies (`...`).
2. **Tier 2 (Skeleton & Call-Graph Slice):**  
   Exports Tier 1 plus direct callers (including callers in other workspace files), callees, and raised exceptions for the target symbol.
3. **Tier 3 (Full Implementation Body):**  
   Exports the source of the requested symbol verbatim from the file, with line numbers, cut to the 2,000-token ceiling.

---

## 4. Formalization & Mathematical Formulations

### 4.1 Feature Extraction
For a given query $(T, F, S)$ where $T$ is the task intent string, $F$ is the target file, and $S$ is the target symbol:
* $x_1 \in \{0, 1, 2\}$: Task Intent Depth ($0 = \text{inspect/call}$, $1 = \text{refactor/patch interface}$, $2 = \text{full algorithmic rewrite}$).
* $x_2 \in \mathbb{N}$: Cyclomatic Complexity ($CC$) of symbol $S$.
* $x_3 \in \mathbb{N}$: In-degree ($Deg_{in}$), the number of callers dependent on $S$.
* $x_4 \in \{0, 1\}$: Presence of explicit type hints and docstrings ($1$ if present, $0$ otherwise).

### 4.2 Decision Model (Logistic Scoring Function)
The logit score $z$ determines the probability that the agent strictly requires the full implementation body (Tier 3):

$$z = w_0 + w_1 x_1 + w_2 x_2 + w_3 x_3 - w_4 x_4$$

The probability $P(\text{Full Context Needed})$ is calculated as:

$$P(\text{Full}) = \frac{1}{1 + e^{-z}}$$

### 4.3 Decision Policy & Thresholds
With hand-picked thresholds $\theta_{\text{low}} = 0.30$ and $\theta_{\text{high}} = 0.65$ (the weights $w_0 \dots w_4$ are fitted on training data, see Block 4):

$$\text{Tier}(P) = \begin{cases} 
\text{Tier 1 (Signatures Only)}, & \text{if } P(\text{Full}) < \theta_{\text{low}} \\
\text{Tier 2 (Skeleton Slice)}, & \text{if } \theta_{\text{low}} \le P(\text{Full}) < \theta_{\text{high}} \\
\text{Tier 3 (Full Body)}, & \text{if } P(\text{Full}) \ge \theta_{\text{high}}
\end{cases}$$

### 4.4 Token Savings Metric
For each retrieval event, relative token reduction is computed against the raw full-file read:

$$\text{Savings (\%)} = \frac{\text{Tokens}_{\text{Full File}} - \text{Tokens}_{\text{Served Tier}}}{\text{Tokens}_{\text{Full File}}} \times 100\%$$

---

## 5. Mapping to the 5 Course Building Blocks

### Block 2: Your Own MCP Server
* **Implementation:** Built using the standard Python `mcp` SDK via stdio transport.
* **Exposed Tools:**
  * `get_adaptive_context(file_path: str, task_intent: str, symbol_name: str | None, min_tier: int | None)`: Runs the decision model and returns the slice; `min_tier` lets the agent ask for a higher tier when a slice was not enough.
  * `find_symbol(name: str)`: Finds where a class, function, or method is defined in the workspace.
  * `get_token_ledger()`: Returns token totals, `escalation_rate`, the session budget, and recent history.
  * `reset_session()`: Starts a fresh session token budget for a new task.
* **Evidence:** Tool tests in `tests/test_server.py`; a real agent session trace comes from the Pi runs in Phase 14.

### Block 3: A Rule Enforced in Code
* **Implementation:** A deterministic safety guardrail implemented in Python before any code is read or served.
* **Enforced Rules:**
  1. *Protected Path Rule:* Only `.py` files inside the workspace root are served, checked on the real path after symlinks; anything else (e.g. `.env`, `.git/`, `credentials.json`, `*.pem`, `../` traversal) raises `SecurityViolationError`.
  2. *Hard Context Ceiling:* No slice is returned above $C_{\max} = 2{,}000$ tokens (as counted by the server's token counter). Oversized slices are cut deterministically at statement boundaries, not by LLM prompting.
* **Evidence:** `tests/test_guard.py` requests `.env`, paths outside the workspace, symlinks, and oversized files, and checks that they are rejected or cut in code.

### Block 4: A Decision Model
* **Implementation:** A logistic model whose weights are fitted by gradient descent on a train split; the thresholds are hand-picked.
* **Evaluation:** 100 labeled task intents (29 Tier 1, 31 Tier 2, 40 Tier 3), split 70/30 by tier. Results are reported separately for train and for the 30-task holdout the fit never saw.
* **Metrics:** 3x3 tier confusion matrix, false alarm rate (served a higher tier than needed), and miss rate (served a lower tier than needed). The holdout numbers are in `README.md`.

### Block 5: Memory or Context Budget Management
* **Implementation:** A per-session token budget plus a ledger that persists across server restarts.
* **Mechanisms:**
  * Caps the tokens served per session and steps down a tier when the chosen one does not fit.
  * Persists the request history, totals, and the workspace symbol index to a local `context_budget.json` ledger.
  * Restart test: after a restart the symbol index is rebuilt from the ledger and unchanged files are not parsed again (the file being served is still parsed for each request).

### Block 9: A Measured Comparison
* **Implementation:** Our own bug-fix benchmark run through the Pi coding agent (method in `eval/PROTOCOL.md`, runner `eval/pi_bench.py`). SWE-bench was ruled out because its grader needs a multi-GB Docker image per task; small exercise sets were ruled out because their single small files leave nothing for ACC-MCP to save.
* **Repositories and tasks:** 10 tasks, each a one-line bug injected into one function of a pinned release of a popular pure-Python library (the SWE-smith approach), graded by that library's own tests:
  * **more-itertools `v11.2.1`** (https://github.com/more-itertools/more-itertools): 4 tasks in `more_itertools/more.py` (5,644 lines, ~45K tokens, larger than the model's 30,208-token window) and 1 in `recipes.py`: `split_at`, `numeric_range.index`, `sliding_window`, `is_sorted`, `constrained_batches`.
  * **click `8.5.0`** (https://github.com/pallets/click): 5 tasks across `core.py` (3,799 lines), `types.py`, `parser.py`, `formatting.py`: argument unpacking, `IntRange` minimum, eager-parameter order, `nargs=-1` envvar splitting, `--help` column layout.
  * 6 tasks are **direct** (the report points at the buggy function); 4 are **call-graph** tasks (`sliding_window`, argument unpacking, eager order, `--help` columns), where the report names a function whose helper has the bug, so Tier 2 (callees) can matter.
* **Comparison:** every task is done twice by hand in interactive Pi (the operator pastes the bug report and follows fixed rules: no hints, only "Proceed as you think best." / "Continue."), with the same model, prompt, tools, settings and time limit; measurements come from Pi's own session file:
  * **Baseline:** Plain Pi with Qwen3.5-4B.
  * **Gated:** Pi with Qwen3.5-4B and ACC-MCP (plus one sentence telling the model to prefer it for reading code).
* **Grading:** the agent sees only a user-style bug report. After its run the original tests are restored; the task is resolved when the target tests pass and the full suite has no new failures.
* **Reported Metrics:** resolved or not; navigation calls before the first edit of the target function and code-reading tokens, measured against the agent's own `grep` + windowed `read` strategy; wasted and large reads; peak context of one model call, total prompt tokens, model calls, tools used; tiers served, and explicit (`min_tier`) and silent escalations. Hypotheses H1-H3 are in `eval/PROTOCOL.md` section 1; the pilot session is excluded from the results.
* **Where:** results go to `eval/results.md`. These runs have not been done yet.

---

## 6. Verification and Requirements (R1–R4)

* **R1 (It Runs):** Runs inside the Pi coding agent against a local `llama-server` serving Qwen3.5-4B (`Q4_K_M` GGUF) on port 8080, with setup steps in `README.md` (pending Phase 14).
* **R2 (Automated Tests):** `pytest` suite testing AST parsing, the call graph, decision model outputs, safety gates, the session budget, and the MCP tools.
* **R3 (AI-Use Log):** Work session transcripts recorded in `ai_log/` with annotations on prompts refined and manual code corrections.
* **R4 (Defence Readiness):** Clear, modular code layout with strict separation between AST parsing (`parser.py`), decision model inference (`decision.py`), guardrails (`guard.py`), budget and symbol index (`budget.py`, `index.py`), and MCP endpoints (`server.py`).
