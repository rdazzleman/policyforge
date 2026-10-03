"""`crosswalk propose` then `crosswalk review`, end to end through the CLI.

The model is faked; what is under test is that its answers reach the file as
notes, that only a person's decision reaches `map`, and that decisions
survive the next proposal run.
"""

from __future__ import annotations

import dataclasses
import json

from click.testing import CliRunner

from policyforge.crosswalk.overlay import ACCEPTED, PROPOSED, REJECTED, load_overlay
from policyforge.crosswalk.propose import NOT_CONFIRMED
from policyforge.ingest.schema import Control, ControlEnhancement
from policyforge.llm.base import LLMResponse
from policyforge.mapping.crosswalk import NIST_ANCHOR

HIPAA = "HIPAA Security Rule"
RID = "164.308(a)(5)(ii)(B)"


def _catalogs():
    def nist(control_id, title, text):
        return Control(
            control_id=control_id,
            title=title,
            framework="NIST 800-53",
            framework_version="r5",
            control_statement=text,
        )

    return [
        nist(
            "SI-3",
            "Malicious Code Protection",
            "Implement malicious code protection mechanisms at system entry and exit points.",
        ),
        nist("AT-2", "Literacy Training and Awareness", "Provide security literacy training."),
        Control(
            control_id="164.308(a)(5)(i)",
            title="Security awareness and training",
            framework=HIPAA,
            framework_version="45 CFR 164",
            control_statement="Implement a security awareness and training program.",
            enhancements=[
                ControlEnhancement(
                    enhancement_id=RID,
                    title="Protection from malicious software",
                    baseline="Addressable",
                    description="Procedures for guarding against, detecting, and reporting "
                    "malicious software.",
                    source_crosswalk={NIST_ANCHOR: "AT-2"},
                )
            ],
        ),
    ]


class Mapper:
    """Maps the malware specification to SI-3 and nothing else to anything."""

    model = "fake-mapper"

    def __init__(self, schema=True):
        self._schema = schema
        self.calls = 0

    def supports_schema(self):
        return self._schema

    def generate_json(self, *, prompt, **kwargs):
        self.calls += 1
        rows = []
        if "Protection from malicious software" in prompt:
            rows = [
                {
                    "control": "SI-3",
                    "relationship": "intersects",
                    "requirement_quote": "guarding against, detecting, and reporting malicious",
                    "control_quote": "malicious code protection mechanisms at system entry",
                }
            ]
        return LLMResponse(text=json.dumps({"mappings": rows}), model="fake")


def _setup(tmp_path, monkeypatch, provider):
    import policyforge.cli as cli_mod

    controls = _catalogs()
    nist = tmp_path / "nist.json"
    hipaa = tmp_path / "hipaa.json"
    nist.write_text(
        json.dumps([dataclasses.asdict(c) for c in controls if c.framework != HIPAA]), "utf-8"
    )
    hipaa.write_text(
        json.dumps([dataclasses.asdict(c) for c in controls if c.framework == HIPAA]), "utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"llm": {"model": "fake-mapper"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: provider)

    def run(*args, input=None):
        return CliRunner().invoke(
            cli_mod.cli,
            [*args, "--controls", str(nist), "--controls", str(hipaa)],
            input=input,
        )

    def crosswalk():
        out = tmp_path / "crosswalk.json"
        result = run("map", "--out", str(out))
        assert result.exit_code == 0, result.output
        return json.loads(out.read_text(encoding="utf-8"))

    return run, crosswalk


OVERLAY = "config/crosswalks/hipaa-security-rule.yaml"


def test_proposals_are_written_and_the_pipeline_is_unchanged(tmp_path, monkeypatch):
    run, crosswalk = _setup(tmp_path, monkeypatch, Mapper())
    before = crosswalk()

    result = run("crosswalk", "propose")

    assert result.exit_code == 0, result.output
    assert "0 confirmed, 1 not confirmed, 1 new" in result.output
    rows = {r.control: r for r in load_overlay(tmp_path / OVERLAY).requirements[RID]}
    assert rows["SI-3"].status == PROPOSED
    assert rows["AT-2"].status == ACCEPTED and rows["AT-2"].flags == [NOT_CONFIRMED]
    assert crosswalk() == before


def test_review_decisions_reach_map_and_survive_the_next_proposal(tmp_path, monkeypatch):
    run, crosswalk = _setup(tmp_path, monkeypatch, Mapper())
    assert run("crosswalk", "propose").exit_code == 0

    # Flagged published pair first (AT-2), then the proposal (SI-3).
    reviewed = run(
        "crosswalk",
        "review",
        "--who",
        "ryan",
        input="r\nnot a training obligation\na\nintersects\n\n",
    )

    assert reviewed.exit_code == 0, reviewed.output
    assert reviewed.output.index("-> AT-2") < reviewed.output.index("-> SI-3")
    assert "2 decision(s) recorded" in reviewed.output
    mapped = crosswalk()
    assert mapped["SI-3"] == {"hipaa": [RID]}
    assert "AT-2" not in mapped
    rows = {r.control: r for r in load_overlay(tmp_path / OVERLAY).requirements[RID]}
    assert rows["AT-2"].status == REJECTED
    assert rows["AT-2"].rationale == "not a training obligation"
    assert rows["AT-2"].reviewed_by["who"] == "ryan"

    again = run("crosswalk", "propose")

    assert again.exit_code == 0, again.output
    rows_after = {r.control: r for r in load_overlay(tmp_path / OVERLAY).requirements[RID]}
    assert {c: r.as_record() for c, r in rows_after.items()} == {
        c: r.as_record() for c, r in rows.items()
    }
    assert "Nothing in" in run("crosswalk", "review").output


def test_a_reviewer_name_that_is_not_utf8_is_a_clean_refusal(tmp_path, monkeypatch):
    """**Through the real command** (1d, on #294). `review` with no `--who`
    records git's user.name. A name that is not UTF-8 is refused rather than
    recorded as mojibake (#287) -- and the refusal must reach the user as an
    `Error:` line, not a traceback. Nothing is decided, so nothing is written."""
    import subprocess

    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    assert run("crosswalk", "propose").exit_code == 0
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    config = tmp_path / ".git" / "config"
    config.write_bytes(config.read_bytes() + b"[user]\n\tname = Bad\xffName\n")
    before = (tmp_path / OVERLAY).read_bytes()

    result = run("crosswalk", "review", input="a\n\n\nq\n")

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), f"not a clean exit: {result.exception!r}"
    assert "Error: crosswalk review (reviewer name)" in result.output, result.output
    assert "0xff" in result.output
    assert "Traceback" not in result.output
    assert (tmp_path / OVERLAY).read_bytes() == before, "a decision was written"


