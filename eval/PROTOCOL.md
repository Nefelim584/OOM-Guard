# Pi Benchmark Protocol (Block 9)

This document is the full method of the project's own benchmark: what is compared, on which repositories and tasks, how each run is carried out and graded, what is measured, and what the results can and cannot show. Every run is done by hand in interactive Pi; the helper script [`pi_bench.py`](pi_bench.py) only prepares a run and measures it afterwards. The tasks are in [`tasks.json`](tasks.json); results go to `results.md`.

---

## 1. Question

Does giving a small local model ACC-MCP change **whether it can fix a bug in real code** and **how much context it uses doing so**, when the model works inside a real coding agent?

- **H1 (context):** with ACC-MCP, the agent's prompts are smaller (lower peak context per model call and fewer total prompt tokens per task), because it reads signature, call-graph, or single-function slices instead of whole files.
- **H2 (success):** with ACC-MCP, the agent resolves at least as many tasks as without it, because the slices it gets still contain what it needs.

Both hypotheses are checked on the same tasks, so each task gives a paired comparison.

---

## 2. Why our own benchmark

| Option | Why not used |
| :--- | :--- |
| SWE-bench / SWE-bench Lite | The official grader builds a Docker image per task (several GB each; built locally on Apple Silicon). Too heavy for a 16 GB laptop that also runs the model, and the large repos (Django, sympy) are far beyond a 4B model. |
| Aider / Exercism Python exercises | Each task is one small file. Reading the whole file is cheap, so ACC-MCP has nothing to save and the comparison would only test the agent. |
| HumanEval / MBPP | Single functions with no repository. There is nothing to navigate, so an MCP server for code context is irrelevant. |

What the comparison needs is **real, popular code with files large enough that reading them whole is expensive or impossible**, plus **tests that decide pass/fail automatically without Docker**. The benchmark therefore injects bugs into pinned releases of two well-known pure-Python libraries and grades with their own test suites. This is the bug-injection approach used by SWE-smith: take a working repository, break one function, and ask the agent to repair it from a bug report.

---

## 3. Repositories

| | more-itertools | click |
| :--- | :--- | :--- |
| Source | https://github.com/more-itertools/more-itertools | https://github.com/pallets/click |
| Pinned release | tag `v11.2.1` | tag `8.5.0` |
| License | MIT | BSD-3-Clause |
| What it is | Extra iterator building blocks on top of `itertools` | Command-line interface framework (Pallets) |
| Runtime dependencies | none | none (on macOS/Linux) |
| Package layout | `more_itertools/` at the repo root | `src/click/` (tests need `PYTHONPATH=src`) |
| Test suite | `tests/`, `unittest.TestCase` classes, run with pytest | `tests/`, plain pytest |
| Files with injected bugs | `more_itertools/more.py`: 5,644 lines, ~175 KB | `src/click/core.py`: 3,799 lines; `types.py`: 1,422; `parser.py`: 533; `formatting.py`: 320 |
| Why chosen | `more.py` is about 45K tokens, **larger than the model's ~30K context window**: the agent cannot read it whole and has to find the right function. Hundreds of small, independent functions. | Real class hierarchy (`Command`, `Parameter`, `Option`, `HelpFormatter`) with code split across modules, so callers and callees matter (Tier 2). `core.py` alone is also larger than a comfortable read. |

Both repos are cloned shallowly at the pinned tag (`setup`). Sizes are for these tags.

---

## 4. Tasks

### 4.1 How the tasks were built

1. Pick a function in one of the files above that has its own tests in the repository.
2. Change **one line or one condition** so the function gives wrong results but the code still imports and runs: a swapped argument order, an off-by-one comparison, a flipped condition.
3. Spread the tasks through the files (start, middle, end of `more.py`; four different click modules), so no task is solved by reading the first lines of a file.
4. Write a **bug report** the way a user would file it: what they called, what they got, what they expected. Reports for more-itertools name the public function, as a user would. Reports for click describe only the user-visible behavior and never name the internal function. No report mentions a file, a line, or the code change.
5. Record the tests that cover the function as the task's **target tests**.
6. Check with `validate` that the target tests pass on the original code and fail with the bug (section 4.3).

### 4.2 Task catalogue

