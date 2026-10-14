"""Every `git archive` the tooling runs keeps the tree's stored line endings (#171).

A host `git archive` converts line endings unless told not to, twice over:
`core.autocrlf` (true system-wide on Git for Windows) and, for any path
`.gitattributes` marks `text`, `core.eol` (native: CRLF on Windows; b5 on
#206). #158 fixed one call site and #171 a second, each by hand. This holds
the CLASS (9b on #206): every argument list under `scripts/` and `src/` that
runs `git ... archive` carries both `-c core.autocrlf=false` and
`-c core.eol=lf`. A new call site without them fails here, as does an old one
that loses either.

The population is every tracked `.py` file under `scripts/` and `src/`, read
as Python (`ast`). A git archive is either a list or tuple literal holding the
strings "git" and "archive", or a call to a git wrapper (`git`, `_git`,
`ctx.git`, anything named `*_git`) with "archive" among its constant
arguments (b5 on #206). A literal holding "archive" without a constant "git"
(`[GIT, "archive", ...]`) is reported as unclassified, so it fails rather
than passing (9b on #206). What it can't see: an archive command built from
a string, from a list assembled at run time, or through a wrapper named
anything else. None exists (measured when this was written), and the extent
test below says which sites it found.

**Scope: line endings only** (9b on #206). Other attributes also change the
bytes `git archive` writes: `ident`, `working-tree-encoding`, `filter`
(clean/smudge) and `export-subst`. None is set in this tree's
`.gitattributes` today. This file holds only the two line-ending settings.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEEDED = ("core.autocrlf=false", "core.eol=lf")


def _strings(nodes) -> list[str]:
    return [n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _callee(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _is_git_wrapper(name: str) -> bool:
    """`git`, `_git`, `ctx.git`, `run_git`, ...: a helper that runs git with
    the arguments it's given (b5 on #206: eight exist under scripts/ and src/)."""
    return name in {"git", "_git"} or name.endswith("_git")


def _archive_calls(source: str) -> list[tuple[int, list[str]]]:
    """(line, the literal strings) of every git archive the source spells out:
    a list or tuple literal holding both "git" and "archive", or a call to a
    git wrapper with "archive" among its constant arguments."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple)):
            strings = _strings(node.elts)
            if "git" in strings and "archive" in strings:
                found.append((node.lineno, strings))
        elif isinstance(node, ast.Call) and _is_git_wrapper(_callee(node)):
            strings = _strings(node.args)
            if "archive" in strings:
                found.append((node.lineno, strings))
    return found


def _unclassified(source: str) -> list[int]:
    """Lines of list or tuple literals that hold "archive" but no constant
    "git", such as `[GIT, "archive", "HEAD"]` with git in a variable (9b on
    #206). They can't be classified as git archives or as anything else, so
    they're reported, not passed: conservation, not a narrower pattern."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.List, ast.Tuple))
        and "archive" in (strings := _strings(node.elts))
        and "git" not in strings
    ]


def _missing(strings: list[str]) -> list[str]:
    return [setting for setting in NEEDED if setting not in strings]


def _tracked_python() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "scripts/*.py", "src/*.py"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return [f for f in out.split("\0") if f]


def _sites() -> dict[str, list[tuple[int, list[str]]]]:
    sites = {}
    for path in _tracked_python():
        calls = _archive_calls((ROOT / path).read_text(encoding="utf-8"))
        if calls:
            sites[path] = calls
    return sites


def test_the_population_finds_the_known_archive_sites():
    """Extent: the two call sites #158 and #171 fixed are both found, so a
    narrowed pathspec or a pattern that stopped matching can't pass on none."""
    sites = _sites()
    assert "scripts/ci_in_docker.py" in sites, sites.keys()
    assert "scripts/publish_public.py" in sites, sites.keys()


def test_every_git_archive_keeps_the_stored_line_endings():
    bad = [
        f"{path}:{line}: missing {_missing(strings)}"
        for path, calls in _sites().items()
        for line, strings in calls
        if _missing(strings)
    ]
    assert bad == [], bad


def test_a_call_without_either_setting_fails():
    plain = '["git", "archive", "--format=tar", "HEAD"]'
    half = '["git", "-c", "core.autocrlf=false", "archive", "HEAD"]'
    both = '["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "HEAD"]'
    assert _missing(_archive_calls(plain)[0][1]) == list(NEEDED)
    assert _missing(_archive_calls(half)[0][1]) == ["core.eol=lf"]
    assert _missing(_archive_calls(both)[0][1]) == []


def test_a_git_wrapper_call_is_held_too():
    """b5 on #206: `_git("archive", ...)` and `ctx.git("archive", ...)` hold no
    list literal, so a list-only guard missed them."""
    bare = '_git("archive", "--format=tar", "HEAD")'
    method = 'ctx.git("archive", "HEAD")'
    kept = 'run_git("-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "HEAD")'
    assert _missing(_archive_calls(bare)[0][1]) == list(NEEDED)
    assert _missing(_archive_calls(method)[0][1]) == list(NEEDED)
    assert _missing(_archive_calls(kept)[0][1]) == []


def test_no_literal_holds_archive_without_naming_git():
    """The real tree: every literal holding "archive" is a classified git
    archive, so none is left unclassified."""
    bad = [
        f"{path}:{line}"
        for path in _tracked_python()
        for line in _unclassified((ROOT / path).read_text(encoding="utf-8"))
    ]
    assert bad == [], bad


def test_git_in_a_variable_is_unclassified_not_passed():
    """9b's arm F on #206: git in a variable passed every other check."""
    assert _unclassified('[GIT, "archive", "HEAD"]') == [1]
    assert _unclassified('["archive", "--list"]') == [1]
    assert _unclassified('["git", "archive", "HEAD"]') == []


def test_a_list_that_merely_mentions_archive_is_not_a_call():
    """Must allow: words that only contain "archive", and other tools."""
    assert _archive_calls('["tar", "archive.tar"]') == []
    assert _unclassified('["tar", "archive.tar"]') == []
    assert _archive_calls('shutil.make_archive("out", "zip", ".")') == []
    assert _archive_calls('subprocess.run(["tar", "-cf", "archive.tar", "."])') == []
