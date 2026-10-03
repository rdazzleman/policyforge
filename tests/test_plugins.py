"""The licensed-framework plugin seam (#32): one resolver, five refusals, and the public
command owns the write.

1d's conditions (comment 564), one test or more each:
1. the public stub owns the licensed write: the guard runs before the plugin
   is called, and the envelope is written after it returns;
2. an import error and an ambiguity are refused, never read as absent;
3. the contract is versioned, and a mismatch is refused at load;
4. loading is lazy: a broken plugin can't break a command that doesn't use it;
6. the resolver's refusals are classified (the exception census, elsewhere).

No plugin here is real. Each is a stand-in built by the test, so what's
measured is the seam, not a parser.
"""

from __future__ import annotations

import sys
import textwrap
import types

import pytest
from click.testing import CliRunner

from policyforge import plugins
from policyforge.ingest.schema import LICENSED_MARKER, Control, read_catalog


def _plugin(*parsers, contract=plugins.CONTRACT_VERSION):
    return types.SimpleNamespace(CONTRACT=contract, PARSERS=list(parsers))


class _Entry:
    """An entry point: a name, a value and a `load()`."""

    def __init__(self, name, obj, value="fake.plugin:PLUGIN"):
        self.name, self.value, self._obj = name, value, obj

    def load(self):
        return self._obj


@pytest.fixture
def install(monkeypatch):
    """Install exactly these plugins: no built-in stand-in, no real entry points."""

    def _install(*entries):
        monkeypatch.setattr(plugins, "BUILTIN", [])
        monkeypatch.setattr(plugins, "_entry_points", lambda: list(entries))
        plugins.reset()

    return _install


def _control(cid="X.1"):
    return Control(cid, "An invented control", "HITRUST-CSF", "synthetic")


def _parser(command="etl-hitrust", framework="HITRUST-CSF", **result):
    calls = []

    def parse(**options):
        calls.append(options)
        return plugins.ParseResult(
            controls=result.pop("controls", [_control()]),
            report="an invented report",
            **result,
        )

    parse.calls = calls
    return plugins.Parser(command, framework, parse)


# ---- the five outcomes of `require` --------------------------------------------------


def test_absent_says_so_and_points_to_generate_parser(install):
    install()
    with pytest.raises(plugins.PluginAbsent) as absent:
        plugins.require("etl-hitrust")
    assert "isn't installed" in str(absent.value)
    assert "generate-parser --framework hitrust --sample" in str(absent.value)


def test_absent_soc2_does_not_point_to_a_generate_parser_it_lacks(install):
    """generate-parser drafts HITRUST and GovRAMP loaders only."""
    install()
    with pytest.raises(plugins.PluginAbsent) as absent:
        plugins.require("etl-soc2-tsc")
    assert "generate-parser" not in str(absent.value)


# ---- every licensed command's refusal has a next step (#101) ---------------------------


def _licensed_commands_in_the_cli() -> set[str]:
    """The commands the CLI actually routes through the plugin seam: the first
    argument of every `_run_licensed(...)` call in cli/etl.py, read from the
    source. The OTHER side from `plugins`' own tables, so a command added to
    the CLI and not to them fails here."""
    import ast
    from pathlib import Path

    import policyforge.cli.etl as etl

    tree = ast.parse(Path(etl.__file__).read_text(encoding="utf-8"))
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "_run_licensed"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }


def test_the_licensed_commands_are_the_ones_the_cli_routes_to_the_plugin():
    commands = _licensed_commands_in_the_cli()
    assert commands, "found no _run_licensed call: the reader no longer reads the CLI"
    assert commands == set(plugins.LICENSED_FRAMEWORK_NAMES)


def _generate_parser_frameworks() -> set[str]:
    """What `generate-parser --framework` accepts, read from the command."""
    import policyforge.cli as cli_mod

    option = next(
        p for p in cli_mod.cli.commands["generate-parser"].params if p.name == "framework"
    )
    return set(option.type.choices)


