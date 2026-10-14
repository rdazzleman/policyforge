"""Assertion 5's content layers: a licensed parser in the released archive is
found by its bytes, whatever its member is called (#237, 80's ruling 3860).

The path rule reads member names, so a parser under any other name read
"none". These run `publish_guard`'s own L2 to L4 over the archive's files,
against the parsers in the history of the local release tag, and a tag that
gives no reference set is CANNOT ANSWER, never a pass.

No network and no licensed bytes: the archive is built here in memory, and
the source history is `test_publish_guard`'s scratch repository, whose
"parsers" are synthetic stand-ins at the licensed paths. Module names are
written as #152's placeholders.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

import pytest

from tests.licensed_text import unmask as _m
from tests.test_publish_guard import PARSER, PUBLIC_ASSEMBLY, _git, repo  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish_guard
import release_check

TAG = "v9.9.9"
TOP = "policyforge-9.9.9"
URL = "https://github.com/rdazzleman/policyforge/archive/refs/tags/v9.9.9.tar.gz"
BACKSLASH = chr(92)
CLEAN = [
    (f"{TOP}/README.md", b"The public tool.\n"),
    (f"{TOP}/src/policyforge/ingest/levelled.py", PUBLIC_ASSEMBLY.encode()),
    (
        f"{TOP}/data/frameworks/nist/controls.json",
        b'[{"control_id": "AC-2", "framework": "NIST SP 800-53"}]',
    ),
]


@pytest.fixture
def tagged(repo, monkeypatch):  # noqa: F811 - the imported fixture, extended
    """The scratch repository with its parser-carrying source tagged as the
    release, and assertion 5 pointed at it."""
    _git(repo["root"], "tag", TAG, repo["source"])
    monkeypatch.setattr(release_check, "SOURCE_ROOT", repo["root"])
    return repo


def _layers(files: list[tuple[str, bytes]]) -> set[str]:
    return {finding.layer for finding in release_check.content_findings(files, TAG)}


# ---- L2's blob id is git's ---------------------------------------------------------


@pytest.mark.parametrize("data", [b"", b"x\n", b"a\r\nb\n", b"\x00\x01binary", PARSER.encode()])
def test_blob_id_is_what_git_hash_object_gives(tmp_path, data):
    """The second instrument is git itself: a different hash, header or
    length would make L2 match nothing on an archive, silently."""
    path = tmp_path / "blob"
    path.write_bytes(data)
    git = subprocess.run(
        ["git", "hash-object", "--no-filters", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    assert publish_guard.blob_id(data) == git


# ---- found whatever the member is called --------------------------------------------


@pytest.mark.parametrize(
    ("member", "data", "layer"),
    [
        (f"{TOP}/src/policyforge/other/rows.py", PARSER, "L2 blob"),  # renamed
        (BACKSLASH.join([TOP, "src", "policyforge", "other", "rows.py"]), PARSER, "L2 blob"),
        (f"{TOP.upper()[:6]}~1/SRC/POLICY~1/OTHER/PARSER~1.PY", PARSER, "L2 blob"),  # 8.3 names
        (
            f"{TOP}/src/policyforge/other/rows.py",
            PARSER.replace("return list(records)", "return [*records]"),
            "L3 content",
        ),  # renamed and edited, keeping a parser-only name
        (
            f"{TOP}/src/policyforge/other/uses.py",
            _m("from policyforge.ingest.@SOC2_TSC@ import x\n"),
            "L3 content",
        ),
        (
            f"{TOP}/data/frameworks/x/controls.json",
            json.dumps({"licence": "licensed", "controls": []}),
            "L4 data",
        ),
        (
            f"{TOP}/data/frameworks/x/controls.json",
            json.dumps([{"control_id": "1", "framework": "Synthetic Licensed"}]),
            "L4 data",
        ),
    ],
    ids=["renamed", "backslash-named", "short-name", "edited", "import", "envelope", "rows"],
)
def test_licensed_content_is_found_under_any_name(tagged, member, data, layer):
    files = [*CLEAN, (member, data.encode())]
    assert release_check.licensed_in_archive([name for name, _ in files]) == [], (
        "premise: the path rule can't see this one"
    )
    assert layer in _layers(files)


def test_a_clean_archive_has_no_content_finding(tagged):
    """The passing case: public assembly that shares a name with the parsers,
    and a public catalog, are not findings."""
    assert release_check.content_findings(CLEAN, TAG) == []


def test_without_the_release_tag_the_content_layers_cannot_answer(repo, monkeypatch):  # noqa: F811
    monkeypatch.setattr(release_check, "SOURCE_ROOT", repo["root"])
    with pytest.raises(publish_guard.NothingToCheck):
        release_check.content_findings(CLEAN, TAG)


def test_a_tag_whose_history_has_no_parsers_cannot_answer(repo, monkeypatch):  # noqa: F811
    _git(repo["root"], "tag", TAG, repo["base"])  # B: before the parsers
    monkeypatch.setattr(release_check, "SOURCE_ROOT", repo["root"])
    with pytest.raises(publish_guard.NothingToCheck, match="carries none of"):
        release_check.content_findings(CLEAN, TAG)


# ---- end to end through main() ------------------------------------------------------


FORMULA = f"""class Policyforge < Formula
  url "{URL}"
  sha256 "{"a" * 64}"
