"""A licensed BYOC catalog may only be written where the model boundary will
read it as licensed (#459, 80's ruling (a)).

The boundary (`llm/boundary.classify_path`) knows a licensed catalog by where
it sits. `etl-hitrust --out output/...` used to pass the write guard, which
asked only whether git would stage the file, and the catalog then classified
as organization-internal, which a hosted model may read. Measured on #459.

Every case runs through both ETLs that share the guard, from a project
directory, since the boundary reads its search paths relative to it.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tests import licensed_fakes
from tests.test_cli import _hitrust


@pytest.fixture(autouse=True)
def _fake_licensed_plugin(monkeypatch):
    """The licensed parsers are a separately distributed plugin (#32); these
    tests pin the public command around them, so a fake answers the parse."""
    licensed_fakes.install(monkeypatch)


def _govramp(monkeypatch, tmp_path, *args):
    import policyforge.cli as cli_mod
    from tests.licensed_fakes import build_workbook

    matrix = build_workbook(tmp_path / "GovRAMP-Controls-Matrix_Mod_Rev5_V1.06.xlsx")
    return CliRunner().invoke(cli_mod.cli, ["etl-govramp", "--export", str(matrix), *args])


ETLS = {"etl-hitrust": _hitrust, "etl-govramp": _govramp}


@pytest.fixture
def project(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    (tmp_path / "local_content").mkdir()
    # A public, bundled-style catalog, to shadow a same-named directory later
    # in the search order, and a directory whose manifest says public domain.
    bundled = tmp_path / "data" / "frameworks" / "nist-800-53-r5"
    bundled.mkdir(parents=True)
    (bundled / "controls.json").write_text("[]", encoding="utf-8")
    (bundled / "framework.yaml").write_text(
        "id: nist-800-53-r5\nlicence: public-domain\n", encoding="utf-8"
    )
    public = tmp_path / "frameworks" / "org-policy"
    public.mkdir(parents=True)
    (public / "framework.yaml").write_text(
        "id: org-policy\nlicence: public-domain\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


REFUSED = {
    "output/": "output/catalog/controls.json",
    "the project root": "controls.json",
    "a framework directory, not as controls.json": "frameworks/byoc/catalog.json",
    "shadowed by a bundled public catalog": "frameworks/nist-800-53-r5/controls.json",
    "a directory declaring itself public domain": "frameworks/org-policy/controls.json",
}
ACCEPTED = {
    "local_content/": "local_content/byoc/controls.json",
    "deep under local_content/": "local_content/a/b/export.json",
    "a new framework directory with no manifest": "frameworks/byoc/controls.json",
}


@pytest.mark.parametrize("etl", ETLS)
@pytest.mark.parametrize("where", REFUSED)
def test_a_destination_the_boundary_would_not_read_as_licensed_is_refused(
    project, monkeypatch, etl, where
):
    out = project / REFUSED[where]
    result = ETLS[etl](monkeypatch, project, "--out", str(out), "--allow-undeclared-licensed")
    assert result.exit_code != 0, result.output
    assert "would be read as" in result.output
    assert "your boundary settings for licensed content would not apply" in result.output
    assert "local_content/" in result.output, "the refusal names where to write instead"
    assert not out.exists()


@pytest.mark.parametrize("etl", ETLS)
@pytest.mark.parametrize("where", ACCEPTED)
def test_a_destination_the_boundary_reads_as_licensed_is_written(project, monkeypatch, etl, where):
    from pathlib import Path

    from policyforge.llm.boundary import LICENSED, classify_path

    out = project / ACCEPTED[where]
    result = ETLS[etl](monkeypatch, project, "--out", str(out), "--allow-undeclared-licensed")
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8")) is not None
    # The guard's promise, checked by the boundary itself after the write.
    assert classify_path(Path(ACCEPTED[where]), {}).klass == LICENSED


def test_force_does_not_bypass_the_boundary(project, monkeypatch):
    """--allow-undeclared-licensed (--force until #372) grants a repository
    permission to carry licensed content; it says nothing about which
    models may read it."""
    out = project / "output" / "controls.json"
    without = _hitrust(monkeypatch, project, "--out", str(out))
    forced = _hitrust(monkeypatch, project, "--out", str(out), "--allow-undeclared-licensed")
    assert without.exit_code != 0 and forced.exit_code != 0
    assert "would be read as" in forced.output


def test_the_guard_asks_about_the_world_after_the_write(project):
    """`frameworks/byoc/` is not a framework until its controls.json exists,
    so a check of the path as it stands calls it organization-internal and
    would refuse the one destination this project's docs name for a user's
    own catalog. Asked as it will be, it is licensed."""
    from pathlib import Path

    from policyforge.llm.boundary import LICENSED, ORG_INTERNAL, classify_path

    path = Path("frameworks/byoc/controls.json")
    assert classify_path(path, {}).klass == ORG_INTERNAL
    assert classify_path(path, {}, assume_written=True).klass == LICENSED
    assert not path.parent.exists(), "asking must not create anything"
