"""The public release commit P (#33): built from the cut, licensed parsers
absent, parent = GitHub's main, published by named refspecs only.

Real git throughout, not fakes: P's construction is git plumbing, and a fake
written from the same understanding as the code would agree with it by
construction. A local bare repository stands in for GitHub (`GITHUB_URL` is
monkeypatched to its path), and a two-line guard script stands in for 1d's
`publish_guard.py`, whose exit code is set per test. Every module name, path
and file content here is invented or structural; nothing is from a licensed
source.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.licensed_text import unmask as _m

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402
from release import Context  # noqa: E402

LICENSED = "src/policyforge/ingest/hitrust.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _real(argv):
    return release._real_run(argv)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A 'GitHub' bare repo holding main at G, and a Forgejo-side clone whose
    history goes past G with a licensed module, a test importing it, and
    public code that does not."""
    github = tmp_path / "github.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(github)], check=True)
    repo = tmp_path / "forgejo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.name", "policyforge-test")
    _git(repo, "config", "user.email", "test@invalid")
    _write(repo, "src/policyforge/__init__.py", '__version__ = "9.9.8"\n')
    _write(repo, "README.md", "public\n")
    base = _commit(repo, "public history")
    _git(repo, "push", "-q", str(github), "HEAD:refs/heads/main")
    # The train: a licensed module, tests importing it three ways, public code.
    _write(repo, LICENSED, "LEVEL = 'x'\n")
    _write(repo, "src/policyforge/ingest/__init__.py", "")
    _write(repo, "src/policyforge/cli.py", "import json\n")
    _write(repo, "tests/test_direct.py", _m("from policyforge.ingest import @HITRUST@\n"))
    _write(
        repo, "tests/test_multiline.py", _m("from policyforge.ingest import (\n    @HITRUST@,\n)\n")
    )
    _write(repo, "tests/test_dotted.py", _m("import policyforge.ingest.@HITRUST@ as h\n"))
    _write(repo, "tests/test_unrelated.py", "# mentions hitrust in a comment only\nimport json\n")
    _write(
        repo,
        "scripts/publish_guard.py",
        "import sys\nsys.exit(int(open(sys.argv[0] + '.code').read()))\n",
    )
    _write(repo, "scripts/publish_guard.py.code", "0")
    _commit(repo, "the train, with a licensed module")
    monkeypatch.setattr(release, "GITHUB_URL", str(github))
    return {"github": github, "repo": repo, "base": base}


def _ctx(world, **kw):
    return Context(version="9.9.0", root=world["repo"], run=_real, forge="forgejo", **kw)


def test_the_licensed_set_is_the_module_and_every_test_importing_it(world):
    removed, refused, classes = release.licensed_set(_ctx(world), "HEAD")
    assert removed == [
        LICENSED,
        "tests/test_direct.py",
        "tests/test_dotted.py",
        "tests/test_multiline.py",
    ], "a multi-line or dotted import must count, and a comment mention must not"
    assert refused == {}
    # Classified, never dropped (ba on #37): the comment-only file is a mention.
    assert classes["mention"] == ["tests/test_unrelated.py"]


def test_a_relative_import_inside_the_package_counts(world):
    _write(world["repo"], "src/policyforge/ingest/loader.py", _m("from .@HITRUST@ import LEVEL\n"))
    _commit(world["repo"], "a relative importer")
    _removed, refused, _ = release.licensed_set(_ctx(world), "HEAD")
    assert refused == {"src/policyforge/ingest/loader.py": "import"}


def test_an_import_held_in_a_string_refuses(world):
    """ba on #37: parser_gate.py's trial-run harness is a STRING that imports
    a licensed module. The AST's import nodes cannot see it; P would ship a
    public gate importing a module P does not contain."""
    _write(
        world["repo"],
        "src/policyforge/ingest/gate.py",
        _m('HARNESS = "import policyforge.ingest.@HITRUST@\\nrun()\\n"\n'),
    )
    _write(world["repo"], "scripts/tool.py", _m('TARGET = "policyforge.ingest.@HITRUST@.LEVEL"\n'))
    _commit(world["repo"], "imports held in strings")
    _removed, refused, classes = release.licensed_set(_ctx(world), "HEAD")
    assert refused == {
        "scripts/tool.py": "string",
        "src/policyforge/ingest/gate.py": "string",
    }
    assert sorted(classes["string"]) == ["scripts/tool.py", "src/policyforge/ingest/gate.py"]


