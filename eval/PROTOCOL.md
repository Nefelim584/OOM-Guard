# Pi Benchmark Protocol (Block 9)

This document is the full method of the project's own benchmark: what is compared, on which repositories and tasks, how each run is carried out and graded, what is measured, and what the results can and cannot show. Every run is done by hand in interactive Pi; the helper script [`pi_bench.py`](pi_bench.py) only prepares a run and measures it afterwards. The tasks are in [`tasks.json`](tasks.json); results go to `results.md`.

---

## 1. Question

Does giving a small local model ACC-MCP make it **cheaper to find and read the code it needs**, without making it **worse at fixing the bug**, when the model works inside a real coding agent?

The comparison is against what the agent really does without ACC-MCP, not against reading whole files. In the pilot session (section 9) the agent found the function with `grep -n` and read it in 10-100 line windows with `read`; it never loaded the ~45K-token file. So the baseline is the agent's own `grep` + windowed `read` strategy.

- **H1 (navigation cost):** with ACC-MCP the agent needs **fewer navigation calls** before it edits the target function, and **fewer code-reading tokens** (output of `read`, `grep`, `sed`, ... plus ACC-MCP slices) than with its own `grep` + `read` strategy.
- **H2 (success):** with ACC-MCP the agent resolves **at least as many** tasks as without it.
- **H3 (peak context):** with ACC-MCP the largest prompt of a single model call is smaller. This may not change: in the pilot most of the context came from the agent's own Python experiments (`python3 -c ...`), not from reading code, and ACC-MCP does not touch those.

All three are checked on the same tasks, so each task gives a paired comparison. The metrics behind them are defined in section 8.

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
| Package layout | `more_itertools/` at the repo root | `src/click/` (tests need `PYTHONPATH=src`, plus a metadata-only install, section 4.3) |
| Test suite | `tests/`, `unittest.TestCase` classes, run with pytest | `tests/`, plain pytest |
| Files with injected bugs | `more_itertools/more.py`: 5,644 lines, ~175 KB; `recipes.py` | `src/click/core.py`: 3,799 lines; `types.py`: 1,422; `parser.py`: 533; `formatting.py`: 320 |
| Why chosen | `more.py` is about 45K tokens, **larger than the model's 30,208-token context window**: the agent cannot read it whole and has to find the right function. Hundreds of small, independent functions. | Real class hierarchy (`Command`, `Parameter`, `Option`, `HelpFormatter`) with code split across modules, so callers and callees matter (Tier 2). `core.py` alone is also larger than a comfortable read. |

Both repos are cloned shallowly at the pinned tag (`setup`). Sizes are for these tags.

---

## 4. Tasks

### 4.1 How the tasks were built

1. Pick a function in one of the files above that has its own tests in the repository.
2. Change **one line or one condition** so the function gives wrong results but the code still imports and runs: a swapped argument order, an off-by-one comparison, a flipped condition.
3. Spread the tasks through the files (start, middle, end of `more.py`; four different click modules), so no task is solved by reading the first lines of a file.
4. Write a **bug report**: what was called, what came out, what was expected, with a small example. Reports for more-itertools name the public function. Reports for click name the public API involved and the module (`src/click/<module>.py`), but not the buggy function. No report mentions a line or the code change. The reports were made easier after the pilot (Phase 25), because the 4B model struggled with vaguer ones even at Q6.
5. Record the tests that cover the function as the task's **target tests**.
6. Check with `validate` that the target tests pass on the original code and fail with the bug (section 4.3).

There are two kinds of task (`kind` in `tasks.json`):

- **`direct`** (6 tasks): the report names or clearly points at the function that has the bug. The agent has to find it in a large file and fix it.
- **`call_graph`** (4 tasks): the report names an **entry** function (`entry` in `tasks.json`) that behaves wrongly, but the bug is in a helper that the entry function calls. The report says so ("the function only chooses a helper", "the order comes from a helper"), but never names the helper. The agent has to follow the call graph from the entry function to the helper. These tasks exist because in bug fixes where the report points at the function itself, the decision model almost always picks Tier 3, so Tier 1 and Tier 2 (signatures and callees) would barely be exercised.

