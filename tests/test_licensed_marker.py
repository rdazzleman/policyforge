"""A licensed catalog carries its licence in the file (#459 (b), 80's ruling).

(a) stopped a licensed ETL writing where the boundary would not read it as
licensed. It could not help a catalog already saved elsewhere by 1.6.1, or
one moved after it was written, because the boundary knew a licensed catalog
only by its path. Now the licensed ETLs write an envelope,
`{"licence": "licensed", "controls": [...]}`, `classify_path` reads it, and
the stricter of path and file wins. An unmarked catalog in a licensed ETL's
shape, saved outside local_content/, is warned about, never refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policyforge.ingest.schema import LICENSED_MARKER, catalog_json, load_controls, read_catalog
from policyforge.llm import boundary
from policyforge.plugins import LICENSED_FRAMEWORK_NAMES

#: The framework names the licensed ETLs write, from the public fixed list:
#: the parsers themselves are in the separately distributed plugin (#32).
HITRUST = LICENSED_FRAMEWORK_NAMES["etl-hitrust"]
GOVRAMP = LICENSED_FRAMEWORK_NAMES["etl-govramp"]
SOC2 = LICENSED_FRAMEWORK_NAMES["etl-soc2-tsc"]

ROOT = Path(__file__).resolve().parents[1]
ROWS = [
    {
        "control_id": "01.a",
        "title": "t",
        "framework": HITRUST,
        "framework_version": "synthetic",
    }
]
HOSTED = {"llm": {"provider": "anthropic", "model": "any"}}


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "output").mkdir()
    (tmp_path / "local_content").mkdir()
    public = tmp_path / "frameworks" / "pub"
    public.mkdir(parents=True)
    (public / "framework.yaml").write_text("id: pub\nlicence: public-domain\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write(relative: str, *, marked: bool, rows=ROWS) -> Path:
    path = Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(catalog_json(rows, licence=LICENSED_MARKER if marked else None), "utf-8")
    return path


# ---- the marker is read, and the stricter class wins ------------------------------------


def test_a_marked_catalog_outside_local_content_is_licensed_and_refused_to_a_hosted_model(project):
    path = _write("output/hitrust/controls.json", marked=True)
    assert boundary.classify_path(path, {}).klass == boundary.LICENSED
    with pytest.raises(boundary.BoundaryViolation):
        boundary.enforce(path, HOSTED)


def test_the_marker_wins_over_a_public_domain_manifest(project):
    path = _write("frameworks/pub/controls.json", marked=True)
    assert boundary.classify_path(path, {}).klass == boundary.LICENSED


def test_the_path_still_wins_over_an_unmarked_file(project):
    """Stricter class wins in both directions: no marker never loosens a path."""
    path = _write("local_content/x/controls.json", marked=False)
    assert boundary.classify_path(path, {}).klass == boundary.LICENSED


def test_absent_a_marker_todays_rules_apply(project):
    assert boundary.classify_path(_write("output/plain.json", marked=False), {}).klass == (
        boundary.ORG_INTERNAL
    )
    notes = Path("output/notes.md")
    notes.write_text("# not a catalog", encoding="utf-8")
    assert boundary.classify_path(notes, {}).klass == boundary.ORG_INTERNAL
    broken = Path("output/broken.json")
    broken.write_text("{not json", encoding="utf-8")
    assert boundary.classify_path(broken, {}).klass == boundary.ORG_INTERNAL


# ---- one reader for both shapes ----------------------------------------------------------


def test_every_reader_reads_the_envelope(project):
    from policyforge.frameworks.registry import known_framework_names
    from policyforge.zardoz.skills import _paths_by_framework

    path = _write("local_content/hitrust-csf/controls.json", marked=True)
    assert [c.control_id for c in load_controls(path)] == ["01.a"]
    assert read_catalog(path) == (ROWS, LICENSED_MARKER)
    assert HITRUST in known_framework_names(roots=[Path("local_content")])
    assert _paths_by_framework([path]), "the shell's catalog paths skipped an envelope"


def test_anything_else_is_refused_not_read_as_empty(project):
    path = Path("output/odd.json")
    path.write_text(json.dumps({"rows": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="not a catalog"):
        read_catalog(path)


def test_every_shipped_catalog_is_still_a_plain_list():
    """Shipped catalogs are public and byte-identical to before (80's
    condition): only the licensed ETLs write the envelope."""
    for path in sorted((ROOT / "data" / "frameworks").glob("*/controls.json")):
        rows, licence = read_catalog(path)
        assert licence is None and isinstance(rows, list), path


# ---- the licensed ETLs write the envelope ----------------------------------------------------


# ---- existing unmarked files: warned, never refused ------------------------------------------


def test_an_unmarked_byoc_shaped_catalog_outside_local_content_is_flagged(project):
    assert boundary.unmarked_licensed_catalog(
        _write("output/hitrust/controls.json", marked=False), {}
    )
    govramp_rows = [dict(ROWS[0], framework=GOVRAMP)]
    assert boundary.unmarked_licensed_catalog(
        _write("output/g/controls.json", marked=False, rows=govramp_rows), {}
    )


@pytest.mark.parametrize(
    ("case", "relative", "marked", "framework"),
    [
        ("marked", "output/a/controls.json", True, HITRUST),
        ("inside local_content", "local_content/a/controls.json", False, HITRUST),
        ("a public framework's rows", "output/b/controls.json", False, "NIST 800-53"),
    ],
)
def test_these_are_not_flagged(project, case, relative, marked, framework):
    path = _write(relative, marked=marked, rows=[dict(ROWS[0], framework=framework)])
    assert not boundary.unmarked_licensed_catalog(path, {}), case


def test_check_warns_on_an_unmarked_byoc_catalog_in_the_project(project):
    from policyforge.content.check import _check_unmarked_licensed_catalogs

    _write("output/hitrust/controls.json", marked=False)
    _write("local_content/ok/controls.json", marked=False)
    _write("output/marked/controls.json", marked=True)
    findings = _check_unmarked_licensed_catalogs(Path.cwd(), {})
    assert [f.path.replace("\\", "/") for f in findings] == ["output/hitrust/controls.json"]
    assert all(f.severity == "warning" for f in findings)
    assert "your boundary settings haven't been applied to it" in findings[0].message


def test_synthesize_warns_and_does_not_refuse(project, capsys):
    from policyforge.cli.documents import _enforce_catalogs

    path = _write("output/hitrust/controls.json", marked=False)
    classified = _enforce_catalogs([path], {})
    assert [c.klass for c in classified] == [boundary.ORG_INTERNAL]
    assert "looks like a licensed BYOC export" in capsys.readouterr().err


def test_policyforge_check_reports_it(project):
    """On the user's path: `check_tree`, as `policyforge check` runs it, not
    the scan function alone (an arm removing the call from `check_tree` left
    the direct test green)."""
    from policyforge.content.check import check_tree

    _write("output/hitrust/controls.json", marked=False)
    content = project / "docs"
    (content / "standards").mkdir(parents=True)
    (content / "standards" / "s.md").write_text(
        "---\ntitle: S\ntier: standard\n---\n\n# S\n", encoding="utf-8"
    )
    report = check_tree(content)
    flagged = [f for f in report.findings if "looks like a licensed BYOC export" in f.message]
    assert [f.path.replace("\\", "/") for f in flagged] == ["output/hitrust/controls.json"]


def test_drift_reads_a_committed_envelope(project, monkeypatch):
    """1d on #459 (b): `drift` reads the COMMITTED catalog as text, through
    `git show`, and parsed it with `json.loads` alone, which raised on an
    envelope. A user with `allow_licensed_in_repo` commits their ETL output,
    so `policyforge drift` and the shell's drift skill met one. Measured on a
    real repository here, on the path `drift` takes."""
    import os
    import subprocess

    from policyforge.frameworks.drift import load_previous

    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    path = _write("frameworks/hitrust-csf/controls.json", marked=True)
    for argv in (
        ["git", "init", "-q"],
        ["git", "add", str(path)],
        ["git", "commit", "-q", "-m", "c"],
    ):
        subprocess.run(argv, check=True, env=env, capture_output=True)
    previous = load_previous(path)
    assert previous is not None, "git show returned nothing: the harness, not the reader"
    assert [c.control_id for c in previous] == ["01.a"]


# ---- check classifies with the project's config (9b on #10) ----------------------------------


VAULT = {"frameworks": {"search_paths": ["vault"]}}


def _vault_and_docs(project: Path) -> Path:
    """A licensed location declared by the user's config, holding an
    unmarked HITRUST-shaped catalog, the shape 9b measured; and the same
    rows under output/, where no configuration makes them licensed."""
    manifest = project / "vault" / "hitrust"
    manifest.mkdir(parents=True)
    (manifest / "framework.yaml").write_text("id: hitrust\nlicence: licensed\n", encoding="utf-8")
    _write("vault/hitrust/controls.json", marked=False)
    _write("output/hitrust/controls.json", marked=False)
    docs = project / "docs"
    (docs / "standards").mkdir(parents=True)
    (docs / "standards" / "s.md").write_text(
        "---\ntitle: S\ntier: standard\n---\n\n# S\n", encoding="utf-8"
    )
    return docs


def _flagged(output: str) -> list[str]:
    return sorted(
        line.replace("\\", "/")
        for line in output.splitlines()
        if "looks like a licensed BYOC export" in line
    )


def test_policyforge_check_does_not_warn_on_a_catalog_the_config_holds_as_licensed(
    project, monkeypatch
):
    """Without the config, `check` told this user their boundary settings
    hadn't been applied, while `synthesize` with the same config refused the
    file. On the user's path: the real `check` command and its config seam."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    docs = _vault_and_docs(project)
    assert boundary.classify_path(Path("vault/hitrust/controls.json"), VAULT).klass == (
        boundary.LICENSED
    ), "the harness: the config must make the vault licensed"
    monkeypatch.setattr(cli_mod, "load_config", lambda: VAULT)

    result = CliRunner().invoke(cli_mod.cli, ["check", "--content-dir", str(docs)])

    flagged = _flagged(result.output)
    assert len(flagged) == 1, result.output
    assert "output/hitrust/controls.json" in flagged[0]
    assert not any("vault/" in line for line in flagged)


def test_the_shells_check_skill_uses_its_config_too(project):
    from types import SimpleNamespace

    from policyforge.zardoz.skills import _check

    docs = _vault_and_docs(project)
    report = _check(SimpleNamespace(content_dir=str(docs), config=VAULT), [])

    flagged = _flagged(report)
    assert len(flagged) == 1, report
    assert "output/hitrust/controls.json" in flagged[0]


def test_the_boundary_check_cannot_be_called_without_a_config(project):
    """1d on #10: a caller that left the config out gave 9b's false warning.
    Required, so forgetting it fails at once rather than classifying wrong."""
    path = _write("output/hitrust/controls.json", marked=False)
    with pytest.raises(TypeError):
        boundary.unmarked_licensed_catalog(path)  # type: ignore[call-arg]


def test_an_unmarked_soc2_shaped_catalog_outside_local_content_is_flagged(project):
    rows = [{"control_id": "CC1.1", "title": "t", "framework": SOC2}]
    path = Path("output") / "tsc" / "controls.json"
    path.parent.mkdir(parents=True)
    path.write_text(catalog_json(rows), encoding="utf-8")
    assert SOC2 in boundary.licensed_etl_frameworks()
    assert boundary.unmarked_licensed_catalog(path, {})