LOADER = """import importlib
"""


def test_a_module_loaded_by_a_computed_name_refuses(world):
    """b5 on #37: a loader given a name built at run time loads a module no
    string or import node names; licensed_builtin.py:148 (#36) is the
    f-string shape. A relative name with a constant package resolves."""
    shapes = {
        "src/policyforge/a_plus.py": """M = "hitrust"
importlib.import_module("policyforge.ingest." + M)
""",
        "src/policyforge/a_fstring.py": """m = "govramp"
importlib.import_module(f"policyforge.ingest.{m}")
""",
        "src/policyforge/a_relative.py": """importlib.import_module(
    ".hitrust", package="policyforge.ingest"
)
""",
        "scripts/a_loop.py": """for m in ("hitrust", "govramp"):
    importlib.import_module("policyforge.ingest." + m)
""",
    }
    for path, body in shapes.items():
        _write(world["repo"], path, LOADER + body)
    _commit(world["repo"], "modules loaded by computed names")
    _removed, refused, _classes = release.licensed_set(_ctx(world), "HEAD")
    assert refused == {
        "scripts/a_loop.py": "dynamic",
        "src/policyforge/a_fstring.py": "dynamic",
        "src/policyforge/a_plus.py": "dynamic",
        "src/policyforge/a_relative.py": "import",
    }


SEAM = (
    LOADER
    + """GENERATE_PARSER_FRAMEWORK = {"etl-hitrust": "hitrust"}


def load(module_name):
    return importlib.import_module(module_name)
"""
)


def test_a_reviewed_dynamic_loader_is_classified_and_an_unreviewed_copy_refuses(world):
    """The public seam loads by a name it is given, by design; it ships
    because it is in REVIEWED_DYNAMIC, and the same text anywhere else is
    refused. Either way it is counted as `dynamic`, never dropped."""
    _write(world["repo"], "src/policyforge/plugins.py", SEAM)
    _write(world["repo"], "src/policyforge/other_seam.py", SEAM)
    _commit(world["repo"], "a reviewed seam and an unreviewed copy")
    _removed, refused, classes = release.licensed_set(_ctx(world), "HEAD")
    assert sorted(classes["dynamic"]) == [
        "src/policyforge/other_seam.py",
        "src/policyforge/plugins.py",
    ]
    assert refused == {"src/policyforge/other_seam.py": "dynamic"}


def test_every_loader_shape_names_what_it_loads():
    """Shapes written from the domain, not from the reader (1d on #37 found
    the file-path and fromlist ones classified as mentions)."""
    known = {
        _m('importlib.import_module("policyforge.ingest.@HITRUST@")'): {
            _m("policyforge.ingest.@HITRUST@")
        },
        'import_module(".govramp", "policyforge.ingest")': {_m("policyforge.ingest.@GOVRAMP@")},
        '__import__("policyforge.ingest", fromlist=["hitrust"])': {
            "policyforge.ingest",
            _m("policyforge.ingest.@HITRUST@"),
        },
        '__import__("policyforge.ingest", None, None, ["soc2_tsc"])': {
            "policyforge.ingest",
            _m("policyforge.ingest.@SOC2_TSC@"),
        },
        'spec_from_file_location("x", "src/policyforge/ingest/hitrust.py")': {
            _m("policyforge.ingest.@HITRUST@")
        },
        'spec_from_file_location("x", location="C:/w/src/policyforge/ingest/govramp.py")': {
            _m("policyforge.ingest.@GOVRAMP@")
        },
        'spec_from_file_location("x", "scripts/tool.py")': {"file:scripts/tool.py"},
    }
    for call, loads in known.items():
        assert release.dynamic_loads_of(call) == loads, call
    # A loader rebound one level (b5 on #37); an unrelated `load` is not one.
    rebound = {
        "from importlib import import_module as load": {_m("policyforge.ingest.@HITRUST@")},
        "import importlib; load = importlib.import_module": {_m("policyforge.ingest.@HITRUST@")},
        "def load(name): pass": set(),
    }
    for binding, loads in rebound.items():
        source = binding + _m("\nload('policyforge.ingest.@HITRUST@')\n")
        assert release.dynamic_loads_of(source) == loads, binding
    unknown = [
        'importlib.import_module("policyforge.ingest." + m)',
        'importlib.import_module(f"policyforge.ingest.{m}")',
        "importlib.import_module(name)",
        'import_module(".hitrust", package=pkg)',
        '__import__("policyforge.ingest", fromlist=names)',
        '__import__("policyforge.ingest", fromlist=[m])',
        'spec_from_file_location("x", root / "hitrust.py")',
        "importlib.util.find_spec(name)",
    ]
    for call in unknown:
        assert release.UNKNOWN_LOAD in release.dynamic_loads_of(call), call


