"""A crosswalk id naming a LEVEL requirement reaches the synthesis (#416).

HITRUST maps per level statement, so `map` writes crosswalk ids like
`11.c Level 2`. `build_synthesis_topic` resolved only control and
enhancement ids, so every one of them resolved to nothing: HITRUST never
reached a synthesis, while `coverage` counted it reached. Levels the declared
scope leaves out stay out, as coverage counts them, and are named; an id
that resolves to nothing in a loaded catalog is named, never dropped.

A synthetic catalog only: made-up references, levels and statements, no
HITRUST text or labels. Expected sets are written out by hand, not derived
from the code under test.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from click.testing import CliRunner

from policyforge.ingest.schema import MATURITY, OVERLAY, Control, Requirement, catalog_json
from policyforge.mapping.crosswalk import build_crosswalk, normalize_framework
from policyforge.synthesis.merge import build_synthesis_topic, synthesis_prompt
from policyforge.topics.coverage import LevelScope

ROOT = Path(__file__).resolve().parent.parent
NIST = ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"

NAME = "Synthetic Levels"
KEY = normalize_framework(NAME)


def _level(ref: str, label: str, statement: str, maps_to: str) -> Requirement:
    return Requirement(
        requirement_id=f"{ref} {label}",
        level=label,
        statement=statement,
        level_kind=MATURITY if label.split()[-1].isdigit() else OVERLAY,
        mappings={"NIST SP 800-53": [maps_to]},
    )


#: One reference, three level statements. Levels 1 and 2 map to IR-4; the
#: overlay maps elsewhere, so an IR-4 topic must not carry it.
X1 = Control(
    "X.1",
    "Invented incident handling",
    NAME,
    "v1",
    control_statement="Invented heading statement.",
    requirements=[
        _level("X.1", "Level 1", "Invented level one: incidents are logged.", "IR-4"),
        _level("X.1", "Level 2", "Invented level two: incidents are triaged hourly.", "IR-4"),
        _level("X.1", "Level Alpha", "Invented overlay: incidents go to the regulator.", "AC-2"),
    ],
)
IR4 = Control("IR-4", "Incident Handling", "NIST 800-53", "r5")
AC2 = Control("AC-2", "Account Management", "NIST 800-53", "r5")
POOL = [IR4, AC2, X1]


def _topic(anchors=("IR-4",), scopes=None, crosswalk=None, pool=POOL):
    crosswalk = build_crosswalk(pool) if crosswalk is None else crosswalk
    return build_synthesis_topic("Incidents", list(anchors), pool, crosswalk, {}, scopes or {})


def _levels(topic) -> list[str]:
    return [r.requirement_id for c in topic.controls if c.framework == NAME for r in c.requirements]


def test_the_crosswalk_names_level_ids_as_map_writes_them():
    """The premise, on the real builder: what the crosswalk holds is level
    ids, not the reference."""
    assert build_crosswalk(POOL)["IR-4"][KEY] == ["X.1 Level 1", "X.1 Level 2"]


def test_a_level_id_reaches_the_synthesis_with_its_statement():
    topic = _topic()
    assert _levels(topic) == ["X.1 Level 1", "X.1 Level 2"]
    prompt = synthesis_prompt(topic)
    assert "X.1 Level 1: Invented level one: incidents are logged." in prompt
    assert "X.1 Level 2: Invented level two: incidents are triaged hourly." in prompt
    # The level the topic did not reach stays out.
    assert "Invented overlay" not in prompt
    assert topic.unresolved == [] and topic.scoped_out == {}
    # No scope declared, so every named level went, and that is said.
    assert topic.unscoped == [NAME]


def test_the_catalog_control_is_not_narrowed_in_place():
    _topic()
    assert len(X1.requirements) == 3


def test_a_reference_named_by_its_own_id_keeps_every_level():
    crosswalk = {"IR-4": {KEY: ["X.1"]}}
    assert _levels(_topic(crosswalk=crosswalk)) == ["X.1 Level 1", "X.1 Level 2", "X.1 Level Alpha"]


def test_a_whole_reference_still_follows_the_scope():
    """1d's arm on PR 419: the scope applies to a reference reached by its own
    id too, not only to the levels the crosswalk named."""
    topic = _topic(crosswalk={"IR-4": {KEY: ["X.1"]}}, scopes={KEY: LevelScope(maturity=1)})
    assert _levels(topic) == ["X.1 Level 1"]


def test_levels_the_scope_drops_from_a_whole_reference_are_counted():
    """80's ruling on PR 419: summarised per reference, not named one by one;
    a level the crosswalk named is still named in `scoped_out`."""
    topic = _topic(crosswalk={"IR-4": {KEY: ["X.1"]}}, scopes={KEY: LevelScope(maturity=1)})
    # Level 2 is above maturity 1, and the overlay is undeclared.
    assert topic.whole_scoped_out == {"X.1": 2}
    assert topic.scoped_out == {}


def test_a_reference_reached_by_a_level_then_whole_keeps_every_level():
    """1d's arm on PR 419: reached by a level first, then by its own id, a
    reference widens to every level rather than staying narrowed."""
    topic = _topic(crosswalk={"IR-4": {KEY: ["X.1 Level 1", "X.1"]}})
    assert _levels(topic) == ["X.1 Level 1", "X.1 Level 2", "X.1 Level Alpha"]


def test_a_level_above_the_declared_maturity_is_left_out_and_named():
    topic = _topic(scopes={KEY: LevelScope(maturity=1)})
    assert _levels(topic) == ["X.1 Level 1"]
    assert "Invented level two" not in synthesis_prompt(topic)
    assert list(topic.scoped_out) == [f"{NAME} X.1 Level 2"]
    assert "above the declared maturity level" in topic.scoped_out[f"{NAME} X.1 Level 2"]
    assert topic.unscoped == []


def test_a_reference_whose_named_levels_are_all_scoped_out_is_not_sent():
    """The overlay is undeclared, so an AC-2 topic reaching only the overlay
    sends nothing from X.1, not its bare heading."""
    topic = _topic(anchors=("AC-2",), scopes={KEY: LevelScope(maturity=2)})
    assert [c.control_id for c in topic.controls] == ["AC-2"]
    assert list(topic.scoped_out) == [f"{NAME} X.1 Level Alpha"]


def test_an_id_no_loaded_catalog_carries_is_named():
    crosswalk = {"IR-4": {KEY: ["X.1 Level 1", "X.9 Level 1"], "not-loaded": ["Z.1"]}}
    topic = _topic(crosswalk=crosswalk)
    assert _levels(topic) == ["X.1 Level 1"]
    # A catalog that isn't loaded is expected to resolve nothing.
    assert topic.unresolved == [f"{NAME} X.9 Level 1"]


def test_a_scope_declared_for_a_catalog_not_in_this_run_is_not_refused():
    """9b on PR 419: `synthesize` loads the catalogs a run lists, so a key
    for a per-level catalog left out of this run is not a typo. The loaded
    framework's own scope still applies."""
    scopes = {KEY: LevelScope(maturity=1), "another-levelled": LevelScope(maturity=2)}
    topic = _topic(scopes=scopes)
    assert _levels(topic) == ["X.1 Level 1"]
    assert list(topic.scoped_out) == [f"{NAME} X.1 Level 2"]


