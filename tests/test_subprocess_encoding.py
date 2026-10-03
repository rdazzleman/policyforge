"""Every subprocess call that decodes text names its encoding (#285, #252).

`subprocess.run(..., text=True)` with no `encoding` decodes with the locale
codec -- cp1252 on most Windows installs. A byte cp1252 leaves undefined
(0x81 0x8D 0x8F 0x90 0x9D) kills the reader thread, so `stdout` is `None`
and the caller fails somewhere else; any other non-ASCII byte decodes
silently wrong. #252 fixed `release_check.py` and guarded that one file,
which is how the verdict reader (#285) kept the same defect: **a guard
written while fixing one case covers only that case.**

So this derives its population from every tracked `.py`, not from a list
of files someone thought of.
"""

from __future__ import annotations

import ast
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Calls that start a child and can decode its output.
ENTRY_POINTS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
#: These always decode with the locale codec and take no `encoding`, so any
#: use is a violation.
ALWAYS_LOCALE = frozenset({"getoutput", "getstatusoutput"})

#: Product sites carried by #287, keyed by (path, enclosing function) with a
#: COUNT, so an exemption covers those calls and not the function. EMPTY since
#: #287 fixed all eight (policyforge-ba): each now decodes UTF-8 -- strictly
#: in the caller where the output is data, with `replace` where it is only
#: shown, or not at all where only a return code is read. Kept, not deleted:
#: the next deferral goes here, and the stale check still covers it.
DEFERRED_TO_287: Counter[tuple[str, str]] = Counter()


def _tracked_python() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.split()
    assert len(out) > 100, f"only {len(out)} tracked .py found; the population is wrong"
    return out


def _decodes_without_encoding(node: ast.Call) -> bool:
    fn = node.func
    if not (isinstance(fn, ast.Attribute) and getattr(fn.value, "id", None) == "subprocess"):
        return False
    if fn.attr in ALWAYS_LOCALE:
        return True
    if fn.attr not in ENTRY_POINTS:
        return False
    keywords = {k.arg: k.value for k in node.keywords if k.arg}
    texty = any(
        isinstance(keywords.get(name), ast.Constant) and keywords[name].value is True
        for name in ("text", "universal_newlines")
    )
    # `encoding=None` is the locale codec by another spelling (ba, on #290).
    encoding = keywords.get("encoding")
    named = encoding is not None and not (
        isinstance(encoding, ast.Constant) and encoding.value is None
    )
    return texty and not named


def violations(source: str, path: str) -> Counter[tuple[str, str]]:
    """How many offending calls sit in each (path, innermost function).

    COUNTED, not a set (ba, on #290): an exemption keyed by function used to
    cover any number of calls inside it, so a NEW locale-decoding call added
    to an exempted function passed unseen."""
    tree = ast.parse(source)
    found: Counter[tuple[str, str]] = Counter()

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = function
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call) and _decodes_without_encoding(child):
                found[(path, name)] += 1
            visit(child, name)

    visit(tree, "<module>")
    return found


def test_no_subprocess_call_decodes_with_the_locale_codec():
    found: Counter[tuple[str, str]] = Counter()
    for path in _tracked_python():
        found += violations((ROOT / path).read_text(encoding="utf-8"), path)

    new = found - DEFERRED_TO_287
    assert not new, (
        "subprocess output decoded with the locale codec (add encoding='utf-8', "
        f"errors='replace'): {sorted(new.items())}"
    )
    stale = DEFERRED_TO_287 - found
    assert not stale, (
        f"fixed, so lower or remove in DEFERRED_TO_287 (and tick #287): {sorted(stale.items())}"
    )


def test_the_subprocess_module_is_never_aliased():
    """The guard matches `subprocess.<name>`. `from subprocess import run` or
    `import subprocess as sp` would put a call outside it, so they are
    refused rather than left as a blind spot."""
    aliased = []
    for path in _tracked_python():
        for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                aliased.append(f"{path}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess" and alias.asname:
                        aliased.append(f"{path}:{node.lineno}")
    assert not aliased, f"subprocess imported under another name: {aliased}"


# --- the detector itself: one must-fail arm and one passing twin per rule ----


def test_the_detector_catches_each_shape():
    cases = {
        "subprocess.run(['x'], text=True)": True,
        "subprocess.check_output(['x'], universal_newlines=True)": True,
        "subprocess.Popen(['x'], stdout=1, text=True)": True,
        "subprocess.getoutput('x')": True,
        "subprocess.run(['x'], text=True, encoding=None)": True,  # the locale, spelled out
        "subprocess.run(['x'], text=True, encoding='utf-8')": False,
        "subprocess.run(['x'], capture_output=True)": False,  # bytes: nothing decoded
        "subprocess.run(['x'], text=False)": False,
        "other.run(['x'], text=True)": False,
    }
    for source, expected in cases.items():
        got = bool(violations(f"import subprocess\n{source}\n", "p.py"))
        assert got is expected, f"{source!r}: detector said {got}, expected {expected}"


def test_a_violation_is_keyed_by_its_innermost_function():
    source = (
        "import subprocess\n"
        "def outer():\n"
        "    def inner():\n"
        "        subprocess.run(['x'], text=True)\n"
        "    return inner\n"
    )
    assert violations(source, "p.py") == Counter({("p.py", "inner"): 1})


def test_a_second_call_in_an_exempted_function_is_still_counted():
    """ba's arm on #290: the same function holding two offending calls
    counts 2, so an exemption for 1 does not cover the second."""
    source = (
        "import subprocess\n"
        "def git():\n"
        "    subprocess.run(['a'], text=True)\n"
        "    subprocess.run(['b'], text=True)\n"
    )
    found = violations(source, "p.py")
    assert found == Counter({("p.py", "git"): 2})
    assert found - Counter({("p.py", "git"): 1}) == Counter({("p.py", "git"): 1})
