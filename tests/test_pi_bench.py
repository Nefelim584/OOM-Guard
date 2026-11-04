"""Tests for the Pi session navigation metrics in eval/pi_bench.py (Phase 24)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "pi_session_mi_difference.jsonl"


def _load_pi_bench():
    """eval/ is not a package, so load the script as a module (registered for dataclasses)."""
    spec = importlib.util.spec_from_file_location("pi_bench", ROOT / "eval" / "pi_bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["pi_bench"] = module
    spec.loader.exec_module(module)
    return module


pi_bench = _load_pi_bench()

# `difference` in the clean more-itertools v11.2.1 more.py: `def` on line 2901, last line 2940
# (read with ast from eval/work/repos/more-itertools/more_itertools/more.py)
TARGET_FILE = "more_itertools/more.py"
TARGET_SPAN = (2901, 2940)


# ---------------------------------------------------------------------------
# 1. Pilot plain session (trimmed copy of eval/work/runs/run1/plain/mi_difference)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pilot():
    return pi_bench.parse_tool_calls(FIXTURE)


def test_pilot_calls_are_classified_by_hand(pilot):
    """Verify each of the 21 tool calls of the pilot gets the kind assigned by reading the session."""
    cwd, calls = pilot
    assert cwd.endswith("runs/run1/plain/mi_difference/workspace")
    assert [c.kind for c in calls] == [
        "code_read",  # find . -name "*.py" | xargs grep -l "def difference"
        "code_read",  # read more.py 1-100
        "code_read",  # grep -n "def difference" more.py
        "code_read",  # read more.py 2901-2960
        "other",  # python3 -c experiment (failed)
        "other",  # python3 -c experiment
        "code_read",  # read more.py 2895-2944
        "edit",  # first fix, line 2933
        "test_run",  # python -m pytest -xvs | head -100
        "test_run",  # pytest ::DifferenceTests (wrong class name)
        "code_read",  # grep -n "class.*Difference" tests/test_more.py
        "test_run",  # pytest ::DifferenceTest
        "code_read",  # read more.py 2915-2934
        "code_read",  # read more.py 2945-2964
        "code_read",  # grep -n "if initial is not None" more.py
        "code_read",  # read more.py 2935-2944
        "edit",  # second fix, line 2938
        "test_run",  # pytest ::DifferenceTest
        "test_run",  # pytest tests/test_more.py -x | tail -20
        "other",  # python3 << EOF check
        "other",  # python3 << EOF check
    ]


def test_pilot_tool_classes_count_calls_and_tokens(pilot):
    """Verify calls per kind and that every output token is counted once with the shared counter."""
    _, calls = pilot
    classes = pi_bench.tool_classes(calls)

    assert {kind: c["calls"] for kind, c in classes.items()} == {
        "code_read": 10, "test_run": 5, "edit": 2, "acc": 0, "other": 4,
    }
    assert sum(c["tokens"] for c in classes.values()) == sum(pi_bench.approx_tokens(c.output) for c in calls)
    assert classes["code_read"]["tokens"] > 0
    assert pi_bench.approx_tokens("x" * 38) == 10
    assert pi_bench.approx_tokens("") == 0


def test_pilot_read_windows_and_edits(pilot):
    """Verify read windows come from offset/limit and edited lines from Pi's patch."""
    _, calls = pilot
    assert [c.window for c in calls if c.window] == [
        (TARGET_FILE, 1, 100),
        (TARGET_FILE, 2901, 2960),
        (TARGET_FILE, 2895, 2944),
        (TARGET_FILE, 2915, 2934),
        (TARGET_FILE, 2945, 2964),
        (TARGET_FILE, 2935, 2944),
    ]
    assert [c.edit for c in calls if c.edit] == [(TARGET_FILE, [2933]), (TARGET_FILE, [2938])]


