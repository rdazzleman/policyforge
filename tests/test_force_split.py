"""`--force` means one thing: overwrite your own local file (#372).

It meant three. `crosswalk seed` and `generate-parser` keep it (overwrite a
local file). `publish` now takes `--overwrite-wiki-changes` (it destroyed
someone else's edit on the wiki), and the licensed `etl-*` commands take
`--allow-undeclared-licensed` (they wrote a licensed catalog where the
repository hadn't declared it may hold one). On those four, `--force` is
still recognised and refuses: one line, exit 2, naming the new flag, before
anything runs, and it never does the old thing. 80's ruling on #372.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

import policyforge.cli as cli_mod
from tests import licensed_fakes

OUT = "local_content/byoc/controls.json"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Not a git repository and no `frameworks.allow_licensed_in_repo`, so a
    licensed write needs the explicit flag (git can't confirm it's ignored)."""
    licensed_fakes.install(monkeypatch)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    (tmp_path / "local_content" / "byoc").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _etl(command: str, project, *flags):
    sample = project / "sample-export.xlsx"
    licensed_fakes.build_workbook(sample)
    source = "--criteria" if command == "etl-soc2-tsc" else "--export"
    return CliRunner().invoke(cli_mod.cli, [command, source, str(sample), "--out", OUT, *flags])


LICENSED_ETLS = ("etl-hitrust", "etl-govramp", "etl-soc2-tsc")


@pytest.mark.parametrize("command", LICENSED_ETLS)
def test_without_either_flag_the_undeclared_write_is_refused(project, command):
    """The control: the write the flags are about is refused by default."""
    result = _etl(command, project)

    assert result.exit_code != 0, result.output
    assert "--allow-undeclared-licensed" in result.output, "the refusal names the new flag"
    assert not (project / OUT).exists()


@pytest.mark.parametrize("command", LICENSED_ETLS)
def test_the_new_flag_does_what_force_did(project, command):
    result = _etl(command, project, "--allow-undeclared-licensed")

    assert result.exit_code == 0, result.output
    assert (project / OUT).exists()


@pytest.mark.parametrize("command", LICENSED_ETLS)
def test_force_refuses_names_the_new_flag_and_writes_nothing(project, command):
    result = _etl(command, project, "--force")

    assert result.exit_code == 2, result.output
    assert result.output.strip().splitlines() == [
        "Error: `--force` no longer applies here: to write --out without "
        "`frameworks.allow_licensed_in_repo`, pass `--allow-undeclared-licensed`."
    ]
    assert not (project / OUT).exists()


@pytest.fixture
def publishing(tmp_path, monkeypatch):
    """`publish` up to the publisher, with what it's asked recorded."""
    import policyforge.cli.content as content_cli
    import policyforge.content.tree as tree
    import policyforge.export.publisher as publisher

    asked: list[dict] = []

    class Report:
        moved, skipped, published = [], [], ["a page"]

        def format_report(self):
            return "published 1"

    def publish_documents(_publisher, _documents, _problems, **kwargs):
        asked.append(kwargs)
        return Report()

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(content_cli, "_publisher_for", lambda *a, **k: object())
    monkeypatch.setattr(content_cli, "_refuse_public_wiki", lambda _p: None)
    monkeypatch.setattr(tree, "load_content_tree", lambda root: ([], []))
    monkeypatch.setattr(publisher, "publish_documents", publish_documents)
    (tmp_path / "docs").mkdir()
    return tmp_path / "docs", asked


def _publish(content, *flags):
    return CliRunner().invoke(cli_mod.cli, ["publish", "--content-dir", str(content), *flags])


def test_publish_overwrites_wiki_changes_only_when_asked(publishing):
    content, asked = publishing

    plain = _publish(content, "--apply")
    overwriting = _publish(content, "--apply", "--overwrite-wiki-changes")

    assert plain.exit_code == 0 and overwriting.exit_code == 0, plain.output + overwriting.output
    assert [a["force"] for a in asked] == [False, True]


def test_publish_force_refuses_before_publishing_anything(publishing):
    content, asked = publishing

    result = _publish(content, "--apply", "--force")

    assert result.exit_code == 2, result.output
    assert result.output.strip().splitlines() == [
        "Error: `--force` no longer applies here: to overwrite pages changed on the "
        "wiki since this tool last wrote them, pass `--overwrite-wiki-changes`."
    ]
    assert asked == [], "the publisher was never reached"


@pytest.mark.parametrize(
    ("command", "keeps_force", "new_flag"),
    [
        (["crosswalk", "seed"], True, None),
        (["generate-parser"], True, None),
        (["publish"], False, "--overwrite-wiki-changes"),
        (["etl-hitrust"], False, "--allow-undeclared-licensed"),
        (["etl-govramp"], False, "--allow-undeclared-licensed"),
        (["etl-soc2-tsc"], False, "--allow-undeclared-licensed"),
    ],
)
def test_help_shows_force_only_where_it_means_overwrite_a_local_file(
    command, keeps_force, new_flag
):
    shown = CliRunner().invoke(cli_mod.cli, [*command, "--help"]).output

    assert ("--force" in shown) is keeps_force, shown
    if new_flag:
        assert new_flag in shown
