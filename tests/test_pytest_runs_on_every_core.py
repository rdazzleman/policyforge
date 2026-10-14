"""The suite runs on every core, in the gate and in CI alike (#86).

pytest on one core left the machine at about a fifth of its CPU while a gate
ran for minutes (80, 2026-10-04). `pytest-xdist` with `-n auto` fixes that,
but only if the gate and CI both pass it: a gate that ran serially while CI
ran in parallel would test a different order from the one CI does, which
is the gap `scripts/check.py` exists to close.

The population is derived from the workflow files, not listed by hand: every
`run:` line that invokes pytest must pass `-n auto`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
#: A `run:` step whose command is pytest itself (`pytest …` or `python -m pytest …`).
_RUNS_PYTEST = re.compile(r"^\s*run:\s*(?:python\s+-m\s+)?pytest\b(.*)$")


def _pytest_runs() -> list[tuple[str, str]]:
    found = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            match = _RUNS_PYTEST.match(line)
            if match:
                found.append((workflow.name, match.group(0).strip()))
    return found


def _passes_n_auto(command: str) -> bool:
    return re.search(r"(?:^|\s)-n\s+auto(?:\s|$)", command) is not None


@pytest.mark.github_config
def test_ci_has_pytest_runs_to_check():
    # The extent: ci.yml's scan-and-test job and its platform matrix today.
    assert len(_pytest_runs()) >= 2, _pytest_runs()


def test_every_ci_pytest_run_uses_every_core():
    missing = [run for run in _pytest_runs() if not _passes_n_auto(run[1])]
    assert not missing, f"pytest steps without -n auto: {missing}"


def test_the_gate_passes_n_auto_to_pytest():
    source = (ROOT / "scripts" / "check.py").read_text(encoding="utf-8")
    calls = re.findall(r'_tool\(\s*"pytest",(.*?)\)', source, flags=re.DOTALL)
    assert calls, "check.py no longer runs pytest through _tool"
    assert all('"-n", "auto"' in call for call in calls), calls


def test_the_docker_replica_of_ci_uses_every_core():
    """`scripts/ci_in_docker.py` replays CI's steps in a container; its pytest
    step passes `-n auto` too (b5 on PR 260)."""
    source = (ROOT / "scripts" / "ci_in_docker.py").read_text(encoding="utf-8")
    steps = re.findall(r'^step "pytest" (.*)$', source, flags=re.MULTILINE)
    assert steps, "ci_in_docker.py no longer has a pytest step"
    assert all(_passes_n_auto(step) for step in steps), steps


def test_xdist_is_declared_and_locked():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"pytest-xdist>=', pyproject), "pytest-xdist isn't in the dev extra"
    lock = (ROOT / "requirements" / "ci.txt").read_text(encoding="utf-8")
    assert re.search(r"^pytest-xdist==", lock, flags=re.MULTILINE), "pytest-xdist isn't locked"


def test_the_matcher_tells_a_parallel_run_from_a_serial_one():
    """Both arms through the same matcher, from hand-written commands."""
    assert _passes_n_auto("pytest -q -rs -n auto --junitxml=x.xml")
    assert _passes_n_auto("pytest -n auto")
    assert not _passes_n_auto("pytest -q -rs")
    assert not _passes_n_auto("pytest -q -rs -n 4")
    assert not _passes_n_auto("pytest -q --name-auto")
    assert _RUNS_PYTEST.match("        run: python -m pytest -q")
    assert not _RUNS_PYTEST.match("        run: python scripts/skip_gate.py pytest.xml")
