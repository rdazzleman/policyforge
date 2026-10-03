"""`scripts/prompt_ledger_grows.py`: the prompt version ledger only grows (#424).

Every case runs against a real repository, so `git show` and `rev-parse`
are what is measured. The hand-edit case is the one #422 left to review.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import prompt_ledger_grows as grows

LEDGER = {
    "note": "n",
    "prompts": {
        "edit.apply": {"1": [{"fingerprint": "aaaaaaaaaaaa", "first_seen": "c1"}]},
        "edit.plan": {
            "2": [{"fingerprint": "bbbbbbbbbbbb", "first_seen": "c1"}],
            "3": [{"fingerprint": "cccccccccccc", "first_seen": "c2"}],
        },
    },
}
IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _repo(tmp_path: Path, ledger: dict | str | None) -> Path:
    """A repository whose `base` branch holds `ledger` (None: no file)."""
    subprocess.run(["git", "init", "-q", "-b", "base", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    if ledger is not None:
        path = tmp_path / grows.LEDGER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ledger if isinstance(ledger, str) else json.dumps(ledger), encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), *IDENT, "commit", "-qm", "base"], check=True)
    return tmp_path


def _head(root: Path, ledger: dict | None) -> None:
    path = root / grows.LEDGER
    if ledger is None:
        path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger), encoding="utf-8")


def _branch_then_grow_base(root: Path) -> None:
    """Branch `feature` off `base`, then add a version on `base` alone: the
    base has grown since the branch point."""
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "feature"], check=True)
    grown = copy.deepcopy(LEDGER)
    grown["prompts"]["edit.plan"]["4"] = [{"fingerprint": "dddddddddddd", "first_seen": "c9"}]
    blob = json.dumps(grown)
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "base"], check=True)
    (root / grows.LEDGER).write_text(blob, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), *IDENT, "commit", "-qam", "base grows"], check=True)
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "feature"], check=True)


def _run(root: Path, base: str = "base") -> tuple[int, list[str]]:
    lines: list[str] = []
    return grows.main(["--base", base], root=root, out=lines.append), lines


def test_a_new_version_passes(tmp_path):
    root = _repo(tmp_path, LEDGER)
    head = copy.deepcopy(LEDGER)
    head["prompts"]["edit.plan"]["4"] = [{"fingerprint": "dddddddddddd", "first_seen": "c3"}]
    head["prompts"]["new.prompt"] = {"1": [{"fingerprint": "eeeeeeeeeeee", "first_seen": "c3"}]}
    _head(root, head)
    code, lines = _run(root)
    assert code == 0, lines
    assert any("3 version(s) recorded there, 5 here, 0 changed or removed" in x for x in lines)


def test_the_hand_edit_422_left_to_review_is_refused(tmp_path):
    """A prompt's text changed at its version AND its recorded fingerprint
    rewritten to agree: the registry-vs-ledger test passes that; this does not."""
    root = _repo(tmp_path, LEDGER)
    head = copy.deepcopy(LEDGER)
    head["prompts"]["edit.plan"]["3"][0]["fingerprint"] = "ffffffffffff"
    _head(root, head)
    code, lines = _run(root)
    assert code == 1
    assert any("edit.plan v3" in line and "ffffffffffff" in line for line in lines), lines


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["edit.plan"]["2"][0].update(first_seen="elsewhere"),
        lambda p: p["edit.plan"].pop("2"),
        lambda p: p.pop("edit.apply"),
    ],
    ids=["first_seen rewritten", "a version removed", "a prompt removed"],
)
def test_anything_recorded_that_changes_or_goes_is_refused(tmp_path, mutate):
    root = _repo(tmp_path, LEDGER)
    head = copy.deepcopy(LEDGER)
    mutate(head["prompts"])
    _head(root, head)
    code, lines = _run(root)
    assert code == 1, lines


def test_a_base_that_does_not_resolve_is_no_answer_not_a_pass(tmp_path):
    root = _repo(tmp_path, LEDGER)
    code, lines = _run(root, base="origin/no-such-branch")
    assert code == 2 and lines[0].startswith("NO ANSWER"), lines
    # The diagnosis a person acts on: fetch the base. Without the resolve
    # check the merge-base step still exits 2, but says "share no history",
    # which points at the wrong fix.
    assert "does not resolve" in lines[0] and "fetch" in lines[1], lines


def test_a_base_ledger_that_is_not_a_ledger_is_no_answer(tmp_path):
    root = _repo(tmp_path, "{not json")
    _head(root, LEDGER)
    code, lines = _run(root)
    assert code == 2 and any(x.startswith("NO ANSWER") for x in lines), lines


def test_a_base_with_no_ledger_passes_and_says_so(tmp_path):
    """`main` before #236 reaches it: a readable fact, not a missing answer."""
    root = _repo(tmp_path, None)
    _head(root, LEDGER)
    code, lines = _run(root)
    assert code == 0 and any("nothing recorded there to hold" in x for x in lines), lines


def test_a_ledger_deleted_here_is_refused(tmp_path):
    root = _repo(tmp_path, LEDGER)
    _head(root, None)
    code, lines = _run(root)
    assert code == 1 and any("gone here" in x for x in lines), lines


def test_a_base_that_grew_after_the_branch_point_is_not_a_removal(tmp_path):
    """policyforge-ba on #435: against the base's TIP, a version added there
    after this branch forked read as "removed here", a false alarm."""
    root = _repo(tmp_path, LEDGER)
    _branch_then_grow_base(root)
    code, lines = _run(root)
    assert code == 0, lines
    assert "branch point" in lines[0]


def test_a_hand_edit_is_still_refused_after_the_base_grew(tmp_path):
    """The branch point must not blind it: the edit is this branch's."""
    root = _repo(tmp_path, LEDGER)
    _branch_then_grow_base(root)
    head = copy.deepcopy(LEDGER)
    head["prompts"]["edit.plan"]["3"][0]["fingerprint"] = "ffffffffffff"
    _head(root, head)
    code, lines = _run(root)
    assert code == 1 and any("edit.plan v3" in line for line in lines), lines


def test_a_base_sharing_no_history_is_no_answer(tmp_path):
    """policyforge-9b on #435: an unrelated base resolves, but there is no
    branch point to compare with, so nothing was compared (#436)."""
    root = _repo(tmp_path, LEDGER)
    tree = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    orphan = subprocess.run(
        ["git", "-C", str(root), *IDENT, "commit-tree", tree, "-m", "unrelated"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    code, lines = _run(root, base=orphan)
    assert code == 2, lines
    assert any("share no history" in line for line in lines), lines
