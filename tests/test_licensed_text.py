"""The fixtures' licensed names stay masked, and come back exactly (#123).

The public release is a root commit, so the publish guard scans its whole
tree, tests included. Spelled literally, the guard's own fixtures refused the
first public 1.7.0 (9b, #123). They now spell licensed modules as
`@MODULE@` and `tests.licensed_text.unmask` restores them.
"""

from __future__ import annotations

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
