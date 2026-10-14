"""The programme profile: `programme.catalogs` names the catalog set (#176).

80's spec: every command whose `--controls` means "the catalog set" takes its
set from `programme.catalogs` when the flag isn't given; the flag, when given,
replaces the profile entirely (no merging); with neither, a command with no
default of its own fails in one line naming the key and the flag, and one
that had a default keeps it only when no profile exists and says so. Names
resolve through the framework registry or are paths; an unknown one refuses
before anything runs. The two commands where `--controls` means one specific
file are left as they are.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import policyforge.cli as cli_mod

#: The commands whose `--controls` is the catalog set, as a user types them,
#: with the arguments each needs besides it. Written out by hand: it is the
#: other side of the census test below.
IN_SCOPE = {
    "map": ["map"],
    "bundle": ["bundle", "Security", "--topics", "topics.yaml"],
    "addresses": ["addresses", "AC-2", "--topics", "topics.yaml"],
    "coverage": ["coverage", "--topics", "topics.yaml"],
    "parameters": ["parameters"],
    "satisfies": ["satisfies", "--all"],
    "synthesize": [
        "synthesize",
        "--topic",
        "Access",
        "--nist-controls",
        "AC-2",
        "--crosswalk",
        "crosswalk.json",
    ],
    "ssp": ["ssp", "--yes"],
    "crosswalk seed": ["crosswalk", "seed"],
    "crosswalk check": ["crosswalk", "check"],
    "crosswalk propose": ["crosswalk", "propose"],
    "crosswalk review": ["crosswalk", "review"],
}

#: Where `--controls` names one specific file with another meaning: `etl-hipaa`
#: (the file it enriches) and `drift` (the updated catalog).
OUT_OF_SCOPE = {"etl-hipaa-crosswalk", "drift"}

#: Commands that kept a default of their own, which holds only with no profile.
HAS_DEFAULT = {
    "map",
    "crosswalk seed",
    "crosswalk check",
    "crosswalk propose",
    "crosswalk review",
}


class _Stop(Exception):
    """Raised by the spy once a command has resolved its catalog set."""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A working directory with two catalogs the registry knows by name, one
    the profile does not name, and what each command needs to get as far as
    resolving its set."""
    monkeypatch.chdir(tmp_path)
    catalogs = tmp_path / "catalogs"
    for name in ("alpha", "beta", "gamma"):
        (catalogs / name).mkdir(parents=True)
        (catalogs / name / "controls.json").write_text("[]", encoding="utf-8")
    # A registry the commands that read one can load before they reach the
    # catalog set: the repository's own example.
    example = Path(__file__).resolve().parent.parent / "config" / "topics.example.yaml"
    (tmp_path / "topics.yaml").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "docs").mkdir()  # `satisfies` reads the content tree first
    (tmp_path / "docs" / "doc.md").write_text("# Doc\n", encoding="utf-8")
    (tmp_path / "crosswalk.json").write_text("{}", encoding="utf-8")
    # The files a command's own default points at.
    for default in (
        "data/frameworks/nist-800-53-r5/controls.json",
        "data/frameworks/hipaa-security-rule/controls.json",
    ):
        path = tmp_path / default
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]", encoding="utf-8")

    seen: list[list[Path]] = []
    import policyforge.cli._common as common

    real = common.resolve_catalogs

    def spy(*args, **kwargs):
        resolved = real(*args, **kwargs)
        seen.append([Path(p) for p in resolved])
        raise _Stop

    for module in ("programme", "documents", "crosswalk"):
        monkeypatch.setattr(f"policyforge.cli.{module}.resolve_catalogs", spy)

    def run(argv, profile=None, extra_config=None):
        config = {"frameworks": {"search_paths": [str(catalogs)]}, **(extra_config or {})}
        if profile is not None:
            config["programme"] = {"catalogs": profile}
        monkeypatch.setattr(cli_mod, "load_config", lambda: config)
        seen.clear()
        result = CliRunner().invoke(cli_mod.cli, argv)
        return result, list(seen)

    run.catalogs = catalogs
    return run