def test_pilot_navigation_metrics_checked_by_hand(pilot):
    """Verify the pilot: 5 navigation calls before the first edit of difference, 2 of 6 reads wasted, no large reads."""
    _, calls = pilot
    metrics = pi_bench.navigation_metrics(calls, TARGET_FILE, TARGET_SPAN)

    assert metrics == {
        "target": {"file": TARGET_FILE, "start": 2901, "end": 2940},
        "navigation_calls": 10,
        "target_edited": True,
        "navigation_calls_before_target_edit": 5,
        "read_windows": 6,
        "wasted_reads": 2,  # lines 1-100 and 2945-2964
        "large_reads": 0,
    }


def test_navigation_metrics_without_target_span(pilot):
    """Verify a task whose clean repo is missing still reports the counts that need no line range."""
    _, calls = pilot
    metrics = pi_bench.navigation_metrics(calls, TARGET_FILE, None)
    assert metrics["target"] is None
    assert metrics["navigation_calls"] == 10
    assert metrics["navigation_calls_before_target_edit"] is None
    assert metrics["wasted_reads"] is None


def test_parse_sessions_reads_a_single_file():
    """Verify the existing session summary still works after the shared reader refactor."""
    pi = pi_bench.parse_sessions(FIXTURE)
    assert pi["session_files"] == 1
    assert pi["compactions"] == 1
    assert pi["user_messages"] == 2
    assert sum(pi["tool_calls"].values()) == 21
    assert pi["tool_errors"] == 3


# ---------------------------------------------------------------------------
# 2. Bash command classification and windows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "output", "kind", "window"),
    [
        ("sed -n '2901,2940p' more_itertools/more.py", "", "code_read", (TARGET_FILE, 2901, 2940)),
        ("sed -n 12p ./more_itertools/more.py", "", "code_read", (TARGET_FILE, 12, 12)),
        ("head -n 50 more_itertools/more.py", "", "code_read", (TARGET_FILE, 1, 50)),
        ("head -30 more_itertools/more.py", "", "code_read", (TARGET_FILE, 1, 30)),
        ("cat /ws/more_itertools/recipes.py", "a\nb\nc\n", "code_read", ("more_itertools/recipes.py", 1, 3)),
        ("cd /ws && rg -n 'def difference'", "", "code_read", None),
        ("tail -n 20 more_itertools/more.py", "", "code_read", None),
        ("echo hi | grep h", "", "other", None),
        ("pip list | grep pytest", "", "other", None),
        ("cd tests && python3 -m pytest -q", "", "test_run", None),
        ("PYTHONPATH=src pytest tests/test_x.py 2>&1 | tail -5", "", "test_run", None),
        ("python3 << 'EOF'\ncat = 1\nprint(cat)\nEOF", "", "other", None),
        ("sed -i 's/b, a/a, b/' more_itertools/more.py", "", "edit", None),
    ],
)
def test_bash_classification(tmp_path: Path, command, output, kind, window):
    """Verify bash calls are split into code reading, tests, edits and other commands."""
    session = tmp_path / "s.jsonl"
    write_session(session, "/ws", [("bash", {"command": command}, output, {})])
    _, (call,) = pi_bench.parse_tool_calls(session)
    assert call.kind == kind
    assert call.window == window
    if kind == "edit":
        assert call.edit == (TARGET_FILE, None)


def test_large_read_and_read_without_limit(tmp_path: Path):
    """Verify a whole-file read is one large read and its window ends at Pi's continuation note."""
    big = "x = 1\n" * 3000  # 18,000 chars -> 4,736 tokens
    partial = "y\n" * 10 + "\n[90 more lines in file. Use offset=11 to continue.]"
    session = tmp_path / "s.jsonl"
    write_session(session, "/ws", [
        ("read", {"path": "/ws/pkg/big.py"}, big, {}),
        ("read", {"path": "pkg/small.py"}, partial, {}),
    ])
    _, calls = pi_bench.parse_tool_calls(session)

    assert [c.window for c in calls] == [("pkg/big.py", 1, 3000), ("pkg/small.py", 1, 10)]
    metrics = pi_bench.navigation_metrics(calls, "pkg/big.py", (10, 20))
    assert metrics["large_reads"] == 1
    assert metrics["wasted_reads"] == 1
    assert metrics["target_edited"] is False


