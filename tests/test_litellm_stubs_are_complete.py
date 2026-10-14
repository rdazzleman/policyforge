"""Every test that stands a fake `litellm` in `sys.modules` makes it complete
for `llm.litellm_map.load()`, and resets load()'s once-per-process guard.

Three files once didn't (#248; #86's isolation audit found two more). Each
passed only when an earlier test in the same process had already run the real
`load()`. Alone, or first on a pytest-xdist worker, `load()` reached the stub
and called a `register_model` it didn't have. Under `-n auto` which test runs
first on a worker varies, so a file like that goes red intermittently.

The population is derived: every `tests/*.py` that calls
`<x>.setitem(sys.modules, "litellm", …)`, found by parsing, not by grep.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _stubs_litellm(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setitem"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr == "modules"
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "litellm"
        ):
            return True
    return False


def _resets_the_guard(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
            and len(node.args) >= 3
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "_registered"
            and isinstance(node.args[2], ast.Constant)
            and node.args[2].value is None
        ):
            return True
    return False


def _gives_register_model(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "register_model" for t in node.targets)
        for node in ast.walk(tree)
    )


def _files() -> dict[str, ast.AST]:
    return {
        path.name: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(TESTS.glob("test_*.py"))
    }


def test_the_population_is_found():
    stubbing = [name for name, tree in _files().items() if _stubs_litellm(tree)]
    # The extent: the five files that stub it today.
    assert len(stubbing) >= 5, stubbing


def test_every_litellm_stub_is_complete_and_resets_the_guard():
    incomplete = [
        name
        for name, tree in _files().items()
        if _stubs_litellm(tree) and not (_resets_the_guard(tree) and _gives_register_model(tree))
    ]
    assert not incomplete, (
        f"these stand a litellm in sys.modules without register_model or a reset of "
        f"litellm_map._registered, so they pass only after a real load(): {incomplete}"
    )


def test_the_checks_tell_a_complete_stub_from_an_incomplete_one():
    """Both arms through the same three checks, from hand-written source."""
    complete = ast.parse(
        "def f(monkeypatch, litellm_map, stub):\n"
        "    stub.register_model = lambda m: None\n"
        "    monkeypatch.setitem(sys.modules, 'litellm', stub)\n"
        "    monkeypatch.setattr(litellm_map, '_registered', None)\n"
    )
    bare = ast.parse(
        "def f(monkeypatch, stub):\n    monkeypatch.setitem(sys.modules, 'litellm', stub)\n"
    )
    other = ast.parse(
        "def f(monkeypatch, stub):\n    monkeypatch.setitem(sys.modules, 'anthropic', stub)\n"
    )
    assert _stubs_litellm(complete) and _resets_the_guard(complete)
    assert _gives_register_model(complete)
    assert _stubs_litellm(bare) and not _resets_the_guard(bare)
    assert not _gives_register_model(bare)
    assert not _stubs_litellm(other)
