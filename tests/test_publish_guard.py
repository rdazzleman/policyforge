"""The publish guard (#33): every layer made to fire on purpose, and to stay
quiet where it must.

Each arm builds its own scratch repository: B (what GitHub has), a source
branch still carrying the licensed parsers (as the Forgejo train does), and
a candidate P. The must-fail arms come first; the must-pass arms show the
guard does not refuse the public tool, including the format hints the user
ruled public and the envelope marker's text in code.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.licensed_text import unmask as _m

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import publish_guard as pg  # noqa: E402

SOC2 = "src/policyforge/ingest/soc2_tsc.py"
HITRUST = "src/policyforge/ingest/hitrust.py"

# A synthetic stand-in for a licensed parser. Only its SHAPE matters here:
# a specific parse function (a licensed-only name) and a public one.
PARSER = '''"""Synthetic stand-in for a licensed parser."""

FRAMEWORK = "SYNTHETIC-LICENSED"


def parse_synthetic_criteria(text):
    return [line for line in text.splitlines() if line]


def build_controls(records):
    return list(records)
'''
PUBLIC_ASSEMBLY = '''"""Public assembly, as #32 moves it (a name the parser also defines)."""


def build_controls(records):
    return list(records)
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def _write(repo: Path, files: dict[str, str | None]) -> None:
    for path, content in files.items():
        target = repo / path
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def _commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> dict:
    """B, then a source branch that adds the licensed parsers on top of it."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    base = _commit(
        r,
        {
            "README.md": "The public tool.\n",
            "local_content/.gitkeep": "",
            "src/policyforge/ingest/levelled.py": PUBLIC_ASSEMBLY,
        },
        "B: what GitHub has",
    )
    _git(r, "switch", "-q", "-c", "train")
    source = _commit(
        r,
        {SOC2: PARSER, HITRUST: _m("from .@SOC2_TSC@ import parse_synthetic_criteria\n")},
        "the licensed parsers",
    )
    _git(r, "switch", "-q", "main")
    return {"root": r, "base": base, "source": source}


def _candidate(repo: dict, files: dict[str, str | None], start: str | None = None) -> str:
    r = repo["root"]
    _git(r, "switch", "-q", "--detach", start or repo["base"])
    return _commit(r, files, "P")


def _layers(repo: dict, candidate: str, base: str | None = None) -> set[str]:
    found = pg.guard(candidate, base or repo["base"], repo["source"], repo["root"])
    return {f.layer for f in found}


# --- must-fail ---------------------------------------------------------------


def test_a_parser_in_the_tree_is_refused_by_path_and_by_blob(repo):
    p = _candidate(repo, {SOC2: PARSER})
    assert {"L1 path", "L2 blob"} <= _layers(repo, p)


@pytest.mark.parametrize("module", ["byoc_loader", "licensed_builtin"])
def test_the_two_modules_part_3_added_are_refused_by_path(repo, module):
    """#48: part 3 moved seven modules, and the guard knew five. A copy of one
    of the other two that imports nothing licensed and defines nothing
    licensed-only matches no L2 blob and no L3 name, so only L1's PATH, from
    the one list, can refuse it."""
    p = _candidate(repo, {f"src/policyforge/ingest/{module}.py": "def helper():\n    return 1\n"})
    found = pg.guard(p, repo["base"], repo["source"], repo["root"])
    assert [f for f in found if f.layer == "L1 path" and module in f.where], found


def test_a_head_that_deleted_the_parser_is_refused_by_its_history(repo):
    """The #33 hazard exactly: the head is clean, the history is not."""
    p = _candidate(repo, {SOC2: None, HITRUST: None}, start=repo["source"])
    assert "L1 path" in _layers(repo, p), "the parent's tree carries the parsers"
    assert "L2 blob" in _layers(repo, p), "their blobs are in the range"
    # Which check spoke: the head's tree ALONE is clean, so a tree-only
    # check would have passed this push.
    assert pg.check_paths([p], repo["root"]) == []


def test_a_renamed_copy_is_refused_by_blob_identity_not_by_path(repo):
    p = _candidate(repo, {"src/policyforge/other/renamed.py": PARSER})
    layers = _layers(repo, p)
    assert "L2 blob" in layers and "L1 path" not in layers


def test_a_renamed_and_edited_copy_is_refused_by_its_definitions(repo):
    edited = PARSER.replace("Synthetic stand-in", "An edited copy")
    p = _candidate(repo, {"src/policyforge/other/edited.py": edited})
    layers = _layers(repo, p)
    assert "L3 content" in layers and "L2 blob" not in layers


def test_an_import_of_a_licensed_module_is_refused(repo):
    p = _candidate(
        repo, {"src/policyforge/cli/x.py": _m("from policyforge.ingest import @HITRUST@\n")}
    )
    assert "L3 content" in _layers(repo, p)


def test_a_dotted_import_in_a_string_is_refused(repo):
    """Monkeypatch targets and generated-code templates import by text."""
    p = _candidate(repo, {"tests/t.py": _m('TARGET = "policyforge.ingest.@SOC2_TSC@.parse"\n')})
    assert "L3 content" in _layers(repo, p)


