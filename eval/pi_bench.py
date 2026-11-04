#!/usr/bin/env python3
"""Pi benchmark helper: you work in Pi yourself, this script prepares and measures (Phase 14).

For each task (a bug injected into a pinned release of more-itertools or click) and each mode
(plain Pi, or Pi + ACC-MCP):

    python3 eval/pi_bench.py prepare mi_split_at --mode plain
        -> builds the broken repo, writes start.sh (opens interactive Pi) and prompt.txt
    bash eval/work/runs/<run>/plain/mi_split_at/start.sh
        -> you paste prompt.txt into Pi, watch the model work, quit Pi when it is done
    python3 eval/pi_bench.py collect mi_split_at --mode plain --notes "what you saw"
        -> reads Pi's session file (tokens per model call, tools, compactions, time), measures
           navigation (code-reading calls and tokens, wasted and large reads, calls before the
           first edit of the target function, silent escalations in acc mode) and the ACC-MCP
           ledger, then runs the repo's own tests to check whether the bug is fixed
    python3 eval/pi_bench.py report
        -> eval/results.md, plain vs ACC-MCP side by side

One-time: `setup` (clone repos, create the test venv), `validate` (each injected bug breaks its
tests), `doctor` (model reachable, Pi can start ACC-MCP), `freeze` (record commit, model, server,
Pi, window and budget for a run id; `prepare` refuses runs that no longer match it).
Method: eval/PROTOCOL.md.
Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent
TASKS_FILE = EVAL_DIR / "tasks.json"
RESULTS_FILE = EVAL_DIR / "results.md"
WORK_DIR = EVAL_DIR / "work"
REPOS_DIR = WORK_DIR / "repos"
VENV_DIR = WORK_DIR / "venv"
BASELINE_DIR = WORK_DIR / "baseline"
RUNS_DIR = WORK_DIR / "runs"

MODES = ("plain", "acc")
DEFAULT_RUN_ID = "bench1"  # "run1" holds the pilot session, which is not part of the results
DEFAULT_ENDPOINT = "http://127.0.0.1:8080"
PROVIDER = "local-llama"
MODEL_ID = "qwen3.5-4b"
# One context window everywhere (Phase 25): the per-slot n_ctx llama-server reports in /props,
# 30,208 on the reference machine. ACC-MCP's session budget is a fixed share of it.
REFERENCE_CONTEXT_WINDOW = 30208
SESSION_BUDGET_SHARE = 0.2
DEFAULT_SESSION_BUDGET = round(REFERENCE_CONTEXT_WINDOW * SESSION_BUDGET_SHARE)  # 6,042
NOT_SERVED_MARKER = "# [ACC-MCP] Not served"
COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".tox", ".mypy_cache")

PROMPT_TEMPLATE = """You are working in a Python repository checked out in the current directory.

Bug report:
{problem}

Find the cause in the library source code and fix it. Do not modify or add tests.
You can run the existing tests with `python -m pytest <path>`.
When you are done, reply with one sentence describing the fix."""

ACC_HINT = (
    "This repository is served by the acc-mcp tools. To read Python code, prefer them over "
    "reading whole files: use mcp__acc_mcp__find_symbol to locate a function, class, or "
    "method, then mcp__acc_mcp__get_adaptive_context with that file_path, symbol_name, and a "
    "short task_intent. If the slice is not enough, call it again with min_tier=3. Slices "
    "show the real file line numbers, so you can edit the file with them."
)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


def load_tasks(path: Path = TASKS_FILE) -> dict[str, Any]:
    """Load eval/tasks.json: {"repos": {...}, "tasks": [...]}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for task in data["tasks"]:
        if task["repo"] not in data["repos"]:
            raise ValueError(f"task {task['id']} uses unknown repo {task['repo']}")
    return data


def find_task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in data["tasks"]:
        if task["id"] == task_id:
            return task
    raise SystemExit(f"unknown task {task_id!r}; known: {', '.join(t['id'] for t in data['tasks'])}")


def build_prompt(task: dict[str, Any]) -> str:
    """What you paste into Pi. Only the bug report is shown, never the file or line."""
    return PROMPT_TEMPLATE.format(problem=task["problem"].strip())


def apply_mutation(file_path: Path, old: str, new: str) -> None:
    """Replace the single occurrence of `old` with `new`; fail loudly otherwise."""
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected exactly 1 occurrence in {file_path}, found {count}")
    file_path.write_text(text.replace(old, new), encoding="utf-8")


# ---------------------------------------------------------------------------
# Workspaces and grading tests
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=pi-bench", "-c", "user.email=pi-bench@localhost", *args],
        cwd=cwd, check=check, capture_output=True, text=True,
    )


def make_workspace(repo_dir: Path, dst: Path, task: dict[str, Any] | None) -> Path:
    """Copy the pinned repo without history, inject the bug, commit it as the only commit."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(repo_dir, dst, ignore=COPY_IGNORE)
    if task is not None:
        apply_mutation(dst / task["file"], task["old"], task["new"])
    _git(dst, "init", "-q")
    (dst / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (dst / ".git" / "info" / "exclude").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n", encoding="utf-8")
    _git(dst, "add", "-A")
    _git(dst, "commit", "-q", "-m", "Initial commit")
    return dst


def tool_env(repo: dict[str, Any], workspace: Path) -> dict[str, str]:
    """Test venv first on PATH; the repo's import path for click (src layout)."""
    env = dict(os.environ)
    env["PATH"] = f"{VENV_DIR / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(VENV_DIR)
    if repo.get("pythonpath"):
        env["PYTHONPATH"] = str(workspace / repo["pythonpath"])
    else:
        env.pop("PYTHONPATH", None)
    return env


@dataclass
class TestRun:
    passed: int = 0
    failed: int = 0
    failed_ids: list[str] = field(default_factory=list)
    timed_out: bool = False


def parse_junit(path: Path) -> TestRun:
    result = TestRun()
    if not path.exists():
        return result
    for case in ET.parse(path).getroot().iter("testcase"):
        outcome = {child.tag for child in case}
        if "skipped" in outcome:
            continue
        if outcome & {"failure", "error"}:
            result.failed += 1
            result.failed_ids.append(f"{case.get('classname', '')}::{case.get('name', '')}")
        else:
            result.passed += 1
    return result


def run_pytest(workspace: Path, targets: list[str], env: dict[str, str], junit: Path) -> TestRun:
    junit.parent.mkdir(parents=True, exist_ok=True)
    junit.unlink(missing_ok=True)
    cmd = [str(VENV_DIR / "bin" / "python"), "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"]
    try:
        proc = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        result = parse_junit(junit)
        result.timed_out = True
        return result
    result = parse_junit(junit)
    if proc.returncode not in (0, 1) and result.passed + result.failed == 0:
        result.failed = 1
        result.failed_ids.append(f"pytest exited with {proc.returncode}: {proc.stdout[-300:]}")
    return result


def baseline_failures(repo_name: str) -> set[str]:
    path = BASELINE_DIR / f"{repo_name}.json"
    return set(json.loads(path.read_text(encoding="utf-8"))["failed_ids"]) if path.exists() else set()


# ---------------------------------------------------------------------------
# Pi configuration
# ---------------------------------------------------------------------------