def test_a_spec_path_names_a_licensed_module_however_it_is_spelled():
    """#47 (1d found it on #37): only the full `policyforge/ingest/<m>.py`
    suffix was read as the licensed module, so a shorter path to the same file
    was a `file:` load and classified as a mention. Shapes written from how a
    path to that file is actually spelled, not from the rule."""
    as_module = {
        '"ingest/hitrust.py"': _m("policyforge.ingest.@HITRUST@"),
        '"../ingest/govramp_export.py"': _m("policyforge.ingest.@GOVRAMP_EXPORT@"),
        '"ingest\\\\soc2_tsc.py"': _m("policyforge.ingest.@SOC2_TSC@"),
        '"ingest/./hitrust_export.py"': _m("policyforge.ingest.@HITRUST_EXPORT@"),
        '"ingest//govramp.py"': _m("policyforge.ingest.@GOVRAMP@"),
        # Windows' filesystem is case-insensitive: this loads the same file.
        '"src/policyforge/Ingest/HITRUST.py"': _m("policyforge.ingest.@HITRUST@"),
        # The full form keeps working.
        '"src/policyforge/ingest/hitrust.py"': _m("policyforge.ingest.@HITRUST@"),
    }
    for path, module in as_module.items():
        loads = release.dynamic_loads_of(f'spec_from_file_location("x", {path})')
        assert loads == {module}, (path, loads)
    # A licensed module's file name outside any `ingest/` segment: what it
    # loads depends on the working directory, so it is unknown, never clear.
    for path in ('"hitrust.py"', '"lib/govramp.py"', '"./soc2_tsc.py"'):
        loads = release.dynamic_loads_of(f'spec_from_file_location("x", {path})')
        assert loads == {release.UNKNOWN_LOAD}, (path, loads)
    # What it must ALLOW: another module under ingest/, and a name that only
    # starts like a licensed one.
    for path, expected in (
        ('"src/policyforge/ingest/levelled.py"', "file:src/policyforge/ingest/levelled.py"),
        ('"ingest/hitrust_notes.py"', "file:ingest/hitrust_notes.py"),
        ('"scripts/tool.py"', "file:scripts/tool.py"),
    ):
        loads = release.dynamic_loads_of(f'spec_from_file_location("x", {path})')
        assert loads == {expected}, (path, loads)


def test_a_spec_path_is_read_as_windows_and_python_read_it():
    """b5 on #57: Windows strips a file name's trailing dots and spaces, and a
    spec may point at the parser's compiled bytecode. Both load the module."""
    for path, module in (
        ('"ingest/hitrust.py."', _m("policyforge.ingest.@HITRUST@")),
        ('"ingest/govramp.py "', _m("policyforge.ingest.@GOVRAMP@")),
        ('"ingest/__pycache__/soc2_tsc.cpython-312.pyc"', _m("policyforge.ingest.@SOC2_TSC@")),
        ('"src/policyforge/ingest/__pycache__/hitrust_export.cpython-314.opt-1.pyc"',
         _m("policyforge.ingest.@HITRUST_EXPORT@")),
        ('"ingest/govramp_export.pyc"', _m("policyforge.ingest.@GOVRAMP_EXPORT@")),
    ):  # fmt: skip
        loads = release.dynamic_loads_of(f'spec_from_file_location("x", {path})')
        assert loads == {module}, (path, loads)
    # The same shapes outside ingest/ are unknown; a PUBLIC module's bytecode
    # is just a file.
    for path, expected in (
        ('"__pycache__/hitrust.cpython-312.pyc"', release.UNKNOWN_LOAD),
        ('"ingest/__pycache__/levelled.cpython-312.pyc"',
         "file:ingest/__pycache__/levelled.cpython-312.pyc"),
    ):  # fmt: skip
        loads = release.dynamic_loads_of(f'spec_from_file_location("x", {path})')
        assert loads == {expected}, (path, loads)


