"""`policyforge init`, and the package data it depends on.

The packaging half matters as much as the command. pyproject.toml lists the
packages and the bundled files by hand, because the catalogs are mapped in
from outside src/, and a hand-kept list is exactly what drifts: a new
subpackage left off ships an install that fails on import, and a catalog
glob would ship somebody's licensed export. These tests hold both lists to
what is actually in the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from policyforge.scaffold import (
    BUNDLED_CATALOGS,
    BYOC_CATALOGS,
    CATALOG_FILES,
    CONFIG_EXAMPLES,
    GITIGNORE,
    init_project,
)

ROOT = Path(__file__).resolve().parents[1]
FRAMEWORKS = ROOT / "data" / "frameworks"


def _pyproject() -> dict:
    # Not `importorskip("tomllib")`: that skipped every packaging check on
    # Python 3.10, the floor, which is where they are least likely to have
    # been tried by hand. See tests/_pyproject.py.
    from tests._pyproject import load_pyproject

    return load_pyproject()


# ---- what ships ---------------------------------------------------------


def test_only_public_domain_catalogs_are_bundled():
    """Decided by each catalog's own manifest, not by what is on disk."""
    public = sorted(
        d.name
        for d in FRAMEWORKS.iterdir()
        if (d / "framework.yaml").is_file()
        and yaml.safe_load((d / "framework.yaml").read_text(encoding="utf-8")).get("licence")
        == "public-domain"
    )
    assert sorted(BUNDLED_CATALOGS) == public


def test_every_other_catalog_directory_is_bring_your_own():
    others = sorted(
        d.name for d in FRAMEWORKS.iterdir() if d.is_dir() and d.name not in BUNDLED_CATALOGS
    )
    assert sorted(BYOC_CATALOGS) == others
    for name in BYOC_CATALOGS:
        assert not (FRAMEWORKS / name / "controls.json").exists()


def test_the_readme_licensing_table_names_every_catalog_that_ships():
    """The root README's licensing table is where a user goes to learn
    **what they are allowed to redistribute**, and nothing was holding it
    to what actually ships.

    42 CFR Part 2 was registered in all three code places a catalog needs
    -- `BUNDLED_CATALOGS`, the `pyproject` package data, and the
    `framework-drift` job -- and was missing from that table, so a user
    reading it counted five bundled catalogs and shipped six. Every code
    registration had a test over it; the document did not.

    Derived from the tree rather than from a list, so it covers the next
    catalog as well as this one. BYOC directories are exempt: they ship no
    `controls.json`, and `test_every_other_catalog_directory_is_bring_your_own`
    is what holds that.

    **Scoped to the table, not to the file.** Searching the whole README
    held only the three catalogs whose path happens to appear once. The
    other three are referenced throughout — `nist-800-53-r5` ten times,
    `hipaa-security-rule` seven — so deleting HIPAA's licensing row left
    its path at lines 41 and 48 and the assertion passed. The guard
    covered half the catalogs, and the half it missed were the
    long-standing ones a reader is most likely to trust.
    """
    table = _licensing_table(ROOT / "README.md")

    shipping = sorted(d.name for d in FRAMEWORKS.iterdir() if (d / "controls.json").is_file())
    assert shipping, "no catalog ships a controls.json -- this test is checking nothing"

    missing = [name for name in shipping if f"data/frameworks/{name}/" not in table]
    assert not missing, (
        f"catalogs that ship but have no row in the README licensing table: {missing}. "
        f"That table is what a user reads to decide what they may redistribute."
    )


def test_the_licensing_table_slice_excludes_the_rest_of_the_readme(tmp_path):
    """The scoping itself, held against a document built for the purpose.

    Widening the slice back to the whole file does not break any assertion
    made against the real README, because every shipping catalog is in the
    table today — so the check above passes either way and the regression
    would be invisible. This is the test that fails when the scope goes.

    Synthetic rather than the real README for the same reason: it needs a
    catalog that is mentioned in the prose and **absent from the table**,
    which is precisely the state the real file must never be in.
    """
    readme = tmp_path / "README.md"
    readme.write_bytes(
        b"Install from `data/frameworks/ghost-catalog/` first.\n"
        b"\n"
        b"| Framework | Status | How |\n"
        b"| --------- | ------ | --- |\n"
        b"| **Real**  | public | `data/frameworks/real-catalog/` |\n"
        b"\n"
        b"See `data/frameworks/ghost-catalog/` for more.\n"
    )

    table = _licensing_table(readme)

    assert "data/frameworks/real-catalog/" in table
    assert "data/frameworks/ghost-catalog/" not in table, (
        "the slice is picking up prose outside the table, so a catalog "
        "mentioned anywhere in the README would pass as listed"
    )


def test_a_readme_with_no_licensing_table_refuses_rather_than_passing(tmp_path):
    """An empty slice would make every assertion against it vacuously
    true, which is the failure this file keeps finding one level down."""
    readme = tmp_path / "README.md"
    readme.write_bytes(b"No table here.\n")

    with pytest.raises(AssertionError, match="expected exactly one licensing table header"):
        _licensing_table(readme)


