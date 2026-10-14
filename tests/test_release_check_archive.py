"""`release_check.archive_names`, and assertion 5 through it, asserted (#233).

Assertion 5 is the check that no licensed parser shipped in the published
archive. `licensed_in_archive` judged name lists, but `archive_names`, which
supplies them, had no test that asserted what it returns: the report tests
reached it over the real network and asserted something else, and PR 231's
stub removed even that exercise. A broken `archive_names` (wrong names, an
empty list, a swallowed error) would have shown only at a real cut, inside
the check that guards licensed content, and an empty list passes everything.

No network: `urllib.request.urlopen` serves a `.tar.gz` built here in memory,
and records each URL it is asked for.

The licensed path is written by hand, with its module as a placeholder
(`tests/licensed_text.py`, #152): a public test file never spells a licensed
module name. Its premise is asserted against `LICENSED_PATHS`, so a set
that loses it goes red here instead of leaving these tests checking nothing.
"""

from __future__ import annotations

import io
import sys
import tarfile
import urllib.request
from pathlib import Path

import pytest

from tests.licensed_text import unmask as _m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release_check

#: GitHub's archive shape: one top directory, then the tree.
TOP = "policyforge-9.9.9"
#: Written by hand from `scripts/licensed_modules.py`'s layout, not read from
#: it (1d on #233): a licensed parser as it would sit in a source tree.
LICENSED = "src/policyforge/ingest/" + _m("@HITRUST@") + ".py"
CLEAN = [
    f"{TOP}/README.md",
    f"{TOP}/src/policyforge/__init__.py",
    f"{TOP}/src/policyforge/ingest/oscal.py",
]
URL = "https://github.com/rdazzleman/policyforge/archive/refs/tags/v9.9.9.tar.gz"


def _targz(names: list[str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in names:
            data = f"# {name}\n".encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _serve(monkeypatch, names: list[str] | None = None, error: Exception | None = None):
    """Patch `urlopen` to serve a tarball of `names` (or raise `error`);
    returns the list of URLs it was asked for."""
    asked: list[str] = []

    def urlopen(url, timeout=None):
        asked.append(url)
        if error is not None:
            raise error
        return _Response(_targz(names or []))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return asked


# ---- the premise ------------------------------------------------------------


def test_the_hand_written_licensed_path_is_one_the_check_knows():
    """If this fails, the set lost the module or moved its directory, and
    the tests below would be checking a path the guard no longer looks for."""
    assert LICENSED in release_check.LICENSED_PATHS, release_check.LICENSED_PATHS


# ---- archive_names -----------------------------------------------------------


def test_archive_names_returns_exactly_the_members_served(monkeypatch):
    names = [*CLEAN, f"{TOP}/{LICENSED}"]
    asked = _serve(monkeypatch, names)
    assert release_check.archive_names(URL) == names
    assert asked == [URL], "fetched once, from the URL given"


def test_archive_names_of_an_empty_archive_is_empty(monkeypatch):
    _serve(monkeypatch, [])
    assert release_check.archive_names(URL) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/rdazzleman/policyforge/archive/refs/tags/v9.9.9.tar.gz",
        "file:///tmp/policyforge-9.9.9.tar.gz",
        "ftp://example.invalid/policyforge.tar.gz",
        "HTTPS://github.com/rdazzleman/policyforge/archive/refs/tags/v9.9.9.tar.gz",
    ],
)
def test_a_non_https_url_is_refused_before_any_fetch(monkeypatch, url):
    asked = _serve(monkeypatch, CLEAN)
    with pytest.raises(ValueError, match="refusing a non-https archive URL"):
        release_check.archive_names(url)
    assert asked == [], "nothing was fetched"


@pytest.mark.parametrize(
    "member",
    [
        f"{TOP}/{LICENSED}",  # GitHub's shape
        LICENSED,  # no top directory (PR 235 pinned this as missed; #236)
        f"{TOP}/vendor/{LICENSED}",  # under another prefix (likewise)
        f"./{LICENSED}",  # a tar written from `.`
        f"a/b/c/{LICENSED}",  # any depth
        # Names that normalise to the entry, which tarfile extracts to the
        # plain path (1d on PR 238, measured): matched on posixpath.normpath.
        f"{TOP}/" + LICENSED.replace("/", "//", 1),
        f"{TOP}/" + LICENSED.replace("/", "/./", 1),
        f"{TOP}/" + LICENSED.replace("/", "/x/../", 1),
        # A directory named like it: normpath drops the `/`, and tarfile reports
        # a directory member without it anyway, so this errs toward failing.
        f"{TOP}/{LICENSED}/",
    ],
    # Named, not shown: the unmasked path would carry a licensed module's name
    # into every test log.
    ids=[
        "github-top",
        "root",
        "other-prefix",
        "dot-slash",
        "deep",
        "double-slash",
        "dot-segment",
        "dot-dot-segment",
        "directory",
    ],
)
def test_a_licensed_path_is_found_at_any_depth(member):
    """80's ruling on #236: the entry itself, or `/` plus the entry, anywhere,
    once the name is normalised. Reported in the archive's own spelling."""
    assert release_check.licensed_in_archive([*CLEAN, member]) == [member]