@pytest.mark.parametrize("command", sorted(_licensed_commands_in_the_cli()))
def test_every_absent_command_says_the_plugin_is_separate_and_gives_a_next_step(install, command):
    """5b's finding on #101 (80, 2068): the refusal said nothing of where the
    plugin comes from, and SOC 2's ended with no next step at all. Each now
    says the plugin is distributed separately, and names a next step that
    RESOLVES: the framework's README, as a file that opens, and generate-parser
    only with a framework it accepts."""
    from pathlib import Path

    from policyforge.scaffold import BYOC_CATALOGS

    install()
    with pytest.raises(plugins.PluginAbsent) as absent:
        plugins.require(command)
    said = str(absent.value)
    assert "distributed separately" in said, said
    marker = "How this framework is brought in: "
    assert marker in said, f"no next step: {said}"
    readme = Path(said.split(marker, 1)[1].strip())
    assert readme.is_file() and readme.name == "README.md", readme
    assert readme.parent.name in BYOC_CATALOGS, "a README every install ships"
    if "generate-parser" in said:
        framework = said.split("--framework ", 1)[1].split()[0]
        assert framework in _generate_parser_frameworks(), framework


def test_a_plugin_whose_declared_module_is_missing_is_not_found_naming_the_path(install):
    install(plugins._Source("gone", "policyforge_no_such_plugin_32:PLUGIN"))
    with pytest.raises(plugins.PluginNotFound) as found:
        plugins.parsers()
    assert "policyforge_no_such_plugin_32:PLUGIN" in str(found.value)


def _module(tmp_path, monkeypatch, name, source):
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)


def test_a_plugin_that_raises_on_import_is_broken_not_absent(install, tmp_path, monkeypatch):
    _module(tmp_path, monkeypatch, "pf_plugin_raises_32", "raise RuntimeError('a bug in it')\n")
    install(plugins._Source("raises", "pf_plugin_raises_32"))
    with pytest.raises(plugins.PluginBroken) as broken:
        plugins.parsers()
    assert "a bug in it" in str(broken.value) and "installed but broken" in str(broken.value)


def test_a_plugin_missing_its_own_dependency_is_broken_not_not_found(
    install, tmp_path, monkeypatch
):
    """The declared module is there; something IT imports is missing. That's
    a broken install, not a wrong path."""
    _module(tmp_path, monkeypatch, "pf_plugin_dep_32", "import pf_dependency_nobody_has_32\n")
    install(plugins._Source("dep", "pf_plugin_dep_32"))
    with pytest.raises(plugins.PluginBroken) as broken:
        plugins.parsers()
    assert "pf_dependency_nobody_has_32" in str(broken.value)


def test_a_plugin_built_for_another_contract_is_refused_naming_both(install):
    install(_Entry("old", _plugin(_parser(), contract=0)))
    with pytest.raises(plugins.PluginVersionMismatch) as mismatch:
        plugins.parsers()
    message = str(mismatch.value)
    assert "contract 0" in message and f"contract {plugins.CONTRACT_VERSION}" in message


@pytest.mark.parametrize(
    ("second", "clash"),
    [
        (lambda: _parser(), "etl-hitrust"),
        (lambda: _parser(command="etl-other"), "HITRUST-CSF"),
    ],
    ids=["the same command", "the same framework"],
)
def test_two_plugins_providing_one_thing_are_refused_naming_both(install, second, clash):
    install(
        _Entry("one", _plugin(_parser()), value="one.plugin"),
        _Entry("two", _plugin(second()), value="two.plugin"),
    )
    with pytest.raises(plugins.PluginAmbiguous) as ambiguous:
        plugins.parsers()
    message = str(ambiguous.value)
    assert repr(clash) in message and "one.plugin" in message and "two.plugin" in message


def test_present_is_provided(install):
    parser = _parser()
    install(_Entry("one", _plugin(parser)))
    assert plugins.require("etl-hitrust").parse is parser.parse


# ---- loaded lazily: a broken plugin can't break a command that doesn't use it ----------


