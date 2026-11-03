# Adaptive Context Compressor MCP (ACC-MCP)
**Course:** Coding for AI & Data Science — Final Project (2026–2027)  
**Target Model:** Qwen3.5-4B (Quantized Q4_K_M via `llama.cpp` / Ollama)  
**Selected Building Blocks:** Block 2, Block 3, Block 4, Block 5, Block 9  

---

## 1. Executive Summary & Motivation

Local, small-scale language models like **Qwen3.5-4B** operate under constrained context budgets (typically 8,192 to 16,384 tokens on an 8 GB laptop). When coding agents handle multi-file codebases, their default retrieval strategy is naive: they inject entire target files into the context window. This creates two immediate points of failure:
1. **Context Bloat & Degradation:** Saturating the context window degrades reasoning accuracy, increases latency, and risks outright out-of-memory (OOM) failures.
2. **Token Waste:** More than 70% of typical developer queries (e.g., verifying a function signature, finding call sites, checking return types) require only interface boundaries, not full function bodies.

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
│     - Drops blacklisted files (.env, credentials, migrations).         │
│     - Truncates output to a hard token ceiling (e.g., 2,000 tokens).   │
│                                                                        │
│  4. Session Context Budget Tracker (Block 5)                           │
│     Logs cumulative token consumption and savings vs. baseline.        │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │ Return compressed AST slice
                               ▼
   ┌────────────────────────────────────────────────────────┐
   │             Qwen3.5-4B Generates Solution              │
   │            (Using 60–85% Fewer Prompt Tokens)          │
   └────────────────────────────────────────────────────────┘
```

---

## 3. The 3-Tier Adaptive Compression Model

When context is requested, the system compiles the code into one of three structural tiers:

1. **Tier 1 (Signatures & Types):**  
   Exports module structure, class definitions, function signatures, docstrings, and type hints. Omits all function bodies (`...`).  
   *Token footprint: ~50–120 tokens.*
2. **Tier 2 (Skeleton & Call-Graph Slice):**  
   Exports Tier 1 plus direct callers, callees, and raised exceptions for the target symbol.  
   *Token footprint: ~200–450 tokens.*
3. **Tier 3 (Full Implementation Body):**  
   Exports the complete uncompressed source code for the requested symbol and its immediate block.  
   *Token footprint: 1,000–3,000+ tokens.*

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
With calibrated thresholds $\theta_{\text{low}} = 0.30$ and $\theta_{\text{high}} = 0.65$:

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
  * `get_adaptive_context(file_path: str, task_intent: str, symbol_name: str | None)`: Runs the decision model and returns the optimal AST slice.
  * `get_token_ledger()`: Returns the cumulative token audit log for the current session.
* **Evidence:** Integration tests in `tests/test_server.py`; a real agent session trace comes from the Pi runs in Phase 14.

### Block 3: A Rule Enforced in Code
* **Implementation:** A deterministic safety guardrail implemented in Python before context generation or write operations.
* **Enforced Rules:**
  1. *Protected Path Rule:* Immediate rejection (`SecurityViolationError`) if a query targets protected files (`.env`, `.git/`, `credentials.json`, `alembic/migrations/`).
  2. *Hard Context Ceiling:* Under no circumstances can any generated context slice exceed $C_{\max} = 2{,}000$ tokens. If Tier 3 exceeds $C_{\max}$, it is truncated deterministically by AST node slicing, not by LLM prompting.
* **Evidence:** Automated test suite attempting to request context for `.env` and oversized files, verifying that the system rejects the operation via code exceptions.

### Block 4: A Decision Model
* **Implementation:** A calibrated classifier trained or fitted to predict whether code tasks require full bodies versus signatures.
* **Evaluation:** Tested across a labeled ground-truth dataset of at least **20 distinct coding tasks** (10 interface-only tasks, 10 deep logic rewrite tasks).
* **Metrics:** Documented confusion matrix, false alarm rate (allocating full context when signatures sufficed), and miss rate (allocating signatures when full bodies were required).

### Block 5: Memory or Context Budget Management
* **Implementation:** An active context budget manager persisting state across agent runs.
* **Mechanisms:**
  * Tracks cumulative prompt token consumption across multi-turn interactions.
  * Persists session cache summaries to a local `context_budget.json` ledger.
  * Restart test: Demonstrates that upon agent restart, the server retains the repository's symbol map and token statistics without re-parsing files.

### Block 9: A Measured Comparison
* **Implementation:** Manual evaluation with the Pi coding agent on real Python tasks that come with tests (see Phase 14 in `todo.md`).
* **Comparison:**
  * **Baseline:** Plain Pi with Qwen3.5-4B.
  * **Gated:** Pi with Qwen3.5-4B and ACC-MCP.
* **Reported Metrics:** Whether the task's tests pass, prompt tokens used, tiers served, and how often the agent escalated with `min_tier`.

---

## 6. Verification and Requirements (R1–R4)

* **R1 (It Runs):** Runs locally against `llama-server` running `Qwen3.5-4B-Q4_K_M.gguf` on port 8080 (or Cerebras Qwen 3.8 27B fallback) via a documented `README.md`.
* **R2 (Automated Tests):** Comprehensive `pytest` suite testing AST parsing, decision threshold outputs, safety gates, and MCP protocol responses.
* **R3 (AI-Use Log):** Work session transcripts recorded in `ai_log/` with annotations on prompts refined and manual code corrections.
* **R4 (Defence Readiness):** Clear, modular code layout with strict separation between AST parsing (`parser.py`), decision model inference (`decision.py`), guardrails (`guard.py`), and MCP endpoints (`server.py`).