def fetch_json(url: str) -> dict[str, Any] | None:
    """GET a JSON document (llama-server /props, /v1/models); None if unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.load(resp)
    except Exception:
        return None


def query_context_window(endpoint: str = DEFAULT_ENDPOINT) -> int:
    """Per-slot context size of the running llama-server (falls back to REFERENCE_CONTEXT_WINDOW)."""
    props = fetch_json(f"{endpoint}/props")
    try:
        return int(props["default_generation_settings"]["n_ctx"])  # type: ignore[index]
    except (TypeError, KeyError, ValueError):
        print(f"[warning] llama-server /props not reachable at {endpoint}; "
              f"assuming the reference window of {REFERENCE_CONTEXT_WINDOW} tokens")
        return REFERENCE_CONTEXT_WINDOW


def session_budget_for(context_window: int) -> int:
    """ACC-MCP session budget as SESSION_BUDGET_SHARE of the model's context window."""
    return round(context_window * SESSION_BUDGET_SHARE)


def write_agent_dir(
    agent_dir: Path, mode: str, workspace: Path, ledger: Path, context_window: int,
    endpoint: str = DEFAULT_ENDPOINT, session_budget: int = DEFAULT_SESSION_BUDGET,
) -> None:
    """An isolated Pi config: your own MCP servers, skills and settings stay out of the run.

    Both modes get the same model and settings; only "acc" gets mcp.json with ACC-MCP.
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    models = {"providers": {PROVIDER: {
        "baseUrl": f"{endpoint}/v1", "api": "openai-completions", "apiKey": "none",
        "models": [{"id": MODEL_ID, "name": "Qwen3.5-4B (local llama-server)",
                    "contextWindow": context_window, "maxTokens": 4096, "reasoning": False, "input": ["text"]}],
    }}}
    # Pi's default reserve (16384) would leave almost nothing of a ~30K window before compaction.
    settings = {"compaction": {"enabled": True, "reserveTokens": 4096, "keepRecentTokens": 8000}}
    (agent_dir / "models.json").write_text(json.dumps(models, indent=2), encoding="utf-8")
    (agent_dir / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    mcp_file = agent_dir / "mcp.json"
    if mode == "acc":
        mcp = {"mcpServers": {"acc-mcp": {
            "command": str(PROJECT_ROOT / ".venv" / "bin" / "python"),
            "args": ["-m", "acc_mcp.server"],
            "cwd": str(workspace),
            "env": {"ACC_MCP_ROOT": str(workspace), "ACC_MCP_LEDGER": str(ledger),
                    "ACC_MCP_SESSION_BUDGET": str(session_budget)},
            "exposure": "direct",
            "timeout": 120,
            "description": "Adaptive code slices of Python files in this repository",
        }}}
        mcp_file.write_text(json.dumps(mcp, indent=2), encoding="utf-8")
    else:
        mcp_file.unlink(missing_ok=True)


def start_script(repo: dict[str, Any], workspace: Path, agent_dir: Path, session_dir: Path, mode: str, hint: bool) -> str:
    """Shell script that opens interactive Pi for one task and mode."""
    pi = ["pi", "--offline", "--model", f"{PROVIDER}/{MODEL_ID}",
          "--no-context-files", "--no-skills", "--no-prompt-templates", "--session-dir", str(session_dir)]
    if mode == "acc" and hint:
        pi += ["--append-system-prompt", ACC_HINT]
    lines = [
        "#!/usr/bin/env bash",
        f"# Pi benchmark: {workspace.parent.name} ({mode}). Paste prompt.txt as your first message.",
        f"cd {shlex.quote(str(workspace))}",
        f"export PI_CODING_AGENT_DIR={shlex.quote(str(agent_dir))}",
        f"export VIRTUAL_ENV={shlex.quote(str(VENV_DIR))}",
        f'export PATH={shlex.quote(str(VENV_DIR / "bin"))}:"$PATH"',
    ]
    if repo.get("pythonpath"):
        lines.append(f"export PYTHONPATH={shlex.quote(str(workspace / repo['pythonpath']))}")
    lines.append("exec " + " ".join(shlex.quote(part) for part in pi))
    return "\n".join(lines) + "\n"


def run_dir_for(run_id: str, mode: str, task_id: str) -> Path:
    return RUNS_DIR / run_id / mode / task_id


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _timestamp(entry: dict[str, Any]) -> datetime | None:
    raw = entry.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _session_files(session_path: Path) -> list[Path]:
    """Pi session files: the file itself, or every .jsonl under a directory."""
    if session_path.is_file():
        return [session_path]
    return sorted(session_path.rglob("*.jsonl")) if session_path.exists() else []


def _session_entries(files: list[Path]) -> Any:
    """Every JSON entry of the session files, in file order; broken lines are skipped."""
    for file in files:
        for line in file.read_text(encoding="utf-8").splitlines():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_sessions(session_dir: Path) -> dict[str, Any]:
    """Measure what happened in Pi from its session files (every .jsonl under session_dir).

    Context of one model call = input + cacheRead + cacheWrite tokens (the whole prompt the
    model processed), as llama-server reported it to Pi.
    """
    calls: list[int] = []
    output_tokens = 0
    user_messages = 0
    tool_calls: dict[str, int] = {}
    tool_result_chars: dict[str, int] = {}
    tool_errors = not_served = compactions = error_stops = 0
    times: list[datetime] = []
    final_text = ""
    files = _session_files(session_dir)

    for entry in _session_entries(files):
        if entry.get("type") == "compaction":
            compactions += 1
            continue
        if entry.get("type") != "message":
            continue
        stamp = _timestamp(entry)
        if stamp:
            times.append(stamp)
        message = entry.get("message") or {}
        role = message.get("role")
        if role == "user":
            user_messages += 1
        elif role == "assistant":
            usage = message.get("usage") or {}
            calls.append(sum(int(usage.get(k) or 0) for k in ("input", "cacheRead", "cacheWrite")))
            output_tokens += int(usage.get("output") or 0)
            if message.get("stopReason") in ("error", "aborted"):
                error_stops += 1
            text = _text_of(message.get("content")).strip()
            if text:
                final_text = text
        elif role == "toolResult":
            name = message.get("toolName", "?")
            text = _text_of(message.get("content"))
            tool_calls[name] = tool_calls.get(name, 0) + 1
            tool_result_chars[name] = tool_result_chars.get(name, 0) + len(text)
            tool_errors += bool(message.get("isError"))
            not_served += NOT_SERVED_MARKER in text

    return {
        "session_files": len(files),
        "user_messages": user_messages,
        "model_calls": len(calls),
        "peak_context_tokens": max(calls, default=0),
        "total_prompt_tokens": sum(calls),
        "output_tokens": output_tokens,
        "tool_calls": tool_calls,
        "tool_result_chars": tool_result_chars,
        "tool_errors": tool_errors,
        "acc_not_served": not_served,
        "compactions": compactions,
        "error_stops": error_stops,
        "minutes": round((max(times) - min(times)).total_seconds() / 60, 1) if len(times) > 1 else 0.0,
        "final_text": final_text[-500:],
    }


# ---------------------------------------------------------------------------
# Navigation metrics (Phase 24): how the agent found and read code
# ---------------------------------------------------------------------------

ACC_PREFIX = "mcp__acc_mcp__"
ACC_SLICE_TOOL = ACC_PREFIX + "get_adaptive_context"
ACC_NAVIGATION_TOOLS = {ACC_SLICE_TOOL, ACC_PREFIX + "find_symbol"}
CALL_KINDS = ("code_read", "test_run", "edit", "acc", "other")
TOKEN_COUNTER = "chars/3.8 (one counter for both modes)"
LARGE_READ_TOKENS = 4000
ESCALATION_LOOKAHEAD = 5

# Programs that locate or read code when they start a pipeline (`pip list | grep x` is not reading code)
READ_PROGRAMS = {"grep", "egrep", "fgrep", "rg", "ag", "sed", "cat", "head", "tail", "awk", "nl", "find", "ls"}
PYTHON_PROGRAMS = {"python", "python3", "pip", "pip3", "uv", "ipython"}
TEST_PROGRAMS = {"pytest", "py.test", "tox", "nox"}
PI_READ_TOOLS = {"read", "grep", "find", "ls"}
PI_EDIT_TOOLS = {"edit", "write"}
SED_RANGE = re.compile(r"^(\d+)(?:,(\d+))?p$")
REDIRECT = re.compile(r"^\d*[<>]")


def approx_tokens(text: str) -> int:
    """Tokens of a tool output: the ACC-MCP heuristic without tiktoken, so every machine counts the same."""
    return max(1, int(len(text) / 3.8)) if text else 0


@dataclass
class ToolCall:
    """One tool call from a Pi session with its result.

    `window` is the (file, first line, last line) the call showed of a file, for `read` and
    for bash `sed -n a,bp` / `head` / `cat`; `edit` is the (file, changed lines) of an edit,
    with None as the lines when they are unknown (`write`, `sed -i`). Paths are relative to
    the workspace.
    """

    name: str
    args: dict[str, Any]
    output: str = ""
    is_error: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    kind: str = "other"
    tokens: int = 0
    window: tuple[str, int, int] | None = None
    edit: tuple[str, list[int] | None] | None = None


def normalize_path(path: str, cwd: str) -> str:
    """Workspace-relative POSIX path, so `./a.py`, `a.py` and `/ws/a.py` compare equal."""
    p = str(path).strip()
    if os.path.isabs(p) and cwd:
        for root in dict.fromkeys((cwd, os.path.realpath(cwd))):
            root = root.rstrip("/")
            if p.startswith(root + "/"):
                p = os.path.relpath(p, root)
                break
    return os.path.normpath(p).replace(os.sep, "/")


def _pipelines(command: str) -> list[list[list[str]]]:
    """Command split into pipelines, each a list of stages, each a token list.

    A heredoc body (`python3 << 'EOF' ...`) is data, so only its first line is read.
    Splitting is plain text, so `|` or `;` inside quotes can split a stage; the
    classification only needs the first program of each stage.
    """
    first_line = command.split("\n", 1)[0]
    text = first_line if "<<" in first_line else command
    pipelines = []
    for chunk in re.split(r"&&|\|\||;|\n", text):
        stages = []
        for part in chunk.split("|"):
            try:
                tokens = shlex.split(part)
            except ValueError:
                tokens = part.split()
            while tokens and re.match(r"^\w+=", tokens[0]):
                tokens = tokens[1:]  # VAR=value prefix
            if tokens:
                stages.append(tokens)
        if stages:
            pipelines.append(stages)
    return pipelines


def _program(tokens: list[str]) -> str:
    name = os.path.basename(tokens[0])
    if name in ("time", "sudo", "command", "xargs") and len(tokens) > 1:
        rest = [t for t in tokens[1:] if not t.startswith("-")]
        return _program(rest) if rest else name
    return name


def _bash_kind(pipelines: list[list[list[str]]]) -> str:
    stages = [stage for pipeline in pipelines for stage in pipeline]
    programs = [_program(stage) for stage in stages]
    for prog, stage in zip(programs, stages):
        if prog in TEST_PROGRAMS or (prog in PYTHON_PROGRAMS and {"pytest", "unittest", "py.test"} & set(stage)):
            return "test_run"
    if any(prog in PYTHON_PROGRAMS for prog in programs):
        return "other"  # the agent's own Python experiments
    if any(prog == "sed" and any(t.startswith("-i") or t == "--in-place" for t in stage)
           for prog, stage in zip(programs, stages)):
        return "edit"
    if any(_program(pipeline[0]) in READ_PROGRAMS for pipeline in pipelines):
        return "code_read"
    return "other"


def _file_args(tokens: list[str]) -> list[str]:
    """Arguments that can be file names: no flags, redirections, or bare numbers."""
    return [t for t in tokens[1:] if not t.startswith("-") and not REDIRECT.match(t) and not t.isdigit()]


def _bash_window(tokens: list[str], output: str, cwd: str) -> tuple[str, int, int] | None:
    """Line window shown by `sed -n a,bp file`, `head [-n N] file` or `cat file`; None otherwise."""
    prog, files = _program(tokens), _file_args(tokens)
    if prog == "sed" and "-n" in tokens and len(files) >= 2:
        for t in tokens:
            m = SED_RANGE.match(t)
            if m and files[-1] != t:
                return normalize_path(files[-1], cwd), int(m.group(1)), int(m.group(2) or m.group(1))
    elif prog == "head" and files:
        count = 10
        for i, t in enumerate(tokens):
            if t == "-n" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
                count = int(tokens[i + 1])
            elif re.match(r"^-n?\d+$", t):
                count = int(t.lstrip("-n"))
        return normalize_path(files[-1], cwd), 1, count
    elif prog == "cat" and len(files) == 1:
        return normalize_path(files[0], cwd), 1, max(1, len(output.splitlines()))
    return None


def _read_window(args: dict[str, Any], output: str, cwd: str) -> tuple[str, int, int] | None:
    """Lines shown by Pi's `read`: offset..offset+limit-1, else up to Pi's continuation note."""
    if not args.get("path"):
        return None
    start = int(args.get("offset") or 1)
    if args.get("limit"):
        end = start + int(args["limit"]) - 1
    else:
        more = re.search(r"Use offset=(\d+) to continue", output)
        end = int(more.group(1)) - 1 if more else start + max(1, len(output.splitlines())) - 1
    return normalize_path(args["path"], cwd), start, end


def patch_lines(patch: str) -> list[int]:
    """Lines of the old file that a unified diff changes (an insertion counts at its position)."""
    lines: list[int] = []
    old: int | None = None
    previous = ""
    for line in patch.splitlines():
        hunk = re.match(r"^@@ -(\d+)", line)
        if hunk:
            old = int(hunk.group(1))
        elif old is not None:
            if line.startswith("-"):
                lines.append(old)
                old += 1
            elif line.startswith("+"):
                if not previous.startswith("-"):
                    lines.append(old)
            else:
                old += 1
        previous = line
    return lines


def _describe(call: ToolCall, cwd: str) -> None:
    """Fill in kind, tokens, window and edit of a call."""
    call.tokens = approx_tokens(call.output)
    path = call.args.get("path")
    if call.name.startswith(ACC_PREFIX):
        call.kind = "acc"
    elif call.name in PI_READ_TOOLS:
        call.kind = "code_read"
        if call.name == "read" and not call.is_error:
            call.window = _read_window(call.args, call.output, cwd)
    elif call.name in PI_EDIT_TOOLS:
        call.kind = "edit"
        if path and not call.is_error:
            lines = patch_lines(call.details.get("patch") or "")
            if not lines and call.details.get("firstChangedLine"):
                lines = [int(call.details["firstChangedLine"])]
            call.edit = (normalize_path(path, cwd), lines or None)
    elif call.name == "bash":
        pipelines = _pipelines(str(call.args.get("command", "")))
        call.kind = _bash_kind(pipelines)
        if call.kind == "code_read" and not call.is_error:
            for pipeline in pipelines:
                call.window = _bash_window(pipeline[0], call.output, cwd)
                if call.window:
                    break
        elif call.kind == "edit" and not call.is_error:
            sed = next(s for p in pipelines for s in p if _program(s) == "sed")
            files = [f for f in _file_args(sed) if not f.startswith(("s/", "s|", "s#"))]
            call.edit = (normalize_path(files[-1], cwd), None) if files else None
    else:
        call.kind = "other"


def parse_tool_calls(session_path: Path, default_cwd: str = "") -> tuple[str, list[ToolCall]]:
    """Workspace (the session's cwd) and every tool call with its result, in call order."""
    cwd = default_cwd
    calls: list[ToolCall] = []
    by_id: dict[str, ToolCall] = {}
    for entry in _session_entries(_session_files(session_path)):
        if entry.get("type") == "session":
            cwd = entry.get("cwd") or cwd
            continue
        if entry.get("type") != "message":
            continue
        message = entry.get("message") or {}
        if message.get("role") == "assistant" and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "toolCall":
                    args = block.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    call = ToolCall(name=str(block.get("name", "?")), args=args)
                    calls.append(call)
                    by_id[str(block.get("id"))] = call
        elif message.get("role") == "toolResult":
            call = by_id.get(str(message.get("toolCallId")))
            if call is not None:
                call.output = _text_of(message.get("content"))
                call.is_error = bool(message.get("isError"))
                call.details = message.get("details") or {}
    for call in calls:
        _describe(call, cwd)
    return cwd, calls


def tool_classes(calls: list[ToolCall]) -> dict[str, dict[str, int]]:
    """Calls and output tokens per kind: code_read, test_run, edit, acc, other."""
    classes = {kind: {"calls": 0, "tokens": 0} for kind in CALL_KINDS}
    for call in calls:
        classes[call.kind]["calls"] += 1
        classes[call.kind]["tokens"] += call.tokens
    return classes


def is_navigation(call: ToolCall) -> bool:
    """A call that locates or reads code: code_read, or ACC-MCP find_symbol / get_adaptive_context."""
    return call.kind == "code_read" or call.name in ACC_NAVIGATION_TOOLS


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def symbol_span(path: Path, symbol: str) -> tuple[int, int] | None:
    """(first, last) line of a function, class or method (`Class.method`), decorators included."""
    if not path.exists():
        return None
    body = ast.parse(path.read_text(encoding="utf-8")).body
    node: ast.AST | None = None
    for part in symbol.split("."):
        node = next(
            (n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part),
            None,
        )
        if node is None:
            return None
        body = node.body
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return start, node.end_lineno or node.lineno


def _edits_target(call: ToolCall, target_file: str, span: tuple[int, int]) -> bool:
    if call.edit is None or call.edit[0] != target_file:
        return False
    lines = call.edit[1]
    return lines is None or any(span[0] <= line <= span[1] for line in lines)


def navigation_metrics(calls: list[ToolCall], target_file: str, span: tuple[int, int] | None) -> dict[str, Any]:
    """Navigation cost against the task's target function (lines taken from the clean repo).

    - navigation calls before the first edit that touches the target function;
    - wasted reads: read windows that do not overlap the target function (any other file too);
    - large reads: one code_read or ACC-MCP call returning more than LARGE_READ_TOKENS tokens,
      which measures whether the agent really loads whole files.
    Line numbers of edits are those of the file at that moment, so an earlier edit above the
    target can shift them a little.
    """
    nav = [is_navigation(c) for c in calls]
    windows = [c.window for c in calls if c.window]
    first_edit = None
    if span is not None:
        first_edit = next((i for i, c in enumerate(calls) if _edits_target(c, target_file, span)), None)
    return {
        "target": {"file": target_file, "start": span[0], "end": span[1]} if span else None,
        "navigation_calls": sum(nav),
        "target_edited": first_edit is not None,
        "navigation_calls_before_target_edit": sum(nav[:first_edit]) if first_edit is not None else None,
        "read_windows": len(windows),
        "wasted_reads": (
            sum(1 for f, a, b in windows if not (f == target_file and _overlaps(a, b, *span)))
            if span else None
        ),
        "large_reads": sum(1 for c in calls if c.kind in ("code_read", "acc") and c.tokens > LARGE_READ_TOKENS),
    }


def silent_escalations(
    calls: list[ToolCall], history: list[dict[str, Any]], cwd: str, lookahead: int = ESCALATION_LOOKAHEAD
) -> dict[str, Any] | None:
    """Tier 1/2 slices after which the agent read the same lines itself within `lookahead` calls.

    Ledger records are matched to the served get_adaptive_context calls in order (refused and
    failed calls are not in the ledger; a capped history lost the oldest records, so both are
    aligned at the end). Any read window counts: `read`, `sed -n`, `head`, `cat`.
    Returns None for a ledger without line ranges (written before Phase 23).
    """
    served = [
        i for i, c in enumerate(calls)
        if c.name == ACC_SLICE_TOOL and not c.is_error and NOT_SERVED_MARKER not in c.output
    ]
    pairs = list(zip(reversed(served), reversed(history)))[::-1]
    count = slices = 0
    for index, record in pairs:
        if record.get("served_tier") not in (1, 2):
            continue
        if "line_ranges" not in record:
            return None
        slices += 1
        ranges = [(normalize_path(r["file"], cwd), int(r["start"]), int(r["end"])) for r in record["line_ranges"]]
        for later in calls[index + 1:index + 1 + lookahead]:
            w = later.window
            if w and any(w[0] == f and _overlaps(w[1], w[2], a, b) for f, a, b in ranges):
                count += 1
                break
    return {
        "silent_escalations": count,
        "tier12_slices": slices,
        "slices_matched": len(served) == len(history),
    }


def ledger_metrics(path: Path, calls: list[ToolCall] | None = None, cwd: str = "") -> dict[str, Any] | None:
    """Tiers, escalations and served tokens from the run's ACC-MCP ledger (None if never written).

    With the session's calls it adds silent escalations and the online miss rate:
    (explicit min_tier escalations + silent escalations) / slices served.
    """
    if not path.exists():
        return None
    ledger = json.loads(path.read_text(encoding="utf-8"))
    history = ledger.get("history", [])
    tiers = {"1": 0, "2": 0, "3": 0}
    model_tiers = {"1": 0, "2": 0, "3": 0}
    for record in history:
        tiers[str(record["served_tier"])] += 1
        if record.get("model_tier") is not None:
            model_tiers[str(record["model_tier"])] += 1
    escalations = int(ledger.get("summary", {}).get("escalations", 0))
    metrics: dict[str, Any] = {
        "requests": len(history),
        "served_tiers": tiers,
        "model_tiers": model_tiers,
        "escalations": escalations,
        "budget_limited": sum(1 for r in history if r.get("budget_limited")),
        "served_tokens": sum(int(r["served_tokens"]) for r in history),
        # Upper bound only; ledgers written before Phase 23 call it baseline_tokens
        "whole_file_tokens": sum(
            int(r.get("whole_file_tokens", r.get("baseline_tokens", 0))) for r in history
        ),
    }
    if calls is not None:
        silent = silent_escalations(calls, history, cwd)
        metrics["silent_escalations"] = silent["silent_escalations"] if silent else None
        metrics["tier12_slices"] = silent["tier12_slices"] if silent else None
        metrics["slices_matched"] = silent["slices_matched"] if silent else None
        metrics["online_miss_rate"] = (
            round((escalations + silent["silent_escalations"]) / len(history), 4)
            if silent and history else None
        )
    return metrics


def grade(task: dict[str, Any], repo: dict[str, Any], workspace: Path, out_dir: Path) -> dict[str, Any]:
    """Save the model's patch, restore the original tests, run target and full tests."""
    test_dir = repo["test_dir"]
    touched_tests = bool(_git(workspace, "status", "--porcelain", "--", test_dir).stdout.strip())
    patch = _git(workspace, "diff", "HEAD", "--", ".", f":(exclude){test_dir}").stdout
    (out_dir / "patch.diff").write_text(patch, encoding="utf-8")
    changed = [f for f in _git(workspace, "diff", "HEAD", "--name-only").stdout.splitlines() if f]

    _git(workspace, "checkout", "HEAD", "--", test_dir, check=False)
    _git(workspace, "clean", "-fdq", "--", test_dir, check=False)

    env = tool_env(repo, workspace)
    target = run_pytest(workspace, task["tests"], env, out_dir / "junit_target.xml")
    full = run_pytest(workspace, [test_dir], env, out_dir / "junit_full.xml")
    new_failures = sorted(set(full.failed_ids) - baseline_failures(task["repo"]))
    return {
        "resolved": target.passed > 0 and target.failed == 0 and not target.timed_out and not new_failures,
        "edited_buggy_file": task["file"] in changed,
        "changed_files": changed,
        "touched_tests": touched_tests,
        "target_tests": asdict(target),
        "full_suite": {"passed": full.passed, "failed": full.failed},
        "new_failures": new_failures[:20],
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> int:
    data = load_tasks()
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    for name, repo in data["repos"].items():
        dst = REPOS_DIR / name
        if dst.exists():
            print(f"[setup] {name}: already at {dst}")
            continue
        print(f"[setup] cloning {repo['url']} @ {repo['tag']}")
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", repo["tag"], repo["url"], str(dst)], check=True)
    if not (VENV_DIR / "bin" / "python").exists():
        print(f"[setup] creating test venv at {VENV_DIR}")
        subprocess.run(["uv", "venv", "-q", "--python", "3.12", str(VENV_DIR)], check=True)
    subprocess.run(["uv", "pip", "install", "-q", "--python", str(VENV_DIR / "bin" / "python"), "pytest"], check=True)
    for name, repo in data["repos"].items():
        if repo.get("pythonpath"):
            stub = write_metadata_stub(REPOS_DIR / name)
            print(f"[setup] {name}: metadata-only {stub.name if stub else 'stub NOT written (no static name/version)'}")
    print("[setup] done. Next: python3 eval/pi_bench.py validate")
    return 0


def write_metadata_stub(repo_dir: Path) -> Path | None:
    """Metadata-only `<name>-<version>.dist-info` for a src-layout repo, in the test venv.

    Such a repo is imported through PYTHONPATH, not installed, so
    `importlib.metadata.version("click")` raises PackageNotFoundError; click's
    tests/test_deprecations.py calls it while pytest collects, which aborted the whole
    clean suite (0 tests passed). The stub has no code, so the package is still imported
    only from the workspace: a fix the agent makes is what the tests run.
    """
    pyproject = repo_dir / "pyproject.toml"
    if not pyproject.exists():
        return None
    project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    name, version = project.get("name"), project.get("version")
    site = next(VENV_DIR.glob("lib/python*/site-packages"), None)
    if not name or not version or site is None:
        return None
    dist = site / f"{name.replace('-', '_')}-{version}.dist-info"
    dist.mkdir(exist_ok=True)
    (dist / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    (dist / "INSTALLER").write_text("pi_bench (metadata only, no code)\n", encoding="utf-8")
    return dist


def cmd_validate(args: argparse.Namespace) -> int:
    """Record each repo's already-failing tests, then check each injected bug breaks its tests."""
    data = load_tasks()
    scratch = WORK_DIR / "validate"
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, repo in data["repos"].items():
        ws = make_workspace(REPOS_DIR / name, scratch / name, None)
        full = run_pytest(ws, [repo["test_dir"]], tool_env(repo, ws), scratch / f"{name}.xml")
        (BASELINE_DIR / f"{name}.json").write_text(json.dumps({"passed": full.passed, "failed_ids": full.failed_ids}, indent=2), encoding="utf-8")
        print(f"[validate] {name}: clean suite {full.passed} passed, {full.failed} failed (baseline)")
        if full.passed == 0:
            # A collection error or a broken environment; the "no new failures" check would be empty
            ok = False
            print(f"[validate] {name}: INVALID - the clean suite passed 0 tests: {full.failed_ids[:3]}")
    for task in data["tasks"]:
        repo = data["repos"][task["repo"]]
        clean = make_workspace(REPOS_DIR / task["repo"], scratch / task["id"] / "clean", None)
        before = run_pytest(clean, task["tests"], tool_env(repo, clean), scratch / task["id"] / "clean.xml")
        try:
            broken = make_workspace(REPOS_DIR / task["repo"], scratch / task["id"] / "broken", task)
        except ValueError as exc:
            print(f"[validate] {task['id']}: INVALID - {exc}")
            ok = False
            continue
        after = run_pytest(broken, task["tests"], tool_env(repo, broken), scratch / task["id"] / "broken.xml")
        valid = before.passed > 0 and before.failed == 0 and after.failed > 0
        ok &= valid
        print(f"[validate] {task['id']}: {'OK     ' if valid else 'INVALID'} original {before.passed} passed/"
              f"{before.failed} failed, with bug {after.passed} passed/{after.failed} failed")
    shutil.rmtree(scratch, ignore_errors=True)
    print("[validate] all tasks valid" if ok else "[validate] some tasks are invalid")
    return 0 if ok else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the model endpoint, Pi, and that Pi can start ACC-MCP over stdio."""
    try:
        with urllib.request.urlopen(f"{args.endpoint}/v1/models", timeout=5) as resp:
            print(f"[doctor] llama-server model: {json.load(resp)['data'][0]['id']}")
    except Exception as exc:
        print(f"[doctor] llama-server not reachable at {args.endpoint}: {exc}")
        return 1
    print(f"[doctor] context window per slot: {query_context_window(args.endpoint)}")
    version = subprocess.run(["pi", "--version"], capture_output=True, text=True)
    print(f"[doctor] pi {version.stdout.strip() or version.stderr.strip()}")
    probe = WORK_DIR / "doctor"
    first = next(iter(load_tasks()["repos"]))
    ws = make_workspace(REPOS_DIR / first, probe / "workspace", None)
    write_agent_dir(probe / "pi-agent", "acc", ws, probe / "ledger.json", query_context_window(args.endpoint), args.endpoint)
    listing = subprocess.run(["pi", "mcp", "list"], cwd=ws, env=dict(os.environ, PI_CODING_AGENT_DIR=str(probe / "pi-agent")),
                             capture_output=True, text=True)
    print(listing.stdout + listing.stderr)
    shutil.rmtree(probe, ignore_errors=True)
    return listing.returncode


def cmd_prepare(args: argparse.Namespace) -> int:
    data = load_tasks()
    task = find_task(data, args.task)
    repo = data["repos"][task["repo"]]
    out_dir = run_dir_for(args.run_id, args.mode, task["id"])
    if out_dir.exists() and not args.force:
        print(f"[prepare] {out_dir} already exists; use --force to start this task over")
        return 1
    context_window = query_context_window(args.endpoint)
    changed = check_frozen(args.run_id, context_window)
    if changed:
        print(f"[prepare] refused: {changed}. Counted runs must match the frozen setup; use a new --run-id.")
        return 1
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    workspace = make_workspace(REPOS_DIR / task["repo"], out_dir / "workspace", task)
    session_budget = args.session_budget or session_budget_for(context_window)
    write_agent_dir(out_dir / "pi-agent", args.mode, workspace, out_dir / "acc_ledger.json",
                    context_window, args.endpoint, session_budget)
    (out_dir / "sessions").mkdir()
    hint = args.mode == "acc" and not args.no_hint
    script = out_dir / "start.sh"
    script.write_text(start_script(repo, workspace, out_dir / "pi-agent", out_dir / "sessions", args.mode, hint), encoding="utf-8")
    script.chmod(0o755)
    prompt = build_prompt(task)
    (out_dir / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    (out_dir / "run.json").write_text(json.dumps({
        "task": task["id"], "repo": task["repo"], "kind": task.get("kind"), "mode": args.mode, "hint": hint,
        "context_window": context_window, "session_budget": session_budget if args.mode == "acc" else None,
        "prepared_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")

    print(f"[prepare] {task['id']} ({args.mode}) ready, context window {context_window}\n")
    print(f"1. Open Pi:\n\n   bash {script}\n")
    print("2. Paste this as your first message (also in prompt.txt):\n")
    print("   " + prompt.replace("\n", "\n   ") + "\n")
    print("3. Let the model work. Follow the operator rules in eval/PROTOCOL.md. Quit Pi when it is done.\n")
    print(f"4. Then run:\n\n   python3 eval/pi_bench.py collect {task['id']} --mode {args.mode}"
          f"{'' if args.run_id == DEFAULT_RUN_ID else ' --run-id ' + args.run_id} --notes \"...\"\n")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    data = load_tasks()
    task = find_task(data, args.task)
    repo = data["repos"][task["repo"]]
    out_dir = run_dir_for(args.run_id, args.mode, task["id"])
    if not (out_dir / "run.json").exists():
        print(f"[collect] nothing prepared at {out_dir}; run `prepare` first")
        return 1
    run = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    pi = parse_sessions(out_dir / "sessions")
    if pi["session_files"] == 0:
        print(f"[collect] no Pi session found in {out_dir / 'sessions'}; was Pi started with start.sh?")
        return 1
    cwd, calls = parse_tool_calls(out_dir / "sessions", default_cwd=str(out_dir / "workspace"))
    span = symbol_span(REPOS_DIR / task["repo"] / task["file"], task["symbol"])
    navigation = {
        "token_counter": TOKEN_COUNTER,
        "tool_classes": tool_classes(calls),
        **navigation_metrics(calls, task["file"], span),
    }
    result = {
        **run,
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "notes": args.notes,
        "pi": pi,
        "navigation": navigation,
        "acc": ledger_metrics(out_dir / "acc_ledger.json", calls, cwd) if args.mode == "acc" else None,
        "grade": grade(task, repo, out_dir / "workspace", out_dir),
    }
    (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    g, a, n = result["grade"], result["acc"], navigation
    print(f"[collect] {task['id']} ({args.mode}): {'RESOLVED' if g['resolved'] else 'NOT resolved'}")
    print(f"  model calls {pi['model_calls']}, peak context {pi['peak_context_tokens']} tokens, "
          f"total prompt {pi['total_prompt_tokens']}, output {pi['output_tokens']}, {pi['minutes']} min")
    print(f"  tools {pi['tool_calls']}, compactions {pi['compactions']}, your messages {pi['user_messages']}")
    print(f"  target tests {g['target_tests']['passed']} passed / {g['target_tests']['failed']} failed; "
          f"new failures elsewhere {len(g['new_failures'])}; edited the buggy file: {g['edited_buggy_file']}")
    before = n["navigation_calls_before_target_edit"]
    print(f"  navigation: {n['navigation_calls']} calls"
          f" ({before if before is not None else 'target never edited'} before the first edit of {task['symbol']}), "
          f"{n['read_windows']} read windows, {n['wasted_reads']} wasted, "
          f"{n['large_reads']} large reads (> {LARGE_READ_TOKENS} tokens)")
    if n["target"] is None:
        print(f"  (no line range for {task['symbol']}: clean repo missing at {REPOS_DIR / task['repo']}?)")
    print("  output tokens by kind: " + ", ".join(
        f"{kind} {c['tokens']} ({c['calls']} calls)" for kind, c in n["tool_classes"].items()))
    if a:
        print(f"  ACC-MCP: {a['requests']} slices, tiers 1/2/3 = {a['served_tiers']['1']}/{a['served_tiers']['2']}/"
              f"{a['served_tiers']['3']}, escalations {a['escalations']} explicit + "
              f"{a['silent_escalations'] if a['silent_escalations'] is not None else '?'} silent "
              f"(online miss rate {a['online_miss_rate']}), {a['served_tokens']} tokens served "
              f"(whole files, an upper bound only: {a['whole_file_tokens']})")
    elif args.mode == "acc":
        print("  ACC-MCP: never called")
    print(f"  saved to {out_dir / 'result.json'}")
    return 0


# ---------------------------------------------------------------------------
# Frozen setup (Phase 26): what a counted run was done with
# ---------------------------------------------------------------------------

SETUP_FILE = "setup.json"
# Files whose uncommitted changes would make the recorded commit hash meaningless
FROZEN_PATHS = ("src", "eval/pi_bench.py", "eval/tasks.json", "eval/PROTOCOL.md", "pyproject.toml", "uv.lock")


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def model_quantization(name: str) -> str | None:
    """GGUF quantization from a model file or id, e.g. `Qwen3.5-4B-Q6_K.gguf` -> `Q6_K`."""
    m = re.search(r"(?i)(?<![A-Za-z0-9])(I?Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32)(?![A-Za-z0-9])", name)
    return m.group(1).upper() if m else None


def collect_setup(endpoint: str) -> dict[str, Any]:
    """Everything a counted run depends on: code version, model, server, agent, window, budget."""
    props = fetch_json(f"{endpoint}/props") or {}
    models = fetch_json(f"{endpoint}/v1/models") or {}
    model_id = ((models.get("data") or [{}])[0]).get("id", "")
    model_path = str(props.get("model_path") or "")
    try:
        window = int(props["default_generation_settings"]["n_ctx"])
    except (KeyError, TypeError, ValueError):
        window = None
    try:
        pi = subprocess.run(["pi", "--version"], capture_output=True, text=True)
        pi_version = pi.stdout.strip() or pi.stderr.strip() or None
    except FileNotFoundError:
        pi_version = None
    dirty = _git(PROJECT_ROOT, "status", "--porcelain", "--", *FROZEN_PATHS, check=False).stdout.strip()
    return {
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "acc_mcp_commit": _git(PROJECT_ROOT, "rev-parse", "HEAD", check=False).stdout.strip() or None,
        "acc_mcp_dirty": bool(dirty),
        "model_id": model_id or None,
        "model_path": model_path or None,
        "quantization": model_quantization(model_path) or model_quantization(model_id),
        "llama_server_build": props.get("build_info"),
        "pi_version": pi_version,
        "context_window": window,
        "session_budget": session_budget_for(window) if window else None,
        "session_budget_share": SESSION_BUDGET_SHARE,
        "tasks_sha256": _sha256(TASKS_FILE),
        "protocol_sha256": _sha256(EVAL_DIR / "PROTOCOL.md"),
    }


def freeze_problems(setup: dict[str, Any], baselines: dict[str, dict[str, Any] | None]) -> list[str]:
    """Reasons the setup cannot be frozen for counted runs; empty when it can."""
    problems = []
    if setup["context_window"] is None:
        problems.append("llama-server /props is not reachable: start the model first")
    if not setup["acc_mcp_commit"]:
        problems.append("no git commit found for ACC-MCP")
    if setup["acc_mcp_dirty"]:
        problems.append(f"uncommitted changes in {', '.join(FROZEN_PATHS)}: commit them so the hash identifies the code")
    if not setup["pi_version"]:
        problems.append("`pi --version` failed: is Pi installed?")
    for repo, baseline in baselines.items():
        if baseline is None:
            problems.append(f"no baseline for {repo}: run `validate`")
        elif int(baseline.get("passed", 0)) == 0:
            problems.append(f"the clean {repo} suite passed 0 tests: run `setup` and `validate` again")
    return problems


def load_baseline(repo_name: str) -> dict[str, Any] | None:
    path = BASELINE_DIR / f"{repo_name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_setup(run_id: str) -> dict[str, Any] | None:
    path = RUNS_DIR / run_id / SETUP_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def cmd_freeze(args: argparse.Namespace) -> int:
    """Record the setup of a run id once, before its first counted run."""
    path = RUNS_DIR / args.run_id / SETUP_FILE
    if path.exists() and not args.force:
        print(f"[freeze] {args.run_id} is already frozen ({path}); use a new --run-id, or --force to replace it")
        return 1
    setup = collect_setup(args.endpoint)
    problems = freeze_problems(setup, {name: load_baseline(name) for name in load_tasks()["repos"]})
    if problems:
        for problem in problems:
            print(f"[freeze] NOT frozen: {problem}")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": args.run_id, **setup}, indent=2), encoding="utf-8")
    print(json.dumps(setup, indent=2))
    print(f"[freeze] {args.run_id} frozen in {path}")
    return 0


def check_frozen(run_id: str, context_window: int) -> str | None:
    """Why `prepare` must not continue under this run id (frozen setup changed), or None."""
    setup = load_setup(run_id)
    if setup is None:
        print(f"[prepare] warning: run {run_id!r} is not frozen; run `freeze --run-id {run_id}` before counted runs")
        return None
    if setup.get("context_window") != context_window:
        return f"context window is {context_window}, but {run_id} was frozen with {setup.get('context_window')}"
    if setup.get("tasks_sha256") != _sha256(TASKS_FILE):
        return f"eval/tasks.json changed since {run_id} was frozen"
    return None


# ---------------------------------------------------------------------------
# Report (Phase 26)
# ---------------------------------------------------------------------------


def _median(values: list[float | None]) -> float | None:
    """Median of the measured values; None (shown as "–") when nothing was measured."""
    known = [v for v in values if v is not None]
    return round(statistics.median(known), 1) if known else None


def _fmt(value: Any) -> str:
    if value is None:
        return "–"
    return f"{value:g}" if isinstance(value, float) else str(value)


def _nav(result: dict[str, Any], key: str) -> Any:
    return (result.get("navigation") or {}).get(key)


def code_reading_tokens(result: dict[str, Any]) -> int | None:
    """Output tokens of the agent's own code reading plus ACC-MCP output (H1)."""
    classes = _nav(result, "tool_classes")
    return classes["code_read"]["tokens"] + classes["acc"]["tokens"] if classes else None


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts and medians for one group of runs (one mode, or one mode and task kind)."""
    return {
        "runs": len(results),
        "resolved": sum(bool(r["grade"]["resolved"]) for r in results),
        "nav_before_edit": _median([_nav(r, "navigation_calls_before_target_edit") for r in results]),
        "target_edited": sum(bool(_nav(r, "target_edited")) for r in results),
        "code_reading_tokens": _median([code_reading_tokens(r) for r in results]),
        "wasted_reads": _median([_nav(r, "wasted_reads") for r in results]),
        "large_reads": sum(_nav(r, "large_reads") or 0 for r in results),
        "peak_context": _median([r["pi"]["peak_context_tokens"] for r in results]),
        "prompt_tokens": _median([r["pi"]["total_prompt_tokens"] for r in results]),
        "model_calls": _median([r["pi"]["model_calls"] for r in results]),
        "minutes": _median([r["pi"]["minutes"] for r in results]),
    }


def acc_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """ACC-MCP use and the decision model's online miss rate over all acc runs."""
    acc_runs = [r for r in results if r["mode"] == "acc"]
    ledgers = [r["acc"] for r in acc_runs if r.get("acc")]
    with_silent = [a for a in ledgers if a.get("silent_escalations") is not None]
    slices = sum(a["requests"] for a in with_silent)
    misses = sum(a["escalations"] + a["silent_escalations"] for a in with_silent)
    return {
        "runs": len(acc_runs),
        "used": sum(1 for a in ledgers if a["requests"]),
        "without_hint": sum(1 for r in acc_runs if r.get("hint") is False),
        "slices": sum(a["requests"] for a in ledgers),
        "served_tiers": {t: sum(a["served_tiers"][t] for a in ledgers) for t in ("1", "2", "3")},
        "model_tiers": {t: sum(a["model_tiers"][t] for a in ledgers) for t in ("1", "2", "3")},
        "explicit_escalations": sum(a["escalations"] for a in ledgers),
        "silent_escalations": sum(a["silent_escalations"] for a in with_silent) if with_silent else None,
        "online_miss_rate": round(misses / slices, 4) if slices else None,
    }


def _setup_lines(setup: dict[str, Any] | None) -> list[str]:
    if setup is None:
        return ["**Setup not frozen** (`freeze` was not run for this run id): these runs are not counted results.", ""]
    commit = (setup.get("acc_mcp_commit") or "?")[:12] + (" (with uncommitted changes)" if setup.get("acc_mcp_dirty") else "")
    budget = setup.get("session_budget")
    rows = [
        ("Frozen at", setup.get("frozen_at")),
        ("ACC-MCP commit", commit),
        ("Model", setup.get("model_id")),
        ("Model file", setup.get("model_path")),
        ("Quantization", setup.get("quantization")),
        ("llama-server build", setup.get("llama_server_build")),
        ("Pi", setup.get("pi_version")),
        ("Context window", setup.get("context_window")),
        ("Session budget", f"{budget} ({setup.get('session_budget_share', 0):.0%} of the window)" if budget else None),
        ("tasks.json / PROTOCOL.md sha256", f"{(setup.get('tasks_sha256') or '?')[:12]} / {(setup.get('protocol_sha256') or '?')[:12]}"),
    ]
    return ["| Item | Value |", "| :--- | :--- |", *(f"| {k} | {_fmt(v)} |" for k, v in rows), ""]


def _summary_table(groups: list[tuple[str, list[dict[str, Any]]]], label: str) -> list[str]:
    lines = [
        f"| {label} | Runs | Resolved (H2) | Nav calls before target edit (H1) | Code-reading tokens (H1) "
        "| Wasted reads | Large reads (total) | Peak context (H3) | Prompt tokens | Model calls | Minutes |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for name, rs in groups:
        if not rs:
            continue
        s = summarize(rs)
        lines.append(
            f"| {name} | {s['runs']} | {s['resolved']}/{s['runs']} | {_fmt(s['nav_before_edit'])} "
            f"| {_fmt(s['code_reading_tokens'])} | {_fmt(s['wasted_reads'])} | {s['large_reads']} "
            f"| {_fmt(s['peak_context'])} | {_fmt(s['prompt_tokens'])} | {_fmt(s['model_calls'])} | {_fmt(s['minutes'])} |"
        )
    return lines + [""]


def _pair(plain: Any, acc: Any) -> str:
    return f"{_fmt(plain)} / {_fmt(acc)}"


def _value_of(result: dict[str, Any] | None, fn: Any) -> Any:
    """fn(result), or None when that mode has no run for the task."""
    return fn(result) if result else None


def render_run(results: list[dict[str, Any]], run_id: str, setup: dict[str, Any] | None,
               kinds: dict[str, str]) -> list[str]:
    """Markdown section for one run id: setup, summaries, ACC-MCP, paired and per-run tables."""
    for r in results:
        r.setdefault("kind", kinds.get(r["task"], "?"))
    by_mode = [(mode, [r for r in results if r["mode"] == mode]) for mode in MODES]
    lines = [f"## Run `{run_id}`", "", "### Setup", "", *_setup_lines(setup)]

    lines += ["### Summary per mode", "",
              "Medians over the runs of each mode, except Resolved and Large reads. – = not measured "
              "(for example, the target function was never edited).", "",
              *_summary_table(by_mode, "Mode")]
    by_kind = [(f"{kind} / {mode}", [r for r in rs if r["kind"] == kind])
               for kind in ("direct", "call_graph") for mode, rs in by_mode]
    lines += ["### Summary per task kind", "",
              "`call_graph` tasks name an entry function whose helper has the bug (`eval/PROTOCOL.md` 4.1).", "",
              *_summary_table(by_kind, "Kind / mode")]

    a = acc_summary(results)
    if a["runs"]:
        tiers = "/".join(str(a["served_tiers"][t]) for t in ("1", "2", "3"))
        picked = "/".join(str(a["model_tiers"][t]) for t in ("1", "2", "3"))
        lines += [
            "### ACC-MCP", "",
            f"- Called in **{a['used']} of {a['runs']}** acc runs ({a['without_hint']} without the hint sentence).",
            f"- Slices served: {a['slices']}; tiers 1/2/3 served = {tiers}, picked by the decision model = {picked}.",
            f"- Escalations: {a['explicit_escalations']} explicit (`min_tier`), {_fmt(a['silent_escalations'])} silent; "
            f"online miss rate **{_fmt(a['online_miss_rate'])}** (explicit + silent / slices, runs with line ranges only).",
            "",
        ]

    lines += ["### Paired per task (plain / acc)", "",
              "| Task | Kind | Resolved | Nav calls before target edit | Code-reading tokens | Wasted reads "
              "| Peak context | ACC tiers 1/2/3 | Escalations explicit / silent |",
              "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"]
    for task in sorted({r["task"] for r in results}):
        plain = next((r for r in results if r["task"] == task and r["mode"] == "plain"), None)
        acc = next((r for r in results if r["task"] == task and r["mode"] == "acc"), None)
        get = _value_of
        ledger = acc.get("acc") if acc else None
        lines.append(
            f"| {task} | {(plain or acc)['kind']} "
            f"| {_pair(get(plain, lambda r: 'yes' if r['grade']['resolved'] else 'no'), get(acc, lambda r: 'yes' if r['grade']['resolved'] else 'no'))} "
            f"| {_pair(get(plain, lambda r: _nav(r, 'navigation_calls_before_target_edit')), get(acc, lambda r: _nav(r, 'navigation_calls_before_target_edit')))} "
            f"| {_pair(get(plain, code_reading_tokens), get(acc, code_reading_tokens))} "
            f"| {_pair(get(plain, lambda r: _nav(r, 'wasted_reads')), get(acc, lambda r: _nav(r, 'wasted_reads')))} "
            f"| {_pair(get(plain, lambda r: r['pi']['peak_context_tokens']), get(acc, lambda r: r['pi']['peak_context_tokens']))} "
            f"| {'/'.join(str(ledger['served_tiers'][t]) for t in ('1', '2', '3')) if ledger else '–'} "
            f"| {_pair(ledger['escalations'], ledger.get('silent_escalations')) if ledger else '–'} |"
        )

    lines += ["", "### Every run", "",
              "| Task | Mode | Hint | Resolved | Calls | Peak ctx | Prompt tokens | Tools used | Min | Notes |",
              "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- | :---: | :--- |"]
    for r in sorted(results, key=lambda r: (r["task"], r["mode"])):
        p, g = r["pi"], r["grade"]
        tools = ", ".join(f"{k.replace(ACC_PREFIX, 'acc:')}×{v}" for k, v in sorted(p["tool_calls"].items()))
        verdict = ("yes" if g["resolved"] else "no") + (" (edited tests)" if g.get("touched_tests") else "")
        hint = "–" if r["mode"] == "plain" else ("yes" if r.get("hint") else "no")
        notes = (r.get("notes") or "").replace("|", "/").replace("\n", " ")
        lines.append(
            f"| {r['task']} | {r['mode']} | {hint} | {verdict} | {p['model_calls']} | {p['peak_context_tokens']} "
            f"| {p['total_prompt_tokens']} | {tools or '–'} | {p['minutes']} | {notes} |"
        )
    return lines + [""]


def render_report(runs: list[tuple[str, list[dict[str, Any]], dict[str, Any] | None]], kinds: dict[str, str]) -> str:
    """eval/results.md: one section per run id, every run reported (PROTOCOL.md section 9)."""
    lines = [
        "# Pi Benchmark Results", "",
        "Plain Pi vs Pi + ACC-MCP, operated by hand in interactive Pi (method and hypotheses H1-H3: "
        "`eval/PROTOCOL.md`). The pilot session (`run1`) is not part of the results.", "",
        "- **H1 (navigation cost):** navigation calls before the first edit of the target function, and "
        "code-reading tokens (the agent's own `read`/`grep`/`sed`/... output plus ACC-MCP slices; characters / 3.8 "
        "for both modes).",
        "- **H2 (success):** resolved tasks.",
        "- **H3 (peak context):** largest prompt of one model call, as llama-server reported it to Pi.", "",
    ]
    for run_id, results, setup in runs:
        lines += render_run(results, run_id, setup, kinds)
    return "\n".join(lines)


def cmd_report(args: argparse.Namespace) -> int:
    kinds = {t["id"]: t.get("kind", "?") for t in load_tasks()["tasks"]}
    runs = []
    for run_id in args.run_id or [DEFAULT_RUN_ID]:
        run_dir = RUNS_DIR / run_id
        results = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(run_dir.glob("*/*/result.json"))]
        if not results:
            print(f"[report] no collected results in {run_dir}")
            return 1
        runs.append((run_id, results, load_setup(run_id)))
    text = render_report(runs, kinds)
    RESULTS_FILE.write_text(text, encoding="utf-8")
    print(text)
    print(f"[report] written to {RESULTS_FILE}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Show every task and whether each mode is prepared / collected in this run."""
    for task in load_tasks()["tasks"]:
        states = []
        for mode in MODES:
            d = run_dir_for(args.run_id, mode, task["id"])
            states.append(f"{mode}: {'collected' if (d / 'result.json').exists() else 'prepared' if d.exists() else '-'}")
        print(f"{task['id']:<24} {'   '.join(states)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="llama-server base URL")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="clone pinned repos and create the test venv")
    sub.add_parser("validate", help="check each injected bug breaks its tests")
    sub.add_parser("doctor", help="check llama-server, Pi, and the ACC-MCP connection")
    for name, help_text in (("prepare", "build a task workspace and start.sh for interactive Pi"),
                            ("collect", "measure a finished Pi session and check the fix")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("task")
        p.add_argument("--mode", choices=MODES, required=True)
        p.add_argument("--run-id", default=DEFAULT_RUN_ID)
        if name == "prepare":
            p.add_argument("--session-budget", type=int, default=None,
                           help=f"ACC-MCP tokens per session (default: {SESSION_BUDGET_SHARE:.0%} of the context window)")
            p.add_argument("--no-hint", action="store_true", help="acc mode without the usage hint")
            p.add_argument("--force", action="store_true", help="start this task over")
        else:
            p.add_argument("--notes", default="", help="what you observed")
    p = sub.add_parser("freeze", help="record the setup of a run id before its first counted run")
    p.add_argument("--run-id", default=DEFAULT_RUN_ID)
    p.add_argument("--force", action="store_true", help="replace an existing frozen setup")
    p = sub.add_parser("report", help="write eval/results.md")
    p.add_argument("--run-id", action="append", default=None,
                   help=f"run id to report; repeat for several runs (default: {DEFAULT_RUN_ID})")
    p = sub.add_parser("list")
    p.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args(argv)
    handlers = {"setup": cmd_setup, "validate": cmd_validate, "doctor": cmd_doctor, "freeze": cmd_freeze,
                "prepare": cmd_prepare, "collect": cmd_collect, "report": cmd_report, "list": cmd_list}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