def test_nothing_is_loaded_until_a_command_asks(install, tmp_path, monkeypatch):
    _module(tmp_path, monkeypatch, "pf_plugin_lazy_32", "raise RuntimeError('broken')\n")
    install(plugins._Source("broken", "pf_plugin_lazy_32"))
    from policyforge import cli as cli_mod

    docs = tmp_path / "docs" / "standards"
    docs.mkdir(parents=True)
    (docs / "s.md").write_text("---\ntitle: S\ntier: standard\n---\n\n# S\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli_mod.cli, ["check", "--content-dir", "docs"])
    assert "installed but broken" not in result.output, result.output
    assert not isinstance(result.exception, plugins.PluginError)
    assert plugins._cache is None, "check loaded the plugins"


# ---- the public command owns the write ----------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A scratch project whose local_content/ reads as gitignored, and an
    export file for `--export` to point at (its contents never parsed here)."""
    monkeypatch.setattr("policyforge.frameworks.registry.is_ignored", lambda path: True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "local_content").mkdir()
    export = tmp_path / "local_content" / "export.csv"
    export.write_text("invented\n", encoding="utf-8")
    return tmp_path


def _etl(*args):
    from policyforge import cli as cli_mod

    return CliRunner().invoke(
        cli_mod.cli, ["etl-hitrust", "--export", "local_content/export.csv", *args]
    )


def test_the_catalog_is_written_in_the_licence_envelope_whatever_the_plugin(project, install):
    install(_Entry("one", _plugin(_parser())))
    out = project / "local_content" / "h" / "controls.json"
    result = _etl("--out", str(out))
    assert result.exit_code == 0, result.output
    rows, licence = read_catalog(out)
    assert licence == LICENSED_MARKER, "the public command must write the envelope"
    assert [r["control_id"] for r in rows] == ["X.1"]


def test_the_suite_sees_no_installed_plugin():
    """conftest's `no_installed_plugins`: a developer with the real plugin
    installed runs the same suite as CI, which never has it (#32, part 3).
    Without the pin, this fails in any environment that has the plugin."""
    plugins.reset()
    assert plugins.parsers() == {}


def test_without_out_the_command_parses_and_writes_nothing(project, install):
    """The safe default for licensed content: it parses in memory and stays
    there. Pinned here with a fake; the plugin's suite pins it with the real
    GovRAMP parser (moved from `test_cli.py` in #32 part 3)."""
    parser = _parser()
    install(_Entry("one", _plugin(parser)))
    before = sorted(p.relative_to(project) for p in project.rglob("*"))
    result = _etl()
    assert result.exit_code == 0, result.output
    assert len(parser.parse.calls) == 1, "it parsed"
    assert "Nothing written" in result.output
    assert sorted(p.relative_to(project) for p in project.rglob("*")) == before


def test_an_unlicensed_destination_is_refused_before_the_plugin_is_called(project, install):
    parser = _parser()
    install(_Entry("one", _plugin(parser)))
    out = project / "output" / "h" / "controls.json"
    result = _etl("--out", str(out))
    assert result.exit_code != 0
    assert "would be read as" in result.output, result.output
    assert parser.parse.calls == [], "the plugin ran before the guard refused"
    assert not out.exists()


def test_no_plugin_manifest_key_can_change_the_licence(project, install):
    parser = _parser(manifest={"licence": "public-domain"}, framework_id="invented-id")
    install(_Entry("one", _plugin(parser)))
    out = project / "local_content" / "h" / "controls.json"
    assert _etl("--out", str(out)).exit_code == 0
    import yaml

    manifest = yaml.safe_load((out.parent / "framework.yaml").read_text(encoding="utf-8"))
    assert manifest["licence"] == "licensed"


def test_a_plugin_that_returns_something_other_than_controls_writes_nothing(project, install):
    install(_Entry("one", _plugin(_parser(controls=[{"control_id": "X.1"}]))))
    out = project / "local_content" / "h" / "controls.json"
    result = _etl("--out", str(out))
    assert result.exit_code != 0
    assert "Nothing was written" in result.output
    assert not out.exists()


@pytest.mark.parametrize("layout", [True, False], ids=["a layout problem", "a content problem"])
def test_a_refusal_says_nothing_was_written_and_hints_only_at_a_layout(project, install, layout):
    def parse(**options):
        raise plugins.ParseRefused("an invented refusal", suggest_generate_parser=layout)

    install(_Entry("one", _plugin(plugins.Parser("etl-hitrust", "HITRUST-CSF", parse))))
    result = _etl()
    assert result.exit_code == 1
    assert "an invented refusal" in result.output and "Nothing was written" in result.output
    assert ("generate-parser --framework hitrust" in result.output) is layout


def test_the_command_with_no_plugin_says_so_without_a_traceback(project, install):
    install()
    result = _etl()
    assert result.exit_code == 1
    assert "isn't installed" in result.output
    assert "Traceback" not in result.output


# ---- the fixed public name list (80's ruling on #32) -----------------------------------


def test_the_unmarked_catalog_warning_needs_no_plugin(install):
    from policyforge.llm.boundary import licensed_etl_frameworks

    install()
    assert licensed_etl_frameworks() == frozenset(plugins.LICENSED_FRAMEWORK_NAMES.values())
    assert plugins._cache is None, "the warning loaded the plugins"
