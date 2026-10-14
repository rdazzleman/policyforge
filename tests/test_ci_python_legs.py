"""The Python legs and the fresh resolve on the private test repo (#380).

`requires-python` claims >=3.10, and per change only 3.12 (CI) and 3.14 (the
local gates) are tested. These jobs run the suite on every claimed version
from the hashed locks, plus one fresh unpinned resolve, on GitHub's runners,
and only for a candidate pushed to rdazzleman/policyforge-ci. These tests pin
what the jobs install, what they run, and where they run, since none of that
can be run here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402

#: Reads GitHub's config, which the public snapshot leaves out (161).
pytestmark = pytest.mark.github_config

#: Written out from `requires-python` and the CPython releases supported today,
#: not read back from ci.yml.
PYTHONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]
LEGS = ("pytest-pythons", "pytest-unpinned")


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _runs(job: str) -> dict[str, str]:
    return {s.get("name", ""): s.get("run", "") for s in _jobs()[job]["steps"]}


def _holds(guard: str, **github: str) -> bool:
    """The guard's value for one context. Only the shape these jobs use: terms
    joined by `&&`, each `github.X == 'v'`, `startsWith(github.X, 'v')` or
    `endsWith(github.X, 'v')`. Anything else raises, so a reshaped guard fails
    here and isn't misread."""
    body = guard.strip()
    assert body.startswith("${{") and body.endswith("}}"), guard
    value = True
    for term in body[3:-2].split("&&"):
        term = term.strip()
        if found := re.fullmatch(r"github\.(\w+) == '([^']*)'", term):
            value = value and github[found[1]] == found[2]
        elif found := re.fullmatch(r"startsWith\(github\.(\w+), '([^']*)'\)", term):
            value = value and github[found[1]].startswith(found[2])
        elif found := re.fullmatch(r"endsWith\(github\.(\w+), '([^']*)'\)", term):
            value = value and github[found[1]].endswith(found[2])
        else:
            raise AssertionError(f"a guard term this reader doesn't know: {term!r}")
    return value


#: Where ci.yml runs, and whether these jobs must run there.
CONTEXTS = [
    (
        "a candidate on the private test repo",
        "rdazzleman/policyforge-ci",
        "push",
        "refs/heads/release/1.8.0",
        True,
    ),
    (
        "the public repo's release push",
        "rdazzleman/policyforge",
        "push",
        "refs/heads/release/1.8.0",
        False,
    ),
    ("the public repo's main", "rdazzleman/policyforge", "push", "refs/heads/main", False),
    (
        "the team's Forgejo train",
        "policyforge/policyforge",
        "push",
        "refs/heads/release/1.8",
        False,
    ),
    (
        "a Forgejo pull request",
        "policyforge/policyforge",
        "pull_request",
        "refs/pull/380/head",
        False,
    ),
    # The owner has moved once already (#244): the fence mustn't depend on it.
    (
        "a candidate after another transfer",
        "someone/policyforge-ci",
        "push",
        "refs/heads/release/1.8.0",
        True,
    ),
    ("the test repo's main", "rdazzleman/policyforge-ci", "push", "refs/heads/main", False),
    (
        "a pull request on the test repo",
        "rdazzleman/policyforge-ci",
        "pull_request",
        "refs/pull/1/head",
        False,
    ),
]


@pytest.mark.parametrize("job", LEGS)
@pytest.mark.parametrize("where, repository, event, ref, runs", CONTEXTS)
def test_the_legs_run_only_for_a_candidate_on_the_private_test_repo(
    job, where, repository, event, ref, runs
):
    guard = _jobs()[job]["if"]
    assert _holds(guard, repository=repository, event_name=event, ref=ref) is runs, where


def test_the_matrix_is_every_python_requires_python_claims():
    assert _jobs()["pytest-pythons"]["strategy"]["matrix"]["python"] == PYTHONS
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'requires-python = ">=(3\.\d+)"', pyproject)[1] == PYTHONS[0]
    assert _jobs()["pytest-pythons"]["strategy"]["fail-fast"] is False


def test_each_python_installs_its_own_hashed_lock():
    install = _runs("pytest-pythons")["Install project"]
    assert "lock=requirements/ci.txt" in install
    assert 'if [ "$PYTHON" = "3.10" ]; then lock=requirements/ci-py310.txt; fi' in install
    pip = [line.strip() for line in install.splitlines() if re.search(r"\bpip install\b", line)]
    assert pip == [
        'pip install --require-hashes --only-binary :all: -r "$lock"',
        "pip install --require-hashes --only-binary :all: -r requirements/build.txt",
        "pip install --no-deps --no-build-isolation -e .",
    ]
    for lock in ("requirements/ci.txt", "requirements/ci-py310.txt"):
        assert (ROOT / lock).is_file(), lock


def test_the_unpinned_leg_resolves_fresh_with_the_locks_extras_and_names_what_it_got():
    install = _runs("pytest-unpinned")["Install project (fresh resolve, no lock)"]
    assert f'pip install -e ".[{release.CUT_EXTRAS}]"' in install
    assert "-r " not in install and "--require-hashes" not in install, "the point is the resolve"
    assert "pip freeze" in install


@pytest.mark.parametrize("job", LEGS)
def test_each_leg_runs_cis_own_pytest_line(job):
    """The same line as scan-and-test, read from it: the other side."""
    assert _runs(job)["pytest"].strip() == _runs("scan-and-test")["pytest"].strip()


def test_the_hashed_legs_gate_their_skips_as_ci_does():
    ours, theirs = _runs("pytest-pythons"), _runs("scan-and-test")
    assert ours["expected skips only"].strip() == theirs["expected skips only"].strip()


def test_the_unpinned_leg_has_no_skip_gate():
    """On purpose (measured on #380's PR): a fresh resolve brings a newer
    LiteLLM, whose version-pinned tests skip, 6 of them. The gate would turn
    the leg red on every LiteLLM release, which says nothing about users. A
    failed test is the blocker, and -rs names every skip in the log."""
    runs = _runs("pytest-unpinned")
    assert "expected skips only" not in runs
    assert not any("skip_gate" in run for run in runs.values())
    assert " -rs " in f" {runs['pytest']} "


def test_every_gitleaks_pin_in_ci_yml_is_the_same():
    """Three jobs install gitleaks. One bump must move all of them."""
    pins = [
        (str(step["env"]["GITLEAKS_VERSION"]), step["env"]["GITLEAKS_SHA256"])
        for job in _jobs().values()
        for step in job.get("steps", [])
        if "GITLEAKS_VERSION" in step.get("env", {})
    ]
    assert len(pins) == 3, pins
    assert len(set(pins)) == 1, pins