def test_a_spec_path_to_a_compiled_extension_module_is_the_module():
    """#59 (ba on 47, from b5's measurement on 57): a parser built as an
    extension module loads through `ExtensionFileLoader`, and its path read as
    `file:`, a mention, which ships. Shapes as builds name them on each
    platform, not as the rule spells them."""
    for path, module in (
        ('"ingest/hitrust.pyd"', _m("policyforge.ingest.@HITRUST@")),
        ('"ingest/govramp.cp312-win_amd64.pyd"', _m("policyforge.ingest.@GOVRAMP@")),
        ('"ingest/soc2_tsc.so"', _m("policyforge.ingest.@SOC2_TSC@")),
        ('"src/policyforge/ingest/hitrust_export.cpython-312-x86_64-linux-gnu.so"',
         _m("policyforge.ingest.@HITRUST_EXPORT@")),
        ('"ingest/govramp_export.abi3.so"', _m("policyforge.ingest.@GOVRAMP_EXPORT@")),
        # macOS, a free-threaded build, and a Windows debug build (`_d`).
        ('"ingest/hitrust.cpython-313t-darwin.so"', _m("policyforge.ingest.@HITRUST@")),
        ('"ingest/hitrust_d.cp314-win_amd64.pyd"', _m("policyforge.ingest.@HITRUST@")),
        # Read as Windows reads it, as the .py forms are.
        ('"INGEST/GOVRAMP.CP312-WIN_AMD64.PYD"', _m("policyforge.ingest.@GOVRAMP@")),
        ('"ingest/soc2_tsc.pyd."', _m("policyforge.ingest.@SOC2_TSC@")),
    ):  # fmt: skip
        loads = release.dynamic_loads_of(f'spec_from_file_location("x", {path})')
        assert loads == {module}, (path, loads)
    # Outside ingest/ it is unknown, as a .py there is.
    for path in ('"hitrust.pyd"', '"lib/soc2_tsc.cpython-312-x86_64-linux-gnu.so"'):
        loads = release.dynamic_loads_of(f'spec_from_file_location("x", {path})')
        assert loads == {release.UNKNOWN_LOAD}, (path, loads)
    # What it must ALLOW: a public module's extension, a name that only starts
    # like a licensed one, and files Python doesn't load as a module.
    for path in (
        "ingest/levelled.cp312-win_amd64.pyd",
        "ingest/hitrust_notes.so",
        "ingest/hitrust.so.txt",
        "ingest/hitrust.dll",
    ):
        loads = release.dynamic_loads_of(f'spec_from_file_location("x", "{path}")')
        assert loads == {f"file:{path}"}, (path, loads)


def test_every_extension_suffix_of_this_interpreter_is_read():
    """The rule spells `.pyd`/`.so` with any tags out by hand; the running
    interpreter's own list is the other side of that check, not the rule's."""
    from importlib.machinery import EXTENSION_SUFFIXES

    assert EXTENSION_SUFFIXES, "premise: this interpreter loads extension modules"
    for suffix in EXTENSION_SUFFIXES:
        path = f"ingest/hitrust{suffix}"
        loads = release.dynamic_loads_of(f'spec_from_file_location("x", "{path}")')
        assert loads == {_m("policyforge.ingest.@HITRUST@")}, (path, loads)


def test_an_extension_module_path_is_refused_by_the_build_itself(world):
    """Through the real licensed_set (#59): the candidate search must reach a
    file whose only spelling is an extension path, including a debug build's
    upper-case `<M>_D.` name, which the `<m>.` file-name search alone misses."""
    _write(
        world["repo"],
        "src/policyforge/linux_ext.py",
        "from importlib.util import spec_from_file_location\n"
        'SPEC = spec_from_file_location("x", "ingest/hitrust.cpython-312-x86_64-linux-gnu.so")\n',
    )
    _write(
        world["repo"],
        "src/policyforge/debug_ext.py",
        "from importlib.util import spec_from_file_location\n"
        'SPEC = spec_from_file_location("x", "INGEST/GOVRAMP_D.CP314-WIN_AMD64.PYD")\n',
    )
    _commit(world["repo"], "licensed modules loaded as extension modules")
    _removed, refused, _classes = release.licensed_set(_ctx(world), "HEAD")
    assert refused == {
        "src/policyforge/linux_ext.py": "import",
        "src/policyforge/debug_ext.py": "import",
    }