### 4.2 Task catalogue

| Task | Kind | Repo | File (line) | Function with the bug | Entry named in the report | Injected change | Target tests | Bug report (summary) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `mi_split_at` | direct | more-itertools | `more.py` (1600) | `split_at` | | `if maxsplit == 1:` → `if maxsplit == 2:` | `SplitAtTests` | `maxsplit=2` splits only once; `maxsplit=1` never stops |
| `mi_numeric_range_index` | direct | more-itertools | `more.py` (2476) | `numeric_range.index` | | `self._start >= value > self._stop` → `self._start > value > self._stop` | `NumericRangeTests` | `.index()` of the first item of a decreasing range raises `ValueError` |
| `mi_sliding_window` | call_graph | more-itertools | `recipes.py` (798) | `_sliding_window_deque` | `sliding_window` | `maxlen=n` → `maxlen=n - 1` | `test_recipes.py::SlidingWindowTests` | windows of `n > 20` are one item short; `sliding_window` only picks a helper |
| `mi_is_sorted` | direct | more-itertools | `more.py` (4079) | `is_sorted` | | strict and non-strict checks swapped | `IsSortedTests` | `is_sorted([1, 2, 2])` is `False`, with `strict=True` it is `True` |
| `mi_constrained_batches` | direct | more-itertools | `more.py` (4981) | `constrained_batches` | | `> max_size` → `>= max_size` | `ConstrainedBatchesTests` | items that exactly fill `max_size` are split into two batches |
| `ck_nargs_tuple` | call_graph | click | `parser.py` (90) | `_unpack_args` | `_OptionParser.parse_args` | `if spos is not None:` → `if spos is None:` before `x.reverse()` | `test_arguments.py::test_nargs_tup` | `nargs=2` arguments arrive reversed; values are split in `parser.py` |
| `ck_int_range_min` | direct | click | `types.py` (664) | `_NumberRangeBase.convert` | | `operator.le if self.min_open else operator.lt` → the two operators swapped | `test_types.py::test_range` | `IntRange(5)` rejects `5`; `min_open=True` accepts the bound |
| `ck_eager_order` | call_graph | click | `core.py` (164) | `iter_params_for_processing` | `Command.parse_args` | `return not item.is_eager, idx` → `return item.is_eager, idx` | `test_commands.py::test_iter_params_for_processing` | eager options run after normal ones; `parse_args` gets the order from a helper |
| `ck_envvar_nargs` | direct | click | `core.py` (2725) | `Parameter.value_from_envvar` | | `self.nargs != 1` → `self.nargs > 1` | `test_arguments.py::test_nargs_envvar` | a `nargs=-1` argument no longer splits its environment variable |
| `ck_help_columns` | call_graph | click | `formatting.py` (19) | `measure_table` | `HelpFormatter.write_dl` | `max(widths.get(idx, 0), term_len(col))` → `term_len(col)` | `test_formatting.py` | names longer than the last entry wrap; `write_dl` gets column widths from a helper |

Exact before/after strings and full bug reports are in `tasks.json`. more-itertools test classes are in `tests/test_more.py` and `tests/test_recipes.py`. Changes in Phase 25: `mi_difference` (used for the pilot) was replaced by `mi_sliding_window`; `ck_help_columns` moved its bug from `write_dl` into the helper `measure_table`; `ck_nargs_tuple` and `ck_eager_order` kept their bugs but their reports now name the entry function and module.

### 4.3 Validation (before any run)

`python3 eval/pi_bench.py validate`:
1. Runs each repository's full test suite on the clean code and stores the failing tests, if any, as the **baseline** (`eval/work/baseline/<repo>.json`). These are not counted against the agent later. **A clean suite that passes 0 tests fails validation**, because then the "no new failures" check in section 7 would compare against nothing.
2. For each task, checks that the "before" string occurs exactly once in the file, that the target tests pass on the clean code, and that at least one target test fails with the bug.