@pytest.mark.parametrize(
    "member",
    [
        f"{TOP}/my{LICENSED}",  # `.../mysrc/policyforge/...`: no component boundary
        f"{TOP}/docs/" + LICENSED.rsplit("/", 1)[1],  # the basename alone, elsewhere
        f"{TOP}/{LICENSED}.bak",  # the entry followed by more
    ],
    ids=["no-boundary", "basename-only", "longer-name"],
)
def test_a_name_that_only_resembles_a_licensed_path_is_not_one(member):
    """The passing cases for the widening: the `/` boundary and the whole
    entry are the PATH rule, so these names don't match it (never by
    basename). That says nothing about their content: a copy of a licensed
    parser under another name or directory is #237's content layers' to find.
    Checked in upper case too, so the case-folding doesn't widen past the
    boundary (1d on PR 238)."""
    assert release_check.licensed_in_archive([*CLEAN, member, member.upper()]) == []


def test_a_differently_capitalised_licensed_path_is_found_in_its_own_spelling():
    """80's ruling on PR 238 (ba's question): case-insensitive, since on
    Windows and macOS's default filesystem it's the same file, and this
    check is about publishing those bytes. The finding keeps the archive's
    spelling."""
    upper_at_depth = f"{TOP}/{LICENSED.upper()}"
    mixed_at_root = "/".join(part.capitalize() for part in LICENSED.split("/"))
    assert upper_at_depth != f"{TOP}/{LICENSED}" and mixed_at_root != LICENSED
    found = release_check.licensed_in_archive([*CLEAN, upper_at_depth, mixed_at_root])
    assert found == sorted([upper_at_depth, mixed_at_root])


def test_a_sibling_that_only_starts_like_a_licensed_module_is_not_one():
    """The boundary is unchanged by case-folding: a notes file beside the
    module, named after it, is not the module."""
    notes = f"{TOP}/" + LICENSED.rsplit(".", 1)[0] + "_notes.md"
    assert release_check.licensed_in_archive([*CLEAN, notes, notes.upper()]) == []


#: A backslash, built so no editor or shell turns it into an escape.
BACKSLASH = chr(92)


def _with_backslashes(path: str) -> str:
    return path.replace("/", BACKSLASH)


@pytest.mark.parametrize(
    "member",
    [
        _with_backslashes(f"{TOP}/{LICENSED}"),  # every separator a backslash, at depth
        f"{TOP}/" + _with_backslashes(LICENSED),  # mixed: `/` then backslashes
        _with_backslashes(f"{TOP}/vendor/") + LICENSED,  # mixed the other way
        _with_backslashes(LICENSED.upper()),  # and in another case
    ],
    ids=["all-backslash", "slash-then-backslash", "backslash-then-slash", "backslash-upper"],
)
def test_a_backslash_separated_licensed_path_is_found_in_its_own_spelling(member):
    """80's ruling (a) on #239: on win32, `tarfile` and Windows' `tar.exe` both
    extract a backslash-named member as the nested path (ba measured both), so
    it publishes the licensed module at its place. Reported as the archive
    spells it."""
    assert BACKSLASH in member
    assert release_check.licensed_in_archive([*CLEAN, member]) == [member]


@pytest.mark.parametrize(
    "member",
    [
        _with_backslashes(f"{TOP}/docs/") + LICENSED.rsplit("/", 1)[1],  # basename elsewhere
        _with_backslashes(f"{TOP}/my{LICENSED}"),  # no component boundary
        _with_backslashes(f"{TOP}/" + LICENSED.rsplit(".", 1)[0] + "_notes.md"),  # a sibling
    ],
    ids=["basename-only", "no-boundary", "sibling-notes"],
)
def test_backslashes_dont_widen_the_path_rule(member):
    """The passing cases: mapped to `/`, these are still not the entry, as
    their `/` spellings aren't (the boundary and the whole entry hold)."""
    assert release_check.licensed_in_archive([*CLEAN, member, member.upper()]) == []


@pytest.mark.parametrize(
    "member",
    [
        f"{TOP}/{LICENSED}.",  # a trailing dot
        f"{TOP}/{LICENSED} ",  # a trailing space
        f"{TOP}/{LICENSED}. .",  # dots and spaces together
        f"{TOP}/" + LICENSED.replace("/ingest/", "/ingest./"),  # a dot on a directory
        f"{TOP}/" + LICENSED.replace("/ingest/", "/ingest /"),  # a space on a directory
        f"{TOP}/{LICENSED}::$DATA",  # the main stream
        f"{TOP}/{LICENSED}:x",  # a named stream
        _with_backslashes(f"{TOP}/{LICENSED}.") + "::$DATA",  # with #239's backslashes
    ],
    ids=[
        "trailing-dot",
        "trailing-space",
        "dots-and-spaces",
        "dot-on-dir",
        "space-on-dir",
        "data-stream",
        "named-stream",
        "backslash-dot-stream",
    ],
)
def test_a_windows_alias_of_a_licensed_path_is_found_in_its_own_spelling(member):
    """80's ruling (a) on #306: Windows drops trailing dots and spaces from each
    component, and `::$DATA` names a file's main stream, so on win32 these
    extract at the licensed path (b5 measured each through `tarfile`).
    Reported as the archive spells it."""
    assert release_check.licensed_in_archive([*CLEAN, member]) == [member]


