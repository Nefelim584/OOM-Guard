# Adaptive Context Compressor MCP (ACC-MCP)
**Course:** Coding for AI & Data Science — Final Project (2026–2027)  
**Target Model:** Qwen3.5-4B (GGUF via `llama-server`). The recorded trace used `Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q6_K.gguf` from Hugging Face (`HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive`), a community fine-tune of Qwen3.5-4B; download and setup are in `README.md` section 6  
**Selected Building Blocks:** Block 2, Block 3, Block 4, Block 5, Block 7, Block 8 (the Pi benchmark in `eval/` is exploratory and not a claimed block)  

---

## 1. Executive Summary & Motivation

Small local models such as **Qwen3.5-4B** run with a small context window on a laptop (this project uses 30,208 tokens, the per-slot window `llama-server` reports on the reference machine). Inside a coding agent, the model finds code by **navigating**: it searches with `grep`/`find` and reads files in line windows, some of which miss the code it needs. Each search and each window is a tool call whose output stays in the conversation. In the pilot session (excluded from the results) the agent never read the ~47K-token file whole. It found the function with `grep` and read it in 6 windows of 10-100 lines, 2 of which missed the function.

**Adaptive Context Compressor (ACC-MCP)** is a standalone Model Context Protocol (MCP) server that gives the agent **symbol-level navigation**. `find_symbol` locates a definition, and `get_adaptive_context` serves exactly that symbol at the depth the task needs. An embedded **Decision Model** picks that depth (signatures, call graph, or verbatim body), and a deterministic code layer enforces token ceilings, a session budget and file security boundaries. An exploratory Pi benchmark (`eval/`) is built to measure whether this makes finding and reading code cheaper than the agent's own `grep` + windowed `read` strategy, without lowering the success rate. It has only trace runs so far, so no result is claimed from it (section 5).

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

### 4.4 Measuring the Saving
The ledger records what ACC-MCP served (tokens per tier and per tool, and the line ranges of every slice). It also compares each slice with the whole file it came from, but only as an **upper bound**:

$$\text{Upper-bound saving (\%)} = \frac{\text{Tokens}_{\text{Whole File}} - \text{Tokens}_{\text{Served}}}{\text{Tokens}_{\text{Whole File}}} \times 100\%$$

This assumes the agent would otherwise read the whole file, which it did not do in the pilot session. The exploratory benchmark (H1 in `eval/PROTOCOL.md`) compares with what the agent really does, measured from Pi's session file in both modes with the same counter (characters / 3.8):

$$\text{Code-reading tokens} = \sum_{c \,\in\, \text{code\_read}} \text{Tokens}_{\text{out}}(c) + \sum_{c \,\in\, \text{ACC-MCP}} \text{Tokens}_{\text{out}}(c)$$

together with the number of navigation calls (code reading plus `find_symbol` / `get_adaptive_context`) before the first edit of the target function.

---

## 5. Mapping to the Course Building Blocks

### Block 2: Your Own MCP Server
* **Implementation:** Built using the standard Python `mcp` SDK via stdio transport.
* **Exposed Tools:**
  * `get_adaptive_context(file_path: str, task_intent: str, symbol_name: str | None, min_tier: int | None)`: Runs the decision model and returns the slice; `min_tier` lets the agent ask for a higher tier when a slice was not enough.
  * `find_symbol(name: str)`: Finds where a class, function, or method is defined in the workspace.
  * `get_token_ledger()`: Returns served tokens per tier and per tool, `escalation_rate`, the session budget, the whole-file upper bound, and recent history with line ranges.
  * `reset_session()`: Starts a fresh session token budget for a new task.
* **Evidence:** Tool tests in `tests/test_server.py`, and a real-agent trace: in run `bench1` (acc, task `mi_split_at`), Pi with Qwen3.5-4B called `find_symbol("split_at")` and got `more_itertools/more.py` lines 1569-1610. It then called `get_adaptive_context` and got the verbatim Tier 3 slice (658 tokens of a ~47K-token file), and the run resolved the bug. The quote is in `README.md` Block 2.

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
* **Uncertainty:** the holdout accuracy of 70.0% has a 95% interval of 52.1–83.3%. It is not significantly better than the hand-picked weights (McNemar p = 1.0), and over 50 random splits it averages 63.7% (Block 8).
* **Online signal (tooling only):** a miss in real use shows up as an explicit escalation (the agent raises `min_tier`) or a silent one (after a Tier 1/2 slice the agent reads the same lines itself within 5 tool calls); `eval/pi_bench.py` measures both. The one acc trace run had 2 slices, both Tier 3, and no escalation. That is not a rate, so none is claimed.

### Block 5: Memory or Context Budget Management
* **Implementation:** A per-session token budget plus a ledger that persists across server restarts.
* **Mechanisms:**
  * Caps the tokens served per session (20% of the window in the benchmark) and steps down a tier when the chosen one does not fit.
  * Persists the request history (with the line ranges each slice showed), totals per tier and per tool, and the workspace symbol index to a local `context_budget.json` ledger. The whole-file comparison is kept only as an upper bound (section 4.4).
  * Restart test: after a restart the symbol index is rebuilt from the ledger and unchanged files are not parsed again (the file being served is still parsed for each request).
  * What a restart remembers and costs, from the tests: in the 3-file test workspace a restart re-indexes with 0 parses (3 files reused), or 1 parse after one file is edited. All-time totals and the last 200 history records come back exactly, and the session budget starts full. The agent pays 0 tokens for this memory, because the ledger and symbol cache stay on the server's disk.

