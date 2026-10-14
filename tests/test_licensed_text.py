"""The fixtures' licensed names stay masked, and come back exactly (#123).

The public release is a root commit, so the publish guard scans its whole
tree, tests included. Spelled literally, the guard's own fixtures refused the
first public 1.7.0 (9b, #123). They now spell licensed modules as
`@MODULE@` and `tests.licensed_text.unmask` restores them.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.licensed_text import unmask

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import publish_guard as pg  # noqa: E402
from licensed_modules import LICENSED_MODULES  # noqa: E402


@pytest.mark.parametrize("module", LICENSED_MODULES)
def test_every_licensed_module_comes_back_exactly(module):
    assert unmask(f"from .@{module.upper()}@ import x") == f"from .{module} import x"


def test_a_placeholder_naming_no_licensed_module_is_an_error():
    """A typo must not leave text the gate under test has no reason to refuse."""
    with pytest.raises(KeyError):
        unmask("from .@NOT_A_LICENSED_MODULE@ import x")


def test_text_without_placeholders_is_unchanged():
    text = "user.email=t@invalid; an @ sign; @lowercase@ is not a placeholder"
    assert unmask(text) == text


def test_no_tracked_file_spells_a_licensed_import():
    """What a root P's whole-tree scan would find, by the guard's own import
    pattern, over every tracked text file. A fixture written literally fails
    here, in the gate, instead of at the next publish.

    Limit, stated: this checks L3's IMPORT half only. Its DEFINITIONS half
    needs the licensed-only names, derived from the history that still holds
    the parsers, which a CI clone need not carry; `publish_guard.py` on the
    built P checks that half."""
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    files = [f for f in listed.split("\0") if f]
    assert len(files) > 100, "git ls-files found too few files: the scan would prove nothing"
    hits = []
    for name in files:
        path = ROOT / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw[:8000]:
            continue
        if m := pg._IMPORT.search(raw.decode("utf-8", errors="replace")):
            hits.append(f"{name}: {m.group(0)!r}")
    assert hits == [], hits


# --- who may use `unmask` (#153; ba and b5 on #152) -----------------------------

#: `unmask` hides licensed names from the publish guard by design, so a test
#: could use it to mask a REAL dependency (`importlib.import_module(_m(...))`,
#: a patch target) where L3 can't see it. The files allowed to import it are
#: named here, so a new one is a deliberate change seen in review.
UNMASK_IMPORTERS = {
    "tests/test_licensed_text.py",
    "tests/test_parser_gate.py",
    "tests/test_public_rehearsal.py",
    "tests/test_publish_guard.py",
    "tests/test_release_check_archive.py",
    "tests/test_release_check_content.py",
    "tests/test_release_public.py",
}

_HELPER = "tests.licensed_text"


#: Calls that load a module by name or by file (9b on #169): a constant
#: naming the helper, positional or keyword, reaches it as surely as an import.
_LOADERS = {
    "import_module",
    "__import__",
    "importorskip",
    "run_module",
    "spec_from_file_location",
    "run_path",
    "SourceFileLoader",
}


def _names_the_helper(value: object) -> bool:
    return isinstance(value, str) and (
        value == _HELPER
        or value.startswith(_HELPER + ".")
        or value.replace("\\", "/").endswith("licensed_text.py")
    )


def _loads_the_helper(node: ast.Call) -> bool:
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if name not in _LOADERS:
        return False
    values = [*node.args, *(k.value for k in node.keywords)]
    return any(isinstance(v, ast.Constant) and _names_the_helper(v.value) for v in values)


def _imports_the_helper(source: str, path: str) -> bool:
    """Whether `source` reaches `tests.licensed_text` by one of the static import
    forms: from it (any names, `*` included), `from tests import licensed_text`
    (aliased or parenthesised too), `import` with or without an alias, a relative
    import, or a loader in `_LOADERS` (`import_module`, `__import__`,
    `pytest.importorskip`, `runpy`, `spec_from_file_location`, `SourceFileLoader`)
    given a CONSTANT naming the module or its file, positionally or by keyword. A
    file that doesn't parse is an ERROR, never skipped: an unreadable file is
    where an importer would hide.

    Not seen, stated so this isn't read as proof (80 on #169): a bare `import
    tests` followed by `tests.licensed_text`, a name built at run time, `exec`,
    `sys.modules`, and a loader not in `_LOADERS`. The backstop for those is
    that the licensed modules don't exist in the public tree, so a test that
    reaches one through `unmask` raises and CI goes red."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        raise AssertionError(
            f"{path} doesn't parse, so its imports can't be read: {error}"
        ) from error
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and module == _HELPER:
                return True
            if (
                node.level == 0
                and module == "tests"
                and any(a.name == "licensed_text" for a in node.names)
            ):
                return True
            if node.level > 0 and (
                module == "licensed_text" or any(a.name == "licensed_text" for a in node.names)
            ):
                return True
        elif isinstance(node, ast.Import):
            if any(a.name == _HELPER or a.name.startswith(_HELPER + ".") for a in node.names):
                return True
        elif isinstance(node, ast.Call) and _loads_the_helper(node):
            return True
    return False


def _tracked_python() -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "*.py"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    files = [f for f in listed.split("\0") if f]
    assert len(files) > 100, "git ls-files found too few .py files: the scan would prove nothing"
    return files


def test_only_the_named_files_import_unmask():
    found = {
        path
        for path in _tracked_python()
        if _imports_the_helper((ROOT / path).read_text(encoding="utf-8"), path)
    }
    assert found == UNMASK_IMPORTERS, (
        f"added: {sorted(found - UNMASK_IMPORTERS)}, gone: {sorted(UNMASK_IMPORTERS - found)}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "from tests.licensed_text import unmask\n",
        "from tests.licensed_text import unmask as _m\n",
        "from tests.licensed_text import *\n",
        "from tests import licensed_text\n",
        "import tests.licensed_text\n",
        "import tests.licensed_text as lt\n",
        "from .licensed_text import unmask\n",
        "from . import licensed_text\n",
        "import importlib\nimportlib.import_module('tests.licensed_text')\n",
        "from importlib import import_module\nimport_module('tests.licensed_text')\n",
        "__import__('tests.licensed_text')\n",
        "def f():\n    from tests.licensed_text import unmask\n",
        "import importlib\nimportlib.import_module(name='tests.licensed_text')\n",
        "import pytest\nlt = pytest.importorskip('tests.licensed_text')\n",
        "import importlib.util as u\nu.spec_from_file_location('lt', 'tests/licensed_text.py')\n",
        "import runpy\nrunpy.run_path('tests/licensed_text.py')\n",
        "import runpy\nrunpy.run_module('tests.licensed_text')\n",
        "from importlib.machinery import SourceFileLoader\n"
        "SourceFileLoader('lt', 'tests\\\\licensed_text.py')\n",
    ],
)
def test_every_import_form_is_seen(source):
    assert _imports_the_helper(source, "tests/test_new.py"), source


def test_a_file_that_merely_names_the_helper_is_not_an_importer():
    source = '"""Explains tests.licensed_text and unmask."""\nimport json\n'
    assert not _imports_the_helper(source, "tests/test_doc.py")


def test_a_file_that_does_not_parse_is_an_error_not_a_skip():
    with pytest.raises(AssertionError, match="doesn't parse"):
        _imports_the_helper("def broken(:\n", "tests/test_broken.py")
