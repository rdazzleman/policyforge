"""Refuse to gate code that is not the tree the gate was run from.

The development install is editable: `pip install -e .` writes the absolute
path of *that* checkout's `src/` into the virtualenv, and every interpreter
from that virtualenv imports `policyforge` from there. That is the right
behaviour in the checkout it was made from and a silent lie in any other.
Run `scripts/check.py` or a bare `pytest` inside a git worktree, and the
tests import the main checkout's source while reporting on the worktree's
tests: a branch's changes are never loaded, and the run says all-pass for
code it did not see.

Four sessions accepted such a pass on the same day before one of them
noticed. The fix for the run is `PYTHONPATH=<tree>/src`, which puts the
tree's source ahead of the pinned path; the fix for the trap is here — a
run whose `policyforge` resolves outside the tree it was invoked from stops
with a non-zero exit and says what to type, rather than passing quietly.

Shared by `scripts/check.py` and `tests/conftest.py`, since pytest is what
people reach for between full gates and so is the path most likely to lie.
Deliberately not a fix for the virtualenv or the `.pth`: the editable
install is correct for the checkout it was made in, and the guard is the
deliverable.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def resolved_origin(name: str = "policyforge") -> Path | None:
    """Where `name` would be imported from in this interpreter, or None.

    `find_spec` rather than an import: the answer is wanted before anything
    from the package has been loaded, and resolving a top-level name
    imports nothing.
    """
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).resolve()


def foreign_source(origin: Path | None, root: Path, *, invocation: str) -> str | None:
    """The refusal to print when `origin` is not under `root/src`, else None.

    `invocation` is how the caller was run (`python scripts/check.py`,
    `pytest`), so the message can say exactly what to type instead.
    """
    root = root.resolve()
    expected = root / "src"
    if origin is not None and _is_under(origin, expected):
        return None

    found = str(origin) if origin is not None else "nowhere (policyforge is not importable)"
    src = str(expected)
    return (
        f"Refusing to run: `policyforge` resolves to\n"
        f"    {found}\n"
        f"but this run was started from\n"
        f"    {root}\n"
        f"so the checks would gate code that is not this tree. This happens in a git\n"
        f"worktree: the editable install pins the checkout it was made from. Put this\n"
        f"tree's source first on the path and run again:\n"
        f"    PYTHONPATH={src} {invocation}\n"
        f'    $env:PYTHONPATH = "{src}"; {invocation}        (PowerShell)\n'
    )


def _is_under(path: Path, ancestor: Path) -> bool:
    try:
        path.relative_to(ancestor)
    except ValueError:
        return False
    return True