@pytest.mark.parametrize(
    "member",
    [
        f"{TOP}/{LICENSED}.bak",  # more after the entry
        f"{TOP}/" + LICENSED.replace(".py", ".pyc.txt"),  # another extension
        f"{TOP}/src/policyforge/" + LICENSED.rsplit("/", 1)[1],  # outside ingest/
        f"{TOP}/{LICENSED}.bak.",  # a trailing dot doesn't reach past `.bak`
    ],
    ids=["bak", "pyc-txt", "outside-ingest", "bak-trailing-dot"],
)
def test_windows_aliasing_doesnt_widen_the_path_rule(member):
    """80's passing cases on #306: with the dots, spaces and stream dropped,
    these are still not the entry. (8.3 short names stay #237's.)"""
    assert release_check.licensed_in_archive([*CLEAN, member, member.upper()]) == []


@pytest.mark.parametrize(
    ("entry", "member"),
    [
        # A future entry spelled with capitals: found only if the entries are
        # case-folded the same way as the member (1d on PR 238).
        ("src/policyforge/ingest/Mixed_Mod.py", f"{TOP}/src/policyforge/ingest/mixed_mod.py"),
        # A future entry written unnormalised: found only if the entries are
        # normalised the same way as the member.
        ("src/policyforge//ingest/stand_in.py", f"{TOP}/src/policyforge/ingest/stand_in.py"),
    ],
    ids=["mixed-case-entry", "unnormalised-entry"],
)
def test_an_entry_spelled_differently_is_matched_the_same_way(monkeypatch, entry, member):
    """Both sides go through one normalisation, so the rule doesn't depend on
    how a future LICENSED_PATHS entry happens to be written. Stand-in names,
    not licensed modules: the set is patched for this test only."""
    monkeypatch.setattr(release_check, "LICENSED_PATHS", (entry,))
    assert release_check.licensed_in_archive([*CLEAN, member]) == [member]


def test_a_clean_archive_has_no_licensed_path():
    assert release_check.licensed_in_archive(CLEAN) == []


# ---- assertion 5, end to end through main() -----------------------------------

FORMULA = f"""class Policyforge < Formula
  desc "Generate cross-mapped security policies"
  homepage "https://github.com/rdazzleman/policyforge"
  url "{URL}"
  sha256 "{"a" * 64}"
  license "Apache-2.0"
end
"""


def _assertion_5(monkeypatch, capsys) -> str:
    """Run the real `main()` with the formula, the tap and docker stubbed, and
    `urlopen` as served: `archive_names` itself is NOT stubbed. Returns
    assertion 5's own section of the report."""
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
    return report[start : report.index("FAILED:", start) if "FAILED:" in report[start:] else None]


def test_assertion_5_reports_a_licensed_parser_in_the_archive(monkeypatch, capsys):
    asked = _serve(monkeypatch, [*CLEAN, f"{TOP}/{LICENSED}"])
    section = _assertion_5(monkeypatch, capsys)
    assert asked == [URL], "the archive the formula names is the one read"
    assert "licensed parser paths in" in section and ": 1" in section, section
    assert f"{TOP}/{LICENSED}" in section, section
    assert "-> PRESENT" in section, section


def test_assertion_5_reports_a_licensed_parser_at_the_archive_root(monkeypatch, capsys):
    """End to end, the case #236 widens: no top directory, still PRESENT."""
    _serve(monkeypatch, [*CLEAN, LICENSED])
    section = _assertion_5(monkeypatch, capsys)
    assert LICENSED in section and "-> PRESENT" in section, section


def test_assertion_5_passes_a_clean_archive(monkeypatch, capsys):
    """The passing case, or the test above could be met by a check that
    reports every archive."""
    _serve(monkeypatch, CLEAN)
    section = _assertion_5(monkeypatch, capsys)
    assert ": 0" in section and "-> none" in section, section
    assert "-> PRESENT" not in section, section


def test_assertion_5_cannot_answer_when_the_archive_cant_be_read(monkeypatch, capsys):
    """A failed fetch is a failure to answer, never a clean archive."""
    _serve(monkeypatch, error=OSError("network down"))
    section = _assertion_5(monkeypatch, capsys)
    assert "could not read the archive: OSError" in section, section
    assert "-> CANNOT ANSWER" in section, section
    assert "-> none" not in section, section
