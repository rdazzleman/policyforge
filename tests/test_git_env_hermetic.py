"""The suite ignores the developer's git environment (#45).

git's environment overrides its config. A session that exported its bot
identity to commit (as 1d asked each session to on Forgejo) turned two
author-asserting tests red, and a GIT_DIR pointing elsewhere broke them the
same way. `conftest.py` removes every GIT_* variable at import; this runs the
two tests that assert on a commit's author in a child pytest with the
variables exported, which is the path a developer's gate takes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

AUTHOR_TESTS = [
    "tests/test_child_output.py::test_the_wiki_log_reads_a_non_ascii_author",
    "tests/test_github_wiki.py::test_a_hand_edit_on_the_wiki_is_reported_with_who_made_it",
]

IDENTITY = {
    "GIT_AUTHOR_NAME": "policyforge-1d",
    "GIT_AUTHOR_EMAIL": "policyforge-1d@noreply.localhost",
    "GIT_COMMITTER_NAME": "policyforge-1d",
    "GIT_COMMITTER_EMAIL": "policyforge-1d@noreply.localhost",
}


def _child_pytest(extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *AUTHOR_TESTS],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@pytest.mark.parametrize(
    "exported",
    [IDENTITY, {"GIT_AUTHOR_NAME": "someone else"}, {"GIT_DIR": "no-such-repository"}],
    ids=["bot-identity", "author-name-alone", "git-dir-elsewhere"],
)
def test_an_exported_git_variable_does_not_change_the_suite(exported, tmp_path):
    if "GIT_DIR" in exported:
        exported = {"GIT_DIR": str(tmp_path / exported["GIT_DIR"])}
    result = _child_pytest(exported)
    tail = result.stdout.strip().splitlines()[-1:] or [result.stderr[-300:]]
    assert result.returncode == 0, tail
    assert "2 passed" in tail[0], tail


def test_no_git_variable_survives_into_the_session_except_terminal_prompt():
    survivors = [k for k in os.environ if k.upper().startswith("GIT_")]
    assert survivors == ["GIT_TERMINAL_PROMPT"]
    assert os.environ["GIT_TERMINAL_PROMPT"] == "0"
