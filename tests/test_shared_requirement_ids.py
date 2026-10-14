"""Two requirements that share an id both reach the synthesis (#187, CR-19).

`build_synthesis_topic` keyed controls by `(framework, id)` in a dict, so of
two requirements sharing an id the last won and the other never reached the
topic, while `/coverage` counted both. 80's ruling on #187: keep every one,
collapse an exact repeat, leave a control's own id ahead of an enhancement id,
and name each shared id in the run output.

The catalog here is HAND-WRITTEN in the shape a HITRUST export takes (a real
one has several shared ids). No licensed text: every statement below is
invented.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from click.testing import CliRunner

from policyforge.ingest.schema import Control, ControlEnhancement, catalog_json
from policyforge.mapping.crosswalk import build_crosswalk
from policyforge.synthesis.merge import build_synthesis_topic

ROOT = Path(__file__).resolve().parent.parent
NIST = ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"


def _nist(control_id: str, **kwargs) -> Control:
    return Control(
        control_id=control_id,
        title=f"NIST {control_id}",
        framework="NIST 800-53",
        framework_version="r5",
        **kwargs,
    )


def _hitrust(control_id: str, statement: str, maps_to: str = "AC-2", **kwargs) -> Control:
    return Control(
        control_id=control_id,
        title="Account management",
        framework="HITRUST",
        framework_version="11",
        control_statement=statement,
        source_crosswalk={"NIST 800-53": maps_to},
        **kwargs,
    )


#: Two different requirements an export files under one id.
FIRST = _hitrust("01.a", "Invented requirement one: accounts are reviewed.")
SECOND = _hitrust("01.a", "Invented requirement two: accounts are disabled on exit.")
#: One requirement written twice: same framework, id and text.
REPEAT = _hitrust("01.b", "Invented requirement three: access is logged.")


def _topic(*controls: Control, anchors=("AC-2",)):
    pool = [_nist("AC-2"), *controls]
    return build_synthesis_topic("Accounts", list(anchors), pool, build_crosswalk(pool), {})


def _statements(topic) -> list[str]:
    return [c.control_statement for c in topic.controls if c.framework == "HITRUST"]


def test_two_different_requirements_sharing_an_id_both_appear():
    topic = _topic(FIRST, SECOND)
    assert _statements(topic) == [FIRST.control_statement, SECOND.control_statement]


def test_an_exact_repeat_appears_once():
    topic = _topic(REPEAT, dataclasses.replace(REPEAT))
    assert _statements(topic) == [REPEAT.control_statement]


def test_a_repeat_with_different_text_is_two_requirements():
    """The collapse compares the text the prompt shows, not just the id: one
    word apart is two requirements, and both are kept."""
    other = dataclasses.replace(REPEAT, control_statement=REPEAT.control_statement + " Daily.")
    assert len(_statements(_topic(REPEAT, other))) == 2


def test_a_controls_own_id_wins_over_an_enhancement_id_equal_to_it():
    """Precedence unchanged (80's ruling, clause 3): a control whose own id is
    `AC-2(1)` is what `AC-2(1)` resolves to, even when another control carries
    an enhancement with that id. Two controls carry it here, either side of
    the own-id control, so neither order hides a regression."""
    enhancement = ControlEnhancement(
        enhancement_id="AC-2(1)", title="e", baseline="", description="d"
    )
    before = _nist("AC-9", enhancements=[enhancement])
    own = _nist("AC-2(1)")
    after = _nist("AC-8", enhancements=[enhancement])
    topic = build_synthesis_topic("Accounts", ["AC-2(1)"], [before, own, after], {}, {})
    assert [c.control_id for c in topic.controls] == ["AC-2(1)"]
    assert topic.shared_ids == {}


def test_an_enhancement_id_with_no_own_id_still_resolves_to_its_carrier():
    """The passing case for the clause above: with no control owning the id,
    the enhancement still pulls in the control that carries it."""
    enhancement = ControlEnhancement(
        enhancement_id="AC-2(1)", title="e", baseline="", description="d"
    )
    carrier = _nist("AC-2", enhancements=[enhancement])
    topic = build_synthesis_topic("Accounts", ["AC-2(1)"], [carrier], {}, {})
    assert [c.control_id for c in topic.controls] == ["AC-2"]


def test_the_shared_id_is_named_and_a_repeat_or_a_single_id_is_not():
    topic = _topic(FIRST, SECOND, REPEAT, dataclasses.replace(REPEAT))
    assert topic.shared_ids == {"HITRUST 01.a": 2}


def test_synthesize_prints_the_shared_id_line(tmp_path, monkeypatch):
    """The user's path: `synthesize` with the catalog on disk and a crosswalk
    built as `map` builds it. The provider is replaced by one that stops the
    run, so the line is printed before any model is reached. The control case,
    a catalog with no shared id, prints no such line."""
    import policyforge.cli as cli_mod

    class _Reached(Exception):
        """Raised where the provider would be built."""

    def _stop(config):
        raise _Reached

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"llm": {"provider": "local"}})
    monkeypatch.setattr(cli_mod, "get_provider", _stop)

    def run(*hitrust: Control):
        catalog = tmp_path / "hitrust.json"
        catalog.write_text(catalog_json([dataclasses.asdict(c) for c in hitrust]), encoding="utf-8")
        crosswalk = tmp_path / "crosswalk.json"
        crosswalk.write_text(json.dumps(build_crosswalk(list(hitrust))), encoding="utf-8")
        return CliRunner().invoke(
            cli_mod.cli,
            [
                "synthesize",
                "--topic",
                "Accounts",
                "--nist-controls",
                "AC-2",
                "--controls",
                str(NIST),
                "--controls",
                str(catalog),
                "--crosswalk",
                str(crosswalk),
                "--out-dir",
                str(tmp_path / "out"),
            ],
        )

    shared = run(FIRST, SECOND)
    assert isinstance(shared.exception, _Reached), (shared.exception, shared.output)
    assert "more than one requirement carries" in shared.output, shared.output
    assert "HITRUST 01.a (2)" in shared.output, shared.output

    single = run(FIRST)
    assert isinstance(single.exception, _Reached), (single.exception, single.output)
    assert "more than one requirement carries" not in single.output, single.output
