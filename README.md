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
│     Selects Tier 1, 2, or 3 based on calibrated thresholds (θ).        │
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
- **Tier 2 (Skeleton & Call-Graph Slice):** Exports Tier 1 plus direct in-degree callers, out-degree callees, raised exceptions, and adjacent dependency signatures.
- **Tier 3 (Full Implementation Body):** Exports the requested symbol copied verbatim from the file (comments and formatting intact), plus top-level imports and, for a method, the enclosing class header. Sibling methods are listed by signature and line range instead of being shown.
- **Edit-ready headers (all tiers):** Every slice starts with lines like `# service.py lines 40-72: PaymentService.validate`, so the agent can open or edit the exact lines. If the token ceiling cuts a Tier 3 slice, it is cut at a statement boundary and a note such as `# [ACC-MCP] lines 120-310 omitted to fit the 2000-token ceiling; request symbol_name="Router.dispatch" to see them` says what is missing. A function that is cut part-way is flagged as incomplete.

---

## 4. Mapping to the 5 Course Building Blocks

### Block 2: Your Own MCP Server (`src/acc_mcp/server.py`)
- **Implementation:** Built using the standard Python `mcp` SDK with forward-compatibility across MCP 1.x and 2.x (`MCPServer` / `FastMCP`) via stdio transport.
- **Exposed Tools:**
  1. `get_adaptive_context(file_path: str, task_intent: str, symbol_name: str | None = None)`: Executes the intent classifier and decision model, retrieves the optimal AST slice, enforces security guardrails, and records audit tokens.
  2. `get_token_ledger()`: Returns the cumulative session token audit log and savings ledger in JSON format.
- **Evidence:** Tested with 8 integration tests in [`tests/test_server.py`](file:///Users/nefelim584/PycharmProjects/OOM-Guard/tests/test_server.py) verifying protocol discovery, tool invocation, error handling, and state persistence across server restarts.

### Block 3: A Rule Enforced in Code (`src/acc_mcp/guard.py`)
- **Implementation:** A deterministic safety guardrail implemented in Python before context generation or write operations.
- **Enforced Rules:**
  1. *Protected Path Rule:* Immediate rejection via `SecurityViolationError` if a query targets sensitive files:
     - Environment configs: `.env`, `.env.local`, `.env.production`
     - Version control metadata: `.git/`, `.git/config`, `.git/HEAD`
     - Secrets: `credentials.json`, `secrets.json`
     - Database migrations: `alembic/migrations/`
     - System paths: `/etc/passwd`, `/proc/`
     - Path traversal attempts: `../../.env`, `foo/../../.git/config`
  2. *Hard Context Ceiling ($C_{\max} = 2{,}000$ tokens):* Under no circumstances can any generated context slice exceed 2,000 tokens. If context exceeds $C_{\max}$, it is truncated **deterministically by AST node slicing in code, not by LLM prompting.** Slices cleanly at statement and function boundaries, appending `...` while guaranteeing valid Python syntax.
- **Evidence:** Tested with 39 automated tests in [`tests/test_guard.py`](file:///Users/nefelim584/PycharmProjects/OOM-Guard/tests/test_guard.py).

### Block 4: A Decision Model (`src/acc_mcp/decision.py` & `evaluate_decision.py`)
- **Feature Vector:** $(x_1, x_2, x_3, x_4)$ where:
  - $x_1 \in \{0, 1, 2\}$: Task Intent Depth ($0 = \text{inspect/call}$, $1 = \text{refactor/patch interface}$, $2 = \text{full algorithmic rewrite}$).
  - $x_2 \in \mathbb{N}$: Cyclomatic Complexity ($CC$) of target symbol.
  - $x_3 \in \mathbb{N}$: In-degree ($Deg_{in}$), number of callers dependent on target.
  - $x_4 \in \{0, 1\}$: Presence of explicit type hints and docstrings.
- **Logistic Scoring Function:**
  $$z = w_0 + w_1 x_1 + w_2 x_2 + w_3 x_3 - w_4 x_4$$
  $$P(\text{Full}) = \frac{1}{1 + e^{-z}}$$
  *Calibrated weights:* $w_0 = -2.0, w_1 = 2.0, w_2 = 0.2, w_3 = 0.3, w_4 = 1.0$.
- **Decision Policy Thresholds:** $\theta_{\text{low}} = 0.30, \theta_{\text{high}} = 0.65$.
- **Formal Evaluation Across 20 Distinct Tasks:** Evaluated against [`data/decision_dataset.json`](file:///Users/nefelim584/PycharmProjects/OOM-Guard/data/decision_dataset.json) (10 interface-only tasks, 10 deep logic rewrite tasks):
  - **Accuracy:** **100.00%** (20/20 tasks correct)
  - **Miss Rate (Type II error):** **0.00%** ($FN = 0$) — zero scenarios where the model failed to provide full context when required for an algorithmic rewrite.
  - **False Alarm Rate (Type I error):** **0.00%** ($FP = 0$) — zero token bloat on interface queries.
  - **Confusion Matrix:**
    $$\begin{pmatrix} TN = 10 & FP = 0 \\ FN = 0 & TP = 10 \end{pmatrix}$$

### Block 5: Memory or Context Budget Management (`src/acc_mcp/budget.py`)
- **Implementation:** An active context budget manager persisting state across agent runs.
- **Mechanisms:**
  - Tracks per-query token consumption and relative token savings:
    $$\text{Savings (\%)} = \frac{\text{Tokens}_{\text{Full File}} - \text{Tokens}_{\text{Served Tier}}}{\text{Tokens}_{\text{Full File}}} \times 100\%$$
  - Persists multi-turn ledger and session summaries to `context_budget.json`.
  - **Cold Restart Test:** Demonstrates that upon process restart, the server retains the repository's symbol map (names, complexities, callers, callees, raised exceptions) and token statistics without re-parsing files.
- **Evidence:** Tested with unit and restart recovery tests in [`tests/test_budget.py`](file:///Users/nefelim584/PycharmProjects/OOM-Guard/tests/test_budget.py).

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

### 3. Running the Decision Model Ground-Truth Benchmark (Block 4)
```bash
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
│       ├── evaluate_decision.py  # Block 4: Ground-truth benchmark evaluation
│       ├── budget.py             # Block 5: Token ledger & restart recovery cache
│       └── server.py             # Block 2: Stdio MCP Server (get_adaptive_context)
├── data/
│   └── decision_dataset.json     # 20 ground-truth coding benchmark tasks
├── tests/
│   ├── test_parser_tier1.py      # Tier 1 AST interface tests
│   ├── test_parser_tier2.py      # Tier 2 call-graph & complexity tests
│   ├── test_guard.py             # Block 3 security boundary & ceiling tests
│   ├── test_decision.py          # Block 4 scoring model & tier policy tests
│   ├── test_decision_eval.py     # Block 4 benchmark accuracy & zero miss tests
│   ├── test_budget.py            # Block 5 multi-turn ledger & restart tests
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
