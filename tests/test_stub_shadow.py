"""Tests for #417: a catalog with controls.json wins over a README-only stub."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import policyforge.cli as cli_mod
from policyforge.frameworks.registry import catalogs_by_id

_FAKE_CONTROL = {
    "control_id": "01.a",
    "title": "Access Control Requirement",
    "framework": "HITRUST CSF",
    "framework_version": "v11",
    "family": None,
    "family_abbr": None,
    "baseline": None,
    "control_statement": "Faked requirement statement for testing.",
    "discussion": "",
    "enhancements": [],
    "related_controls": [],
    "source_crosswalk": {},
    "source_path": None,
}


def test_catalog_with_controls_wins_over_readme_only_stub(tmp_path, monkeypatch):
    """When a framework id exists as a README-only stub under data/frameworks/
    and as a catalog with controls under local_content/, catalogs_by_id resolves
    to the one with controls.json (#417)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    # 1. README-only stub under data/frameworks/hitrust-csf/
    stub_dir = tmp_path / "data" / "frameworks" / "hitrust-csf"
    stub_dir.mkdir(parents=True)
    (stub_dir / "README.md").write_text("# HITRUST CSF\n\nInstructions...\n", encoding="utf-8")

    # 2. BYOC catalog under local_content/hitrust-csf/ with controls.json
    byoc_dir = tmp_path / "local_content" / "hitrust-csf"
    byoc_dir.mkdir(parents=True)
    (byoc_dir / "framework.yaml").write_text(
        "id: hitrust-csf\nname: HITRUST CSF\nlicence: licensed\n",
        encoding="utf-8",
    )
    (byoc_dir / "controls.json").write_text(
        json.dumps([_FAKE_CONTROL]),
        encoding="utf-8",
    )

    catalogs = catalogs_by_id()

    assert "hitrust-csf" in catalogs
    resolved = catalogs["hitrust-csf"]
    assert resolved.path.resolve() == byoc_dir.resolve()
    assert resolved.has_controls is True


def test_two_catalogs_both_with_controls_keep_first_search_path(tmp_path, monkeypatch):
    """When two catalogs both have controls.json, the first search path wins
    (preserving existing behavior)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    first_dir = tmp_path / "data" / "frameworks" / "hitrust-csf"
    first_dir.mkdir(parents=True)
    (first_dir / "framework.yaml").write_text(
        "id: hitrust-csf\nname: HITRUST CSF\nlicence: licensed\n",
        encoding="utf-8",
    )
    (first_dir / "controls.json").write_text(
        json.dumps([_FAKE_CONTROL]),
        encoding="utf-8",
    )

    second_dir = tmp_path / "local_content" / "hitrust-csf"
    second_dir.mkdir(parents=True)
    (second_dir / "framework.yaml").write_text(
        "id: hitrust-csf\nname: HITRUST CSF\nlicence: licensed\n",
        encoding="utf-8",
    )
    (second_dir / "controls.json").write_text(
        json.dumps([_FAKE_CONTROL]),
        encoding="utf-8",
    )

    catalogs = catalogs_by_id()
    assert catalogs["hitrust-csf"].path.resolve() == first_dir.resolve()


def test_of_two_readme_only_stubs_the_first_search_path_wins(tmp_path, monkeypatch):
    """b5 on PR 428: only a catalog WITH controls displaces an earlier entry.
    Between two README-only stubs the first search path is kept, so the
    "has no controls.json under <path>" refusal names that directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    first_dir = tmp_path / "data" / "frameworks" / "hitrust-csf"
    second_dir = tmp_path / "local_content" / "hitrust-csf"
    for stub in (first_dir, second_dir):
        stub.mkdir(parents=True)
        (stub / "README.md").write_text("# HITRUST CSF\n", encoding="utf-8")

    resolved = catalogs_by_id()["hitrust-csf"]
    assert resolved.path.resolve() == first_dir.resolve()
    assert resolved.has_controls is False


#: The README-only stubs `init` writes, as issue 417 names them. Hand-listed,
#: not read from the bundle, so a stub dropped from `init` shows up here as a
#: failure rather than as a smaller loop.
INIT_STUBS = ("hitrust-csf", "govramp", "soc2-tsc", "hitrust-ai")


@pytest.mark.own_config
@pytest.mark.parametrize("framework_id", INIT_STUBS)
def test_init_then_a_byoc_catalog_resolves_to_the_catalog(tmp_path, monkeypatch, framework_id):
    """The user's path (417): `policyforge init`, then a licensed ETL writes
    its catalog under `local_content/<id>/`, then `programme.catalogs` names
    the id. Before the fix the stub won, and every `--controls` command
    refused with "has no controls.json under data/frameworks/<id>"."""
    from click.testing import CliRunner

    from policyforge.cli._common import resolve_catalogs

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli_mod.cli, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    stub = tmp_path / "data" / "frameworks" / framework_id
    assert [p.name for p in stub.iterdir()] == ["README.md"], "init no longer writes this stub"

    byoc = tmp_path / "local_content" / framework_id
    byoc.mkdir(parents=True)
    (byoc / "framework.yaml").write_text("licence: licensed\n", encoding="utf-8")
    (byoc / "controls.json").write_text(json.dumps([_FAKE_CONTROL]), encoding="utf-8")

    config = {"programme": {"catalogs": [framework_id]}}
    # `map`, `coverage` and `synthesize` read the profile through this.
    (resolved,) = resolve_catalogs((), config=config)
    assert Path(resolved).resolve() == (byoc / "controls.json").resolve()
    # `generate` finds a synthesis's `derived_from` catalogs through this.
    assert catalogs_by_id(config)[framework_id].path.resolve() == byoc.resolve()