| Task | Repo | File (line) | Function | Injected change | Target tests | Bug report (summary) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `mi_split_at` | more-itertools | `more.py` (1600) | `split_at` | `if maxsplit == 1:` → `if maxsplit == 2:` | `SplitAtTests` | `maxsplit=2` splits only once; `maxsplit=1` never stops |
| `mi_numeric_range_index` | more-itertools | `more.py` (2476) | `numeric_range.index` | `self._start >= value > self._stop` → `self._start > value > self._stop` | `NumericRangeTests` | `.index()` of the first item of a decreasing range raises `ValueError` |
| `mi_difference` | more-itertools | `more.py` (2940) | `difference` | `map(func, b, a)` → `map(func, a, b)` | `DifferenceTest` | differences come out negated / wrong for custom functions |
| `mi_is_sorted` | more-itertools | `more.py` (4079) | `is_sorted` | strict and non-strict checks swapped | `IsSortedTests` | `is_sorted([1, 2, 2])` is `False`, with `strict=True` it is `True` |
| `mi_constrained_batches` | more-itertools | `more.py` (4981) | `constrained_batches` | `> max_size` → `>= max_size` | `ConstrainedBatchesTests` | items that exactly fill `max_size` are split into two batches |
| `ck_nargs_tuple` | click | `parser.py` (90) | `_unpack_args` | `if spos is not None:` → `if spos is None:` before `x.reverse()` | `test_arguments.py::test_nargs_tup` | `nargs=2` arguments arrive reversed |
| `ck_int_range_min` | click | `types.py` (664) | `_NumberRangeBase.convert` | `operator.le if self.min_open else operator.lt` → the two operators swapped | `test_types.py::test_range` | `IntRange(5)` rejects `5`; `min_open=True` accepts the bound |
| `ck_eager_order` | click | `core.py` (164) | `iter_params_for_processing` | `return not item.is_eager, idx` → `return item.is_eager, idx` | `test_commands.py::test_iter_params_for_processing` | eager options (`--version`, `--help`) run after normal ones |
| `ck_envvar_nargs` | click | `core.py` (2725) | `Parameter.value_from_envvar` | `self.nargs != 1` → `self.nargs > 1` | `test_arguments.py::test_nargs_envvar` | a `nargs=-1` argument no longer splits its environment variable |
| `ck_help_columns` | click | `formatting.py` (255) | `HelpFormatter.write_dl` | `term_len(first) <= ...` → `term_len(first) < ...` | `test_formatting.py` | in `--help`, the longest option's description wraps to the next line |

Exact before/after strings and full bug reports are in `tasks.json`. more-itertools test classes are in `tests/test_more.py`.

### 4.3 Validation (before any run)

`python3 eval/pi_bench.py validate`:
1. Runs each repository's full test suite on the clean code and stores the failing tests, if any, as the **baseline** (`eval/work/baseline/<repo>.json`). These are not counted against the agent later.
2. For each task, checks that the "before" string occurs exactly once in the file, that the target tests pass on the clean code, and that at least one target test fails with the bug.

A task that fails validation is fixed or dropped before running the benchmark.

---

## 5. Conditions

Each task runs once in each of two modes. The **only** differences between the modes are the ACC-MCP server and one hint sentence.

### 5.1 Held constant in both modes