def test_patch_lines_counts_old_file_lines():
    """Verify replacements, insertions and deletions map to lines of the file before the edit."""
    patch = "--- a.py\n+++ a.py\n@@ -10,4 +10,5 @@\n a\n-b\n+B\n c\n+new\n d\n"
    assert pi_bench.patch_lines(patch) == [11, 13]


def test_symbol_span_includes_decorators(tmp_path: Path):
    """Verify the target line range of a method is read from the clean file, decorators included."""
    source = tmp_path / "mod.py"
    source.write_text(
        "class Box:\n"
        "    @property\n"
        "    def size(self):\n"
        "        return 1\n"
        "\n"
        "def free():\n"
        "    pass\n",
        encoding="utf-8",
    )
    assert pi_bench.symbol_span(source, "Box.size") == (2, 4)
    assert pi_bench.symbol_span(source, "free") == (6, 7)
    assert pi_bench.symbol_span(source, "Box.missing") is None
    assert pi_bench.symbol_span(tmp_path / "absent.py", "free") is None


# ---------------------------------------------------------------------------
# 3. Silent escalations in an acc session (synthetic)
# ---------------------------------------------------------------------------


def write_session(path: Path, cwd: str, calls: list[tuple[str, dict, str, dict]]) -> None:
    """Write a minimal Pi session: one assistant tool call and its result per call."""
    entries = [{"type": "session", "cwd": cwd}]
    for i, (name, args, output, extra) in enumerate(calls):
        call_id = f"call_{i}"
        entries.append({"type": "message", "message": {
            "role": "assistant",
            "content": [{"type": "toolCall", "id": call_id, "name": name, "arguments": args}],
            "usage": {"input": 100, "output": 10},
        }})
        entries.append({"type": "message", "message": {
            "role": "toolResult", "toolCallId": call_id, "toolName": name,
            "content": [{"type": "text", "text": output}], "isError": False, **extra,
        }})
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


ACC = "mcp__acc_mcp__"
MORE = "/ws/more_itertools/more.py"


def ledger_record(query_id: str, served_tier: int, model_tier: int, min_tier, ranges) -> dict:
    return {
        "query_id": query_id, "file_path": MORE, "served_tier": served_tier, "model_tier": model_tier,
        "requested_min_tier": min_tier, "served_tokens": 100, "whole_file_tokens": 45000,
        "line_ranges": [{"file": MORE, "symbol": s, "start": a, "end": b} for s, a, b in ranges],
    }