def test_quitting_review_keeps_the_decisions_already_made(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    assert run("crosswalk", "propose").exit_code == 0

    result = run("crosswalk", "review", "--who", "ryan", input="a\n\n\nq\n")

    assert result.exit_code == 0, result.output
    assert "1 decision(s) recorded" in result.output
    assert "1 still need review" in result.output
    rows = {r.control: r for r in load_overlay(tmp_path / OVERLAY).requirements[RID]}
    assert rows["AT-2"].reviewed_by and rows["AT-2"].flags == []
    assert rows["SI-3"].status == PROPOSED


def test_propose_refuses_a_provider_that_cannot_hold_a_schema(tmp_path, monkeypatch):
    provider = Mapper(schema=False)
    run, _ = _setup(tmp_path, monkeypatch, provider)

    result = run("crosswalk", "propose")

    assert result.exit_code != 0
    assert "schema" in result.output
    assert provider.calls == 0
    assert not (tmp_path / OVERLAY).exists()


def test_propose_names_an_unknown_requirement(tmp_path, monkeypatch):
    provider = Mapper()
    run, _ = _setup(tmp_path, monkeypatch, provider)

    result = run("crosswalk", "propose", "--only", "164.999(z)")

    assert result.exit_code != 0
    assert "164.999(z)" in result.output
    assert provider.calls == 0


def test_propose_only_asks_about_the_named_requirement(tmp_path, monkeypatch):
    provider = Mapper()
    run, _ = _setup(tmp_path, monkeypatch, provider)

    result = run("crosswalk", "propose", "--only", RID)

    assert result.exit_code == 0, result.output
    assert provider.calls == 1


def test_review_without_an_overlay_says_what_to_run(tmp_path, monkeypatch):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    result = run("crosswalk", "review")
    assert result.exit_code != 0
    assert "crosswalk propose" in result.output


def test_accepted_relationships_are_keyed_as_the_crosswalk_files_requirements():
    from policyforge.crosswalk.overlay import accepted_relationships, parse_overlay

    overlay = parse_overlay(
        {
            "framework": HIPAA,
            "requirements": {
                RID: [
                    {"control": "SI-3", "relationship": "intersects", "status": "accepted"},
                    {"control": "AT-2", "relationship": "equal", "status": "rejected"},
                    {"control": "IR-6", "relationship": "subset", "status": "proposed"},
                ]
            },
        }
    )

    assert accepted_relationships([overlay]) == {("hipaa", RID, "SI-3"): "intersects"}


def test_coverage_reports_a_requirement_the_overlay_records_as_reached_in_part(
    tmp_path, monkeypatch
):
    run, _ = _setup(tmp_path, monkeypatch, Mapper())
    topics = tmp_path / "topics.yaml"
    topics.write_text(
        "topics:\n"
        "  - name: Endpoint Protection\n"
        "    owner: Endpoint Engineering\n"
        "    nist_controls: [SI-3]\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "crosswalks").mkdir(parents=True)
    overlay = tmp_path / OVERLAY
    overlay.write_text(
        "framework: HIPAA Security Rule\n"
        "requirements:\n"
        f"  {RID}:\n"
        "    - {control: SI-3, relationship: intersects, status: accepted}\n",
        encoding="utf-8",
    )

    partly = run("coverage", "--topics", str(topics))

    assert partly.exit_code == 0, partly.output
    assert "0 of 2 requirements map to an owned NIST control" in partly.output
    assert "1 more are reached only in part" in partly.output

    overlay.write_text(overlay.read_text(encoding="utf-8").replace("intersects", "subset"), "utf-8")
    fully = run("coverage", "--topics", str(topics))

    assert "1 of 2 requirements map to an owned NIST control" in fully.output
    assert "reached only in part" not in fully.output