def test_synthesize_names_unresolved_scoped_out_and_unscoped(tmp_path, monkeypatch):
    """The user's path: `synthesize` with the catalog on disk and a crosswalk
    built as `map` builds it. The provider is replaced by one that stops the
    run, after the lines are printed and before any model is reached."""
    import policyforge.cli as cli_mod

    class _Reached(Exception):
        """Raised where the provider would be built."""

    def _stop(config):
        raise _Reached

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "get_provider", _stop)

    catalog = tmp_path / "levels.json"
    catalog.write_text(catalog_json([dataclasses.asdict(X1)]), encoding="utf-8")
    crosswalk = build_crosswalk([X1])
    crosswalk["IR-4"][KEY].append("X.9 Level 1")
    crosswalk_path = tmp_path / "crosswalk.json"
    crosswalk_path.write_text(json.dumps(crosswalk), encoding="utf-8")

    def run(scoping: dict):
        config = {"llm": {"provider": "local"}, "frameworks": {"scoping": scoping}}
        monkeypatch.setattr(cli_mod, "load_config", lambda: config)
        monkeypatch.setattr(
            "policyforge.frameworks.registry.config_or_defaults", lambda what, *a, **k: config
        )
        result = CliRunner().invoke(
            cli_mod.cli,
            [
                "synthesize",
                "--topic",
                "Incidents",
                "--nist-controls",
                "IR-4",
                "--controls",
                str(NIST),
                "--controls",
                str(catalog),
                "--crosswalk",
                str(crosswalk_path),
                "--out-dir",
                str(tmp_path / "out"),
            ],
        )
        assert isinstance(result.exception, _Reached), (result.exception, result.output)
        return result.output

    scoped = run({NAME: {"maturity": 1}})
    assert f"{NAME} X.9 Level 1" in scoped, scoped
    assert f"{NAME} X.1 Level 2 (above the declared maturity level" in scoped, scoped
    assert "No level scoping is declared" not in scoped, scoped

    unscoped = run({})
    assert f"No level scoping is declared for {NAME}" in unscoped, unscoped
    assert "left out by frameworks.scoping" not in unscoped, unscoped
    assert "further level" not in unscoped, unscoped

    # A reference reached whole: one summary line for what the scope drops.
    crosswalk_path.write_text(json.dumps({"IR-4": {KEY: ["X.1"]}}), encoding="utf-8")
    whole = run({NAME: {"maturity": 1}})
    assert (
        "2 further levels outside the declared scope were left out of 1 whole reference (X.1)"
        in whole
    ), whole