@pytest.fixture
def acc_run(tmp_path: Path):
    """An acc session with one silent escalation, one re-read too late, and one explicit escalation."""
    gac = ACC + "get_adaptive_context"
    session = tmp_path / "acc.jsonl"
    write_session(session, "/ws", [
        (ACC + "find_symbol", {"name": "split_at"}, '{"matches": []}', {}),                 # 0
        (gac, {"file_path": TARGET_FILE, "symbol_name": "split_at", "task_intent": "inspect"},
         "# more.py lines 100-130: split_at\ndef split_at(...): ...", {}),                    # 1  Tier 1 (q1)
        ("bash", {"command": "python -m pytest tests -x"}, "1 failed", {}),                   # 2
        ("read", {"path": TARGET_FILE, "offset": 110, "limit": 30}, "...", {}),               # 3  silent for q1
        (gac, {"file_path": TARGET_FILE, "symbol_name": "numeric_range.index", "task_intent": "trace"},
         "# more.py lines 300-400: numeric_range", {}),                                       # 4  Tier 2 (q2)
        ("bash", {"command": "sed -n '10,20p' more_itertools/more.py"}, "...", {}),           # 5  other lines
        ("bash", {"command": "python3 -c 'print(1)'"}, "1", {}),                              # 6
        ("bash", {"command": "ls more_itertools"}, "more.py", {}),                            # 7
        ("bash", {"command": "python -m pytest tests -x"}, "1 failed", {}),                   # 8
        ("bash", {"command": "python -m pytest tests -x"}, "1 failed", {}),                   # 9
        ("read", {"path": TARGET_FILE, "offset": 360, "limit": 6}, "...", {}),                # 10 6th call after q2
        (gac, {"file_path": TARGET_FILE, "symbol_name": "numeric_range.index", "task_intent": "trace",
               "min_tier": 3}, "# more.py lines 350-370: numeric_range.index", {}),           # 11 Tier 3 (q3)
        ("bash", {"command": "sed -n '350,370p' more_itertools/more.py"}, "...", {}),         # 12 after Tier 3
        (gac, {"file_path": TARGET_FILE, "symbol_name": "x", "task_intent": "fix"},
         "# [ACC-MCP] Not served: the 6000-token session budget is used up", {}),             # 13 not in ledger
        ("edit", {"path": TARGET_FILE, "edits": []}, "Successfully replaced 1 block(s).",
         {"details": {"firstChangedLine": 360}}),                                             # 14
    ])
    ledger = tmp_path / "acc_ledger.json"
    ledger.write_text(json.dumps({
        "summary": {"escalations": 1},
        "history": [
            ledger_record("q1", 1, 1, None, [("split_at", 100, 130)]),
            ledger_record("q2", 2, 2, None, [("numeric_range", 300, 400), ("numeric_range.index", 350, 370)]),
            ledger_record("q3", 3, 2, 3, [("numeric_range.index", 350, 370)]),
        ],
    }), encoding="utf-8")
    cwd, calls = pi_bench.parse_tool_calls(session)
    return cwd, calls, ledger


def test_silent_escalation_counted_once_within_five_calls(acc_run):
    """Verify only the Tier 1 slice re-read within 5 calls is a silent escalation."""
    cwd, calls, ledger = acc_run
    history = json.loads(ledger.read_text(encoding="utf-8"))["history"]

    result = pi_bench.silent_escalations(calls, history, cwd)

    assert result == {"silent_escalations": 1, "tier12_slices": 2, "slices_matched": True}


def test_ledger_metrics_report_online_miss_rate(acc_run):
    """Verify silent and explicit escalations are reported together as the online miss rate."""
    cwd, calls, ledger = acc_run

    metrics = pi_bench.ledger_metrics(ledger, calls, cwd)

    assert metrics["requests"] == 3
    assert metrics["escalations"] == 1
    assert metrics["silent_escalations"] == 1
    assert metrics["online_miss_rate"] == round(2 / 3, 4)
    assert metrics["whole_file_tokens"] == 3 * 45000
    assert "silent_escalations" not in pi_bench.ledger_metrics(ledger)


def test_acc_navigation_metrics(acc_run):
    """Verify ACC-MCP lookups count as navigation and the target edit is found by its changed line."""
    _, calls, _ = acc_run

    metrics = pi_bench.navigation_metrics(calls, TARGET_FILE, (350, 370))

    # find_symbol + 4 get_adaptive_context + read, sed, ls, read, sed
    assert metrics["navigation_calls"] == 10
    assert metrics["navigation_calls_before_target_edit"] == 10
    assert metrics["read_windows"] == 4
    assert metrics["wasted_reads"] == 2  # lines 110-139 and 10-20
    assert pi_bench.tool_classes(calls)["acc"]["calls"] == 5


# ---------------------------------------------------------------------------
# 4. Protocol fixes (Phase 25)
# ---------------------------------------------------------------------------