A task that fails validation is fixed or dropped before running the benchmark.

**Click baseline fix (Phase 25).** Before this fix the clean click suite passed 0 tests: `tests/test_deprecations.py` calls `importlib.metadata.version("click")` while pytest collects it, and click is imported through `PYTHONPATH=src` instead of being installed, so the call raised `PackageNotFoundError` and pytest stopped the whole run. `setup` now writes a **metadata-only** `click-8.5.0.dist-info` (name and version, no code) into the test venv. Version lookups work, and click is still imported only from the workspace, so the tests always run the agent's version of the code.

---

## 5. Conditions

Each task runs once in each of two modes. The **only** differences between the modes are the ACC-MCP server and one hint sentence.

### 5.1 Held constant in both modes

| Item | Value |
| :--- | :--- |
| Model | Qwen3.5-4B GGUF served by `llama-server` at `http://127.0.0.1:8080`. The exact file is printed by `doctor` and must be recorded with the results. |
| Context window | **30,208 tokens**: the per-slot `n_ctx` that `llama-server /props` reports on the reference machine, given to Pi as the model's `contextWindow`. `prepare` reads it from the server and records it in `run.json`; if the server cannot be reached it assumes 30,208 and prints a warning. The same number is used in `README.md` and `Description.md`. |
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
- `ACC_MCP_ROOT` = the task workspace; `ACC_MCP_LEDGER` = a per-run file outside the workspace; session budget = **20% of the context window** (6,042 tokens for 30,208), so ACC-MCP slices can never fill more than a fifth of the window. `prepare --session-budget N` overrides it.
- One sentence is appended to the system prompt (`ACC_HINT` in `pi_bench.py`): prefer `find_symbol` and `get_adaptive_context` over reading whole files, and call again with `min_tier=3` if a slice is not enough.
- The built-in tools stay available, so the agent can always fall back to `read` and `bash`.

`--no-hint` runs acc mode without the sentence, to separate the effect of the tools from the effect of the instruction.

---

## 6. Procedure for one run

The runs are done by hand: the operator opens Pi, gives the model the task, watches it work, and stops. The script only prepares the run beforehand and measures it afterwards.

Once per run id, before its first counted run, the setup is **frozen** (`pi_bench.py freeze`, section 11); `results.md` prints it with that run's results.

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

