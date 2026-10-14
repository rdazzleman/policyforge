"""A licensed catalog never reaches a bundled file through a link (#319, CR-29).

The bundled-directory guard asked about DIRECTORIES: where a new file would
land. An `--out` outside `data/frameworks/` that is a hard link to a bundled
`controls.json` passed it, and writing the catalog changed the bundled file,
with exit 0. ba measured that through the real `etl-govramp --force` on
Windows (NTFS) and on Linux (Python 3.12, in the runtime image) before the
fix. Two layers now, each covering the other's miss (80's ruling):

- the guard asks whether the target file, or the manifest beside it, IS a
  bundled file (`_bundled_file_at`), and refuses by name;
- the catalog and manifest are written as a new file moved over the name
  (`replace_text_lf`), so a link the guard misses still doesn't write
  through.

Every case checks the disk: the bundled file's bytes, before and after.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests import licensed_fakes
from tests.test_cli import _hitrust

BUNDLED_CATALOG = b'[{"control_id": "PUBLIC-1", "title": "an invented public control"}]\n'
BUNDLED_MANIFEST = b"id: govramp\nlicence: public-domain\n"


@pytest.fixture(autouse=True)
def _fake_licensed_plugin(monkeypatch):
    """The licensed parsers are a separately distributed plugin (#32); a fake
    answers the parse, because the write guards are what these pin."""
    licensed_fakes.install(monkeypatch)


def _govramp(monkeypatch, tmp_path, *args):
    import policyforge.cli as cli_mod

    matrix = licensed_fakes.build_workbook(tmp_path / "GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx")
    return CliRunner().invoke(cli_mod.cli, ["etl-govramp", "--export", str(matrix), *args])


ETLS = {"etl-govramp": _govramp, "etl-hitrust": _hitrust}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project with a bundled catalog and manifest, and a local_content/."""
    import policyforge.cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    bundled = tmp_path / "data" / "frameworks" / "govramp"
    bundled.mkdir(parents=True)
    (bundled / "controls.json").write_bytes(BUNDLED_CATALOG)
    (bundled / "framework.yaml").write_bytes(BUNDLED_MANIFEST)
    (tmp_path / "local_content" / "byoc").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _bundled(project: Path, name: str) -> Path:
    return project / "data" / "frameworks" / "govramp" / name


OUT = "local_content/byoc/controls.json"


@pytest.mark.parametrize("etl", ETLS)
def test_an_out_hard_linked_to_a_bundled_catalog_is_refused(project, monkeypatch, etl):
    os.link(_bundled(project, "controls.json"), project / OUT)

    result = ETLS[etl](monkeypatch, project, "--out", OUT, "--allow-undeclared-licensed")

    assert result.exit_code != 0, result.output
    assert "is the same file as" in result.output
    assert "must never be written there" in result.output
    assert _bundled(project, "controls.json").read_bytes() == BUNDLED_CATALOG


def test_a_manifest_hard_linked_to_a_bundled_one_is_refused(project, monkeypatch):
    """The catalog's manifest is written beside it, so it is asked too: here
    the catalog is a new file and only the manifest is the link."""
    os.link(_bundled(project, "framework.yaml"), project / "local_content/byoc/framework.yaml")

    result = _govramp(monkeypatch, project, "--out", OUT, "--allow-undeclared-licensed")

    assert result.exit_code != 0, result.output
    assert "framework.yaml is the same file as" in result.output
    assert _bundled(project, "framework.yaml").read_bytes() == BUNDLED_MANIFEST
    assert not (project / OUT).exists()


def _can_symlink() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.symlink(Path(tmp) / "a", Path(tmp) / "b")
        except OSError:  # Windows without the symlink privilege
            return False
    return True


# Defined only where a symlink can be made, NOT skipped elsewhere: a skip on
# Windows would fail `scripts/skip_gate.py` in every local gate, and listing it
# in tests/ci_expected_skips.txt would fail CI, where Linux runs it. So it runs
# in CI and wherever symlinks work, and on such a Windows machine it isn't
# collected. ba measured the symlink case in the Linux container before the
# fix: already refused, by the resolved-path spelling test.
if _can_symlink():

    def test_an_out_symlinked_to_a_bundled_catalog_is_refused(project, monkeypatch):
        os.symlink(_bundled(project, "controls.json"), project / OUT)

        result = _govramp(monkeypatch, project, "--out", OUT, "--allow-undeclared-licensed")

        assert result.exit_code != 0, result.output
        assert _bundled(project, "controls.json").read_bytes() == BUNDLED_CATALOG


def test_past_the_check_the_write_replaces_rather_than_writes_through(project, monkeypatch):
    """The second layer on its own: with the file check answering "not a
    bundled file", the catalog still lands in a NEW file at the --out name,
    and the bundled file keeps its bytes."""
    from policyforge.cli import etl

    monkeypatch.setattr(etl, "_bundled_file_at", lambda path: None)
    os.link(_bundled(project, "controls.json"), project / OUT)

    result = _govramp(monkeypatch, project, "--out", OUT, "--allow-undeclared-licensed")

    assert result.exit_code == 0, result.output
    assert _bundled(project, "controls.json").read_bytes() == BUNDLED_CATALOG
    written = project / OUT
    assert not os.path.samefile(written, _bundled(project, "controls.json"))
    assert b"licen" in written.read_bytes()


def test_a_new_out_under_local_content_is_still_written(project, monkeypatch):
    """What the guard must ALLOW: the ordinary case."""
    result = _govramp(monkeypatch, project, "--out", OUT, "--allow-undeclared-licensed")

    assert result.exit_code == 0, result.output
    assert b"X.1" in (project / OUT).read_bytes()
    assert (project / "local_content/byoc/framework.yaml").exists()
    assert _bundled(project, "controls.json").read_bytes() == BUNDLED_CATALOG


def test_an_existing_catalog_of_the_users_is_overwritten(project, monkeypatch):
    """A re-import over the user's own earlier catalog still works: a file
    that exists but is no bundled file is not refused."""
    (project / OUT).write_text("[]\n", encoding="utf-8")

    result = _govramp(monkeypatch, project, "--out", OUT, "--allow-undeclared-licensed")

    assert result.exit_code == 0, result.output
    assert b"X.1" in (project / OUT).read_bytes()


# ---- the helper ----------------------------------------------------------


def test_replace_text_lf_leaves_a_hard_linked_twin_alone(tmp_path):
    from policyforge.textfile import replace_text_lf

    twin = tmp_path / "twin.json"
    twin.write_bytes(b"original\n")
    target = tmp_path / "target.json"
    os.link(twin, target)

    replace_text_lf(target, "new\r\ntext\n")

    assert twin.read_bytes() == b"original\n"
    assert target.read_bytes() == b"new\ntext\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["target.json", "twin.json"]


def test_a_failed_replace_leaves_the_file_and_no_temporary(tmp_path, monkeypatch):
    from policyforge.textfile import replace_text_lf

    target = tmp_path / "target.json"
    target.write_bytes(b"kept\n")

    def refuse(*_args):
        raise OSError("simulated: the move failed")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="simulated"):
        replace_text_lf(target, "lost\n")

    assert target.read_bytes() == b"kept\n"
    assert [p.name for p in tmp_path.iterdir()] == ["target.json"]
