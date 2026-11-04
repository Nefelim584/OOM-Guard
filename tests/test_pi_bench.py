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