def test_a_licensed_catalog_envelope_is_refused(repo):
    p = _candidate(repo, {"data/x/controls.json": '{"licence": "licensed", "controls": []}'})
    assert "L4 data" in _layers(repo, p)


def test_anything_under_local_content_is_refused(repo):
    p = _candidate(repo, {"local_content/export.csv": "a,b\n"})
    assert "L1 path" in _layers(repo, p)


def test_after_the_move_a_renamed_and_edited_copy_is_still_refused(repo):
    """9b on #35: after #32 the SOURCE's tree no longer holds the parsers,
    only its history does. L3's names must still derive, from history, or
    L3 goes silent while L1, L2 and L4 keep reporting."""
    r = repo["root"]
    _git(r, "switch", "-q", "train")
    moved = _commit(r, {SOC2: None, HITRUST: None}, "#32: the parsers move out")
    edited = PARSER.replace("Synthetic stand-in", "An edited copy")
    p = _candidate(repo, {"src/policyforge/other/legacy.py": edited}, start=moved)
    found = pg.guard(p, moved, moved, r)
    assert "L3 content" in {f.layer for f in found}, found


def test_a_history_whose_parsers_define_no_specific_name_is_refused(repo):
    """The floor under L3: an empty derivation exits 2, never 0."""
    r = repo["root"]
    _git(r, "switch", "-q", "--detach", repo["base"])
    thin = _commit(r, {SOC2: "def f():\n    return 1\n"}, "a parser with no specific name")
    p = _candidate(repo, {"README.md": "changed\n"})
    with pytest.raises(pg.NothingToCheck, match="no parser-only names"):
        pg.guard(p, repo["base"], thin, r)


def test_a_renamed_copy_of_an_already_public_parser_is_refused(tmp_path):
    """Found reviewing #37: B (GitHub's main, 1.6.1) already carries a
    parser, so a byte-identical renamed copy in P is reachable from B and is
    in no object the push adds. P's TREE still becomes public main, so the
    blob layer reads it and refuses."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    public = _commit(r, {SOC2: PARSER, "README.md": "public\n"}, "B: the parser already public")
    moved = _commit(r, {SOC2: None}, "#32: the parser moves out")
    p = _commit(r, {"src/policyforge/other/renamed.py": PARSER}, "P: a renamed copy")
    found = pg.guard(p, public, moved, r)
    assert "L2 blob" in {f.layer for f in found}, found


def test_an_unmarked_catalog_of_licensed_rows_is_refused(repo):
    """b5 on #35: a catalog written before #10 has no envelope. Its rows
    still name the framework, by the parser's own FRAMEWORK constant."""
    rows = '[{"control_id": "X.1", "framework": "SYNTHETIC-LICENSED", "title": "t"}]'
    p = _candidate(repo, {"data/frameworks/synthetic/controls.json": rows})
    assert "L4 data" in _layers(repo, p)


def test_the_framework_name_matches_however_it_is_written(repo):
    rows = '[{"control_id": "X.1", "framework": "Synthetic Licensed"}]'
    p = _candidate(repo, {"data/x/controls.json": rows})
    assert "L4 data" in _layers(repo, p)


ROWS = '[{"control_id": "X.1", "framework": "SYNTHETIC-LICENSED"}]'


def _big_rows(n: int = 30_000) -> list[dict]:
    return [
        {"control_id": f"X.{i}", "framework": "SYNTHETIC-LICENSED", "title": "t" * 60}
        for i in range(n)
    ]


@pytest.mark.parametrize("enveloped", [False, True], ids=["rows-only", "enveloped"])
def test_a_catalog_of_any_size_is_read(repo, enveloped):
    """9b on #35: a 2 MB cap skipped a 2.36 MB licensed catalog, exit 0."""
    rows = _big_rows()
    body = json.dumps({"licence": "licensed", "controls": rows} if enveloped else rows)
    assert len(body) > 2_500_000
    p = _candidate(repo, {"data/x/controls.json": body})
    assert "L4 data" in _layers(repo, p)


def test_a_utf16_catalog_is_parsed(repo):
    """NULs in the first 8000 bytes stop L3's text scan, not L4's parse."""
    r = repo["root"]
    _git(r, "switch", "-q", "--detach", repo["base"])
    path = r / "data" / "x" / "controls.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps({"licence": "licensed", "controls": []}).encode("utf-16"))
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "utf-16 catalog")
    p = _git(r, "rev-parse", "HEAD").strip()
    assert "L4 data" in _layers(repo, p)