@pytest.mark.parametrize("name", sorted(IN_SCOPE))
def test_no_flag_and_a_profile_uses_exactly_the_profiles_set(project, name):
    result, seen = project(IN_SCOPE[name], profile=["alpha", "beta"])

    assert isinstance(result.exception, _Stop), result.output
    assert [p.parent.name for p in seen[0]] == ["alpha", "beta"]
    assert "catalogs: programme (2): alpha, beta" in result.output


@pytest.mark.parametrize("name", sorted(IN_SCOPE))
def test_the_flag_replaces_the_profile_and_is_not_merged_with_it(project, name):
    flag = str(project.catalogs / "gamma" / "controls.json")
    result, seen = project([*IN_SCOPE[name], "--controls", flag], profile=["alpha", "beta"])

    assert isinstance(result.exception, _Stop), result.output
    assert [p.parent.name for p in seen[0]] == ["gamma"]
    assert "catalogs: programme" not in result.output


@pytest.mark.parametrize("name", sorted(set(IN_SCOPE) - HAS_DEFAULT))
def test_with_neither_a_command_with_no_default_fails_in_one_line(project, name):
    result, seen = project(IN_SCOPE[name])

    assert result.exit_code == 2, result.output
    assert seen == []
    assert "programme.catalogs" in result.output and "--controls" in result.output


@pytest.mark.parametrize("name", sorted(HAS_DEFAULT))
def test_a_commands_own_default_holds_only_with_no_profile_and_says_so(project, name):
    result, seen = project(IN_SCOPE[name])

    assert isinstance(result.exception, _Stop), result.output
    assert next(p.parts[-3:] for p in seen[0]) == ("frameworks", "nist-800-53-r5", "controls.json")
    assert "no programme.catalogs set, so the default" in result.output

    with_profile, with_seen = project(IN_SCOPE[name], profile=["alpha"])
    assert [p.parent.name for p in with_seen[0]] == ["alpha"]
    assert "no programme.catalogs set" not in with_profile.output


@pytest.mark.parametrize("name", sorted(IN_SCOPE))
def test_an_unknown_name_refuses_naming_it_before_anything_runs(project, name):
    result, _ = project(IN_SCOPE[name], profile=["alpha", "no-such-framework"])

    assert result.exit_code == 2, result.output
    assert "no-such-framework" in result.output
    assert not isinstance(result.exception, _Stop)


def test_a_path_in_the_profile_is_a_catalog_too(project):
    path = project.catalogs / "gamma" / "controls.json"
    _, seen = project(IN_SCOPE["coverage"], profile=["alpha", str(path)])

    assert [p.parent.name for p in seen[0]] == ["alpha", "gamma"]


@pytest.mark.parametrize("bad", [[], "alpha", [1], [""], {"a": 1}])
def test_a_profile_that_is_not_a_list_of_names_is_refused(project, bad):
    result, seen = project(IN_SCOPE["coverage"], profile=bad)

    assert result.exit_code == 2, result.output
    assert "programme.catalogs" in result.output
    assert seen == []


def test_a_named_framework_with_no_catalog_file_is_refused(project):
    (project.catalogs / "hollow").mkdir()
    (project.catalogs / "hollow" / "manifest.yaml").write_text("id: hollow\n", encoding="utf-8")

    result, _ = project(IN_SCOPE["coverage"], profile=["hollow"])

    assert result.exit_code == 2, result.output
    assert "hollow" in result.output


# ---- the commands left out are unchanged, and every `--controls` is accounted for ----


def _controls_commands() -> dict[str, click.Parameter]:
    """Every command in the tree that takes `--controls`, by its name as typed."""
    found: dict[str, click.Parameter] = {}

    def walk(group: click.Group, prefix: str) -> None:
        for name, command in group.commands.items():
            if isinstance(command, click.Group):
                walk(command, f"{prefix}{name} ")
                continue
            for param in command.params:
                if "--controls" in getattr(param, "opts", ()):
                    found[f"{prefix}{name}"] = param

    walk(cli_mod.cli, "")
    return found


def test_every_controls_option_is_classified():
    """The total must account for all of them: a new command that takes
    `--controls` is in scope or out of it by someone's decision, not by what
    it happened to do."""
    found = _controls_commands()
    assert set(found) == set(IN_SCOPE) | OUT_OF_SCOPE
    assert len(found) == 14  # 12 in scope (nine declarations; crosswalk's is shared by four) + 2


