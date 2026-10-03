"""The defects policyforge-1d's review of the crosswalk branch reproduced.

Each test is named for the property that was missing and fails on the code
reviewed at `c514059`. B1 and R1-R9 refer to that review.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.crosswalk.overlay import (
    OverlayError,
    apply_overlays,
    load_overlay,
    load_overlays,
    parse_overlay,
    seed_overlay,
)
from policyforge.crosswalk.propose import RELATIONSHIP_PROPOSED
from policyforge.ingest.schema import Control, load_controls
from policyforge.llm.base import LLMResponse
from policyforge.mapping.crosswalk import build_crosswalk
from tests.test_crosswalk_cli import HIPAA, OVERLAY, RID, Mapper, _catalogs, _setup

ROOT = Path(__file__).resolve().parents[1]
LOCAL_LLM = {"provider": "local", "base_url": "http://localhost:11434/v1", "model": "qwen3"}
HOSTED = {"provider": "anthropic", "model": "claude-sonnet-5"}


# ---- B1: licensed text never lands in a tracked file ----------------------


class ScopeRecordingMapper(Mapper):
    """Maps like `Mapper`, and records the ledger scope each call ran in."""

    def __init__(self):
        super().__init__()
        self.content_classes = []

    def generate_json(self, *, prompt, **kwargs):
        from policyforge.llm import ledger

        scope = ledger.current_scope()
        self.content_classes.append(scope.content_class if scope else None)
        return super().generate_json(prompt=prompt, **kwargs)


def _licensed_setup(tmp_path, monkeypatch, llm, provider):
    import policyforge.cli as cli_mod

    controls = _catalogs()
    export_dir = tmp_path / "local_content"
    export_dir.mkdir()
    nist = tmp_path / "nist.json"
    hipaa = export_dir / "hipaa-licensed.json"
    nist.write_text(
        json.dumps([dataclasses.asdict(c) for c in controls if c.framework != HIPAA]), "utf-8"
    )
    hipaa.write_text(
        json.dumps([dataclasses.asdict(c) for c in controls if c.framework == HIPAA]), "utf-8"
    )
    config = {"llm": dict(llm), "frameworks": {"search_paths": [str(export_dir)]}}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda c: provider)
    return CliRunner().invoke(
        cli_mod.cli,
        ["crosswalk", "propose", "--controls", str(nist), "--controls", str(hipaa)],
    )


def test_propose_over_a_licensed_catalog_writes_no_requirement_text(tmp_path, monkeypatch):
    provider = ScopeRecordingMapper()

    result = _licensed_setup(tmp_path, monkeypatch, LOCAL_LLM, provider)

    assert result.exit_code == 0, result.output
    written = (tmp_path / OVERLAY).read_text(encoding="utf-8")
    assert "guarding against" not in written
    assert "malicious code protection" not in written
    row = next(r for r in load_overlay(tmp_path / OVERLAY).requirements[RID] if r.control == "SI-3")
    assert row.evidence["requirement"].startswith("sha256:")
    assert row.evidence["control"].startswith("sha256:")
    # R9: the ledger says what the calls carried.
    assert provider.content_classes and set(provider.content_classes) == {"licensed"}


def test_propose_refuses_a_licensed_catalog_for_a_hosted_provider(tmp_path, monkeypatch):
    """R9: nothing is sent, and nothing is written."""
    provider = ScopeRecordingMapper()

    result = _licensed_setup(tmp_path, monkeypatch, HOSTED, provider)

    assert result.exit_code != 0
    assert "REFUSED" in result.output
    assert provider.calls == 0
    assert not (tmp_path / OVERLAY).exists()


def test_public_catalog_quotes_are_still_kept(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    assert run("crosswalk", "propose").exit_code == 0
    row = next(r for r in load_overlay(tmp_path / OVERLAY).requirements[RID] if r.control == "SI-3")
    assert "malicious" in row.evidence["requirement"]


@pytest.mark.parametrize("command", ["seed", "propose", "review"])
def test_an_overlay_is_never_written_into_the_bundled_catalogs(tmp_path, monkeypatch, command):
    (tmp_path / "data" / "frameworks").mkdir(parents=True)
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    flag = "--out" if command == "seed" else "--overlay"
    target = tmp_path / "data" / "frameworks" / "hipaa-overlay.yaml"

    result = run("crosswalk", command, flag, str(target))

    assert result.exit_code != 0
    assert "data/frameworks" in result.output
    assert not target.exists()


def test_overlays_are_gitignored_here_and_by_init():
    from policyforge.scaffold import GITIGNORE

    assert "config/crosswalks/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "config/crosswalks/" in GITIGNORE.splitlines()


# ---- R1: a model reply never changes a report before review ---------------


def test_propose_leaves_coverage_exactly_as_it_was(tmp_path, monkeypatch):
    class Superset(Mapper):
        def generate_json(self, *, prompt, **kwargs):
            if "Protection from malicious software" not in prompt:
                return super().generate_json(prompt=prompt, **kwargs)
            rows = [
                {
                    "control": "AT-2",
                    "relationship": "superset",
                    "requirement_quote": "guarding against, detecting, and reporting malicious",
                    "control_quote": "Provide security literacy training",
                }
            ]
            return LLMResponse(text=json.dumps({"mappings": rows}), model="fake")

    run, _ = _setup(tmp_path, monkeypatch, Superset())
    topics = tmp_path / "topics.yaml"
    topics.write_text(
        "topics:\n  - name: Training\n    owner: People\n    nist_controls: [AT-2]\n", "utf-8"
    )
    assert run("crosswalk", "seed").exit_code == 0
    before = run("coverage", "--topics", str(topics)).output

    assert run("crosswalk", "propose").exit_code == 0

    assert run("coverage", "--topics", str(topics)).output == before
    row = next(r for r in load_overlay(tmp_path / OVERLAY).requirements[RID] if r.control == "AT-2")
    assert row.relationship == "unspecified"
    assert row.proposed_relationship == "superset"
    assert RELATIONSHIP_PROPOSED in row.flags


def test_a_relationship_a_person_set_survives_every_propose_run():
    from datetime import date

    from policyforge.crosswalk.propose import Proposal, ProposedMapping, merge

    overlay = parse_overlay(
        {
            "framework": HIPAA,
            "requirements": {
                RID: [{"control": "AT-2", "relationship": "equal", "status": "accepted"}]
            },
        }
    )
    mapping = ProposedMapping("AT-2", "superset", "reporting malicious software", "training")
    for _ in range(2):
        merge(
            overlay,
            [Proposal(RID, ["AT-2"], mappings=[mapping])],
            _catalogs(),
            model="m",
            today=date(2026, 9, 17),
        )

    (row,) = overlay.requirements[RID]
    assert row.relationship == "equal"
    assert row.proposed_relationship == "superset"
    assert row.flags == [RELATIONSHIP_PROPOSED]


def test_review_promotes_the_suggested_relationship_and_can_override_it(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    (tmp_path / "config" / "crosswalks").mkdir(parents=True)
    (tmp_path / OVERLAY).write_text(
        "framework: HIPAA Security Rule\n"
        "requirements:\n"
        f"  {RID}:\n"
        "    - control: AT-2\n"
        "      status: accepted\n"
        "      proposed_relationship: superset\n"
        f"      flags: [{RELATIONSHIP_PROPOSED}]\n"
        "    - control: SI-3\n"
        "      status: proposed\n"
        "      proposed_relationship: intersects\n",
        encoding="utf-8",
    )

    result = run("crosswalk", "review", "--who", "ryan", input="a\n\n\na\nequal\n\n")

    assert result.exit_code == 0, result.output
    rows = {r.control: r for r in load_overlay(tmp_path / OVERLAY).requirements[RID]}
    assert rows["AT-2"].relationship == "superset"
    assert rows["SI-3"].relationship == "equal"
    assert rows["AT-2"].proposed_relationship == rows["SI-3"].proposed_relationship == ""


# ---- R2: every catalog's way of naming 800-53 -----------------------------


@pytest.mark.parametrize("catalog", ["fedramp", "arc-ampe"])
def test_catalogs_keyed_nist_800_53_seed_and_reject(catalog):
    controls = load_controls(ROOT / "data" / "frameworks" / catalog / "controls.json")
    framework = controls[0].framework
    overlay = seed_overlay(controls, framework)
    rid, rows = next((rid, rows) for rid, rows in overlay.requirements.items() if rows)
    assert rows, f"{catalog} seeded no pairs"

    before = build_crosswalk(controls)
    assert any(rid in ids for entry in before.values() for ids in entry.values())
    for row in rows:
        row.status = "rejected"
    apply_overlays(controls, [overlay])

    after = build_crosswalk(controls)
    assert not any(rid in ids for entry in after.values() for ids in entry.values())


def test_a_govramp_style_catalog_keyed_nist_800_53_is_overlaid():
    control = Control(
        control_id="AC-2",
        title="Account Management",
        framework="GovRAMP",
        framework_version="r5",
        source_crosswalk={"NIST 800-53": "AC-2"},
    )
    overlay = seed_overlay([control], "GovRAMP")
    assert [r.control for r in overlay.requirements["AC-2"]] == ["AC-2"]
    overlay.requirements["AC-2"][0].status = "rejected"
    apply_overlays([control], [overlay])
    assert control.source_crosswalk == {}


# ---- R3 and R4: a file that cannot mean one thing is refused ---------------


def test_the_same_pair_cannot_be_both_accepted_and_rejected():
    with pytest.raises(OverlayError, match="already row 1"):
        parse_overlay(
            {
                "framework": HIPAA,
                "requirements": {
                    RID: [
                        {"control": "SI-3", "status": "accepted"},
                        {"control": "si-3", "status": "rejected"},
                    ]
                },
            }
        )


def test_control_ids_are_upper_cased():
    overlay = parse_overlay(
        {"framework": HIPAA, "requirements": {RID: [{"control": "si-3(1)", "status": "accepted"}]}}
    )
    assert overlay.requirements[RID][0].control == "SI-3(1)"


def test_two_overlays_for_one_framework_are_refused(tmp_path):
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(
            "framework: HIPAA Security Rule\nrequirements: {}\n", encoding="utf-8"
        )
    with pytest.raises(OverlayError, match="both map"):
        load_overlays(tmp_path)


# ---- R5: synthesize does not read a crosswalk older than the overlay -------


def test_synthesize_refuses_a_crosswalk_older_than_an_overlay(tmp_path, monkeypatch):
    import os

    import policyforge.cli as cli_mod

    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    crosswalk = tmp_path / "crosswalk.json"
    assert run("map", "--out", str(crosswalk)).exit_code == 0
    assert run("crosswalk", "seed").exit_code == 0
    stamp = crosswalk.stat().st_mtime
    os.utime(tmp_path / OVERLAY, (stamp + 10, stamp + 10))

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Endpoint",
            "--nist-controls",
            "SI-3",
            "--controls",
            str(tmp_path / "nist.json"),
            "--controls",
            str(tmp_path / "hipaa.json"),
            "--crosswalk",
            str(crosswalk),
            "--out-dir",
            str(tmp_path / "synthesis"),
        ],
    )

    assert result.exit_code != 0
    assert "policyforge map" in result.output


# ---- R6: overlays are written with LF --------------------------------------


def test_seed_writes_lf_only(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    assert run("crosswalk", "seed").exit_code == 0
    assert b"\r" not in (tmp_path / OVERLAY).read_bytes()


# ---- R7: nothing a model quoted can drive the reviewer's terminal ----------


ESCAPE = "\x1b[2J\x1b]0;owned\x07"


def test_control_characters_are_removed_from_a_hand_edited_overlay():
    overlay = parse_overlay(
        {
            "framework": HIPAA,
            "requirements": {
                RID: [
                    {
                        "control": "SI-3",
                        "status": "accepted",
                        "rationale": f"fine{ESCAPE}",
                        "evidence": {"requirement": f"quote{ESCAPE}"},
                    }
                ]
            },
        }
    )
    row = overlay.requirements[RID][0]
    assert "\x1b" not in row.rationale and "\x07" not in row.rationale
    assert "\x1b" not in row.evidence["requirement"]


def test_an_escape_sequence_in_a_quote_never_reaches_review(tmp_path, monkeypatch):
    class Escaping(Mapper):
        def generate_json(self, *, prompt, **kwargs):
            if "Protection from malicious software" not in prompt:
                return super().generate_json(prompt=prompt, **kwargs)
            rows = [
                {
                    "control": "SI-3",
                    "relationship": "intersects",
                    # The escape's letters are one stray word at the end, inside
                    # the matcher's tolerance for a quote this long: the review's
                    # reproduction, which old and new matchers both accept.
                    "requirement_quote": "guarding against, detecting, and reporting "
                    "malicious software [2J",
                    "control_quote": "malicious code protection mechanisms at system entry",
                }
            ]
            return LLMResponse(text=json.dumps({"mappings": rows}), model="fake")

    run, _ = _setup(tmp_path, monkeypatch, Escaping())
    assert run("crosswalk", "propose").exit_code == 0
    overlay = load_overlay(tmp_path / OVERLAY)
    stored = next(r for r in overlay.requirements[RID] if r.control == "SI-3")
    assert "\x1b" not in stored.evidence["requirement"]

    # Skip through every row, so the one carrying the quote is printed.
    result = run("crosswalk", "review", "--who", "ryan", input="s\ns\n")

    assert "-> SI-3" in result.output
    assert "\x1b" not in result.output and "\x07" not in result.output


# ---- second review: the typo check, crosswalk provenance, ids in review ----


def _bundled(*names):
    controls = []
    for name in names:
        controls += load_controls(ROOT / "data" / "frameworks" / name / "controls.json")
    return controls


@pytest.mark.parametrize(
    "loaded",
    [
        ("nist-800-53-r5",),
        ("nist-800-53-r5", "hipaa-security-rule"),
        ("nist-800-53-r5", "arc-ampe"),
        ("nist-800-53-r5", "fedramp"),
    ],
)
def test_fedramp_and_arc_ampe_overlays_do_not_break_commands_that_do_not_load_them(loaded):
    """Reproduced in review: both catalogs number requirements with 800-53 ids, and
    the typo check read a FedRAMP overlay as a misspelled 800-53 one."""
    fedramp = seed_overlay(_bundled("fedramp"), "FedRAMP")
    arc = seed_overlay(_bundled("arc-ampe"), "ARC-AMPE")
    controls = _bundled(*loaded)
    apply_overlays(controls, [fedramp, arc])


@pytest.mark.parametrize("name", ["HIPPA Security Rule", "HIPAA", "hipaa  security rule"])
def test_a_misspelled_or_shortened_loaded_framework_is_still_refused(name):
    controls = _bundled("nist-800-53-r5", "hipaa-security-rule")
    overlay = seed_overlay(controls, "HIPAA Security Rule")
    overlay.framework = name
    if " ".join(name.split()).casefold() == "hipaa security rule":
        assert apply_overlays(controls, [overlay]) > 0
        return
    with pytest.raises(OverlayError, match="HIPAA Security Rule"):
        apply_overlays(controls, [overlay])


def test_the_map_command_runs_with_a_fedramp_overlay_and_nist_alone(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    runner = CliRunner()
    fedramp = str(ROOT / "data" / "frameworks" / "fedramp" / "controls.json")
    nist = str(ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json")
    seeded = runner.invoke(
        cli_mod.cli,
        ["crosswalk", "seed", "--framework", "FedRAMP", "--controls", nist, "--controls", fedramp],
    )
    assert seeded.exit_code == 0, seeded.output

    result = runner.invoke(cli_mod.cli, ["map", "--controls", nist, "--out", "cw.json"])

    assert result.exit_code == 0, result.output


def _synthesize(tmp_path, crosswalk):
    import policyforge.cli as cli_mod

    return CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Endpoint",
            "--nist-controls",
            "SI-3",
            "--controls",
            str(tmp_path / "nist.json"),
            "--controls",
            str(tmp_path / "hipaa.json"),
            "--crosswalk",
            str(crosswalk),
            "--out-dir",
            str(tmp_path / "synthesis"),
        ],
    )


def test_synthesize_refuses_a_crosswalk_whose_overlay_was_since_deleted(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    crosswalk = tmp_path / "crosswalk.json"
    assert run("crosswalk", "seed").exit_code == 0
    assert run("map", "--out", str(crosswalk)).exit_code == 0
    (tmp_path / OVERLAY).unlink()

    result = _synthesize(tmp_path, crosswalk)

    assert result.exit_code != 0
    assert "policyforge map" in result.output


def test_synthesize_refuses_a_crosswalk_whose_overlay_changed_within_the_same_second(
    tmp_path, monkeypatch
):
    import os

    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    crosswalk = tmp_path / "crosswalk.json"
    assert run("crosswalk", "seed").exit_code == 0
    assert run("map", "--out", str(crosswalk)).exit_code == 0
    stamp = crosswalk.stat().st_mtime
    overlay = tmp_path / OVERLAY
    overlay.write_text(
        overlay.read_text(encoding="utf-8").replace("accepted", "rejected", 1), "utf-8"
    )
    os.utime(overlay, (stamp, stamp))

    result = _synthesize(tmp_path, crosswalk)

    assert result.exit_code != 0
    assert "policyforge map" in result.output


def test_synthesize_accepts_a_crosswalk_built_from_the_overlays_on_disk(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    crosswalk = tmp_path / "crosswalk.json"
    assert run("crosswalk", "seed").exit_code == 0
    assert run("map", "--out", str(crosswalk)).exit_code == 0

    result = _synthesize(tmp_path, crosswalk)

    assert "policyforge map" not in result.output


def test_a_crosswalk_with_no_record_is_refused_once_overlays_exist(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")
    assert run("crosswalk", "seed").exit_code == 0

    result = _synthesize(tmp_path, crosswalk)

    assert result.exit_code != 0
    assert "policyforge map" in result.output


#: The control characters the escapes below decode to. Printable text such as
#: "[2J" may remain once they are removed.
HOSTILE = "\x1b\x07\u202e"


def test_requirement_ids_and_framework_names_are_cleaned_before_review(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    (tmp_path / "config" / "crosswalks").mkdir(parents=True)
    # YAML's own escapes, which is how a control character gets into the file.
    escapes = "\\e[2J\\a\\u202E"
    (tmp_path / OVERLAY).write_text(
        f'framework: "HIPAA Security Rule{escapes}"\n'
        "requirements:\n"
        f'  "{RID}{escapes}":\n'
        "    - {control: SI-3, status: proposed}\n",
        encoding="utf-8",
    )
    import yaml

    raw = yaml.safe_load((tmp_path / OVERLAY).read_text(encoding="utf-8"))
    assert any(ch in raw["framework"] for ch in HOSTILE), "the file does carry them"

    overlay = load_overlay(tmp_path / OVERLAY)
    (rid,) = overlay.requirements
    for text in (rid, overlay.framework):
        assert not any(ch in text for ch in HOSTILE)

    result = run("crosswalk", "review", "--overlay", str(tmp_path / OVERLAY), input="q\n")

    assert not any(ch in result.output for ch in HOSTILE)


def test_overlays_named_yml_are_read_too(tmp_path):
    (tmp_path / "hipaa.yml").write_text(
        "framework: HIPAA Security Rule\nrequirements: {}\n", "utf-8"
    )
    assert [o.framework for o in load_overlays(tmp_path)] == ["HIPAA Security Rule"]


# ---- a cut-off proposal reply (1.2.1's truncation contract) ----------------


def _truncation(subject="crosswalk/hipaa-security-rule"):
    from policyforge.llm.base import TruncatedResponse

    return TruncatedResponse(
        subject=subject,
        site="crosswalk-propose",
        first_budget=6000,
        budget=12000,
        stop_reason="length",
        model="fake",
        text="{ partial",
    )


class CutOff(Mapper):
    """Cut off on the malware specification, answers everything else."""

    def __init__(self):
        super().__init__()
        self.cut_off = 0

    def generate_json(self, *, prompt, **kwargs):
        if "Protection from malicious software" in prompt:
            self.calls += 1
            self.cut_off += 1
            raise _truncation()
        return super().generate_json(prompt=prompt, **kwargs)


def test_a_cut_off_proposal_reply_is_not_retried_and_leaves_the_requirement_alone(
    tmp_path, monkeypatch
):
    provider = CutOff()
    run, _ = _setup(tmp_path, monkeypatch, provider)
    assert run("crosswalk", "seed").exit_code == 0
    before = (tmp_path / OVERLAY).read_text(encoding="utf-8")

    result = run("crosswalk", "propose")

    assert result.exit_code == 1, result.output
    assert RID in result.output
    assert "cut off at the model's output budget" in result.output
    # effort.call_json already retried at a larger budget; asked once here.
    assert provider.cut_off == 1
    assert (tmp_path / OVERLAY).read_text(encoding="utf-8") == before


def test_a_cut_off_reply_is_recorded_as_truncated_not_as_an_unreadable_one():
    from policyforge.crosswalk.candidates import WordIndex, catalog_entries
    from policyforge.crosswalk.propose import propose_for, requirements_of

    controls = _catalogs()
    entries = catalog_entries(controls)
    requirement = next(r for r in requirements_of(controls, HIPAA) if r.requirement_id == RID)
    provider = CutOff()

    proposal = propose_for(
        requirement,
        framework=HIPAA,
        published=["AT-2"],
        entries=entries,
        index=WordIndex(entries),
        provider=provider,
    )

    assert proposal.truncated and proposal.error
    assert proposal.mappings == [] and proposal.refused == []
    assert provider.cut_off == 1


def test_a_run_with_no_failures_still_exits_zero(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    assert run("crosswalk", "propose").exit_code == 0


# ---- a reply that names no control (measured on glm, 2026-09-17) ----------


NAMELESS = {
    "relationship": "superset",
    "requirement_quote": "guarding against, detecting, and reporting malicious software",
    "control_quote": "malicious code protection mechanisms at system entry",
}


class Nameless(Mapper):
    """Answers the malware specification with rows that omit `control`."""

    def __init__(self, then_answer=False):
        super().__init__()
        self.then_answer = then_answer
        self.asked = 0

    def generate_json(self, *, prompt, **kwargs):
        if "Protection from malicious software" in prompt:
            self.asked += 1
            if self.then_answer and self.asked > 1:
                return super().generate_json(prompt=prompt, **kwargs)
            return LLMResponse(text=json.dumps({"mappings": [NAMELESS]}), model="fake")
        return super().generate_json(prompt=prompt, **kwargs)


def _propose_malware(provider, published=("AT-2",)):
    from policyforge.crosswalk.candidates import WordIndex, catalog_entries
    from policyforge.crosswalk.propose import propose_for, requirements_of

    controls = _catalogs()
    entries = catalog_entries(controls)
    requirement = next(r for r in requirements_of(controls, HIPAA) if r.requirement_id == RID)
    return propose_for(
        requirement,
        framework=HIPAA,
        published=list(published),
        entries=entries,
        index=WordIndex(entries),
        provider=provider,
    )


def test_a_reply_naming_no_control_is_asked_again_once():
    """Measured on glm: relationship and both quotes present, `control` absent."""
    provider = Nameless(then_answer=True)

    proposal = _propose_malware(provider)

    assert [m.control for m in proposal.mappings] == ["SI-3"]
    assert provider.asked == 2
    assert proposal.error == ""


def test_a_reply_naming_no_control_twice_leaves_the_requirement_unproposed():
    provider = Nameless()

    proposal = _propose_malware(provider)

    assert proposal.mappings == []
    assert proposal.error and "named no control" in proposal.error
    assert provider.asked == 2
    # What was refused is still reported, and counted as nameless.
    assert len(proposal.refused) == 1 and proposal.nameless == 1


def test_an_empty_list_of_mappings_is_an_answer_not_an_unusable_reply():
    class Empty(Mapper):
        def generate_json(self, *, prompt, **kwargs):
            self.calls += 1
            return LLMResponse(text=json.dumps({"mappings": []}), model="fake")

    provider = Empty()
    proposal = _propose_malware(provider)

    assert proposal.mappings == [] and proposal.error == ""
    assert provider.calls == 1


def test_the_run_says_why_rows_were_refused(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Nameless())

    result = run("crosswalk", "propose")

    assert "naming no candidate control" in result.output
    assert result.exit_code == 1
