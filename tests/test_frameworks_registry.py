"""Where framework content lives, and who may hold a copy of it.

The rule is not "licensed content never goes in a repository" — an
organization's own private repo very often may hold its MyCSF export, and
telling it otherwise is a restriction this project has no standing to
impose. The rule is "licensed content never goes in a repository that has
not declared the right to hold it", which is a permission only the owner can
grant, and grants once, in config.

So the tests that matter are the four corners: public-domain content
committed (fine), licensed content kept out of git (fine), licensed content
committed with the declaration (fine), and licensed content committed
without it (an error, and the only one).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.frameworks import registry
from policyforge.frameworks.registry import (
    LICENSED,
    PUBLIC_DOMAIN,
    check_licences,
    discover,
    frameworks_config,
    load_framework,
)


def _framework(root: Path, name: str, manifest: str | None = "", controls: bool = True) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    if controls:
        (directory / "controls.json").write_text("[]", encoding="utf-8")
    if manifest:
        (directory / "framework.yaml").write_text(manifest, encoding="utf-8")
    return directory


PUBLIC = "id: nist-800-53-r5\nname: NIST SP 800-53\nlicence: public-domain\n"
PROPRIETARY = "id: hitrust-csf\nname: HITRUST CSF\nlicence: licensed\n"


@pytest.fixture
def tracked(monkeypatch):
    """Control what git would say, so tests don't depend on a real repo."""
    state: dict[str, bool | None] = {}

    def _is_tracked(path: Path):
        return state.get(Path(path).name, False)

    monkeypatch.setattr(registry, "is_tracked", _is_tracked)
    return state


# --------------------------------------------------------------------------
# Reading a manifest
# --------------------------------------------------------------------------


def test_a_declared_public_domain_catalog_is_redistributable(tmp_path):
    directory = _framework(tmp_path, "nist-800-53-r5", PUBLIC)

    framework = load_framework(directory)

    assert framework.licence == PUBLIC_DOMAIN
    assert framework.redistributable
    assert framework.declared


def test_an_undeclared_catalog_is_treated_as_licensed(tmp_path):
    """Assuming content is freely redistributable because nobody said
    otherwise is the failure mode with consequences."""
    directory = _framework(tmp_path, "mystery", manifest=None)

    framework = load_framework(directory)

    assert framework.licence == LICENSED
    assert not framework.redistributable
    assert not framework.declared


def test_an_unparseable_manifest_does_not_grant_redistribution(tmp_path):
    directory = _framework(tmp_path, "broken", "licence: [unclosed\n")

    assert not load_framework(directory).redistributable


def test_the_american_spelling_is_accepted_too(tmp_path):
    directory = _framework(tmp_path, "x", "license: public-domain\n")

    assert load_framework(directory).redistributable


# --------------------------------------------------------------------------
# Finding them
# --------------------------------------------------------------------------


def test_catalogs_are_found_across_every_search_path(tmp_path):
    _framework(tmp_path / "bundled", "nist-800-53-r5", PUBLIC)
    _framework(tmp_path / "mine", "hitrust-csf", PROPRIETARY)

    found = discover(roots=[tmp_path / "bundled", tmp_path / "mine"])

    assert sorted(f.id for f in found) == ["hitrust-csf", "nist-800-53-r5"]


def test_the_first_search_path_wins(tmp_path):
    """So a repository can shadow a bundled catalog with its own newer
    export without deleting anything."""
    _framework(tmp_path / "mine", "nist-800-53-r5", PUBLIC)
    _framework(tmp_path / "bundled", "nist-800-53-r5", PUBLIC)

    found = discover(roots=[tmp_path / "mine", tmp_path / "bundled"])

    assert len(found) == 1
    assert found[0].path == tmp_path / "mine" / "nist-800-53-r5"


