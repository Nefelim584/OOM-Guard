# Adaptive Context Compressor MCP (ACC-MCP / OOM-Guard)

[![Python: 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://python.org)
[![Tests: 219](https://img.shields.io/badge/Tests-219-brightgreen.svg)](tests)
[![MCP SDK: 1.x / 2.x](https://img.shields.io/badge/MCP%20SDK-1.x%20%2F%202.x-purple.svg)](https://modelcontextprotocol.io)
[![Target Model: Qwen3.5-4B](https://img.shields.io/badge/Target%20Model-Qwen3.5--4B-orange.svg)](https://huggingface.co/Qwen)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> **Course:** Coding for AI & Data Science — Final Project (2026–2027)  
> **Target Model:** Qwen3.5-4B (`Q4_K_M` GGUF, served locally by `llama-server`)  
> **Course Building Blocks:** **Block 2**, **Block 3**, **Block 4**, **Block 5**, **Block 9**  
> **Requirements Addressed:** **R1** (It Runs), **R2** (Automated Tests), **R3** (AI-Use Log), **R4** (Defence Readiness)  

---

## 1. Executive Summary & Problem Formulation

Small local models such as **Qwen3.5-4B** run with a small context window on a laptop (this project assumes 8,192 tokens). A coding agent that reads whole source files into that window fills it quickly:
1. **Context bloat:** a full window leaves less room for the task, and an overfull request fails outright.
2. **Token waste:** many questions (checking a signature, a type hint, or who calls a function) need only the interface, not the function bodies.

**Adaptive Context Compressor (ACC-MCP)** is a Model Context Protocol (MCP) server that sits between the agent and the code. Instead of returning raw files, a **Decision Model** picks how much of a symbol to show (signatures, call graph, or full body) and serves that slice of the file. Rules written in code enforce a token ceiling, a per-session token budget, and which files may be read at all.

---

## 2. System Architecture & Information Flow

```
┌────────────────────────────────────────────────────────┐
│         Agent Harness (e.g., Pi Coding Agent)          │
│        with Qwen3.5-4B (local, via llama-server)       │
└───────────────────────────┬────────────────────────────┘
                            │ MCP tool calls (stdio):
                            │ get_adaptive_context(file_path, task_intent,
                            │                      symbol_name?, min_tier?)
                            │ find_symbol · get_token_ledger · reset_session
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     ACC-MCP Server (src/acc_mcp/server.py)             │
│                                                                        │
│  1. Path Guard (Block 3 - guard.py)                                    │
│     Only .py files inside the workspace root; secrets/.git rejected.   │
│                                                                        │
│  2. Workspace Index (Block 5 - index.py)                               │
│     Hash-checked symbol cache for every .py file; cross-file callers.  │
│                                                                        │
│  3. AST Extractor (parser.py)                                          │
│     Signatures, call graph, complexity, verbatim bodies with lines.    │
│                                                                        │
│  4. Decision Model (Block 4 - decision.py)                             │
│     Intent + AST features -> P(Full Context Needed) -> Tier 1/2/3.     │
│     Fitted weights, hand-picked thresholds. min_tier can raise it.     │
│                                                                        │
│  5. Token Ceiling + Session Budget (Block 3 guard.py, Block 5 budget.py)│
│     Slice <= min(C_max = 2,000, tokens left in session budget).        │
│     Steps down a tier if needed; ledger saved to context_budget.json.  │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │ Slice with "# file lines a-b: symbol" headers
                               ▼
┌────────────────────────────────────────────────────────┐
│         Qwen3.5-4B works from the bounded slice        │
└────────────────────────────────────────────────────────┘
```

---

## 3. The 3-Tier Adaptive Compression Model

When context is requested, ACC-MCP renders the target symbol (or the whole file) at one of three tiers:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 1: Signatures, Types & Docstrings                                 │
│ All function and method bodies replaced by `...`.                      │
└────────────────────────────────────────────────────────────────────────┘
                               ▲
                               │ P(Full) < 0.30
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 2: Skeleton & Call-Graph Slice                                    │
│ Tier 1 + callers (incl. other files), callees, raised exceptions.      │
└────────────────────────────────────────────────────────────────────────┘
                               ▲
                               │ 0.30 <= P(Full) < 0.65
┌────────────────────────────────────────────────────────────────────────┐
│ Tier 3: Full Implementation Body                                       │
│ The target copied verbatim from the file, cut to the token ceiling.    │
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
- **Implementation:** Built on the Python `mcp` SDK over stdio. It imports `MCPServer` (SDK 2.x) and falls back to `FastMCP` (SDK 1.x); `uv.lock` pins `mcp` 2.3.0.
- **Exposed Tools:**
  1. `get_adaptive_context(file_path, task_intent, symbol_name=None, min_tier=None)`: checks the path, runs the decision model, and returns the slice. `min_tier` (1–3) lets the agent ask for more context when a slice was not enough; it can raise the model's tier but never lower it. Each request is recorded in the ledger.
  2. `find_symbol(name)`: finds where a class, function, or method is defined anywhere in the workspace and returns file path (relative to the workspace root), qualified name, kind, and line range as JSON (at most 50 matches).
  3. `get_token_ledger()`: returns all-time totals, `escalation_rate`, the current session budget, and the recent request history as JSON.
  4. `reset_session()`: starts a fresh session token budget; all-time totals and history are kept.
- **Evidence:** In-process tool tests in [`tests/test_server.py`](tests/test_server.py) (discovery, each tool, errors, `min_tier`, budget, ledger persistence across restarts). No test yet starts the server as a subprocess or drives it from a real agent; that is planned in Phase 14.

### Block 3: A Rule Enforced in Code (`src/acc_mcp/guard.py`)
- **Implementation:** Deterministic checks in Python that run before any code is read or served.
- **Enforced Rules:**
  1. *Protected Path Rule:* Only `.py` files inside the workspace root (`ACC_MCP_ROOT`, default: the server's working directory) are served. The check runs on both the path as sent and its real path after symlinks are followed, so `notes.py -> .env` is rejected. Anything else raises `SecurityViolationError`:
     - Paths outside the root: absolute paths elsewhere, `../` traversal, symlinks that leave the root
     - Non-Python files: `README.md`, `config.yaml`, `*.pyc`
     - Environment configs: `.env`, `.env.local`, `.env.production`
     - Version control metadata: anything under `.git/`
     - Secrets and keys: `credentials.json`, `secrets.json`, anything under `.ssh/` or `.aws/`, `*.pem`, `*.key`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `.npmrc`, `.pypirc`, `.netrc`
  2. *Hard Context Ceiling ($C_{\max} = 2{,}000$ tokens):* no slice is returned above the ceiling, as measured by the server's token counter (`tiktoken` `cl100k_base`, or a characters/3.8 estimate if `tiktoken` is unavailable; this is not Qwen's own tokenizer). Tier 3 is cut by the parser at statement boundaries with an "omitted lines" note. `GuardRail.enforce_ceiling` then checks every slice; if one is still too long, it keeps whole top-level statements that fit (re-rendered from the AST, so comments are lost on that path) and adds a `# [ACC-MCP] Context truncated` banner. No LLM is involved in cutting.
- **Evidence:** [`tests/test_guard.py`](tests/test_guard.py), plus the cut-slice tests in [`tests/test_parser_verbatim.py`](tests/test_parser_verbatim.py).

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

### Block 5: Memory or Context Budget Management (`src/acc_mcp/budget.py`, `src/acc_mcp/index.py`)
- **Session Budget:** All slices in a session share a token budget (`ACC_MCP_SESSION_BUDGET`, default 6,000 tokens). Each slice is cut to $\min(C_{\max}, \text{tokens left})$. If the chosen tier does not fit, the server serves one tier lower and says so in a `# [ACC-MCP] Served Tier n instead of Tier m` note; a tier forced with `min_tier` that does not fit is refused with a `# [ACC-MCP] Not served` message. `reset_session` starts a new budget.
- **Ledger:** Every request is recorded with its served tier, model tier, `min_tier`, and token counts. Savings per request are measured against reading the whole file:
  $$\text{Savings (\%)} = \frac{\text{Tokens}_{\text{Full File}} - \text{Tokens}_{\text{Served Tier}}}{\text{Tokens}_{\text{Full File}}} \times 100\%$$
  The ledger is saved once per request to the file named by `ACC_MCP_LEDGER`, or to `context_budget.json` in the server's working directory. Totals cover all requests; the history keeps only the last 200 records.
- **Workspace Symbol Index (`index.py`):** Walks every servable `.py` file under the workspace root (skipping `.venv`, `venv`, `.git`, `__pycache__`, `node_modules`) and stores each file's symbol map (names, line ranges, complexities, callers, callees, raised exceptions) in the ledger. Each file's SHA-256 is compared with the cached `file_hash`: unchanged files are reused, changed files are re-parsed, deleted files are dropped.
- **Cold Restart Test:** After a restart, the index is rebuilt from the ledger and unchanged files are not parsed again (the tests count parser calls). The file served by `get_adaptive_context` is still parsed on each request, because slices are cut from its AST.
- **Evidence:** [`tests/test_budget.py`](tests/test_budget.py), [`tests/test_index.py`](tests/test_index.py), and the budget tests in [`tests/test_server.py`](tests/test_server.py).

### Block 9: A Measured Comparison (Manual Evaluation with Pi)
- **Question:** does ACC-MCP change whether a small local model fixes a bug in real code, and how much context it uses, when the model works inside a real coding agent (Pi)? The full method is in [`eval/PROTOCOL.md`](eval/PROTOCOL.md).
- **Why our own benchmark:** SWE-bench's grader needs a Docker image of several GB per task; Exercism-style exercises are single small files where ACC-MCP has nothing to save; HumanEval/MBPP have no repository at all. The benchmark instead injects bugs into real libraries (the SWE-smith approach) and grades them with the libraries' own tests, without Docker.
- **Repositories:**

  | Repo | Pinned release | Files with injected bugs | Why |
  | :--- | :--- | :--- | :--- |
  | [more-itertools](https://github.com/more-itertools/more-itertools) | `v11.2.1` | `more_itertools/more.py` (5,644 lines, ~175 KB) | One file of ~45K tokens, larger than the model's ~30K window, so the agent must find the right function. |
  | [click](https://github.com/pallets/click) | `8.5.0` | `src/click/core.py` (3,799 lines), `types.py`, `parser.py`, `formatting.py` | Classes and methods spread over modules, so callers and callees matter. |

- **Tasks (5 per repo):** each changes one line or condition in one function that has its own tests: `split_at`, `numeric_range.index`, `difference`, `is_sorted`, `constrained_batches` (more-itertools); argument unpacking, `IntRange` minimum, eager-parameter order, `nargs=-1` envvar splitting, `--help` column layout (click). The agent gets only a user-style bug report, never the file or line, in a fresh repo whose only commit already contains the bug. Each change, its target tests and its report are listed in the protocol.
- **How a run is done:** by hand, in normal interactive Pi. For each task and mode, `prepare` builds the broken repo and a `start.sh` that opens Pi with an isolated configuration; the operator pastes the bug report, watches the model work under fixed operator rules (no hints; only "Proceed as you think best." / "Continue."), and quits when it is done or stuck. `collect` then reads Pi's own session file for the measurements and runs the repo's tests to check the fix.
- **Conditions:** every task is done twice with the same model, prompt, tools, compaction settings, operator rules and time limit. Only the acc run adds the ACC-MCP server (tools declared directly) and one sentence telling the model to prefer it for reading code.
- **Grading:** after restoring the original tests, a task is resolved when the target tests pass and the full suite has no failures beyond those of the clean repo.
- **Measured per run:** resolved or not, peak context of one model call, total prompt tokens, model calls, output tokens, tools used, compactions, wall time, and (with ACC-MCP) tiers served, `min_tier` escalations and budget step-downs.
- **Helper script:** [`eval/pi_bench.py`](eval/pi_bench.py) (`setup`, `validate`, `doctor`, `prepare`, `collect`, `list`, `report`). It never drives Pi itself.
- **Status: not run yet.** Results will go to `eval/results.md`; until then this README makes no claim about token savings or task success.
- An earlier simulated benchmark was removed: it marked every gated run as a success without checking the model's answer, and one generated file produced most of its reported saving.

---

## 5. Course Verification Checklist (R1–R4)

| Requirement | Description | Status | Evidence |
| :--- | :--- | :---: | :--- |
| **R1 (It Runs)** | Runs inside a real agent (Pi) against a local `llama-server` with Qwen3.5-4B. | **PENDING** | The stdio server starts with `python -m acc_mcp.server`; the Pi setup and a real run are Phase 14. |
| **R2 (Automated Tests)** | `pytest` suite for parser, call graph, guard, decision model, budget, index, and server. | **VERIFIED** | 219 tests in 10 test files (`uv run pytest`). |
| **R3 (AI-Use Log)** | Work session transcripts recorded in `ai_log/` with prompt refinements and code fixes. | **VERIFIED** | [`ai_log/AI_USE_LOG.md`](ai_log/AI_USE_LOG.md) and the session transcripts in [`ai_log/`](ai_log). |
| **R4 (Defence Readiness)** | Modular code with one job per file. | **VERIFIED** | `parser.py`, `guard.py`, `decision.py`, `evaluate_decision.py`, `budget.py`, `index.py`, `tokens.py`, `server.py`. |

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
uv run pytest

# Or directly via python3
python3 -m pytest
```

### 3. Fitting and Evaluating the Decision Model (Block 4)
```bash
# Fits the weights on the train split and prints train and holdout metrics separately
uv run python src/acc_mcp/evaluate_decision.py
```

---

## 7. Connecting ACC-MCP to an Agent (R1)

### 1. Serve Qwen3.5-4B with `llama-server` (`llama.cpp`)
```bash
# With a GGUF you already have
llama-server -m /path/to/Qwen3.5-4B-Q4_K_M.gguf -c 8192 --port 8080

# Or let llama.cpp fetch it from Hugging Face
llama-server -hf unsloth/Qwen3.5-4B-GGUF:Q4_K_M -c 8192 --port 8080
```
This gives an OpenAI-compatible endpoint at `http://127.0.0.1:8080/v1`.

### Pi coding agent
Pi (1.0.2+) has built-in MCP support. Register ACC-MCP for the project the agent works on:
```bash
cd /path/to/the/project
pi mcp add acc-mcp --exposure direct \
  --env ACC_MCP_ROOT="$PWD" --env ACC_MCP_LEDGER=/tmp/acc_ledger.json \
  -- /path/to/OOM-Guard/.venv/bin/python -m acc_mcp.server
pi mcp list    # should show acc-mcp connected with 4 tools
```
`direct` exposure declares the four tools to the model like built-in tools (`mcp__acc_mcp__get_adaptive_context`, ...). The benchmark runner writes an equivalent config into an isolated Pi config folder for each run, so no global setup is needed for `eval/`.

### 2. Register ACC-MCP as a stdio MCP server
Any MCP client (Claude Desktop, Cursor, or Pi through its MCP adapter extension) can start the server with a config like this:
```json
{
  "mcpServers": {
    "acc-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/OOM-Guard", "python", "-m", "acc_mcp.server"],
      "env": {
        "ACC_MCP_ROOT": "/path/to/the/project/the/agent/works/on",
        "ACC_MCP_SESSION_BUDGET": "6000"
      }
    }
  }
}
```
- `ACC_MCP_ROOT` is the only folder whose `.py` files are served; relative `file_path` values are resolved against it.
- The ledger is written to `ACC_MCP_LEDGER` if set, otherwise to `context_budget.json` in the server's working directory (here `/path/to/OOM-Guard`).

---

## 8. Repository Layout

```
OOM-Guard/
├── src/
│   └── acc_mcp/
│       ├── __init__.py           # Package exports
│       ├── parser.py             # AST parsing, complexity, call graph, Tier 1/2/3 slices
│       ├── tokens.py             # Pluggable token counter (llama-server/Ollama endpoint, tiktoken fallback)
│       ├── guard.py              # Block 3: Workspace/path rules & token ceiling
│       ├── decision.py           # Block 4: Intent classifier, logistic scoring, weight fitting
│       ├── evaluate_decision.py  # Block 4: Train/holdout split, weight fit, and report
│       ├── budget.py             # Block 5: Session budget, token ledger & symbol cache
│       ├── index.py              # Block 5: Workspace symbol index (hash-checked cache)
│       └── server.py             # Block 2: Stdio MCP server and its four tools
├── data/
│   └── decision_dataset.json     # 100 labeled task intents for the decision model
├── tests/
│   ├── conftest.py               # Shared fixtures
│   ├── test_parser_tier1.py      # Tier 1 AST interface tests
│   ├── test_parser_tier2.py      # Tier 2 call-graph & complexity tests
│   ├── test_parser_verbatim.py   # Verbatim Tier 3, line headers, and cut-slice notes
│   ├── test_callgraph.py         # Cross-file, import-aware call graph tests
│   ├── test_guard.py             # Block 3 path rules & ceiling tests
│   ├── test_decision.py          # Block 4 classifier, scoring, fitting & tier policy tests
│   ├── test_decision_eval.py     # Block 4 dataset, split, fit, and train/holdout metrics tests
│   ├── test_budget.py            # Block 5 ledger, session budget & restart tests
│   ├── test_index.py             # Block 5 symbol index, cache reuse & find_symbol tests
│   └── test_server.py            # Block 2 MCP tool tests
├── eval/
│   ├── PROTOCOL.md               # Block 9: Pi benchmark method
│   ├── tasks.json                # 10 bug-injection tasks (more-itertools, click)
│   ├── pi_bench.py               # Helper: prepare a run for interactive Pi, collect its measurements
│   └── work/                     # (git-ignored) cloned repos, test venv, run outputs
├── ai_log/
│   ├── AI_USE_LOG.md             # R3: Summary of AI work sessions, prompts & fixes
│   └── *.md                      # Exported session transcripts
├── AGENTS.md                     # Instructions for AI coding agents working on this repo
├── Description.md                # Project proposal / formal description
├── LICENSE                       # MIT License
├── main.py                       # Placeholder entry point (not used by the server)
├── pyproject.toml                # Project metadata & pytest configuration
├── todo.md                       # 22-phase implementation plan
└── README.md                     # This document
```

---

## 9. License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