def test_an_upper_case_spec_path_is_refused_by_the_build_itself(world):
    """b5 on #57: the case-insensitive rule was only reached by a test of
    dynamic_loads_of. licensed_set's candidate search was case-sensitive, so
    a file whose ONLY spelling of the module was upper case was never read,
    and shipped. Through the real licensed_set this time."""
    _write(
        world["repo"],
        "src/policyforge/upper_path.py",
        "from importlib.util import spec_from_file_location\n"
        'SPEC = spec_from_file_location("x", "INGEST/HITRUST.PY")\n',
    )
    _commit(world["repo"], "a licensed module loaded by an upper-case path")
    _removed, refused, _classes = release.licensed_set(_ctx(world), "HEAD")
    assert refused == {"src/policyforge/upper_path.py": "import"}


def test_a_class_named_like_a_module_does_not_drop_a_public_test_from_p(world):
    """What the case-insensitive search must ALLOW: Python imports are
    case-sensitive, so `HitrustLikeError` names no module. A first version
    searched every module name with `-i`; the real tree's exception census
    (which loads by computed name) then classified `dynamic`, and a test in
    that class leaves P. Only a module's FILE NAME is searched in any case."""
    _write(
        world["repo"],
        "tests/test_census_like.py",
        "import importlib\n"
        "class HitrustLikeError(ValueError): pass\n"
        "def load(name): return importlib.import_module(name)\n",
    )
    _commit(world["repo"], "a test naming a class like a module, loading by computed name")
    removed, _refused, _classes = release.licensed_set(_ctx(world), "HEAD")
    assert "tests/test_census_like.py" not in removed, removed


def test_a_short_spec_path_to_a_licensed_parser_refuses_the_build(world):
    """End to end, through licensed_set: the short form is refused, not shipped."""
    _write(
        world["repo"],
        "src/policyforge/short_path.py",
        "from importlib.util import spec_from_file_location\n"
        'SPEC = spec_from_file_location("x", "ingest/hitrust.py")\n',
    )
    _commit(world["repo"], "a licensed module loaded by a short path")
    _removed, refused, _classes = release.licensed_set(_ctx(world), "HEAD")
    assert refused == {"src/policyforge/short_path.py": "import"}


def test_a_file_path_or_fromlist_load_of_a_licensed_module_refuses(world):
    _write(
        world["repo"],
        "src/policyforge/by_path.py",
        "from importlib.util import spec_from_file_location\n"
        'SPEC = spec_from_file_location("x", "src/policyforge/ingest/hitrust.py")\n',
    )
    _write(
        world["repo"],
        "scripts/by_fromlist.py",
        'MOD = __import__("policyforge.ingest", fromlist=["hitrust"])\n',
    )
    _commit(world["repo"], "a licensed module loaded by path and by fromlist")
    _removed, refused, _classes = release.licensed_set(_ctx(world), "HEAD")
    assert refused == {"scripts/by_fromlist.py": "import", "src/policyforge/by_path.py": "import"}


def test_a_map_of_framework_names_is_a_mention():
    """plugins.py's `{"etl-hitrust": "hitrust"}` names generate-parser
    frameworks, not modules; a bare-name rule would refuse the public seam."""
    source = 'GENERATE_PARSER_FRAMEWORK = {"etl-hitrust": "hitrust", "etl-govramp": "govramp"}'
    assert release.dynamic_loads_of(source) == set()
    assert release.string_refs_of(source) is False


def test_every_reviewed_dynamic_entry_still_loads_by_a_computed_name():
    """REVIEWED_DYNAMIC cannot go stale quietly: each entry must still be a
    file in this tree whose loader takes a name known only at run time."""
    for path in release.REVIEWED_DYNAMIC:
        source = (release.REPO_ROOT / path).read_text(encoding="utf-8")
        assert release.UNKNOWN_LOAD in release.dynamic_loads_of(source), path