### Block 7: A Learned Model Compared with Baselines
* **Implementation:** a multinomial Naive Bayes classifier (`src/acc_mcp/intent_model.py`) that predicts the tier from the words of `task_intent` (Laplace smoothing, log-probabilities, no new dependencies). It targets the main Block 4 error source, the keyword rule, and is evaluated offline only; the server does not use it.
* **Evaluation:** trained on the Block 4 train split (70 tasks, seed 21) and compared on the same 30 holdout tasks with two baselines: always the most common train tier (Tier 3), and the keyword rule read directly as a tier.
* **Result:** holdout accuracy is 46.7% for Naive Bayes, 70.0% for the keyword rule and 40.0% for the majority baseline. Naive Bayes overfits (98.6% on its own train tasks) and does **not** beat the keyword rule. The server is unchanged: it keeps using the Block 4 logistic model, whose $x_1$ comes from the keyword rule. Its most distinctive words (`callers`, `rename`, `fix`, `inspect`, ...) are close to the keyword lists.

### Block 8: Uncertainty on the Holdout
* **Implementation:** `src/acc_mcp/uncertainty.py` (no new dependencies): Wilson and seeded bootstrap 95% intervals for accuracy, miss rate and false alarm rate; exact McNemar tests and paired bootstrap intervals on the same holdout tasks; and a sweep over 50 stratified splits.
* **Results:**
  * Every holdout accuracy interval is about 30 points wide (fitted weights: 70.0%, 52.1–83.3%).
  * Fitted vs hand-picked weights: +3.3 points, paired interval −20.0 to +26.7, McNemar p = 1.0, so fitting did not measurably help. It traded misses for false alarms.
  * Naive Bayes vs keyword rule: −23.3 points, paired interval −43.3 to −3.3, McNemar p = 0.065: weak evidence that the keyword rule is better.
  * Over 50 splits, the mean holdout accuracy is 63.7% for the logistic model, 59.4% for Naive Bayes and 69.2% for the keyword rule alone. Seed 21 was a favourable split for the logistic model.
* **What the data cannot conclude:** there are 30 holdout tasks, the labels were written by the project author, and train and holdout share code snippets. The numbers describe the models on this dataset only, and differences whose paired interval includes 0 are not evidence.

### Exploratory: Pi Benchmark (not a claimed block)
* **What it is:** a bug-injection benchmark (`eval/PROTOCOL.md`, `eval/pi_bench.py`) on pinned releases of more-itertools and click. It has 10 tasks (6 direct, 4 call-graph), done by hand in interactive Pi, plain vs ACC-MCP, and graded by the libraries' own tests. It measures navigation calls before the target edit, code-reading tokens, wasted and large reads, peak context, and with ACC-MCP the tiers and explicit and silent escalations.
* **What exists:** the pilot (plain, `mi_difference`) and one trace pair of `mi_split_at`; both trace runs resolved the bug.
* **Why it is not claimed:** separating ACC-MCP from run-to-run noise of an unseeded model needs at least 10 tasks × 3 runs per mode (about 60 hand runs). The one `mi_split_at` pair differs by a factor of 9 in model calls (11 plain, 98 acc), mostly because of one wrong first fix, which shows how large that noise is.

---

## 6. Verification and Requirements (R1–R4)

* **R1 (It Runs):** Runs on the project's model from `README.md` on a fresh clone: `git clone`, `uv sync`, `uv run pytest` to install and test, then `llama-server -hf …`, `pi_bench.py setup` / `prepare`, and `start.sh` to run ACC-MCP inside Pi (README section 6). No API key, password or personal data is needed or stored; the model runs locally. Verified by the acc trace run `bench1/acc/mi_split_at` (committed in `eval/traces/bench1_acc_mi_split_at/`; model `Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q6_K.gguf`; ACC-MCP commit `d52cf62`), in which Pi called `find_symbol` and `get_adaptive_context` and resolved the bug (Block 2).
* **R2 (It Is Checked):** 307 `pytest` tests in 13 files with known answers (hand-counted metrics, known Wilson intervals and McNemar p-values, exact served line ranges, rejected paths, hand-checked navigation counts of a real Pi session), passing on a fresh clone. They cover AST parsing, the call graph, decision model outputs, safety gates, the session budget, the MCP tools, the Block 7 intent model, the Block 8 statistics, and the benchmark's measurement code.
* **R3 (AI-Use Log):** Exported AI sessions in `ai_log/` and, in `ai_log/AI_USE_LOG.md`, notes on what was kept, what was fixed by hand, and which prompts were rewritten and why. Partial: the notes are being reorganised into one entry per session, and the sessions of Phases 4–20 have no transcript in the folder.
* **R4 (Individual Defence):** At the defence the student runs the demo (README section 6) and explains a piece of code the teacher picks. The code is split one job per module: AST parsing (`parser.py`), decision model (`decision.py`, `evaluate_decision.py`), the offline intent model and uncertainty analysis (`intent_model.py`, `uncertainty.py`), guardrails (`guard.py`), budget and symbol index (`budget.py`, `index.py`), token counting (`tokens.py`), and MCP endpoints (`server.py`).
