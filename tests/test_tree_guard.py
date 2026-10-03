"""The gate refuses to run against a `policyforge` that is not this tree's.

Run from a git worktree, `scripts/check.py` and a bare `pytest` used to
import the main checkout's source — the editable install pins it — and
report all-pass on a branch whose code was never loaded. Four sessions
accepted such a pass on one day. `scripts/tree_guard.py` turns that into a
refusal that names both paths and the invocation that fixes it.

Three claims: a foreign resolution is refused with the fix spelled out; a
resolution under this tree's `src/` is not; and this very run is one of the
latter, since these tests are themselves collected by the guarded
`conftest.py` and would not be running otherwise.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard():
    return _load("tree_guard")


def test_a_foreign_resolution_is_refused_naming_both_paths_and_the_fix(guard, tmp_path):
    tree = tmp_path / "worktree"
    elsewhere = tmp_path / "main" / "src" / "policyforge" / "__init__.py"

    message = guard.foreign_source(elsewhere, tree, invocation="python scripts/check.py")

    assert message is not None
    assert str(elsewhere) in message
    assert str(tree.resolve()) in message
    assert f"PYTHONPATH={tree.resolve() / 'src'} python scripts/check.py" in message
    assert "PowerShell" in message


def test_a_resolution_under_this_tree_is_not_refused(guard, tmp_path):
    tree = tmp_path / "worktree"
    own = tree / "src" / "policyforge" / "__init__.py"

    assert guard.foreign_source(own, tree, invocation="pytest") is None


def test_an_unimportable_package_is_refused_rather_than_ignored(guard, tmp_path):
    message = guard.foreign_source(None, tmp_path, invocation="pytest")

    assert message is not None
    assert "not importable" in message


def test_a_sibling_directory_with_the_same_prefix_is_still_foreign(guard, tmp_path):
    """`src2/` is not `src/`; the comparison is by path segment, not by string."""
    tree = tmp_path / "wt"
    lookalike = tmp_path / "wt" / "src2" / "policyforge" / "__init__.py"

    assert guard.foreign_source(lookalike, tree, invocation="pytest") is not None


def test_this_run_resolves_policyforge_inside_this_tree(guard):
    """The guard in conftest.py let this session start, so it must agree."""
    origin = guard.resolved_origin()

    assert origin is not None
    assert guard.foreign_source(origin, ROOT, invocation="pytest") is None


def test_the_gate_script_exits_two_before_running_anything(monkeypatch, tmp_path, capsys):
    """check.py refuses first, so no check is even attempted."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    check = _load("check")
    ran: list = []
    monkeypatch.setattr(check, "run", lambda label, cmd: ran.append(label) or True)
    monkeypatch.setattr(
        check.tree_guard,
        "resolved_origin",
        lambda name="policyforge": tmp_path / "elsewhere" / "src" / "policyforge" / "__init__.py",
    )

    code = check.main()

    assert code == 2
    assert ran == []
    assert "Refusing to run" in capsys.readouterr().err


def test_the_gate_script_runs_normally_when_the_tree_is_its_own(monkeypatch):
    """A main-checkout run is unaffected: the guard returns nothing and the
    checks proceed (faked here, so nothing is actually executed)."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    check = _load("check")
    ran: list = []
    monkeypatch.setattr(check, "run", lambda label, cmd: ran.append(label) or True)
    monkeypatch.setattr(check, "check_semgrep", lambda: None)
    monkeypatch.setattr(check, "check_gitleaks", lambda: None)
    monkeypatch.setattr(
        check.tree_guard,
        "resolved_origin",
        lambda name="policyforge": check.REPO_ROOT / "src" / "policyforge" / "__init__.py",
    )

    # Both optional tools are faked as absent above, and an unacknowledged
    # skip now fails the gate on its own (tests/test_check_gate.py). This
    # test is about the guard standing aside, so the skips are accepted
    # explicitly rather than the assertion being relaxed to let them pass.
    code = check.main(["--allow-skip", "semgrep", "--allow-skip", "gitleaks"])

    assert code == 0
    assert "pytest" in ran


def test_the_interpreter_agrees_with_find_spec():
    """`resolved_origin` predicts what an import would load."""
    guard = _load("tree_guard")
    import policyforge

    assert guard.resolved_origin() == Path(policyforge.__file__).resolve()
    assert "policyforge" in sys.modules