def test_a_docstring_naming_a_module_is_a_mention_not_a_refusal(world):
    _write(
        world["repo"],
        "src/policyforge/notes.py",
        _m('"""Explains that policyforge.ingest.@HITRUST@ moved to a plugin."""\n\nX = 1\n'),
    )
    _commit(world["repo"], "prose only")
    _removed, refused, classes = release.licensed_set(_ctx(world), "HEAD")
    assert "src/policyforge/notes.py" not in refused
    assert "src/policyforge/notes.py" in classes["mention"]


def test_a_file_that_does_not_parse_cannot_be_cleared(world):
    _write(world["repo"], "src/policyforge/broken.py", "def hitrust(:\n")
    _commit(world["repo"], "unparseable")
    _removed, refused, _ = release.licensed_set(_ctx(world), "HEAD")
    assert refused == {"src/policyforge/broken.py": "unparsed"}


def test_the_build_prints_every_class_count(world):
    _public, lines = release.build_public(_ctx(world))
    counts = next(line for line in lines if line.startswith("files naming a licensed module"))
    for kind in ("import", "dynamic", "string", "unparsed", "mention"):
        assert f"{kind} " in counts, counts


def test_public_code_still_importing_a_licensed_module_refuses_the_build(world):
    _write(
        world["repo"], "src/policyforge/cli.py", _m("from policyforge.ingest import @HITRUST@\n")
    )
    _commit(world["repo"], "the seam is not in yet")
    public, lines = release.build_public(_ctx(world))
    assert public == ""
    assert any("REFUSED" in line and "src/policyforge/cli.py" in line for line in lines), lines


def test_p_drops_the_licensed_set_and_has_github_main_as_its_only_parent(world):
    ctx = _ctx(world)
    public, lines = release.build_public(ctx)
    assert public, lines
    repo = world["repo"]
    assert _git(repo, "log", "-1", "--format=%P", public) == world["base"]
    names = set(_git(repo, "ls-tree", "-r", "--name-only", public).splitlines())
    assert LICENSED not in names and "tests/test_direct.py" not in names
    assert {"README.md", "src/policyforge/cli.py", "tests/test_unrelated.py"} <= names
    # Nothing from the train's history is reachable from P but not from G.
    assert _git(repo, "rev-list", f"{world['base']}..{public}").splitlines() == [public]


def test_p_is_the_same_commit_when_built_again(world):
    """The script exits at the approval and is re-run: a P that changed across
    that pause would be a commit nobody installed."""
    first, _ = release.build_public(_ctx(world))
    second, _ = release.build_public(_ctx(world))
    assert first and first == second


def test_the_guards_exit_code_decides_and_nothing_else(world):
    ctx = _ctx(world)
    assert release._public_built(ctx).ok and ctx.notes["public_sha"]
    _write(world["repo"], "scripts/publish_guard.py.code", "1")
    refused = _ctx(world)
    check = release._public_built(refused)
    assert not check.ok and "public_sha" not in refused.notes
    assert any("exit 1" in line for line in check.measured)


def test_the_guard_is_called_by_its_named_interface(world):
    """1d's guard (#35) takes `--candidate --base --source`, named. This PR
    first called it positionally, which its argparse would reject (exit 2)
    and so refuse every build, and the fake guard here, ignoring its
    arguments, could not see that. Once #35 is on the train this should
    parse the argv with the guard's own parser, as release_check's is."""
    ctx = _ctx(world)
    script = world["repo"] / "scripts" / "publish_guard.py"
    argv = release.guard_argv(ctx, script, "p" * 40, "g" * 40)
    pairs = dict(zip(argv[2::2], argv[3::2], strict=False))
    assert pairs["--candidate"] == "p" * 40 and pairs["--base"] == "g" * 40
    assert pairs["--source"] == _git(world["repo"], "rev-parse", "HEAD")
    assert pairs["--root"] == str(world["repo"])


def test_a_missing_guard_is_a_refusal(world):
    (world["repo"] / "scripts" / "publish_guard.py").unlink()
    ctx = _ctx(world)
    assert not release._public_built(ctx).ok