| Item | Value |
| :--- | :--- |
| Model | Qwen3.5-4B GGUF served by `llama-server` at `http://127.0.0.1:8080`. The exact file is printed by `doctor` and must be recorded with the results. |
| Context window | per-slot `n_ctx` read from `llama-server /props` (30,208 on the reference machine), given to Pi as the model's `contextWindow` |
| Max output per call | 4,096 tokens |
| Agent | Pi coding agent in its normal **interactive** terminal UI, opened by the operator with the task's `start.sh` |
| Tools | Pi's defaults: `read`, `bash`, `edit`, `write` |
| Pi configuration | a fresh `PI_CODING_AGENT_DIR` per run containing only `models.json`, `settings.json` (and `mcp.json` in acc mode). The user's own MCP servers, skills, prompt templates, themes and `AGENTS.md`/`CLAUDE.md` files are not loaded (`--no-context-files --no-skills --no-prompt-templates --no-themes`). |
| Compaction | on, `reserveTokens` 4,096, `keepRecentTokens` 8,000 (Pi's default reserve of 16,384 would leave little of a 30K window) |
| Prompt | the same template for every task: the bug report, "find the cause in the library source code and fix it, do not modify or add tests, you can run the tests with `python -m pytest <path>`, reply with one sentence" |
| Workspace | a fresh copy of the pinned repo with the bug committed as its only commit |
| Python for the agent's commands | `eval/work/venv` (Python 3.12 + pytest), first on `PATH`; `PYTHONPATH=src` for click |
| Time limit | 30 minutes per run (operator stops the run, section 6.1) |
| Order | for each task, plain then acc, so slow drift (heat, other load, the operator getting used to a task) affects both modes alike |

### 5.2 The treatment (acc mode only)

- ACC-MCP is started by Pi as a stdio MCP server (`.venv/bin/python -m acc_mcp.server`) with `direct` exposure, so its tools are declared to the model like built-in tools: `mcp__acc_mcp__get_adaptive_context`, `mcp__acc_mcp__find_symbol`, `mcp__acc_mcp__get_token_ledger`, `mcp__acc_mcp__reset_session`.
- `ACC_MCP_ROOT` = the task workspace; `ACC_MCP_LEDGER` = a per-run file outside the workspace; session budget 6,000 tokens.
- One sentence is appended to the system prompt (`ACC_HINT` in `pi_bench.py`): prefer `find_symbol` and `get_adaptive_context` over reading whole files, and call again with `min_tier=3` if a slice is not enough.
- The built-in tools stay available, so the agent can always fall back to `read` and `bash`.

`--no-hint` runs acc mode without the sentence, to separate the effect of the tools from the effect of the instruction.

---

## 6. Procedure for one run

The runs are done by hand: the operator opens Pi, gives the model the task, watches it work, and stops. The script only prepares the run beforehand and measures it afterwards.

1. **Prepare** (`pi_bench.py prepare <task> --mode <plain|acc>`): copy the pinned repo into `eval/work/runs/<run-id>/<mode>/<task>/workspace/` without `.git`, inject the bug, `git init`, commit everything as "Initial commit" (test caches are hidden via `.git/info/exclude`). Write the isolated Pi configuration (section 5), `start.sh`, and `prompt.txt`.
2. **Open Pi**: `bash .../start.sh`. It changes into the workspace, sets the Pi config folder, the test venv and (click) `PYTHONPATH`, and starts interactive Pi with the local model; acc mode also appends the hint.
3. **Give the task**: paste `prompt.txt` as the first message, unchanged.
4. **Watch** the model work, following the operator rules below.
5. **Quit Pi** when a stop condition is met.
6. **Collect** (`pi_bench.py collect <task> --mode <...> --notes "..."`): read Pi's session file and, in acc mode, the ACC-MCP ledger; save the model's code changes (`patch.diff`, tests excluded) and whether it touched `tests/`; restore `tests/`; run the target tests and the full suite; write `result.json` with the operator's notes.

### 6.1 Operator rules

The operator is part of the experiment, so the same rules apply in both modes:

- Send the prompt from `prompt.txt` unchanged as the first message. Do not add hints about the file, the function, or the fix.
- If the model asks a question, answer only: `Proceed as you think best.`
- If the model stops before saying it is done (for example it only describes a plan), send `Continue.` At most 3 times per run.
- Do not edit files, run commands in the workspace, or approve anything outside what Pi asks for itself.
- **Stop** (quit Pi) when the model says it is done, when it has been stuck in the same loop for about 5 minutes, after the third `Continue.`, or at 30 minutes.
- Write down in `--notes` what you saw: how it searched for the code, wrong turns, whether it used ACC-MCP, errors, why you stopped.

The number of operator messages is counted from the session (`user_messages`), so extra nudges are visible in the results.

---

## 7. Grading

A task is **resolved** when, after step 5:
- every target test passes (at least one ran), and
- the full suite has no failing test that was not already failing on the clean repo (the baseline from `validate`).

Also recorded: whether the agent changed the file that contains the bug, which files it changed, and whether it edited tests. The operator's notes record why the run was stopped.

---

## 8. Metrics

| Metric | Definition | Source |
| :--- | :--- | :--- |
| Resolved | section 7 | test runs |
| Model calls | number of assistant responses (one per agent step) | Pi session file (`sessions/*.jsonl`), assistant messages |
| Peak context | largest prompt of a single model call: input + cache-read + cache-write tokens, as llama-server reports them to Pi. Directly comparable to the context window. | `usage` of each assistant message |
| Total prompt tokens | sum of the prompt sizes over all calls: how much context the model processed for the whole task | same |
| Output tokens | tokens generated | same |
| Tool calls / tool output | number of calls and characters returned, per tool | tool-result messages in the session |
| Compactions | times Pi had to summarize the conversation to stay in the window | compaction entries in the session |
| Operator messages | messages the operator sent (1 = only the prompt) | user messages in the session |
| Minutes | time from the first to the last message | session timestamps |
| Operator notes | what the operator observed and why the run stopped | `collect --notes` |
| ACC-MCP requests, tiers | requests served and their tier (1/2/3), and the tier the decision model picked | ACC-MCP ledger |
| Escalations | requests where the agent raised `min_tier` above the model's tier | ACC-MCP ledger |
| Budget step-downs / refusals | slices served one tier lower, or refused, because the session budget was nearly used up | ledger and tool output |
| ACC-MCP tokens vs whole file | tokens served vs tokens of the whole files those slices came from | ACC-MCP ledger |

---

## 9. Analysis

- **Per mode:** resolved count out of 10, and the median of peak context, total prompt tokens, model calls and wall time.
- **Per task (paired):** the same task in both modes, side by side in `results.md`, so a difference can be traced to what the agent did (tools used, tiers served, escalations).
- **Was ACC-MCP used at all?** The number of acc runs in which the model actually called an ACC-MCP tool. If a 4B model ignores the tools, the acc runs measure Pi alone, and that is itself a result.
- With 10 tasks and one run each, a difference of one or two resolved tasks is within noise. To see the spread, repeat the whole run with a new `--run-id` and report each run, not only the best.
- Results are reported as they come out, including if ACC-MCP makes things worse.

---

## 10. Threats to validity

- **Small sample:** 10 tasks, one run per task and mode by default.
- **Injected bugs:** single-line changes are easier to localize than real bugs and may not represent real maintenance work. The reports were written by the project author.
- **Training data:** both libraries are popular and likely in the model's training data, so the model may recognize the code. This affects both modes equally but can inflate the success rate.
- **Sampling:** the model's sampling is not seeded; reruns differ.
- **Operator effect:** a person runs Pi and decides when to stop. The operator rules (6.1) limit this, and every extra message is counted, but the operator knows the bug and could steer unconsciously. Ideally the operator does not read the injected change in `tasks.json` before running.
- **Hint confound:** acc mode adds one instruction sentence; `--no-hint` runs separate its effect from the tools'.
- **Token accounting:** context numbers are what llama-server reports to Pi. ACC-MCP's own counts use a different tokenizer (tiktoken or a character estimate), so its tokens and Pi's are compared only within their own source.
- **One machine, one model:** results hold for this model file, quantization and context size.

---

## 11. Running it

From the repository root, with the model already running on port 8080:

One-time preparation:

```bash
uv sync                                   # ACC-MCP must be importable from .venv
python3 eval/pi_bench.py setup            # clone both repos at their tags, create eval/work/venv
python3 eval/pi_bench.py validate         # baseline + all 10 tasks must be OK
python3 eval/pi_bench.py doctor           # model, Pi version; acc-mcp must be connected with 4 tools
```

For each task, plain first, then acc:

```bash
python3 eval/pi_bench.py prepare mi_difference --mode plain     # prints the next steps and the prompt
bash eval/work/runs/run1/plain/mi_difference/start.sh           # interactive Pi; paste the prompt; quit when done
python3 eval/pi_bench.py collect mi_difference --mode plain --notes "found it with grep, fixed in 6 steps"

python3 eval/pi_bench.py prepare mi_difference --mode acc
bash eval/work/runs/run1/acc/mi_difference/start.sh
python3 eval/pi_bench.py collect mi_difference --mode acc --notes "..."
```

Progress and results:

```bash
python3 eval/pi_bench.py list             # which task/mode pairs are prepared or collected
python3 eval/pi_bench.py report           # writes eval/results.md
```

Use `--run-id` for a repeat of the whole set, and `prepare --mode acc --no-hint` to try ACC-MCP without the hint sentence. Everything for one run is in `eval/work/runs/<run-id>/<mode>/<task>/`: `start.sh`, `prompt.txt`, `sessions/` (Pi session), `acc_ledger.json`, `patch.diff`, `junit_target.xml`, `junit_full.xml`, `result.json`.
