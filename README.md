# Adaptive Context Compressor MCP (ACC-MCP / OOM-Guard)

[![Python: 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://python.org)
[![Tests: 268](https://img.shields.io/badge/Tests-268-brightgreen.svg)](tests)
[![MCP SDK: 1.x / 2.x](https://img.shields.io/badge/MCP%20SDK-1.x%20%2F%202.x-purple.svg)](https://modelcontextprotocol.io)
[![Target Model: Qwen3.5-4B](https://img.shields.io/badge/Target%20Model-Qwen3.5--4B-orange.svg)](https://huggingface.co/Qwen)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> **Course:** Coding for AI & Data Science — Final Project (2026–2027)  
> **Target Model:** Qwen3.5-4B (GGUF, served locally by `llama-server`; the exact file and quantization of the benchmark runs are recorded by `pi_bench.py freeze`)  
> **Course Building Blocks:** **Block 2**, **Block 3**, **Block 4**, **Block 5**, **Block 7**, **Block 8** (the Pi benchmark in `eval/` is exploratory and not a claimed block)  
> **Requirements Addressed:** **R1** (It Runs), **R2** (Automated Tests), **R3** (AI-Use Log), **R4** (Defence Readiness)  

---

## 1. Executive Summary & Problem Formulation

Small local models such as **Qwen3.5-4B** run with a small context window on a laptop (this project uses 30,208 tokens, the per-slot window `llama-server` reports on the reference machine). Inside a coding agent, the model does not usually read whole files into that window. It **navigates**: it searches with `grep`/`find`, then reads the file in line windows (`read` with `offset`/`limit`), and some of those windows land in the wrong place. Every search and every window is another tool call whose output stays in the conversation.

This is what the pilot session showed (plain Pi, a bug in more-itertools' `difference`, inside a 5,644-line file of about 45K tokens; the session is not part of the results). The agent never read the whole file. It found the function with `find | xargs grep` and `grep -n`, then read it in 6 windows of 10-100 lines, 2 of which missed the function. Code reading was 10 of its 65 tool calls; 48 were its own Python experiments.

**Adaptive Context Compressor (ACC-MCP)** is a Model Context Protocol (MCP) server that gives the agent **symbol-level navigation** instead. `find_symbol` says where a class, function or method is defined, and `get_adaptive_context` returns exactly that symbol at the depth the task needs: signatures (Tier 1), signatures plus callers and callees (Tier 2), or the verbatim body with its real line numbers (Tier 3). A **Decision Model** picks the tier from the task intent. Rules written in code enforce a token ceiling, a per-session token budget, and which files may be read at all.

Whether this makes a small model **cheaper at finding and reading code without making it worse at fixing bugs** is the question of the exploratory Pi benchmark in [`eval/`](eval/PROTOCOL.md). That benchmark compares ACC-MCP with the agent's own `grep` + windowed `read` strategy and counts whole-file reads instead of assuming them. It has only trace runs so far, so the project claims no result from it (see "Exploratory: Pi Benchmark" below).

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

## 4. Mapping to the Course Building Blocks

### Block 2: Your Own MCP Server (`src/acc_mcp/server.py`)
- **Implementation:** Built on the Python `mcp` SDK over stdio. It imports `MCPServer` (SDK 2.x) and falls back to `FastMCP` (SDK 1.x); `uv.lock` pins `mcp` 2.3.0.
- **Exposed Tools:**
  1. `get_adaptive_context(file_path, task_intent, symbol_name=None, min_tier=None)`: checks the path, runs the decision model, and returns the slice. `min_tier` (1–3) lets the agent ask for more context when a slice was not enough; it can raise the model's tier but never lower it. Each request is recorded in the ledger.
  2. `find_symbol(name)`: finds where a class, function, or method is defined anywhere in the workspace and returns file path (relative to the workspace root), qualified name, kind, and line range as JSON (at most 50 matches).
  3. `get_token_ledger()`: returns served tokens per tier and per tool, `escalation_rate`, the current session budget, the whole-file comparison as an upper bound, and the recent request history (with the line ranges each slice showed) as JSON.
  4. `reset_session()`: starts a fresh session token budget; all-time totals and history are kept.
- **Evidence:** In-process tool tests in [`tests/test_server.py`](tests/test_server.py) (discovery, each tool, errors, `min_tier`, budget, ledger persistence across restarts).
- **Real-agent trace:** Pi with Qwen3.5-4B called the server over stdio in run `bench1`, acc mode, task `mi_split_at`. The session file is `eval/work/runs/bench1/acc/mi_split_at/sessions/2026-11-04T07-57-30-322Z_01a1a590-2751-73f4-92b9-a0fc35a9bcf5.jsonl` (git-ignored; the measured summary is in that folder's `result.json`). The model's first two tool calls, verbatim:
  ```text
  mcp__acc_mcp__find_symbol {"name": "split_at"}
  → {"query": "split_at", "matches": [{"file_path": "more_itertools/more.py", "symbol": "split_at",
     "kind": "function", "lineno": 1569, "end_lineno": 1610}], "total_matches": 1, "truncated": false}

  mcp__acc_mcp__get_adaptive_context {"file_path": "more_itertools/more.py",
     "task_intent": "check signature and trace callers", "symbol_name": "split_at"}
  → # more.py lines 3-59: imports
    import math
    ...
    # more.py lines 1569-1610: split_at
    def split_at(iterable, pred, maxsplit=-1, keep_separator=False):
        """Yield lists of items from *iterable*, where each list is delimited by
    ...                                   (Tier 3, 658 tokens of a ~47K-token file)
  ```
  The model then edited `split_at` directly (2 navigation calls before the first edit). It later called `get_adaptive_context` once more with `min_tier=3`. The run **resolved** the bug, but only after a wrong first fix: 98 model calls and 22.6 minutes in total, with 20 edits and 74 bash commands. This is one run, a trace that the server works inside a real agent, not a measurement.

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

  **Read these with Block 8:** the 95% interval of the 70.0% holdout accuracy is 52.1–83.3%. The difference from the hand-picked weights is not significant (McNemar p = 1.0). Over 50 random splits the fitted model averages 63.7%, so seed 21 is a favourable draw.
- **What the fit shows:**
  - The tier is still decided almost entirely by $x_1$. With the fitted weights, on the dataset $x_1 = 0$ gives $P \approx 0.1$–$0.22$ (Tier 1), $x_1 = 2$ gives $P \ge 0.9$ (Tier 3), and $x_1 = 1$ gives $P \approx 0.55$–$0.72$, which sits right on $\theta_{\text{high}}$.
  - $x_2$ (complexity) and $x_4$ (annotations) get weights near zero. Setting either to 0 changes at most 3 train and 2 holdout tiers, because many $x_1 = 1$ tasks sit so close to $\theta_{\text{high}}$ that a tiny shift moves them.
  - $x_3$ (in-degree) gets a **negative** weight. For $x_1 = 1$ it decides between Tier 2 (the symbol has callers) and Tier 3 (it has none), and changes 10 train and 4 holdout tiers. This comes from the data, not from a design idea; with this dataset, more callers does not mean more context is needed.
  - Most errors come from the keyword classifier, not the weights. Intents with no keyword fall back to $x_1 = 1$. Caller questions phrased with inspection words ("Find every function that calls…") get $x_1 = 0$. Body edits phrased with Tier 2 verbs ("Add retry logic inside…") get $x_1 = 1$.
- **Limits of this measurement:** the labels and intents were written by the project author, not collected from users. Train and holdout tasks can share a code snippet, so $x_2$–$x_4$ values are not fully unseen. Only 2 intents are real (from the server ledger). The acc trace run added two more real ones ("check signature and trace callers", "fix debug"); they are not in the dataset.
- **Online signal (tooling only):** in real use a miss shows up in two ways. An **explicit escalation** is the agent calling again with a `min_tier` above the model's tier (`escalation_rate` in `get_token_ledger`). A **silent escalation** is the agent getting a Tier 1 or Tier 2 slice and then reading some of the same lines itself within the next 5 tool calls. `eval/pi_bench.py` measures both. The one acc trace run so far had 2 slices, both Tier 3, with no escalation of either kind. That is not a rate, so no online miss rate is claimed.
- **Which tiers bug fixes exercise:** bug fixes need the body, so the decision model mostly picks Tier 3 for them; it did so for both slices of the trace run. The exploratory benchmark therefore has 4 call-graph tasks (the report names a function whose helper has the bug), so that Tier 1 and Tier 2 would be used at all.
- **Uncertainty and a learned alternative:** see Block 8 for intervals on these holdout numbers and Block 7 for a Naive Bayes intent model compared with the keyword rule.

### Block 5: Memory or Context Budget Management (`src/acc_mcp/budget.py`, `src/acc_mcp/index.py`)
- **Session Budget:** All slices in a session share a token budget (`ACC_MCP_SESSION_BUDGET`, default 6,000 tokens, about 20% of the 30,208-token window; the benchmark sets exactly 20%, 6,042). Each slice is cut to $\min(C_{\max}, \text{tokens left})$. If the chosen tier does not fit, the server serves one tier lower and says so in a `# [ACC-MCP] Served Tier n instead of Tier m` note; a tier forced with `min_tier` that does not fit is refused with a `# [ACC-MCP] Not served` message. `reset_session` starts a new budget.
- **Ledger:** Every request is recorded with its served tier, model tier, `min_tier`, served tokens, and the line ranges it showed (file, symbol, first and last line; for a slice cut by the ceiling, only the lines really shown). The summary reports what the server really knows: served tokens per tier and per tool (`get_adaptive_context`, `find_symbol`). The comparison with the whole file is kept only as a labelled **upper bound** (`whole_file_tokens`, `upper_bound_savings_pct`):
  $$\text{Upper-bound saving (\%)} = \frac{\text{Tokens}_{\text{Whole File}} - \text{Tokens}_{\text{Served}}}{\text{Tokens}_{\text{Whole File}}} \times 100\%$$
  It is an upper bound because it assumes the agent would otherwise read the whole file, which it did not do in the pilot session. The exploratory benchmark compares with the agent's own `grep` + windowed `read` strategy instead. Ledgers written before this change (`baseline_tokens`, `savings_percentage`) still load. The ledger is saved once per request to the file named by `ACC_MCP_LEDGER`, or to `context_budget.json` in the server's working directory. Totals cover all requests; the history keeps only the last 200 records.
- **Workspace Symbol Index (`index.py`):** Walks every servable `.py` file under the workspace root (skipping `.venv`, `venv`, `.git`, `__pycache__`, `node_modules`) and stores each file's symbol map (names, line ranges, complexities, callers, callees, raised exceptions) in the ledger. Each file's SHA-256 is compared with the cached `file_hash`: unchanged files are reused, changed files are re-parsed, deleted files are dropped.
- **Cold Restart Test:** After a restart, the index is rebuilt from the ledger and unchanged files are not parsed again (the tests count parser calls). The file served by `get_adaptive_context` is still parsed on each request, because slices are cut from its AST.
- **What a restart remembers, and what it costs (from the restart tests):** in the 3-file test workspace, a restarted server re-indexes with **0 parses (3 files reused)**, and after one file is edited, with **1 parse (2 reused)**. All-time totals (requests, served tokens per tier and per tool, escalations, tier counts) and the last 200 history records are restored exactly. The session budget starts full again (0 of 6,000 tokens used), because a new server process means a new agent context. Remembering costs the agent **0 tokens**: the ledger and the symbol cache live on disk on the server side and never enter the conversation. Only slices the agent asks for are counted.
- **Evidence:** [`tests/test_budget.py`](tests/test_budget.py), [`tests/test_index.py`](tests/test_index.py), and the budget tests in [`tests/test_server.py`](tests/test_server.py).

### Block 7: A Learned Model Compared with Baselines (`src/acc_mcp/intent_model.py`)
- **Question:** Block 4 found that most decision errors come from the keyword rule that turns `task_intent` into $x_1$, not from the weights. Can a model **learned from the intent's words** do better?
- **Model:** multinomial Naive Bayes over the lowercased words of `task_intent` (`snake_case` names split into words), Laplace smoothing ($\alpha = 1$), log-probabilities, no new dependencies. Words never seen in training are ignored, and ties go to the higher tier. It is a separate artifact from the Block 4 logistic model, evaluated offline only and **not used by the server**.
- **Setup:** the same split as Block 4 (70 train / 30 holdout tasks, stratified, seed 21). Baselines on the same 30 tasks: always the most common train tier (Tier 3), and the current keyword rule read directly as a tier (depth 0/1/2 → Tier 1/2/3). Run `python3 src/acc_mcp/intent_model.py`.
- **Holdout results (30 tasks):**

  | Model | Accuracy | Miss rate | False alarm rate |
  | :--- | :---: | :---: | :---: |
  | Naive Bayes (words of `task_intent`) | 46.7% | 47.6% (10) | 33.3% (6) |
  | Keyword rule (depth + 1) | **70.0%** | 38.1% (8) | **5.6%** (1) |
  | Most common train tier (Tier 3) | 40.0% | 0.0% (0) | 100.0% (18) |

  Naive Bayes holdout confusion matrix (rows = actual tier, columns = predicted 1/2/3): Tier 1 `5 3 1`, Tier 2 `4 3 2`, Tier 3 `3 3 6`. The keyword rule's: Tier 1 `8 1 0`, Tier 2 `1 8 0`, Tier 3 `1 6 5`.
- **It overfits:** 98.6% on its own 70 train tasks against 46.7% on the holdout. With 70 short intents, most words occur in only one or two of them.
- **What it learned:** the most likely words of every tier are shared ones ("the", "does", "of", "and"). The most distinctive words are: Tier 1 `arguments`, `take`, `type`, `inspect`; Tier 2 `update`, `callers`, `rename`, `calls`, `sites`; Tier 3 `loop`, `fix`, `logic`, `implement`. These are close to the keyword lists, plus topic words like `loop` or `config` that only happen to occur in one tier.
- **Result:** Naive Bayes does **not** beat the keyword rule. On this split it is 23.3 points lower, and averaged over 50 splits it is 59.4% against 69.2% (Block 8). Of its 16 holdout errors, the keyword rule got 9 right. The keyword rule stays in the server; a learned intent model would need many more, and real, intents.
- **Evidence:** [`tests/test_intent_model.py`](tests/test_intent_model.py): smoothing checked by hand on a 6-intent set, fitting only on train intents, all models scored on the same holdout ids, metrics consistent with the confusion matrix, reproducible report.

### Block 8: Uncertainty on the Holdout (`src/acc_mcp/uncertainty.py`)
- **Why:** Blocks 4 and 7 quote one split with 30 holdout tasks, so a single accuracy cannot stand on its own.
- **Methods (no new dependencies):** Wilson 95% intervals; percentile bootstrap 95% intervals (2,000 resamples of the 30 holdout tasks, seed 8); paired comparisons on the same tasks, using the exact McNemar test (binomial on the discordant tasks) and a paired bootstrap interval for the accuracy difference; and split sensitivity, where the stratified split and both fits are repeated for 50 seeds. Run `python3 src/acc_mcp/uncertainty.py` (about a minute, for the 50 weight fits).
- **Holdout intervals (seed-21 split, Wilson 95%):**

  | Model | Accuracy | Miss rate | False alarm rate |
  | :--- | :---: | :---: | :---: |
  | Logistic, fitted weights (Block 4) | 70.0% (52.1–83.3%) | 14.3% (5.0–34.6%) | 33.3% (16.3–56.3%) |
  | Logistic, hand-picked weights | 66.7% (48.8–80.8%) | 28.6% (13.8–50.0%) | 22.2% (9.0–45.2%) |
  | Naive Bayes (Block 7) | 46.7% (30.2–63.9%) | 47.6% (28.3–67.6%) | 33.3% (16.3–56.3%) |
  | Keyword rule | 70.0% (52.1–83.3%) | 38.1% (20.8–59.1%) | 5.6% (1.0–25.8%) |
  | Most common train tier | 40.0% (24.6–57.7%) | 0.0% (0.0–15.5%) | 100.0% (82.4–100.0%) |

  The bootstrap intervals are similar (fitted weights' accuracy 53.3–86.7%). Every accuracy interval is about 30 points wide.
- **Paired comparisons (same 30 tasks):**

  | Comparison | A − B | Paired bootstrap 95% | Only A right / only B right | Exact McNemar p |
  | :--- | :---: | :---: | :---: | :---: |
  | Fitted vs hand-picked weights (Block 4) | +3.3 pp | −20.0 to +26.7 pp | 7 / 6 | 1.000 |
  | Naive Bayes vs keyword rule (Block 7) | −23.3 pp | −43.3 to −3.3 pp | 2 / 9 | 0.065 |

- **Split sensitivity (holdout accuracy over 50 stratified splits):**

  | Model | Mean | SD | Min | Max | Seed 21 |
  | :--- | :---: | :---: | :---: | :---: | :---: |
  | Logistic decision model (refitted per split) | 63.7% | 5.9 pp | 53.3% | 76.7% | 70.0% |
  | Naive Bayes (refitted per split) | 59.4% | 9.4 pp | 30.0% | 73.3% | 46.7% |
  | Keyword rule (no fitting) | 69.2% | 6.1 pp | 56.7% | 80.0% | 70.0% |

- **What this shows:**
  - **Fitting the Block 4 weights did not measurably improve accuracy.** The two weight sets disagree on 13 holdout tasks, split 7 to 6 (p = 1.0). The fit traded misses (6 → 3) for false alarms (4 → 6).
  - **The seed-21 split is a favourable draw for the logistic model:** 70.0% there, against a mean of 63.7% over 50 splits. Over those splits, the keyword rule read directly as a tier (69.2%) is on average more accurate than the logistic model built on top of it (63.7%).
  - **Naive Bayes vs keyword rule:** the paired bootstrap interval excludes 0, but the exact McNemar test does not reach 0.05 (p = 0.065, 2 vs 9 discordant tasks). This is weak evidence that the keyword rule is better on this split. The 50-split means point the same way (59.4% vs 69.2%), and seed 21 was one of Naive Bayes' worse splits.
- **What the data cannot conclude:** there are 30 holdout tasks, the labels were written by the project author, and train and holdout tasks share code snippets. These numbers describe the models on this dataset only. A difference whose paired interval includes 0, or whose McNemar p is above 0.05, is not evidence that one model is better.
- **Evidence:** [`tests/test_uncertainty.py`](tests/test_uncertainty.py):
  - Wilson intervals against known values;
  - a fixed bootstrap seed gives the same interval, and the interval contains the estimate;
  - McNemar gives p = 1 for identical predictions and matches hand-counted cases;
  - the seed sweep never fits on its own holdout;
  - the seed-21 row reproduces the Block 4 and Block 7 numbers.

### Exploratory: Pi Benchmark (not a claimed block)
- **What `eval/` measures:** a bug-injection benchmark on pinned releases of more-itertools and click, with 10 tasks: 6 direct and 4 call-graph tasks. Each task is done by hand in interactive Pi, plain vs ACC-MCP, and graded by the libraries' own tests. Measured from Pi's session file: navigation calls before the first edit of the target function, code-reading tokens, wasted and large reads, and peak context; with ACC-MCP also tiers and explicit and silent escalations. The full method and hypotheses are in [`eval/PROTOCOL.md`](eval/PROTOCOL.md), and the helper is [`eval/pi_bench.py`](eval/pi_bench.py) (`setup`, `validate`, `doctor`, `freeze`, `prepare`, `collect`, `list`, `report`).
- **What exists:** the pilot (plain, `mi_difference`; it showed the agent greps and reads line windows instead of whole files) and trace runs of `mi_split_at` in both modes. Both trace runs resolved the bug. The acc run is the real-agent trace quoted under Block 2.
- **Why it is not claimed:** comparing two modes with an unseeded model needs at least 10 tasks × 3 runs per mode (about 60 hand runs of up to 30 minutes each). One run per task cannot separate ACC-MCP from run-to-run noise: the single `mi_split_at` pair differs by a factor of 9 in model calls (11 plain, 98 acc), mostly because of one wrong first fix. So no navigation, token or success result is claimed from it.
- An earlier simulated benchmark was removed: it marked every gated run as a success without checking the model's answer, and one generated file produced most of its reported saving.

---

## 5. Course Verification Checklist (R1–R4)

| Requirement | Description | Status | Evidence |
| :--- | :--- | :---: | :--- |
| **R1 (It Runs)** | Runs inside a real agent (Pi) against a local `llama-server` with Qwen3.5-4B. | **VERIFIED** | Run `bench1`, acc, `mi_split_at` (session `eval/work/runs/bench1/acc/mi_split_at/sessions/2026-11-04T07-57-30-322Z_01a1a590-….jsonl`): Pi called `mcp__acc_mcp__find_symbol {"name": "split_at"}` and got `more_itertools/more.py` lines 1569-1610. It then called `get_adaptive_context` and got the verbatim Tier 3 slice `# more.py lines 1569-1610: split_at`, and the run resolved the bug. Full quote under Block 2. |
| **R2 (Automated Tests)** | `pytest` suite for parser, call graph, guard, decision model, budget, index, server, the Block 7 intent model, the Block 8 statistics, and the benchmark's measurement code. | **VERIFIED** | 13 test files (`uv run pytest`); the passing count of the Phase 30 run goes here and in the badge (Step 30.5). |
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
llama-server -m /path/to/Qwen3.5-4B-Q4_K_M.gguf -c 30208 --parallel 1 --port 8080

# Or let llama.cpp fetch it from Hugging Face
llama-server -hf unsloth/Qwen3.5-4B-GGUF:Q4_K_M -c 30208 --parallel 1 --port 8080
```
This gives an OpenAI-compatible endpoint at `http://127.0.0.1:8080/v1`. Check the window with `curl -s http://127.0.0.1:8080/props`: `default_generation_settings.n_ctx` should be 30,208; the benchmark reads it from there.

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
        "ACC_MCP_SESSION_BUDGET": "6042"
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
│       ├── intent_model.py       # Block 7: Naive Bayes intent classifier vs baselines (offline)
│       ├── uncertainty.py        # Block 8: Wilson/bootstrap intervals, McNemar, split sensitivity
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
│   ├── test_intent_model.py      # Block 7 Naive Bayes, baselines, same-holdout checks
│   ├── test_uncertainty.py       # Block 8 intervals, McNemar, paired bootstrap, seed sweep
│   ├── test_budget.py            # Block 5 ledger, session budget & restart tests
│   ├── test_index.py             # Block 5 symbol index, cache reuse & find_symbol tests
│   ├── test_server.py            # Block 2 MCP tool tests
│   ├── test_pi_bench.py          # Exploratory benchmark: navigation metrics, freeze, report, protocol
│   └── fixtures/
│       └── pi_session_mi_difference.jsonl  # Trimmed pilot Pi session used by test_pi_bench.py
├── eval/                         # Exploratory Pi benchmark (not a claimed block)
│   ├── PROTOCOL.md               # Pi benchmark method and hypotheses H1-H3
│   ├── tasks.json                # 10 bug-injection tasks (6 direct, 4 call-graph)
│   ├── pi_bench.py               # Helper: freeze, prepare a run for interactive Pi, collect and report
│   └── work/                     # (git-ignored) cloned repos, test venv, run outputs
├── ai_log/
│   ├── AI_USE_LOG.md             # R3: Summary of AI work sessions, prompts & fixes
│   └── *.md                      # Exported session transcripts
├── AGENTS.md                     # Instructions for AI coding agents working on this repo
├── Description.md                # Project proposal / formal description
├── LICENSE                       # MIT License
├── main.py                       # Placeholder entry point (not used by the server)
├── pyproject.toml                # Project metadata & pytest configuration
├── todo.md                       # 30-phase implementation plan
└── README.md                     # This document
```

---

## 9. License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