def test_the_in_scope_options_are_optional_and_the_others_are_as_they_were():
    found = _controls_commands()
    for name in IN_SCOPE:
        param = found[name]
        assert not param.required, name
        assert param.multiple, name
        assert param.default in (None, ()), name
    # `drift`'s names the one updated catalog; `etl-hipaa`'s the file it enriches.
    assert found["drift"].required and not found["drift"].multiple
    assert not found["etl-hipaa-crosswalk"].multiple


# ---- what 9b found on PR 410 ---------------------------------------------------------------


REPO = Path(__file__).resolve().parent.parent


def _json_run(tmp_path, monkeypatch, argv, profile):
    """A real catalog from the repository's bundled set, named by the profile,
    and a command that prints a document on stdout."""
    monkeypatch.chdir(tmp_path)
    example = REPO / "config" / "topics.example.yaml"
    (tmp_path / "topics.yaml").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "docs").mkdir()
    config = {
        "frameworks": {"search_paths": [str(REPO / "data" / "frameworks")]},
        "programme": {"catalogs": profile},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    return CliRunner().invoke(cli_mod.cli, argv)


def test_the_status_line_is_on_stderr_so_a_json_run_still_parses(tmp_path, monkeypatch):
    import json

    result = _json_run(
        tmp_path,
        monkeypatch,
        ["coverage", "--topics", "topics.yaml", "--json"],
        ["nist-800-53-r5"],
    )

    assert result.exit_code in (0, 1), result.output
    assert "catalogs: programme (1): nist-800-53-r5" in result.stderr
    assert "catalogs:" not in result.stdout
    json.loads(result.stdout)  # the whole of stdout is the document


def test_a_command_default_is_said_on_stderr_too(project, tmp_path):
    result, _ = project(IN_SCOPE["map"])
    assert "no programme.catalogs set, so the default" in result.stderr
    assert "catalogs:" not in result.stdout


def _licensed_run(tmp_path, monkeypatch, extra_argv, profile):
    """`ssp` against a licensed catalog (a GovRAMP-shaped one under
    local_content/) with a hosted provider: the boundary must refuse it,
    however the catalog was named."""
    monkeypatch.chdir(tmp_path)
    catalog = tmp_path / "local_content" / "govramp"
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / "controls.json").write_text("[" + _LICENSED_CONTROL + "]", encoding="utf-8")
    config = {
        "llm": {"provider": "anthropic", "model": "claude-sonnet-5"},
        "frameworks": {"search_paths": [str(tmp_path / "local_content")]},
    }
    if profile:
        config["programme"] = {"catalogs": ["govramp"]}
    built = []

    class Fake:
        calls: list = []

        def generate(self, **kwargs):
            self.calls.append(kwargs)

        def check(self):
            return True

    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda cfg: built.append(cfg) or Fake())
    result = CliRunner().invoke(
        cli_mod.cli, ["ssp", "--out", str(tmp_path / "ssp.xlsx"), "--yes", *extra_argv]
    )
    return result, built


_LICENSED_CONTROL = (
    '{"control_id": "IA-5", "title": "Authenticator Management", "framework": "NIST 800-53", '
    '"framework_version": "Rev 5", "family": null, "family_abbr": null, "baseline": null, '
    '"control_statement": "Manage system authenticators.", "discussion": "", '
    '"enhancements": [], "related_controls": [], "source_crosswalk": {}, "source_path": null}'
)


def test_a_licensed_catalog_named_in_the_profile_is_refused_as_it_is_by_the_flag(
    tmp_path, monkeypatch
):
    """The profile names files and nothing else: the boundary check each
    command makes is the one it made for `--controls`."""
    by_flag, _ = _licensed_run(
        tmp_path,
        monkeypatch,
        ["--controls", str(tmp_path / "local_content" / "govramp" / "controls.json")],
        profile=False,
    )
    assert by_flag.exit_code != 0 and "REFUSED" in by_flag.output

    by_profile, built = _licensed_run(tmp_path, monkeypatch, [], profile=True)

    assert by_profile.exit_code != 0, by_profile.output
    assert "REFUSED" in by_profile.output
    assert built == [], "the provider was never built"