def test_the_candidate_push_is_one_named_refspec_and_lands_p(world):
    ctx = _ctx(world)
    assert release._public_built(ctx).ok
    assert release._candidate_ready(ctx).ok
    release._push_candidate(ctx)
    assert release._candidate_pushed(ctx).ok
    heads = _git(world["github"], "for-each-ref", "--format=%(refname)", "refs/heads")
    assert heads.splitlines() == ["refs/heads/main", "refs/heads/release-candidate/9.9.0"]
    # The set of names alone cannot see main MOVED: before the approval,
    # GitHub main must still be G (a mutation pushing main too survived a
    # names-only check).
    assert _git(world["github"], "rev-parse", "refs/heads/main") == world["base"]


def test_main_is_a_fast_forward_to_p_and_the_candidate_is_deleted(world, monkeypatch):
    ctx = _ctx(world)
    assert release._public_built(ctx).ok
    release._push_candidate(ctx)
    monkeypatch.setattr(release, "_main_merged", lambda c: release.Check(True, ["merged"]))
    assert release._public_main_ready(ctx).ok
    release._push_public_main(ctx)
    done = release._public_main_done(ctx)
    assert done.ok, done.measured
    assert _git(world["github"], "rev-parse", "refs/heads/main") == ctx.notes["public_sha"]


def test_a_moved_github_main_refuses_the_main_push(world, monkeypatch):
    """The lease: if GitHub main moved after P was built, P is not pushed over it."""
    ctx = _ctx(world)
    assert release._public_built(ctx).ok
    release._push_candidate(ctx)
    other = world["repo"].parent / "other"
    subprocess.run(["git", "clone", "-q", str(world["github"]), str(other)], check=True)
    _git(other, "config", "user.name", "x")
    _git(other, "config", "user.email", "x@invalid")
    _write(other, "moved.txt", "someone pushed\n")
    moved = _commit(other, "moved")
    _git(other, "push", "-q", "origin", "HEAD:refs/heads/main")
    assert not release._candidate_ready(ctx).ok, "the gate sees main moved"
    release._push_public_main(ctx)
    assert _git(world["github"], "rev-parse", "refs/heads/main") == moved


def test_the_tag_names_p_on_the_forgejo_path():
    ctx = Context(
        version="9.9.9", forge="forgejo", notes={"public_sha": "p" * 40, "main_sha": "m" * 40}
    )
    assert release._released_commit(ctx) == "p" * 40
    ctx.forge = "github"
    assert release._released_commit(ctx) == "m" * 40


def test_the_forgejo_order_puts_p_before_the_install_and_main_before_the_tag():
    keys = [s.key for s in release.steps("forgejo")]
    assert keys.index("public-build") < keys.index("public-candidate") < keys.index("head-install")
    assert keys.index("main") < keys.index("public-main") < keys.index("tag")
    assert [s.key for s in release.steps()] == [s.key for s in release._base_steps()]


def test_the_seam_contract_is_in_every_install():
    script = release._install_script("9.9.9")
    for command, args, _ in release.SEAM_COMMANDS:
        assert f"policyforge {command} {args} > /tmp/seam.out" in script
    assert script.count("! grep -q Traceback /tmp/seam.out") == 3
    assert script.count(f"grep -q '{release.SEAM_REFUSAL}' /tmp/seam.out") == 3