end
"""


def _targz(files: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        top = tarfile.TarInfo(TOP)  # a directory member, as GitHub's archives carry
        top.type, top.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(top)
        for name, data in files:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _assertion_5(monkeypatch, capsys, files: list[tuple[str, bytes]]) -> tuple[str, str]:
    """The real `main()` with the formula and docker stubbed and the archive
    served from memory. Returns assertion 5's section and the whole report:
    other assertions read this checkout and fail here whatever 5 says, so its
    own FAILED lines speak for 5, not the exit code."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=None: _Response(_targz(files))
    )
    monkeypatch.setattr(release_check, "fetch_formula", lambda url=None: FORMULA)
    monkeypatch.setattr(release_check, "hash_of", lambda url: "a" * 64)
    monkeypatch.setattr(
        release_check,
        "run_install_check",
        lambda version, image=None: (False, ["docker is not on PATH"]),
    )
    release_check.main(["--version", "9.9.9", "--allow-skip", "install"])
    report = capsys.readouterr().out
    start = report.index("5. The released archive carries no licensed parser")
    return report[start : report.index("=" * 62, report.index("-> ", start))], report


def test_main_reports_a_renamed_parser_present_where_the_path_rule_says_none(
    tagged, monkeypatch, capsys
):
    """The issue's case: under a renamed path, assertion 5 read "none". The
    path layer still does; the content layers don't."""
    section, report = _assertion_5(
        monkeypatch, capsys, [*CLEAN, (f"{TOP}/src/policyforge/other/rows.py", PARSER.encode())]
    )
    path_part, _, content_part = section.partition("content layers")
    assert "-> none" in path_part, section
    assert "L2 blob" in content_part and "-> PRESENT" in content_part, section
    assert "FAILED: the released archive carries licensed content" in report


def test_main_passes_a_clean_archive_through_both_layers(tagged, monkeypatch, capsys):
    section, report = _assertion_5(monkeypatch, capsys, CLEAN)
    path_part, _, content_part = section.partition("content layers")
    assert "-> none" in path_part and "-> none" in content_part, section
    assert "PRESENT" not in section and "CANNOT ANSWER" not in section, section
    assert "content layers could not run" not in report
    assert "carries licensed content" not in report


def test_main_without_the_tag_cannot_answer_and_still_reports_the_path_layer(
    repo,  # noqa: F811
    monkeypatch,
    capsys,
):
    """80's ruling: no reference set is CANNOT ANSWER and a failure, and the
    path layer runs and reports either way."""
    monkeypatch.setattr(release_check, "SOURCE_ROOT", repo["root"])
    licensed_path = f"{TOP}/" + _m("src/policyforge/ingest/@HITRUST@.py")
    section, report = _assertion_5(monkeypatch, capsys, [*CLEAN, (licensed_path, b"x")])
    path_part, _, content_part = section.partition("content layers")
    assert licensed_path in path_part and "-> PRESENT" in path_part, section
    assert "-> CANNOT ANSWER" in content_part and "-> none" not in content_part, section
    assert "FAILED: the content layers could not run" in report