def test_metadata_stub_makes_version_lookup_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify the stub gives importlib.metadata a version for a src-layout repo without installing code."""
    import importlib.metadata

    repo = tmp_path / "click"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "click"\nversion = "8.5.0"\n', encoding="utf-8")
    venv = tmp_path / "venv"
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    monkeypatch.setattr(pi_bench, "VENV_DIR", venv)

    stub = pi_bench.write_metadata_stub(repo)

    assert stub == site / "click-8.5.0.dist-info"
    (dist,) = importlib.metadata.distributions(name="click", path=[str(site)])
    assert dist.version == "8.5.0"
    assert sorted(p.name for p in site.iterdir()) == ["click-8.5.0.dist-info"]  # no code

    (repo / "pyproject.toml").write_text('[project]\nname = "x"\ndynamic = ["version"]\n', encoding="utf-8")
    assert pi_bench.write_metadata_stub(repo) is None


def test_validate_fails_when_clean_suite_passes_no_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify a clean suite with 0 passed tests (e.g. a collection error) makes validate fail."""
    repos = {"click": {"test_dir": "tests", "pythonpath": "src"}}
    monkeypatch.setattr(pi_bench, "load_tasks", lambda: {"repos": repos, "tasks": []})
    monkeypatch.setattr(pi_bench, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(pi_bench, "BASELINE_DIR", tmp_path / "baseline")
    monkeypatch.setattr(pi_bench, "make_workspace", lambda src, dst, task: dst)

    broken = pi_bench.TestRun(passed=0, failed=1, failed_ids=["::tests.test_deprecations"])
    monkeypatch.setattr(pi_bench, "run_pytest", lambda *a, **k: broken)
    assert pi_bench.cmd_validate(None) == 1

    monkeypatch.setattr(pi_bench, "run_pytest", lambda *a, **k: pi_bench.TestRun(passed=765))
    assert pi_bench.cmd_validate(None) == 0


def test_session_budget_is_a_share_of_the_context_window():
    """Verify the benchmark's ACC-MCP budget is 20% of the window (6,042 for the reference 30,208)."""
    assert pi_bench.REFERENCE_CONTEXT_WINDOW == 30208
    assert pi_bench.session_budget_for(30208) == 6042 == pi_bench.DEFAULT_SESSION_BUDGET
    assert pi_bench.session_budget_for(8192) == 1638


def test_tasks_mix_direct_and_call_graph_tasks():
    """Verify every task says how it is found, and call-graph tasks name the function the report points at."""
    tasks = pi_bench.load_tasks()["tasks"]
    assert len(tasks) == 10
    kinds = {t["id"]: t["kind"] for t in tasks}
    assert set(kinds.values()) == {"direct", "call_graph"}
    assert sorted(i for i, k in kinds.items() if k == "call_graph") == [
        "ck_eager_order", "ck_help_columns", "ck_nargs_tuple", "mi_sliding_window",
    ]
    for task in tasks:
        if task["kind"] == "call_graph":
            assert task["entry"] != task["symbol"]
            assert task["entry"].split(".")[-1] in task["problem"]
            assert task["symbol"] not in task["problem"]  # the report never names the buggy helper


# ---------------------------------------------------------------------------
# 5. Frozen setup and report (Phase 26)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "quantization"),
    [
        ("/models/Qwen3.5-4B-Q6_K.gguf", "Q6_K"),
        ("unsloth/Qwen3.5-4B-GGUF:Q4_K_M", "Q4_K_M"),
        ("Qwen3.5-4B-UD-IQ4_XS-00001-of-00002.gguf", "IQ4_XS"),
        ("qwen3.5-4b-bf16.gguf", "BF16"),
        ("qwen3.5-4b", None),
    ],
)
def test_model_quantization(name, quantization):
    """Verify the quantization is read from the model file name, not from `Qwen3.5`."""
    assert pi_bench.model_quantization(name) == quantization