def test_a_directory_that_is_not_a_catalog_is_skipped(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.txt").write_text("hi", encoding="utf-8")

    assert discover(roots=[tmp_path]) == []


def test_a_missing_search_path_is_not_an_error(tmp_path):
    assert discover(roots=[tmp_path / "nope"]) == []


# --------------------------------------------------------------------------
# The four corners
# --------------------------------------------------------------------------


def test_public_domain_content_may_be_committed(tmp_path, tracked):
    _framework(tmp_path, "nist-800-53-r5", PUBLIC)
    tracked["nist-800-53-r5"] = True

    assert check_licences({}, roots=[tmp_path]).ok


def test_licensed_content_kept_out_of_git_is_fine(tmp_path, tracked):
    _framework(tmp_path, "hitrust-csf", PROPRIETARY)
    tracked["hitrust-csf"] = False

    assert check_licences({}, roots=[tmp_path]).ok


def test_licensed_content_committed_without_the_declaration_is_an_error(tmp_path, tracked):
    _framework(tmp_path, "hitrust-csf", PROPRIETARY)
    tracked["hitrust-csf"] = True

    report = check_licences({}, roots=[tmp_path])

    assert not report.ok
    assert "has not declared the right to hold it" in report.errors[0].message


def test_licensed_content_committed_with_the_declaration_is_permitted(tmp_path, tracked):
    """An organization's own repo may hold its own licensed export; only its
    owner can say so, and this is where they say it."""
    _framework(tmp_path, "hitrust-csf", PROPRIETARY)
    tracked["hitrust-csf"] = True
    config = {"frameworks": {"allow_licensed_in_repo": True}}

    report = check_licences(config, roots=[tmp_path])

    assert report.ok
    assert report.allowed


def test_an_undeclared_catalog_warns_without_blocking(tmp_path, tracked):
    _framework(tmp_path, "mystery", manifest=None)
    tracked["mystery"] = False

    report = check_licences({}, roots=[tmp_path])

    assert report.ok
    assert any("no framework.yaml" in f.message for f in report.findings)


def test_when_git_cannot_answer_the_position_is_unverified_not_safe(tmp_path, tracked):
    _framework(tmp_path, "hitrust-csf", PROPRIETARY)
    tracked["hitrust-csf"] = None

    report = check_licences({}, roots=[tmp_path])

    assert report.ok, "unknown is not an error"
    assert any("unverified" in f.message for f in report.findings)


# --------------------------------------------------------------------------
# Config shapes
# --------------------------------------------------------------------------


def test_the_legacy_list_shaped_config_does_not_crash():
    """It was documented for a long time and read by nothing, so config
    files in the wild contain it."""
    legacy = {
        "frameworks": [
            {"id": "nist-800-53-r5", "source": "bundled"},
            {"id": "hitrust", "source": "byoc", "path": "local_content/hitrust/"},
        ]
    }

    normalized = frameworks_config(legacy)

    assert "local_content" in normalized["search_paths"]
    assert not normalized.get("allow_licensed_in_repo"), "an old config cannot grant it"


def test_the_permission_is_never_inferred_from_an_old_config():
    assert not frameworks_config({"frameworks": []}).get("allow_licensed_in_repo")
    assert not frameworks_config({}).get("allow_licensed_in_repo")


def test_search_paths_come_from_config_when_given():
    paths = registry.search_paths({"frameworks": {"search_paths": ["a", "b"]}})

    assert paths == [Path("a"), Path("b")]


# --------------------------------------------------------------------------
# The commands
# --------------------------------------------------------------------------


def test_the_frameworks_command_exits_nonzero_on_a_breach(tmp_path, tracked, monkeypatch):
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    _framework(tmp_path, "hitrust-csf", PROPRIETARY)
    tracked["hitrust-csf"] = True
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"frameworks": {"search_paths": [str(tmp_path)]}}
    )

    result = CliRunner().invoke(cli_mod.cli, ["frameworks"])

    assert result.exit_code == 1
    assert "LICENSED" in result.output


def test_the_frameworks_command_is_happy_when_the_right_is_declared(tmp_path, tracked, monkeypatch):
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    _framework(tmp_path, "hitrust-csf", PROPRIETARY)
    tracked["hitrust-csf"] = True
    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda: {"frameworks": {"search_paths": [str(tmp_path)], "allow_licensed_in_repo": True}},
    )

    result = CliRunner().invoke(cli_mod.cli, ["frameworks"])

    assert result.exit_code == 0
    assert "permitted by your own configuration" in result.output


def test_the_check_command_fails_on_a_licence_breach(tmp_path, tracked, monkeypatch):
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    catalogs = tmp_path / "catalogs"
    _framework(catalogs, "hitrust-csf", PROPRIETARY)
    tracked["hitrust-csf"] = True
    docs = tmp_path / "docs"
    (docs / "standards").mkdir(parents=True)
    (docs / "standards" / "a.md").write_text("# A\n\nText.\n", encoding="utf-8")
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"frameworks": {"search_paths": [str(catalogs)]}}
    )

    result = CliRunner().invoke(cli_mod.cli, ["check", "--content-dir", str(docs)])

    assert result.exit_code == 1
    assert "Framework licences:" in result.output


def test_the_frameworks_command_outside_a_project_names_init_and_fails(tmp_path, monkeypatch):
    """What someone who installed the package sees before running `init`.

    It used to print "No frameworks found" and exit 0, so the first command a
    new user ran gave no route forward, and a script checking catalogs passed.
    """
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    result = CliRunner().invoke(cli_mod.cli, ["frameworks"])

    assert result.exit_code == 1
    assert "policyforge init" in result.output


def test_the_frameworks_command_succeeds_in_an_initialised_project(tmp_path, monkeypatch):
    """The Homebrew formula's test runs exactly this: init, then frameworks."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    runner = CliRunner()
    assert runner.invoke(cli_mod.cli, ["init"]).exit_code == 0

    result = runner.invoke(cli_mod.cli, ["frameworks"])

    assert result.exit_code == 0, result.output
    assert "nist-800-53-r5" in result.output
