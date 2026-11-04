# Adaptive Context Compressor MCP (ACC-MCP / OOM-Guard)

[![Python: 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://python.org)
[![Tests: 110 Passed](https://img.shields.io/badge/Tests-110%20Passed-brightgreen.svg)](https://pytest.org)
[![Protocol: MCP 2.x](https://img.shields.io/badge/Protocol-MCP%202.x-purple.svg)](https://modelcontextprotocol.io)
[![Target Model: Qwen3.5-4B](https://img.shields.io/badge/Target%20Model-Qwen3.5--4B-orange.svg)](https://huggingface.co/Qwen)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> **Course:** Coding for AI & Data Science — Final Project (2026–2027)  
> **Target Model:** Qwen3.5-4B (Quantized `Q4_K_M` via `llama.cpp` / Ollama / Cerebras fallback)  
> **Course Building Blocks:** **Block 2**, **Block 3**, **Block 4**, **Block 5**, **Block 9**  
> **Requirements Addressed:** **R1** (It Runs), **R2** (Automated Tests), **R3** (AI-Use Log), **R4** (Defence Readiness)  

---

## 1. Executive Summary & Problem Formulation

Local, small-scale language models like **Qwen3.5-4B** operate under constrained context budgets (typically 8,192 to 16,384 tokens on an 8 GB / 16 GB laptop). When coding agents handle real-world software repositories, their default retrieval strategy is naive: **they dump entire uncompressed source files into the context window.**

This creates two critical points of failure:
1. **Context Bloat & Out-of-Memory (OOM) Crashes:** Saturating the context window degrades reasoning accuracy, creates "lost-in-the-middle" attention degradation, and causes catastrophic context length overflow errors.
2. **Token Waste:** More than 70% of developer queries (e.g., inspecting an API signature, checking type hints, tracing caller dependencies) require only interface boundaries, not full function implementation bodies.

**Adaptive Context Compressor (ACC-MCP)** is a standalone Model Context Protocol (MCP) server that acts as an intelligent context gatekeeper. Instead of returning raw source files, an embedded **Decision Model** predicts the minimum context fidelity needed for the task and serves a pruned Abstract Syntax Tree (AST) slice. A deterministic code layer enforces strict token ceilings and security boundaries.

---

## 2. System Architecture & Information Flow

```
┌────────────────────────────────────────────────────────┐
│         Agent Harness (e.g., Pi Coding Agent)          │
│               with Qwen3.5-4B (Local)                  │
└───────────────────────────┬────────────────────────────┘
                            │ MCP Request:
                            │ get_adaptive_context(file_path, task_intent)
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     ACC-MCP Server Engine (Stdio)                      │
│                                                                        │
│  1. AST Code Extractor (src/acc_mcp/parser.py)                         │
│     Parses Python code into AST nodes, signatures, and call-graphs.    │
│                                                                        │
│  2. Decision Model (Block 4 - src/acc_mcp/decision.py)                 │
│     Extracts query & AST features -> computes P(Full Context Needed).  │
│     Selects Tier 1, 2, or 3 based on hand-picked thresholds (θ).       │
│                                                                        │
│  3. Hard Code Guardrail (Block 3 - src/acc_mcp/guard.py)               │
│     - Protected Path Rule: Rejects .env, .git, credentials, alembic.   │
│     - Hard Token Ceiling: Deterministic AST slicing (C_max <= 2,000).  │
│                                                                        │
│  4. Session Context Budget Tracker (Block 5 - src/acc_mcp/budget.py)   │
│     Logs cumulative token consumption & savings in context_budget.json │
│     Caches repository symbol map for instant cold restart recovery.    │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │ Return compressed AST slice
                               ▼
┌────────────────────────────────────────────────────────┐
│             Qwen3.5-4B Generates Solution              │
│           (From a Compressed, Bounded Context)         │
└────────────────────────────────────────────────────────┘
```

---

## 3. The 3-Tier Adaptive Compression Model

When context is requested, ACC-MCP dynamically compiles the target file into one of three structural tiers:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 1: Signatures, Types & Docstrings                                 │
│ Omits all function and method bodies (`...`). Footprint: ~50–120 toks. │
└────────────────────────────────────────────────────────────────────────┘
                               ▲
                               │ P(Full) < 0.30
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 2: Skeleton & Call-Graph Slice                                    │
│ Tier 1 + direct callers, callees, exceptions. Footprint: ~200–450 toks │
└────────────────────────────────────────────────────────────────────────┘
                               ▲
                               │ 0.30 <= P(Full) < 0.65
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 3: Full Implementation Body                                       │
│ Complete uncompressed implementation of target. Footprint: 1,000–3,000 │
└────────────────────────────────────────────────────────────────────────┘
                               ▲
                               │ P(Full) >= 0.65
```

- **Tier 1 (Signatures & Types):** Exports class headers, function signatures, type annotations, and docstrings. Function bodies are stubbed with `...`.
- **Tier 2 (Skeleton & Call-Graph Slice):** Exports Tier 1 plus direct in-degree callers, out-degree callees, raised exceptions, and adjacent dependency signatures. Callers in other workspace files are found through `import x`, `import x as y`, `from x import y` and relative imports, and are listed with file path and call line. Calls are matched to a definition, not by bare name: `self.m()` / `super().m()` resolve within the class and its bases, and `obj.m()` resolves when `obj` has a parameter annotation or was assigned `Cls(...)`. An `obj.m()` call with an unknown receiver type is shown as a *possible* caller and is not counted.
- **Tier 3 (Full Implementation Body):** Exports the requested symbol copied verbatim from the file (comments and formatting intact), plus top-level imports and, for a method, the enclosing class header. Sibling methods are listed by signature and line range instead of being shown.
- **Edit-ready headers (all tiers):** Every slice starts with lines like `# service.py lines 40-72: PaymentService.validate`, so the agent can open or edit the exact lines. If the token ceiling cuts a Tier 3 slice, it is cut at a statement boundary and a note such as `# [ACC-MCP] lines 120-310 omitted to fit the 2000-token ceiling; request symbol_name="Router.dispatch" to see them` says what is missing. A function that is cut part-way is flagged as incomplete.

---

## 4. Mapping to the 5 Course Building Blocks

### Block 2: Your Own MCP Server (`src/acc_mcp/server.py`)
- **Implementation:** Built using the standard Python `mcp` SDK with forward-compatibility across MCP 1.x and 2.x (`MCPServer` / `FastMCP`) via stdio transport.
- **Exposed Tools:**
  1. `get_adaptive_context(file_path: str, task_intent: str, symbol_name: str | None = None)`: Executes the intent classifier and decision model, retrieves the optimal AST slice, enforces security guardrails, and records audit tokens.
  2. `find_symbol(name: str)`: Finds where a class, function, or method is defined anywhere in the workspace, returning file path (relative to the workspace root), qualified name, kind, and line range as JSON.
  3. `get_token_ledger()`: Returns the cumulative session token audit log and savings ledger in JSON format.
- **Evidence:** Tested with 8 integration tests in [`tests/test_server.py`](file:///Users/nefelim584/PycharmProjects/OOM-Guard/tests/test_server.py) verifying protocol discovery, tool invocation, error handling, and state persistence across server restarts.

### Block 3: A Rule Enforced in Code (`src/acc_mcp/guard.py`)
- **Implementation:** A deterministic safety guardrail implemented in Python before context generation or write operations.
- **Enforced Rules:**
  1. *Protected Path Rule:* Only `.py` files inside the workspace root (`ACC_MCP_ROOT`, default: the server's working directory) are served. The check runs on both the path as sent and its real path after symlinks are followed, so `notes.py -> .env` is rejected. Anything else raises `SecurityViolationError`:
     - Paths outside the root: absolute paths elsewhere, `../` traversal, symlinks that leave the root
     - Non-Python files: `README.md`, `config.yaml`, `*.pyc`
     - Environment configs: `.env`, `.env.local`, `.env.production`
     - Version control metadata: `.git/`
     - Secrets and keys: `credentials.json`, `secrets.json`, `.ssh/`, `.aws/`, `*.pem`, `*.key`, `id_rsa`, `.npmrc`, `.pypirc`
     - Database migrations: `alembic/migrations/`
  2. *Hard Context Ceiling ($C_{\max} = 2{,}000$ tokens):* Under no circumstances can any generated context slice exceed 2,000 tokens. If context exceeds $C_{\max}$, it is truncated **deterministically by AST node slicing in code, not by LLM prompting.** Slices cleanly at statement and function boundaries, appending `...` while guaranteeing valid Python syntax.
- **Evidence:** Tested with 39 automated tests in [`tests/test_guard.py`](file:///Users/nefelim584/PycharmProjects/OOM-Guard/tests/test_guard.py).

### Block 4: A Decision Model (`src/acc_mcp/decision.py` & `evaluate_decision.py`)
- **Feature Vector:** $(x_1, x_2, x_3, x_4)$ where:
  - $x_1 \in \{0, 1, 2\}$: Task Intent Depth ($0 = \text{inspect/call}$, $1 = \text{refactor/patch interface}$, $2 = \text{full algorithmic rewrite}$).
  - $x_2 \in \mathbb{N}$: Cyclomatic Complexity ($CC$) of target symbol.
  - $x_3 \in \mathbb{N}$: In-degree ($Deg_{in}$), number of resolved callers of the target in the same file and in other workspace files (possible callers are not counted).
  - $x_4 \in \{0, 1\}$: Presence of explicit type hints and docstrings.
- **Logistic Scoring Function:**
  $$z = w_0 + w_1 x_1 + w_2 x_2 + w_3 x_3 - w_4 x_4$$
  $$P(\text{Full}) = \frac{1}{1 + e^{-z}}$$
  *Fitted weights:* $w_0 = -1.2421, w_1 = 2.1909, w_2 = -0.0228, w_3 = -0.1530, w_4 = 0.0407$ (before Phase 21 they were hand-picked: $-2.0, 2.0, 0.2, 0.3, 1.0$).
- **Decision Policy Thresholds:** $\theta_{\text{low}} = 0.30, \theta_{\text{high}} = 0.65$. These are still hand-picked; only the weights are fitted.
- **Intent Classifier:** $x_1$ is set by counting keyword hits per depth. Keywords match whole word forms only (`add`, `adds`, `added`, `adding` but not `address`; `type`/`types` but not `typed`; `list` but not the annotation `list[int]`). A query with no keyword hit falls back to $x_1 = 1$.
- **Dataset:** [`data/decision_dataset.json`](data/decision_dataset.json) has 100 short task intents: 29 Tier 1, 31 Tier 2, 40 Tier 3. Labels follow one rule: Tier 1 if signatures, docstrings or types answer the task; Tier 2 if it reaches callers or callees; Tier 3 if the body must be read or edited. Many intents are written the way people actually ask ("Why does…", "Explain…", "Who calls…") rather than with the classifier's verbs. Tasks share 29 code snippets through `snippet_ref`, so the same symbol appears with intents of different tiers, and called or unannotated symbols appear in every tier.
- **Fitting:** `fit_weights` in `decision.py` runs plain batch gradient descent on the logistic loss (no new dependencies). The target is $y = (\text{tier} - 1)/2 \in \{0, 0.5, 1\}$, with a small L2 penalty, starting from zero. The split is 70/30, stratified by tier and seeded, so every tier is on both sides. Run `python3 src/acc_mcp/evaluate_decision.py` to refit and print the report; `tests/test_decision_eval.py` fails if the constants in `decision.py` drift from the fit.
- **Holdout results (30 tasks the fit never saw):**

  | | Accuracy | Miss rate | False alarm rate |
  | :--- | :---: | :---: | :---: |
  | Fitted weights, holdout | **70.0%** | **14.3%** (3) | **33.3%** (6) |
  | Fitted weights, train (70 tasks) | 67.1% | 20.0% (10) | 31.0% (13) |
  | Old hand-picked weights, same holdout | 66.7% | 28.6% (6) | 22.2% (4) |

  Holdout 3x3 confusion matrix (rows = actual tier, columns = predicted tier 1/2/3): Tier 1 `8 0 1`, Tier 2 `1 3 5`, Tier 3 `1 1 10`. Tier 2 recall is only 33%: most Tier 2 tasks get Tier 3.
- **What the fit shows:**
  - The tier is still decided almost entirely by $x_1$. With the fitted weights, on the dataset $x_1 = 0$ gives $P \approx 0.1$–$0.22$ (Tier 1), $x_1 = 2$ gives $P \ge 0.9$ (Tier 3), and $x_1 = 1$ gives $P \approx 0.55$–$0.72$, which sits right on $\theta_{\text{high}}$.
  - $x_2$ (complexity) and $x_4$ (annotations) get weights near zero. Setting either to 0 changes at most 3 train and 2 holdout tiers, because many $x_1 = 1$ tasks sit so close to $\theta_{\text{high}}$ that a tiny shift moves them.
  - $x_3$ (in-degree) gets a **negative** weight. For $x_1 = 1$ it decides between Tier 2 (the symbol has callers) and Tier 3 (it has none), and changes 10 train and 4 holdout tiers. This comes from the data, not from a design idea; with this dataset, more callers does not mean more context is needed.
  - Most errors come from the keyword classifier, not the weights. Intents with no keyword fall back to $x_1 = 1$. Caller questions phrased with inspection words ("Find every function that calls…") get $x_1 = 0$. Body edits phrased with Tier 2 verbs ("Add retry logic inside…") get $x_1 = 1$.
- **Limits of this measurement:** the labels and intents were written by the project author, not collected from users. Train and holdout tasks can share a code snippet, so $x_2$–$x_4$ values are not fully unseen. Only 2 intents are real (from the server ledger); real Pi intents will be added once the Phase 14 runs exist.
- The real-world signal is `escalation_rate` in `get_token_ledger`: the share of requests where the agent had to raise `min_tier` above the model's choice because the slice it got was not enough.

### Block 5: Memory or Context Budget Management (`src/acc_mcp/budget.py`)
- **Implementation:** An active context budget manager persisting state across agent runs.
- **Mechanisms:**
  - Tracks per-query token consumption and relative token savings:
    $$\text{Savings (\%)} = \frac{\text{Tokens}_{\text{Full File}} - \text{Tokens}_{\text{Served Tier}}}{\text{Tokens}_{\text{Full File}}} \times 100\%$$
  - Persists multi-turn ledger and session summaries to `context_budget.json`.
  - **Workspace Symbol Index (`src/acc_mcp/index.py`):** Walks every servable `.py` file under the workspace root (skipping `.venv`, `venv`, `.git`, `__pycache__`, `node_modules`) and stores each file's symbol map (names, line ranges, complexities, callers, callees, raised exceptions) in the ledger. Each file's SHA-256 is compared with the cached `file_hash`: unchanged files are reused, changed files are re-parsed, deleted files are dropped.
  - **Cold Restart Test:** After a restart, the index is rebuilt from the ledger and unchanged files are not parsed again (the tests count parser calls). The file served by `get_adaptive_context` is still parsed on each request, because slices are cut from its AST.
- **Evidence:** Tested in [`tests/test_budget.py`](file:///Users/nefelim584/PycharmProjects/OOM-Guard/tests/test_budget.py) and [`tests/test_index.py`](file:///Users/nefelim584/PycharmProjects/OOM-Guard/tests/test_index.py).

### Block 9: A Measured Comparison (Manual Evaluation with Pi)
- **Status:** Evaluation is done manually with the Pi coding agent (plain Pi vs Pi + ACC-MCP) on real Python tasks that come with tests. The protocol and results will live in `eval/` (see Phase 14 in `todo.md`).
- An earlier simulated benchmark was removed: it marked every gated run as a success without checking the model's answer, and one generated file produced most of its reported saving.

---

## 5. Course Verification Checklist (R1–R4)

| Requirement | Description | Status | Evidence |
| :--- | :--- | :---: | :--- |
| **R1 (It Runs)** | Runs locally against `llama-server` (Qwen3.5-4B) or Ollama/Cerebras fallback. | **PENDING** | Stdio MCP server (`src/acc_mcp/server.py`), local setup guide below; real-agent (Pi) setup in Phase 14. |
| **R2 (Automated Tests)** | Comprehensive `pytest` suite testing parser, decision model, guardrails, and server. | **VERIFIED** | **110 passing tests** across 8 test suites running in ~1.0 second. |
| **R3 (AI-Use Log)** | Work session transcripts recorded in `ai_log/` with prompt refinements and code fixes. | **VERIFIED** | Full chronology & annotations in [`ai_log/AI_USE_LOG.md`](file:///Users/nefelim584/PycharmProjects/OOM-Guard/ai_log/AI_USE_LOG.md). |
| **R4 (Defence Readiness)** | Clean, modular code layout with strict separation of concerns. | **VERIFIED** | Clean package layout: `parser.py`, `guard.py`, `decision.py`, `budget.py`, `server.py`. |

---

## 6. Installation & Quickstart

### Prerequisites
- Python 3.12+
- `uv` (recommended) or standard `pip`

### 1. Installation
```bash
git clone https://github.com/nefelim584/OOM-Guard.git
cd OOM-Guard

# Using uv (fastest)
uv sync

# Or using pip in a virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Running the Automated Test Suite (R2)
```bash
# Run full 110-test suite via uv
uv run pytest

# Or directly via python3
python3 -m pytest
```

### 3. Fitting and Evaluating the Decision Model (Block 4)
```bash
# Fits the weights on the train split and prints train and holdout metrics separately
python3 src/acc_mcp/evaluate_decision.py
```

---

## 7. Connecting ACC-MCP to Your Agent or IDE (R1)

### Option A: Running against Local `llama-server` (`llama.cpp`)
1. Download quantized Qwen3.5-4B GGUF:
   ```bash
   huggingface-cli download Qwen/Qwen2.5-Coder-3B-Instruct-GGUF qwen2.5-coder-3b-instruct-q4_k_m.gguf --local-dir ./models
   ```
2. Start `llama-server` on port 8080:
   ```bash
   ./llama-server -m ./models/qwen2.5-coder-3b-instruct-q4_k_m.gguf -c 8192 --port 8080
   ```
3. Point an MCP-capable coding agent at that endpoint and register ACC-MCP as a stdio server (see Option C). Step-by-step Pi coding agent setup is added in Phase 14.

### Option B: Running against `Ollama`
```bash
ollama run qwen2.5-coder:3b
```
Then register ACC-MCP with your agent as in Option C.

### Option C: Configuring in Claude Desktop / Cursor / Antigravity IDE (Stdio MCP)
Add ACC-MCP to your MCP settings file (`claude_desktop_config.json` or `mcp_servers.json`):
```json
{
  "mcpServers": {
    "acc-mcp": {
      "command": "python3",
      "args": ["-m", "acc_mcp.server"],
      "cwd": "/path/to/OOM-Guard"
    }
  }
}
```

---

## 8. Repository Layout

```
OOM-Guard/
├── src/
│   └── acc_mcp/
│       ├── __init__.py           # Package exports
│       ├── parser.py             # AST parsing, CC calculator, Tier 1/2/3 extraction
│       ├── tokens.py             # Pluggable token counter (Endpoint & Heuristic)
│       ├── guard.py              # Block 3: Security path rules & AST ceiling slicing
│       ├── decision.py           # Block 4: Intent classifier & logistic scoring
│       ├── evaluate_decision.py  # Block 4: Train/holdout split, weight fit, and report
│       ├── budget.py             # Block 5: Token ledger & restart recovery cache
│       ├── index.py              # Block 5: Workspace symbol index (hash-checked cache)
│       └── server.py             # Block 2: Stdio MCP Server (get_adaptive_context)
├── data/
│   └── decision_dataset.json     # 20 ground-truth coding benchmark tasks
├── tests/
│   ├── test_parser_tier1.py      # Tier 1 AST interface tests
│   ├── test_parser_tier2.py      # Tier 2 call-graph & complexity tests
│   ├── test_callgraph.py         # Cross-file, import-aware call graph tests
│   ├── test_guard.py             # Block 3 security boundary & ceiling tests
│   ├── test_decision.py          # Block 4 scoring model & tier policy tests
│   ├── test_decision_eval.py     # Block 4 dataset, split, fit, and train/holdout metrics tests
│   ├── test_budget.py            # Block 5 multi-turn ledger & restart tests
│   ├── test_index.py             # Block 5 symbol index, cache reuse & find_symbol tests
│   └── test_server.py            # Block 2 MCP protocol & tool integration tests
├── ai_log/
│   └── AI_USE_LOG.md             # R3: Complete transcript of AI prompts & fixes
├── pyproject.toml                # Project metadata & pytest configuration
├── todo.md                       # 10-phase project implementation roadmap
└── README.md                     # Comprehensive project documentation
```

---

## 9. License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
