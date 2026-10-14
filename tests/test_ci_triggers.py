"""CI runs once per PR commit: push only where PRs merge in (#80).

The workflow fired on every `push` AND on `pull_request`, so a commit on a PR
branch ran the whole suite twice (64 of 148 commits, 9b measured it). On
Forgejo both runs test the PR head, the same tree. These tests pin the
trigger so an edit that drops the branch filter (and doubles the load again)
fails here, not on the runner.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

#: Reads GitHub's config, which the public snapshot leaves out (161).
pytestmark = pytest.mark.github_config

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _triggers() -> dict:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    # YAML 1.1 reads a bare `on` key as the boolean True.
    return workflow.get("on", workflow.get(True))


def test_push_runs_only_on_the_branches_prs_merge_into():
    """Written from where PRs land (the train and main), not read back from
    the file: a push to a PR branch is covered by its pull_request run."""
    assert _triggers()["push"] == {"branches": ["main", "release/**"]}


def test_every_pr_commit_still_runs_on_pull_request():
    """What must stay: the pull_request run is now the only run a PR branch
    gets, so each new head (`synchronize`) has to trigger it."""
    pull_request = _triggers()["pull_request"]
    assert {"opened", "synchronize", "reopened"} <= set(pull_request["types"])


def test_no_other_event_triggers_the_suite():
    """A new event (a schedule, workflow_dispatch) is a decision, not a drift."""
    assert set(_triggers()) == {"push", "pull_request"}


# ---- the pip cache (#80, fix 2) ---------------------------------------------


def _scan_and_test_steps() -> list[dict]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return workflow["jobs"]["scan-and-test"]["steps"]


def test_the_pip_cache_is_keyed_on_every_lock_the_job_installs():
    """The expected set comes from the OTHER side: the `-r <lock>` of every
    pip install the job runs, not the cache's own list. So a lock added to an
    install and not to the key fails here, rather than restoring a cache
    keyed on an older set."""
    runs = "\n".join(step.get("run", "") for step in _scan_and_test_steps())
    installed = set(re.findall(r"pip\"?\s+install\b[^\n]*?-r\s+(\S+)", runs))
    assert installed, "found no hashed install: the pattern no longer reads the job"
    setup = next(s for s in _scan_and_test_steps() if "setup-python" in s.get("uses", ""))
    options = setup["with"]
    assert options.get("cache") == "pip"
    assert set(options["cache-dependency-path"].split()) == installed


def test_every_install_the_cache_feeds_still_requires_hashes():
    """The cache holds downloads, not trust: every hashed install must keep
    `--require-hashes`, so a cached wheel is checked like a fresh one."""
    runs = [s.get("run", "") for s in _scan_and_test_steps()]
    installs = [
        line.strip()
        for run in runs
        for line in run.splitlines()
        if re.search(r"pip\"?\s+install\b", line) and " -r " in line
    ]
    assert installs
    assert all("--require-hashes" in line for line in installs), installs


def test_every_cached_install_refuses_to_build_from_source():
    """ba on #88: pip caches a wheel it BUILDS from a source archive and
    doesn't re-verify it against the lock. `--only-binary :all:` means no
    such wheel exists here, so the cache holds only hash-checked downloads."""
    runs = [s.get("run", "") for s in _scan_and_test_steps()]
    installs = [
        line.strip()
        for run in runs
        for line in run.splitlines()
        if re.search(r"pip\"?\s+install\b", line) and " -r " in line
    ]
    assert installs
    assert all("--only-binary :all:" in line for line in installs), installs