All metrics come from files written during the run (Pi's session file, the ACC-MCP ledger, the test results), so measuring costs the operator nothing. `collect` computes them and stores them in `result.json`.

**Navigation (H1).** Every tool call is put in one class: **code_read** (Pi's `read`, `grep`, `find`, `ls`, and bash commands whose pipeline starts with `grep`, `rg`, `sed`, `cat`, `head`, `tail`, `awk`, `nl`, `find` or `ls`), **acc** (any ACC-MCP tool), **test_run** (`pytest`, `python -m pytest`/`unittest`, `tox`, `nox`), **edit** (`edit`, `write`, `sed -i`), and **other** (everything else, mostly the agent's own Python experiments). Output tokens are counted with **one counter for both modes**, characters / 3.8, so plain and acc numbers are directly comparable. The target function's line range is read with `ast` from the clean repository.

| Metric | Definition | Source |
| :--- | :--- | :--- |
| Navigation calls before the target edit | code_read calls plus ACC-MCP `find_symbol` / `get_adaptive_context` calls made before the first edit that changes a line of the target function (edited lines come from Pi's edit patch). Empty if the target was never edited. **Primary H1 metric.** | session file + clean repo |
| Code-reading tokens | output tokens of code_read calls plus ACC-MCP slices (`acc`). **Primary H1 metric.** | session file |
| Tokens per class | output tokens of code_read, acc, test_run, edit, other calls | session file |
| Read windows / wasted reads | windows the agent read itself (`read` offset..offset+limit, `sed -n a,bp`, `head`, `cat`); wasted = windows that do not overlap the target function, including any other file | session file + clean repo |
| Large reads | single code_read or acc calls that returned more than 4,000 tokens: measures whether the agent really reads whole files instead of assuming it | session file |

**Context and effort (H3).**

| Metric | Definition | Source |
| :--- | :--- | :--- |
| Peak context | largest prompt of a single model call: input + cache-read + cache-write tokens, as llama-server reports them to Pi. Directly comparable to the 30,208-token window. **Primary H3 metric.** | `usage` of each assistant message |
| Total prompt tokens | sum of the prompt sizes over all calls: how much context the model processed for the whole task | same |
| Model calls, output tokens | assistant responses and tokens generated | same |
| Tool calls / tool output | number of calls and characters returned, per tool | tool-result messages in the session |
| Compactions | times Pi had to summarize the conversation to stay in the window | compaction entries in the session |
| Operator messages | messages the operator sent (1 = only the prompt) | user messages in the session |
| Minutes | time from the first to the last message | session timestamps |

**Success (H2).** Resolved (section 7), whether the buggy file was edited, whether tests were edited, and the operator's notes (`collect --notes`).

**ACC-MCP (acc mode only).**

| Metric | Definition | Source |
| :--- | :--- | :--- |
| Requests, tiers | slices served, their tier (1/2/3), and the tier the decision model picked | ACC-MCP ledger |
| Explicit escalations | requests where the agent raised `min_tier` above the model's tier | ACC-MCP ledger |
| Silent escalations | Tier 1 or Tier 2 slices after which the agent read some of the same lines itself (`read`, `sed -n`, `head`, `cat`) within the next 5 tool calls: the slice was not enough, but the agent did not say so through `min_tier` | ledger line ranges + session file |
| Online miss rate | (explicit + silent escalations) / slices served: the decision model's miss rate on real use, next to its offline holdout result (Block 4) | both |
| Budget step-downs / refusals | slices served one tier lower, or refused, because the session budget was nearly used up | ledger and tool output |
| Whole-file tokens | tokens of the whole files the slices came from. **An upper bound only**: agents read windows, not whole files, so this is not a measured saving and is not used for any hypothesis. | ACC-MCP ledger |

---

## 9. Analysis

- **Pilot excluded.** The pilot session (`eval/work/runs/run1/plain/mi_difference`) was run before the metrics, the click baseline and the tasks were fixed, and `mi_difference` is no longer a task. It informed the hypotheses and is the fixture for the metric tests, but it is **not part of the results**. Counted runs use the run id `bench1` (the default), so `report` never reads `run1`.
- **H1 per mode:** median navigation calls before the target edit and median code-reading tokens. **H2:** resolved count out of 10. **H3:** median peak context. Also the median total prompt tokens, model calls and minutes.
- **Per task (paired):** the same task in both modes, side by side in `results.md`, so a difference can be traced to what the agent did (tools used, tiers served, wasted reads, escalations). With one run per task and mode, the per-task difference is the main evidence; medians only summarize it.
- **Per kind:** `direct` and `call_graph` tasks are also summarized separately, because ACC-MCP's Tier 2 (callees with their signatures) can only help on the second kind.
- **Was ACC-MCP used at all?** The number of acc runs in which the model actually called an ACC-MCP tool. If a 4B model ignores the tools, the acc runs measure Pi alone, and that is itself a result.
- **Decision model online:** explicit and silent escalations and the online miss rate, over all acc runs, next to the offline holdout numbers.
- **Did the agent read whole files?** The large-read count in plain mode. If it is 0, the whole-file comparison is irrelevant for this agent and is reported only as an upper bound.
- With 10 tasks and one run each, a difference of one or two resolved tasks is within noise. To see the spread, repeat the whole run with a new `--run-id` and report each run, not only the best.
- Results are reported as they come out, including if ACC-MCP makes things worse.

---

## 10. Threats to validity

- **Small sample:** 10 tasks, one run per task and mode by default.
- **Injected bugs:** single-line changes are easier to localize than real bugs and may not represent real maintenance work. The reports were written by the project author, and after the pilot they were made easier on purpose (module names for click, a pointer to the helper in `call_graph` tasks), so the success rate is not comparable to benchmarks with real bug reports.
- **Few call-graph tasks:** 4 of the 10 tasks need call-graph navigation; in the other 6 the decision model will mostly pick Tier 3, so Tier 1 and Tier 2 are exercised by a small number of runs.
- **Navigation metrics are heuristics:** bash commands are classified by their first program, `tail` and `awk` give no line window, and an edit above the target function can shift the line numbers of later edits a little.
- **Training data:** both libraries are popular and likely in the model's training data, so the model may recognize the code. This affects both modes equally but can inflate the success rate.
- **Sampling:** the model's sampling is not seeded; reruns differ.
- **Operator effect:** a person runs Pi and decides when to stop. The operator rules (6.1) limit this, and every extra message is counted, but the operator knows the bug and could steer unconsciously. Ideally the operator does not read the injected change in `tasks.json` before running.
- **Hint confound:** acc mode adds one instruction sentence; `--no-hint` runs separate its effect from the tools'.
- **Token accounting:** context numbers are what llama-server reports to Pi. Navigation tokens use characters / 3.8 for both modes. ACC-MCP's own ledger uses tiktoken, so ledger tokens, navigation tokens and Pi's context are each compared only within their own source.
- **One machine, one model:** results hold for this model file, quantization and context size.

---

## 11. Running it

From the repository root, with the model already running on port 8080:

One-time preparation:

```bash
uv sync                                   # ACC-MCP must be importable from .venv
python3 eval/pi_bench.py setup            # clone both repos at their tags, create eval/work/venv, click metadata stub
python3 eval/pi_bench.py validate         # both clean suites pass > 0 tests, all 10 tasks OK
python3 eval/pi_bench.py doctor           # model, Pi version; acc-mcp must be connected with 4 tools
python3 eval/pi_bench.py freeze           # record commit, model file, quantization, llama-server build, Pi, window, budget
```

`freeze` writes `eval/work/runs/bench1/setup.json` and refuses while the model is not running, Pi is missing, a clean suite passes 0 tests, or `src/`, `eval/pi_bench.py`, `eval/tasks.json`, `eval/PROTOCOL.md`, `pyproject.toml` or `uv.lock` have uncommitted changes (the commit hash would not identify the code). After that, `prepare` refuses a run whose context window or `tasks.json` differs from the frozen setup. A changed setup needs a new `--run-id`.

For each task, plain first, then acc:

```bash
python3 eval/pi_bench.py prepare mi_split_at --mode plain       # prints the next steps and the prompt
bash eval/work/runs/bench1/plain/mi_split_at/start.sh           # interactive Pi; paste the prompt; quit when done
python3 eval/pi_bench.py collect mi_split_at --mode plain --notes "found it with grep, fixed in 6 steps"

python3 eval/pi_bench.py prepare mi_split_at --mode acc
bash eval/work/runs/bench1/acc/mi_split_at/start.sh
python3 eval/pi_bench.py collect mi_split_at --mode acc --notes "..."
```

Progress and results:

```bash
python3 eval/pi_bench.py list             # which task/mode pairs are prepared or collected
python3 eval/pi_bench.py report           # writes eval/results.md
python3 eval/pi_bench.py report --run-id bench1 --run-id bench2 --run-id nohint1   # every run, one section each
```

`results.md` has, per run id: the frozen setup; a summary per mode and per task kind (resolved, navigation calls before the target edit, code-reading tokens, wasted and large reads, peak context, prompt tokens, model calls, minutes); ACC-MCP use, tiers and escalations with the online miss rate; a paired plain / acc table per task; and every run with the operator's notes.

Use a new `--run-id` (and `freeze` it) for a repeat of the whole set. `prepare --mode acc --no-hint` tries ACC-MCP without the hint sentence; give those runs their own run id (e.g. `nohint1`), because an acc run of a task occupies the same folder with or without the hint. Everything for one run is in `eval/work/runs/<run-id>/<mode>/<task>/`: `start.sh`, `prompt.txt`, `sessions/` (Pi session), `acc_ledger.json`, `patch.diff`, `junit_target.xml`, `junit_full.xml`, `result.json`.
