"""conftest's public-snapshot test (161): the tests marked `github_config` are
deselected only in a tree with NEITHER GitHub file, so a tree that lost one by
accident still runs them and fails on what it lost (b5 on PR 459)."""

from __future__ import annotations

import pytest

from tests.conftest import public_snapshot


@pytest.mark.parametrize(
    "present, snapshot",
    [
        ((), True),
        ((".github/workflows/",), False),
        ((".github/dependabot.yml",), False),
        ((".github/dependabot.yaml",), False),
        ((".github/workflows/", ".github/dependabot.yml"), False),
        ((".github/PULL_REQUEST_TEMPLATE.md",), True),
    ],
)
def test_only_a_tree_with_neither_is_the_public_snapshot(tmp_path, present, snapshot):
    for path in present:
        target = tmp_path / path
        if path.endswith("/"):
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x\n", encoding="utf-8")
    assert public_snapshot(tmp_path) is snapshot