GOOD_SETUP = {"context_window": 30208, "acc_mcp_commit": "abcdef1234567890", "acc_mcp_dirty": False, "pi_version": "1.0.2"}


def test_freeze_problems():
    """Verify freezing needs the model running, committed code, Pi, and baselines that pass tests."""
    assert pi_bench.freeze_problems(GOOD_SETUP, {"click": {"passed": 765}, "more-itertools": {"passed": 600}}) == []

    bad = {**GOOD_SETUP, "context_window": None, "acc_mcp_dirty": True, "pi_version": None}
    problems = pi_bench.freeze_problems(bad, {"click": {"passed": 0}, "more-itertools": None})
    assert len(problems) == 5
    assert any("passed 0 tests" in p and "click" in p for p in problems)
    assert any("no baseline for more-itertools" in p for p in problems)
    assert any("uncommitted changes" in p for p in problems)


def test_freeze_writes_setup_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify freeze records the setup, refuses to overwrite it, and refuses a broken baseline."""
    import argparse

    monkeypatch.setattr(pi_bench, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(pi_bench, "collect_setup", lambda endpoint: {**GOOD_SETUP, "quantization": "Q6_K"})
    monkeypatch.setattr(pi_bench, "load_tasks", lambda: {"repos": {"click": {}}, "tasks": []})
    monkeypatch.setattr(pi_bench, "load_baseline", lambda name: {"passed": 765})
    args = argparse.Namespace(run_id="bench9", force=False, endpoint="http://x")

    assert pi_bench.cmd_freeze(args) == 0
    assert pi_bench.load_setup("bench9") == {"run_id": "bench9", **GOOD_SETUP, "quantization": "Q6_K"}
    assert pi_bench.cmd_freeze(args) == 1

    monkeypatch.setattr(pi_bench, "load_baseline", lambda name: {"passed": 0})
    assert pi_bench.cmd_freeze(argparse.Namespace(run_id="bench10", force=False, endpoint="http://x")) == 1
    assert pi_bench.load_setup("bench10") is None


def test_prepare_check_against_frozen_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify a run that no longer matches the frozen window or tasks.json is refused."""
    monkeypatch.setattr(pi_bench, "RUNS_DIR", tmp_path / "runs")
    frozen = tmp_path / "runs" / "bench1" / "setup.json"
    frozen.parent.mkdir(parents=True)
    frozen.write_text(json.dumps({"context_window": 30208, "tasks_sha256": pi_bench._sha256(pi_bench.TASKS_FILE)}))

    assert pi_bench.check_frozen("bench1", 30208) is None
    assert "8192" in pi_bench.check_frozen("bench1", 8192)
    assert pi_bench.check_frozen("not_frozen", 8192) is None  # only a warning

    frozen.write_text(json.dumps({"context_window": 30208, "tasks_sha256": "0" * 64}))
    assert "tasks.json changed" in pi_bench.check_frozen("bench1", 30208)


def make_result(task, mode, kind, resolved, nav_before, code_read_tokens, acc_tokens=0, acc=None, navigation=True):
    """A result.json as collect writes it, with only the fields the report reads."""
    result = {
        "task": task, "mode": mode, "kind": kind, "hint": mode == "acc", "notes": "grep | read",
        "pi": {"peak_context_tokens": 10000, "total_prompt_tokens": 30000, "model_calls": 10, "minutes": 2.5,
               "tool_calls": {"read": 3}},
        "grade": {"resolved": resolved, "touched_tests": False},
        "acc": acc,
    }
    if navigation:
        classes = {k: {"calls": 0, "tokens": 0} for k in pi_bench.CALL_KINDS}
        classes["code_read"] = {"calls": 3, "tokens": code_read_tokens}
        classes["acc"] = {"calls": 1, "tokens": acc_tokens}
        result["navigation"] = {
            "navigation_calls_before_target_edit": nav_before, "target_edited": nav_before is not None,
            "wasted_reads": 1, "large_reads": 0, "tool_classes": classes,
        }
    return result


