"""The `slow` set is measured, applied from one list, and still run by the
gate and CI (#292).

`slow` exists so carriers and readers can leave the slowest tests out of the
files outside their change. That makes it a place a test could be hidden, so
every way into it is closed except a measured time: scripts/slow_tests.py
writes tests/slow_tests.txt from a serial run's junit XML, conftest marks
what it lists, and nothing else marks a test slow.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import slow_tests as st

ROOT = Path(__file__).resolve().parents[1]


def _junit(tmp_path: Path, *cases: str) -> Path:
    path = tmp_path / "pytest.xml"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        + "".join(cases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def _case(classname: str, name: str, seconds: float, *, skipped: bool = False) -> str:
    inner = '<skipped type="pytest.skip" message="x"/>' if skipped else ""
    return f'<testcase classname="{classname}" name="{name}" time="{seconds}">{inner}</testcase>'


# ---- the list is derived, never written ------------------------------------------


def test_the_list_takes_every_case_past_the_margin_and_none_near_the_line(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    junit = _junit(
        tmp_path,
        _case("tests.test_a", "test_under_the_line", 4.99),
        _case("tests.test_a", "test_on_the_line", 5.0),
        _case("tests.test_a", "test_inside_the_margin", 5.99),
        _case("tests.test_a", "test_past_the_margin", 6.0),
        _case("tests.test_a.TestGroup", "test_in_a_class[p-1]", 12.5),
        _case("tests.test_a", "test_skipped_but_slow", 30.0, skipped=True),
    )

    rows = st.measured(junit, root=tmp_path)

    assert rows == [
        (12.5, "tests/test_a.py::TestGroup::test_in_a_class[p-1]"),
        (6.0, "tests/test_a.py::test_past_the_margin"),
    ]
    written = tmp_path / "slow.txt"
    written.write_text(st.render(rows, "a test run"), encoding="utf-8")
    assert st.read(written) == {node: seconds for seconds, node in rows}


def test_every_listed_test_carries_a_measured_time_past_the_margin():
    text = st.LIST.read_text(encoding="utf-8")
    assert "# Source: " in text, "the list names the run it came from"
    listed = st.read()
    assert st.CUT == st.THRESHOLD * 1.2 == 6.0, "5 s and a 20% margin (80 on #292)"
    under = {node: seconds for node, seconds in listed.items() if seconds < st.CUT}
    assert not under, f"listed slow below {st.CUT:g} s: {under}"


def _defined(path: Path) -> set[str]:
    """`name` and `Class::name` for every test function in a module."""
    names = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}::{inner.name}")
    return names


def test_every_listed_test_is_one_that_exists():
    """By module and function. A parametrisation id that has gone is not
    caught here: the marking test below collects only two modules."""
    missing = []
    for node in st.read():
        module, _, rest = node.partition("::")
        path = ROOT / module
        if not path.is_file() or re.sub(r"\[.*\]$", "", rest) not in _defined(path):
            missing.append(node)
    assert not missing, f"listed slow, but no such test: {missing}"


def test_no_test_marks_itself_slow():
    written = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "slow"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "mark"
            ):
                written.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert written == ["tests/conftest.py:" + str(_conftest_mark_line())], written


def _conftest_mark_line() -> int:
    for number, line in enumerate(
        (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8").splitlines(), 1
    ):
        if "item.add_marker(pytest.mark.slow)" in line:
            return number
    raise AssertionError("conftest no longer marks the listed tests")


# ---- conftest applies it, and the gate and CI still run it ------------------------


def test_conftest_marks_exactly_the_listed_tests_in_two_modules():
    modules = ["tests/test_publish_public.py", "tests/test_litellm_map.py"]
    listed = {node for node in st.read() if node.split("::")[0] in modules}
    assert len(listed) >= 2, "premise: both modules have listed tests"

    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-m", "slow", *modules],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )  # fmt: skip

    collected = {line.strip() for line in done.stdout.splitlines() if "::" in line}
    assert collected == listed, done.stdout[-2000:] + done.stderr[-2000:]


@pytest.mark.github_config
def test_the_batch_gate_and_ci_run_the_slow_tests_too():
    """Only carriers and readers leave them out. A `-m` on either full run
    would drop them from the one place they still run."""
    check = (ROOT / "scripts" / "check.py").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    gate_args = re.search(r'_tool\("pytest",([^)]*)\)', check)
    assert gate_args, "check.py's pytest step moved: find its arguments again"
    assert '"-m"' not in gate_args.group(1)
    ci_runs = re.findall(r"^\s*run: pytest\b.*$", ci, re.MULTILINE)
    # scan-and-test, the platform legs, the Python legs and the unpinned leg (#380).
    assert len(ci_runs) == 4, ci_runs
    assert not [run for run in ci_runs if re.search(r"\s-m\s", run)], ci_runs
    assert "not slow" not in check and "not slow" not in ci