def test_a_framework_re_exported_from_the_public_assembler_is_still_known(repo):
    """#38 made HITRUST's parser import FRAMEWORK from the public levelled.py.
    Reading only the assignment form lost the name; the re-export is followed
    (1d, reviewing #38). No fixed list here, so only that path can name it."""
    r = repo["root"]
    _git(r, "switch", "-q", "--detach", repo["base"])
    reexport = _commit(
        r,
        {
            SOC2: PARSER.replace(
                'FRAMEWORK = "SYNTHETIC-LICENSED"\n', "from .levelled import FRAMEWORK\n"
            ),
            "src/policyforge/ingest/levelled.py": PUBLIC_ASSEMBLY
            + '\nFRAMEWORK = "SYNTHETIC-LICENSED"\n',
        },
        "the constant moves to the public assembler",
    )
    p = _candidate(repo, {"data/x/controls.json": ROWS})
    found = pg.guard(p, repo["base"], reexport, r)
    assert "L4 data" in {f.layer for f in found}, found


def test_the_public_fixed_list_names_a_framework_on_its_own(repo):
    """The second instrument: no FRAMEWORK anywhere in the parsers, only the
    public list (#36; 80's ruling 562). It must still name the framework."""
    r = repo["root"]
    _git(r, "switch", "-q", "--detach", repo["base"])
    listed = _commit(
        r,
        {
            SOC2: PARSER.replace('FRAMEWORK = "SYNTHETIC-LICENSED"\n', ""),
            "src/policyforge/plugins.py": (
                'LICENSED_FRAMEWORK_NAMES = {"etl-synthetic": "SYNTHETIC-LICENSED"}\n'
            ),
        },
        "only the public list names it",
    )
    p = _candidate(repo, {"data/x/controls.json": ROWS})
    found = pg.guard(p, repo["base"], listed, r)
    assert "L4 data" in {f.layer for f in found}, found


def test_a_source_whose_parsers_name_no_framework_is_refused(repo):
    """The floor under L4's rows rule: no FRAMEWORK constant, nothing to
    recognise an unmarked catalog by, so exit 2, never a pass."""
    r = repo["root"]
    _git(r, "switch", "-q", "--detach", repo["base"])
    unnamed = _commit(
        r, {SOC2: PARSER.replace('FRAMEWORK = "SYNTHETIC-LICENSED"\n', "")}, "unnamed"
    )
    p = _candidate(repo, {"README.md": "changed\n"})
    with pytest.raises(pg.NothingToCheck, match="FRAMEWORK constants"):
        pg.guard(p, repo["base"], unnamed, r)


# --- must-pass ---------------------------------------------------------------


def test_the_public_tool_publishes_cleanly(repo):
    """Format hints stay public (the user, #32 comment 501), the envelope's
    marker appears as text in code and docs, and the assembly the parsers
    share moved public under #32: none of it is a finding."""
    p = _candidate(
        repo,
        {
            "src/policyforge/ingest/parser_codegen.py": (
                '"""A MyCSF CSV is a rendered SSRS report: headers like `Textbox52`,\n'
                '"Level 1 Organizational Factors:" captions, "Level FedRAMP"."""\n'
            ),
            "src/policyforge/ingest/schema.py": (
                'LICENSED_MARKER = "licensed"  # {"licence": "licensed"}\n'
            ),
            "README.md": 'A licensed catalog is written as `{"licence": "licensed", ...}`.\n',
            "data/frameworks/nist/controls.json": (
                '[{"control_id": "AC-2", "framework": "NIST SP 800-53"}]'
            ),
        },
    )
    assert pg.guard(p, repo["base"], repo["source"], repo["root"]) == []


def test_the_stated_limit_a_rewritten_copy_passes(repo):
    """Pinned so the docstring's limit stays true: renamed, with its
    identifiers rewritten, a copy passes every layer. Review of P covers it."""
    rewritten = PARSER.replace("parse_synthetic_criteria", "read_rows").replace(
        "Synthetic stand-in for a licensed parser", "Rows"
    )
    p = _candidate(repo, {"src/policyforge/other/rows.py": rewritten})
    assert pg.guard(p, repo["base"], repo["source"], repo["root"]) == []


# --- nothing to check is refused, not passed ----------------------------------


def test_an_empty_range_is_refused(repo):
    with pytest.raises(pg.NothingToCheck, match="holds no commit"):
        pg.guard(repo["base"], repo["base"], repo["source"], repo["root"])


def test_a_source_without_the_parsers_is_refused(repo):
    p = _candidate(repo, {"README.md": "changed\n"})
    # By its own message: the empty-names floor further down raises the same
    # exception, so without `match` it would answer for this one (an arm
    # removing this floor survived until the message was pinned).
    with pytest.raises(pg.NothingToCheck, match="carries none of"):
        pg.guard(p, repo["base"], repo["base"], repo["root"])


# --- the command line: the push is conditioned on these codes -----------------


def test_exit_codes(repo):
    def code(candidate, source):
        return pg.main(
            [
                "--candidate", candidate,
                "--base", repo["base"],
                "--source", source,
                "--root", str(repo["root"]),
            ]
        )  # fmt: skip

    clean = _candidate(repo, {"README.md": "changed\n"})
    dirty = _candidate(repo, {SOC2: PARSER})
    assert code(clean, repo["source"]) == 0
    assert code(dirty, repo["source"]) == 1
    assert code(repo["base"], repo["source"]) == 2