def test_the_seam_phrase_is_what_the_real_command_prints_with_no_plugin(tmp_path, monkeypatch):
    """The other side of the seam check: the public CLI itself, run as the
    container runs it (real arguments, a git directory ignoring
    local_content/), with no plugin registered. The phrase the install greps
    for must be in what it prints, or every install refuses at the cut (9b,
    reviewing #50: the plugin was renamed and the grep was not)."""
    from click.testing import CliRunner

    from policyforge import plugins
    from policyforge.cli import cli

    monkeypatch.setattr(plugins, "BUILTIN", [])
    monkeypatch.setattr(plugins, "_entry_points", lambda: [])
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q", "."], check=True)
    (tmp_path / ".gitignore").write_text("local_content/\n", encoding="utf-8")
    (tmp_path / "seam.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "seam.txt").write_text("CC1.1 x\n", encoding="utf-8")
    for command, args, names_generator in release.SEAM_COMMANDS:
        result = CliRunner().invoke(cli, [command, *args.split()])
        assert result.exit_code != 0, (command, result.output)
        assert release.SEAM_REFUSAL in result.output, (command, result.output)
        assert ("generate-parser" in result.output) is names_generator, (command, result.output)
        assert not (tmp_path / args.split("--out ")[1]).exists(), command


def test_the_seam_runs_with_real_arguments_and_soc2_needs_no_generator():
    """Measured on #36: with no arguments Click refuses the missing option
    before the plugin is asked for, so a no-argument check tests Click. And
    `generate-parser` has no SOC 2 path, so SOC 2's message must not be held
    to naming it."""
    checks = {
        command: check
        for (command, _, _), check in zip(
            release.SEAM_COMMANDS, release.SEAM_CHECKS[1:], strict=False
        )
    }
    for command in ("etl-hitrust", "etl-govramp", "etl-soc2-tsc"):
        assert "--out local_content/" in checks[command], "real arguments"
        assert "test ! -e local_content/" in checks[command], "nothing written"
    assert "generate-parser" in checks["etl-hitrust"]
    assert "generate-parser" in checks["etl-govramp"]
    assert "generate-parser" not in checks["etl-soc2-tsc"]
    assert "git init" in release.SEAM_CHECKS[0] and "local_content/" in release.SEAM_CHECKS[0]


def test_no_published_script_carries_text_the_publish_guard_refuses():
    """ba on #33: #35's guard reads an import's TEXT anywhere in a published
    file (L3), docstrings included, and every script under scripts/ ships in
    P. A docstring in release.py spelling a relative import of a licensed
    module refused the first P that could be built. Read with the guard's own
    pattern, since the guard is what must not fire."""
    import publish_guard

    scripts = sorted((ROOT / "scripts").glob("*.py"))
    assert len(scripts) > 10, scripts
    for path in scripts:
        text = path.read_text(encoding="utf-8")
        found = publish_guard._IMPORT.search(text)
        assert found is None, f"{path.name}: {found.group(0)!r} would refuse P (L3)"


def test_release_check_finds_a_licensed_parser_in_an_archive():
    import release_check

    names = ["policyforge-9.9.9/README.md", f"policyforge-9.9.9/{LICENSED}"]
    assert release_check.licensed_in_archive(names) == [f"policyforge-9.9.9/{LICENSED}"]
    assert release_check.licensed_in_archive(["policyforge-9.9.9/README.md"]) == []


def test_a_patch_release_never_reaches_github(world):
    """The user's cadence (#33, CLAUDE.md): every MINOR release goes public
    as a filtered snapshot; PATCH releases stay private on Forgejo. Every
    GitHub-facing step refuses X.Y.Z with Z > 0, and nothing is built."""
    ctx = _ctx(world)
    ctx.version = "9.9.1"
    # No Forgejo client and no GitHub reachable from here: a refusal that still
    # queried either would raise, not refuse.
    ctx.forgejo = None
    built = release._public_built(ctx)
    assert not built.ok and "public_sha" not in ctx.notes
    assert "PATCH" in built.measured[0], built.measured
    # Each GitHub-facing gate refuses BY THE CADENCE, pinned by its message:
    # the candidate and tag gates would refuse anyway here (no P, P not on
    # main), so `not ok` alone cannot tell the cadence ran (1d on #37).
    plan = {s.key: s for s in release.steps("forgejo")}
    for key in ("public-build", "public-candidate", "public-main", "tag"):
        check = plan[key].gate(ctx)
        assert not check.ok and "PATCH" in check.measured[0], (key, check.measured)
    # Nothing reached the stand-in GitHub: only main, still at G.
    heads = _git(world["github"], "for-each-ref", "--format=%(refname)", "refs/heads")
    assert heads.splitlines() == ["refs/heads/main"]
    assert _git(world["github"], "rev-parse", "refs/heads/main") == world["base"]


def test_a_minor_release_is_allowed_public():
    for version in ("1.7.0", "1.8.0", "2.0.0"):
        ctx = Context(version=version, forge="forgejo")
        assert release._public_release_allowed(ctx).ok, version
    for version in ("1.7.1", "1.8.12"):
        ctx = Context(version=version, forge="forgejo")
        assert not release._public_release_allowed(ctx).ok, version