def _licensing_table(readme: Path) -> str:
    """Just the licensing table, from its header row to the blank line.

    Returned as its own slice so a catalog mentioned elsewhere in the
    README cannot satisfy a check about the table. Refuses rather than
    returning "" when the table cannot be found, because an empty slice
    would make every assertion against it vacuously true — the failure
    this file keeps finding one level down.
    """
    text = readme.read_bytes().decode("utf-8")
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith("| Framework")]
    if len(starts) != 1:
        raise AssertionError(
            f"expected exactly one licensing table header in {readme.name}, "
            f"found {len(starts)}. If the README grew a second table, this "
            f"helper has to be told which one is the licensing table."
        )
    start = starts[0]
    end = next(
        (i for i in range(start, len(lines)) if not lines[i].strip()),
        len(lines),
    )
    rows = lines[start:end]
    assert len(rows) > 2, f"licensing table has no rows: {rows}"
    return "\n".join(rows)


def test_package_data_names_exactly_the_files_init_copies():
    package_data = _pyproject()["tool"]["setuptools"]["package-data"]
    expected = {f"{c}/{f}" for c in BUNDLED_CATALOGS for f in CATALOG_FILES}
    expected |= {f"{c}/README.md" for c in BYOC_CATALOGS}
    assert set(package_data["policyforge._bundled.frameworks"]) == expected
    assert not any("*" in entry for entry in package_data["policyforge._bundled.frameworks"])
    assert set(package_data["policyforge._bundled.config"]) == set(CONFIG_EXAMPLES)


def test_every_package_under_src_is_listed():
    """Listed by hand, so a new subpackage must be added by hand too."""
    listed = set(_pyproject()["tool"]["setuptools"]["packages"])
    discovered = {
        ".".join(p.parent.relative_to(ROOT / "src").parts)
        for p in (ROOT / "src").rglob("__init__.py")
    }
    mapped = set(_pyproject()["tool"]["setuptools"]["package-dir"]) - {""}
    assert listed == discovered | mapped


def test_the_mapped_directories_exist():
    for package, directory in _pyproject()["tool"]["setuptools"]["package-dir"].items():
        assert (ROOT / directory).is_dir(), f"{package} maps to missing {directory}"


def test_the_license_is_declared_as_spdx():
    """Homebrew and PyPI both read it; a file pointer alone names no licence."""
    assert _pyproject()["project"]["license"] == "Apache-2.0"


# ---- the command --------------------------------------------------------


def test_init_lays_out_a_project_that_matches_the_repository(tmp_path):
    report = init_project(tmp_path / "proj")
    project = tmp_path / "proj"

    for catalog in BUNDLED_CATALOGS:
        for name in CATALOG_FILES:
            copied = project / "data" / "frameworks" / catalog / name
            # Bytes: a catalog is checked against its recorded hash.
            assert copied.read_bytes() == (FRAMEWORKS / catalog / name).read_bytes()
    for catalog in BYOC_CATALOGS:
        assert (project / "data" / "frameworks" / catalog / "README.md").is_file()
        assert not (project / "data" / "frameworks" / catalog / "controls.json").exists()
    for name in CONFIG_EXAMPLES:
        assert (project / "config" / name).read_bytes() == (ROOT / "config" / name).read_bytes()
    assert (project / "local_content" / ".gitkeep").is_file()
    assert (project / ".gitignore").read_text(encoding="utf-8") == GITIGNORE
    assert report.kept == []


def test_init_never_overwrites(tmp_path):
    init_project(tmp_path)
    edited = tmp_path / "config" / "config.example.yaml"
    edited.write_text("mine", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("my own\n", encoding="utf-8")

    report = init_project(tmp_path)

    assert report.written == []
    assert edited.read_text(encoding="utf-8") == "mine"
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "my own\n"


def test_the_written_gitignore_keeps_what_the_repository_keeps_out():
    """The same reasons apply to a project as to this repository."""
    ours = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for line in GITIGNORE.splitlines():
        if line and not line.startswith("#"):
            assert line in ours, f"{line!r} is not ignored by this repository"
    for required in ("config/config.yaml", "config/topics.yaml", "output/", ".env"):
        assert required in GITIGNORE.splitlines()


def test_init_command_reports_and_leaves_a_usable_catalog_set(tmp_path):
    from policyforge.cli import cli
    from policyforge.frameworks.registry import check_licences

    result = CliRunner().invoke(cli, ["init", str(tmp_path / "proj")])
    assert result.exit_code == 0, result.output
    assert "wrote  data/frameworks/nist-800-53-r5/controls.json" in result.output

    report = check_licences(roots=[tmp_path / "proj" / "data" / "frameworks"])
    assert report.ok
    assert sorted(f.id for f in report.frameworks) == sorted(BUNDLED_CATALOGS)

    again = CliRunner().invoke(cli, ["init", str(tmp_path / "proj")])
    assert again.exit_code == 0
    assert "wrote" not in again.output


def test_the_package_version_matches_pyproject():
    """A release tags one version; the command must report the same one."""
    import policyforge

    assert policyforge.__version__ == _pyproject()["project"]["version"]