LEDGER = {"requests": 2, "served_tiers": {"1": 1, "2": 0, "3": 1}, "model_tiers": {"1": 2, "2": 0, "3": 0},
          "escalations": 1, "silent_escalations": 0}


@pytest.fixture
def report_results():
    return [
        make_result("mi_split_at", "plain", "direct", True, 5, 900),
        make_result("mi_split_at", "acc", "direct", True, 2, 100, acc_tokens=300, acc=LEDGER),
        make_result("ck_help_columns", "plain", "call_graph", False, None, 2000),
    ]


def test_report_summaries_per_mode_and_kind(report_results):
    """Verify medians skip unmeasured values and every hypothesis column is filled from the results."""
    text = pi_bench.render_report([("bench1", report_results, None)], kinds={})

    assert "**Setup not frozen**" in text
    # Mode | Runs | Resolved | nav before edit (None skipped) | code-reading tokens (900, 2000 -> 1450)
    assert "| plain | 2 | 1/2 | 5 | 1450 | 1 | 0 | 10000 |" in text
    assert "| acc | 1 | 1/1 | 2 | 400 |" in text  # 100 own reads + 300 ACC-MCP
    assert "| call_graph / plain | 1 | 0/1 | – | 2000 |" in text
    assert "call_graph / acc" not in text


def test_report_acc_section_and_paired_table(report_results):
    """Verify ACC-MCP use, the online miss rate, and plain / acc pairs, with – for a missing run."""
    text = pi_bench.render_report([("bench1", report_results, None)], kinds={})

    assert "Called in **1 of 1** acc runs (0 without the hint sentence)." in text
    assert "tiers 1/2/3 served = 1/0/1, picked by the decision model = 2/0/0" in text
    assert "1 explicit (`min_tier`), 0 silent; online miss rate **0.5**" in text
    assert "| mi_split_at | direct | yes / yes | 5 / 2 | 900 / 400 | 1 / 1 | 10000 / 10000 | 1/0/1 | 1 / 0 |" in text
    assert "| ck_help_columns | call_graph | no / – | – / – | 2000 / – | 1 / – | 10000 / – | – | – |" in text
    assert "grep / read" in text  # notes cannot break the table


def test_report_setup_table_and_several_runs(report_results):
    """Verify the frozen setup is printed and every run id gets its own section."""
    setup = {**GOOD_SETUP, "quantization": "Q6_K", "session_budget": 6042, "session_budget_share": 0.2}
    text = pi_bench.render_report(
        [("bench1", report_results, setup), ("bench2", report_results[:1], None)], kinds={},
    )
    assert "| ACC-MCP commit | abcdef123456 |" in text
    assert "| Quantization | Q6_K |" in text
    assert "| Session budget | 6042 (20% of the window) |" in text
    assert "## Run `bench1`" in text and "## Run `bench2`" in text


def test_summarize_result_without_navigation():
    """Verify a result collected before Phase 24 is reported as not measured, not as zero."""
    summary = pi_bench.summarize([make_result("t", "plain", "direct", True, None, 0, navigation=False)])
    assert summary["nav_before_edit"] is None
    assert summary["code_reading_tokens"] is None
    assert summary["large_reads"] == 0


def test_old_ledger_without_line_ranges_gives_no_silent_count(acc_run):
    """Verify a pre-Phase 23 ledger reports silent escalations as unknown, not as zero."""
    cwd, calls, ledger = acc_run
    data = json.loads(ledger.read_text(encoding="utf-8"))
    for record in data["history"]:
        del record["line_ranges"]
    ledger.write_text(json.dumps(data), encoding="utf-8")

    metrics = pi_bench.ledger_metrics(ledger, calls, cwd)

    assert metrics["silent_escalations"] is None
    assert metrics["online_miss_rate"] is None
