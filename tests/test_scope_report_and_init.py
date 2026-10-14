"""`init` writes the registry the scope facts call for, and `coverage` says what
the declared scope expects that the registry doesn't have (22, part 3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge.topics.build import load_pool
from policyforge.topics.registry import Topic, parse_topics
from policyforge.topics.scope_facts import ScopeFacts
from policyforge.topics.scope_report import format_scope, scope_report

pytestmark = pytest.mark.own_config


@pytest.fixture(autouse=True)
def _in_the_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _init(directory: Path, *args: str):
    return CliRunner().invoke(cli_mod.cli, ["init", str(directory), *args])


def _names(directory: Path) -> list[str]:
    registry = yaml.safe_load((directory / "config" / "topics.yaml").read_text(encoding="utf-8"))
    return [t["name"] for t in registry["topics"]]


def _write_programme(directory: Path, programme: dict) -> None:
    path = directory / "config" / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["programme"].update(programme)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


# ---- init -----------------------------------------------------------------------


def test_init_with_no_facts_writes_the_sixteen_and_says_what_was_not_declared(tmp_path):
    result = _init(tmp_path)

    assert result.exit_code == 0, result.output
    assert len(_names(tmp_path)) == 16
    assert "16 topics from the scope facts" in result.output
    assert "not declared, so off: builds_software, own_facilities, personal_data" in result.output
    assert "Encryption & Key Management folds into Data Protection" in result.output


def test_the_four_facts_a_healthcare_saas_declares_give_twenty_two(tmp_path):
    result = _init(
        tmp_path,
        "--builds-software",
        "--no-own-facilities",
        "--personal-data",
        "phi",
        "--baseline",
        "800-53",
        "--ai",
        "none",
    )

    assert result.exit_code == 0, result.output
    names = _names(tmp_path)
    assert len(names) == 22
    assert {"Secure Development Lifecycle", "Privacy", "HIPAA Privacy Practices"} <= set(names)
    assert "Physical & Environmental Security" not in names
    assert not any(n.startswith("AI ") for n in names)


def test_the_facts_are_written_to_config_yaml_where_the_next_run_reads_them(tmp_path):
    _init(tmp_path, "--personal-data", "pii", "--ai", "user", "--builds-software")

    programme = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text("utf-8"))[
        "programme"
    ]
    assert programme["personal_data"] == "pii"
    assert programme["ai"] == "user"
    assert programme["builds_software"] is True
    assert "own_facilities" not in programme  # undeclared stays absent, as a comment
    assert "# own_facilities:" in (tmp_path / "config" / "config.yaml").read_text("utf-8")


def test_an_existing_config_is_read_for_its_facts_and_never_rewritten(tmp_path):
    _init(tmp_path)
    _write_programme(tmp_path, {"personal_data": "phi"})
    before = (tmp_path / "config" / "config.yaml").read_bytes()
    (tmp_path / "config" / "topics.yaml").unlink()

    result = _init(tmp_path, "--ai", "user")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "config" / "config.yaml").read_bytes() == before
    names = _names(tmp_path)
    assert "HIPAA Privacy Practices" in names  # from the config
    assert "Responsible Use of AI" in names  # from the option
    assert "add under `programme:`: ai: user" in result.output


def test_an_existing_registry_is_kept_whatever_the_facts(tmp_path):
    _init(tmp_path)
    mine = (tmp_path / "config" / "topics.yaml").read_bytes()

    result = _init(tmp_path, "--ai", "builder", "--personal-data", "phi")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "config" / "topics.yaml").read_bytes() == mine
    assert "kept   config/topics.yaml" in result.output
    assert "topics from the scope facts" not in result.output
    # Both files were there, so the facts were neither used nor written, and
    # the note says that, not that they were used (ba's finding on this PR).
    assert "neither used nor written" in result.output
    assert "were used for config/topics.yaml" not in result.output
    assert "add under `programme:`: personal_data: phi, ai: builder;" in result.output


def test_a_kept_registry_beside_a_new_config_says_it_was_not_rebuilt(tmp_path):
    _init(tmp_path)
    (tmp_path / "config" / "config.yaml").unlink()
    mine = (tmp_path / "config" / "topics.yaml").read_bytes()

    result = _init(tmp_path, "--ai", "builder")

    assert result.exit_code == 0, result.output
    assert (tmp_path / "config" / "topics.yaml").read_bytes() == mine
    assert "was already there, so it was not rebuilt" in result.output
    # The facts did reach the config that was written.
    programme = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text("utf-8"))
    assert programme["programme"]["ai"] == "builder"
    assert "neither used nor written" not in result.output


def test_a_fact_outside_the_vocabulary_in_an_existing_config_refuses_naming_it(tmp_path):
    _init(tmp_path)
    _write_programme(tmp_path, {"personal_data": "ephi"})
    (tmp_path / "config" / "topics.yaml").unlink()

    result = _init(tmp_path)

    assert result.exit_code == 2, result.output
    assert "programme.personal_data" in result.output
    assert not (tmp_path / "config" / "topics.yaml").exists()


def test_refresh_takes_no_scope_fact_options(tmp_path):
    _init(tmp_path)

    result = CliRunner().invoke(cli_mod.cli, ["init", str(tmp_path), "--refresh", "--ai", "user"])

    assert result.exit_code == 2, result.output
    assert "scope-fact options" in result.output


# ---- coverage -------------------------------------------------------------------


def _pool() -> list[Topic]:
    return parse_topics(load_pool())


def _registry(*names: str) -> list[Topic]:
    keep = set(names)
    return [t for t in _pool() if t.name in keep]


def test_an_area_the_declared_scope_expects_with_no_topic_is_named():
    registry = [t for t in _pool() if t.name != "Incident Response"]

    report = scope_report(registry, ScopeFacts(), _pool())

    assert report.missing == ["Incident response"]


def test_a_core_area_folded_into_another_topic_is_still_covered_by_its_anchors():
    pool = _pool()
    identity = next(t for t in pool if t.name == "Identity Lifecycle & Access Review")
    remote = next(t for t in pool if t.name == "Remote & Third-Party Access")
    anchors = [*identity.nist_controls, *remote.nist_controls]
    merged = Topic(**{**identity.__dict__, "nist_controls": anchors})
    registry = [merged] + [t for t in pool if t.name not in (identity.name, remote.name)]

    report = scope_report(registry, ScopeFacts(ai="none"), pool)

    assert "Access control" not in report.missing and "Remote access" not in report.missing


def test_a_conditional_area_is_expected_only_when_its_fact_is_on():
    without = [t for t in _pool() if t.name != "Secure Development Lifecycle"]

    on = scope_report(without, ScopeFacts(builds_software=True), _pool())
    off = scope_report(without, ScopeFacts(builds_software=False), _pool())
    unset = scope_report(without, ScopeFacts(), _pool())

    assert any("evelopment" in name for name in on.missing)
    assert not any("evelopment" in name for name in off.missing)
    assert not any("evelopment" in name for name in unset.missing)
    assert "builds_software" in unset.undeclared


def test_more_than_twenty_five_topics_warns_and_names_the_count():
    report = scope_report(_pool(), ScopeFacts(), _pool())

    assert report.topic_count == 33 and report.over_ceiling
    lines = format_scope(report)
    assert lines[0].startswith("warning: 33 topics, past the 25")


def test_exactly_twenty_five_topics_does_not_warn():
    keep = {t.name for t in _pool()[:25]}
    registry = [t for t in _pool() if t.name in keep]

    report = scope_report(registry, ScopeFacts(), _pool())

    assert report.topic_count == 25 and not report.over_ceiling
    assert not any(line.startswith("warning:") for line in format_scope(report))


def test_coverage_prints_the_scope_section_and_json_carries_it(tmp_path, monkeypatch):
    _init(tmp_path, "--builds-software", "--personal-data", "phi", "--ai", "builder")
    registry = tmp_path / "config" / "topics.yaml"
    data = yaml.safe_load(registry.read_text(encoding="utf-8"))
    data["topics"] = [t for t in data["topics"] if t["name"] != "Incident Response"]
    registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    text = CliRunner().invoke(cli_mod.cli, ["coverage"])
    as_json = CliRunner().invoke(cli_mod.cli, ["coverage", "--json"])

    assert text.exit_code == 0, text.output
    assert "Areas the declared scope expects" in text.output
    assert "  - Incident response" in text.output
    document = json.loads(as_json.stdout)
    assert document["scope_facts"]["missing"] == ["Incident response"]
    assert document["scope_facts"]["topic_count"] == 24


def test_a_registry_that_is_clean_prints_no_scope_section(tmp_path, monkeypatch):
    _init(
        tmp_path,
        "--no-builds-software",
        "--no-own-facilities",
        "--personal-data",
        "none",
        "--baseline",
        "none",
        "--ai",
        "none",
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli_mod.cli, ["coverage"])

    assert result.exit_code == 0, result.output
    assert "Areas the declared scope expects" not in result.output
    assert "warning:" not in result.output
    assert "Scope facts not declared" not in result.output
