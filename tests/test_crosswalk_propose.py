"""A model's proposals become notes for a reviewer, and never decisions.

The fake provider answers with whatever rows a test gives it, so each test
pins what happens to one kind of answer: a grounded mapping, a fabricated
quote, an id off the list, a near-miss wrapper, a failure.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from policyforge.crosswalk.candidates import WordIndex, catalog_entries
from policyforge.crosswalk.overlay import (
    ACCEPTED,
    PROPOSED,
    REJECTED,
    MappingRow,
    apply_overlays,
    parse_overlay,
    seed_overlay,
)
from policyforge.crosswalk.propose import (
    NOT_CONFIRMED,
    RELATIONSHIP_PROPOSED,
    Proposal,
    ProposedMapping,
    merge,
    parse_reply,
    propose_for,
    requirements_of,
)
from policyforge.ingest.schema import Control, ControlEnhancement
from policyforge.llm.base import LLMResponse, SchemaReplyError
from policyforge.mapping.crosswalk import NIST_ANCHOR, build_crosswalk

HIPAA = "HIPAA Security Rule"


def _nist(control_id, title, text):
    return Control(
        control_id=control_id,
        title=title,
        framework="NIST 800-53",
        framework_version="r5",
        control_statement=text,
    )


def _catalogs():
    return [
        _nist(
            "SI-3",
            "Malicious Code Protection",
            "Implement malicious code protection mechanisms at system entry and exit points "
            "to detect and eradicate malicious code.",
        ),
        _nist("AT-2", "Literacy Training and Awareness", "Provide security literacy training."),
        _nist("SI-1", "Policy and Procedures", "Develop system and information integrity policy."),
        _nist("IR-6", "Incident Reporting", "Require personnel to report suspected incidents."),
        Control(
            control_id="164.308(a)(5)(i)",
            title="Security awareness and training",
            framework=HIPAA,
            framework_version="45 CFR 164",
            control_statement="Implement a security awareness and training program.",
            enhancements=[
                ControlEnhancement(
                    enhancement_id="164.308(a)(5)(ii)(B)",
                    title="Protection from malicious software",
                    baseline="Addressable",
                    description="Procedures for guarding against, detecting, and reporting "
                    "malicious software.",
                    source_crosswalk={NIST_ANCHOR: "AT-2"},
                )
            ],
        ),
    ]


class Replies:
    """Returns `reply` as the model's text, or raises it."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    def supports_schema(self):
        return True

    def generate_json(self, *, prompt, **kwargs):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return LLMResponse(text=self.reply, model="fake")


SI3 = {
    "control": "SI-3",
    "relationship": "intersects",
    "requirement_quote": "guarding against, detecting, and reporting malicious software",
    "control_quote": "malicious code protection mechanisms at system entry",
}


def _propose(reply, published=("AT-2",)):
    controls = _catalogs()
    entries = catalog_entries(controls)
    requirement = next(
        r for r in requirements_of(controls, HIPAA) if r.requirement_id == "164.308(a)(5)(ii)(B)"
    )
    provider = Replies(reply)
    proposal = propose_for(
        requirement,
        framework=HIPAA,
        published=list(published),
        entries=entries,
        index=WordIndex(entries),
        provider=provider,
    )
    return proposal, provider


def test_a_grounded_mapping_is_kept():
    proposal, _ = _propose(json.dumps({"mappings": [SI3]}))
    assert [m.control for m in proposal.mappings] == ["SI-3"]
    assert not proposal.refused and not proposal.error


def test_the_parent_standard_is_in_the_prompt_and_the_published_pairs_are_not_labelled():
    _, provider = _propose(json.dumps({"mappings": []}))
    prompt = provider.prompts[0]
    assert "UNDER THE STANDARD" in prompt
    assert "Security awareness and training" in prompt
    assert "AT-2" in prompt
    for giveaway in ("published", "CPRT", "NIST's mapping"):
        assert giveaway not in prompt


@pytest.mark.parametrize(
    "row",
    [
        {**SI3, "control_quote": "implement multifactor authentication for remote access"},
        {**SI3, "requirement_quote": "encrypt all electronic protected health information"},
        {**SI3, "control": "AC-2"},
        {**SI3, "relationship": "related"},
    ],
)
def test_an_ungrounded_or_invalid_mapping_is_refused(row):
    proposal, _ = _propose(json.dumps({"mappings": [row]}))
    assert proposal.mappings == []
    assert len(proposal.refused) == 1


def test_a_control_named_twice_is_kept_once():
    proposal, _ = _propose(json.dumps({"mappings": [SI3, SI3]}))
    assert [m.control for m in proposal.mappings] == ["SI-3"]


@pytest.mark.parametrize(
    "text",
    [
        "```json\n" + json.dumps([SI3]) + "\n```",
        json.dumps([SI3]),
        "```\n" + json.dumps({"mappings": [SI3]}) + "\n```",
    ],
)
def test_near_miss_wrappers_are_read(text):
    assert parse_reply(text) == [SI3]


def test_a_reply_refused_by_the_provider_is_recovered_from_its_text():
    """glm returned 9 of 75 replies inside a code fence, which the schema check rejects."""
    fenced = "```json\n" + json.dumps([SI3]) + "\n```"
    proposal, _ = _propose(SchemaReplyError("not the schema", text=fenced))
    assert [m.control for m in proposal.mappings] == ["SI-3"]


@pytest.mark.parametrize(
    "failure",
    [SchemaReplyError("not the schema", text="None of these apply."), RuntimeError("timeout")],
)
def test_a_failure_is_recorded_not_read_as_no_mappings(failure):
    """An empty answer and a failed one must stay distinguishable."""
    proposal, _ = _propose(failure)
    assert proposal.error
    assert proposal.mappings == []


class Sequence(Replies):
    """Gives each reply in turn: text is returned, an exception is raised."""

    def __init__(self, *replies):
        super().__init__(None)
        self.replies = list(replies)

    def generate_json(self, *, prompt, **kwargs):
        self.prompts.append(prompt)
        reply = self.replies[min(len(self.prompts), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, model="fake")


def _propose_with(provider):
    controls = _catalogs()
    entries = catalog_entries(controls)
    requirement = next(r for r in requirements_of(controls, HIPAA) if r.requirement_id == RID)
    return propose_for(
        requirement,
        framework=HIPAA,
        published=["AT-2"],
        entries=entries,
        index=WordIndex(entries),
        provider=provider,
    )


PROSE = SchemaReplyError("not the schema", text="Let me analyze the requirement carefully.")


def test_an_unreadable_reply_is_asked_again_once():
    """glm answered 6 of 75 requirements with prose reasoning and no rows."""
    provider = Sequence(PROSE, json.dumps({"mappings": [SI3]}))

    proposal = _propose_with(provider)

    assert [m.control for m in proposal.mappings] == ["SI-3"]
    assert proposal.error == ""
    assert len(provider.prompts) == 2


def test_two_unreadable_replies_are_an_error_after_two_calls():
    provider = Sequence(PROSE, PROSE, json.dumps({"mappings": [SI3]}))

    proposal = _propose_with(provider)

    assert proposal.error and proposal.mappings == []
    assert len(provider.prompts) == 2


class RateLimitError(Exception):
    """Like LiteLLM's: not a RuntimeError."""


@pytest.mark.parametrize("failure", [RuntimeError("timeout"), RateLimitError("rate limit")])
def test_a_provider_failure_is_recorded_not_retried_and_does_not_raise(failure):
    provider = Sequence(failure, json.dumps({"mappings": [SI3]}))

    proposal = _propose_with(provider)

    assert str(failure) in proposal.error
    assert len(provider.prompts) == 1


def test_a_readable_empty_answer_is_not_retried():
    provider = Sequence(json.dumps({"mappings": []}), json.dumps({"mappings": [SI3]}))

    proposal = _propose_with(provider)

    assert proposal.mappings == [] and proposal.error == ""
    assert len(provider.prompts) == 1


# ---- merge: notes for a reviewer, never decisions -------------------------


RID = "164.308(a)(5)(ii)(B)"


def _merge(overlay, mappings, candidates=("AT-2", "IR-6", "SI-1", "SI-3")):
    proposal = Proposal(RID, list(candidates), mappings=mappings)
    return merge(overlay, [proposal], _catalogs(), model="fake-model", today=date(2026, 9, 17))


def _mapping(control, relationship="intersects"):
    return ProposedMapping(control, relationship, "reporting malicious software", "report it")


def test_a_new_pair_is_proposed_and_does_not_reach_the_pipeline():
    controls = _catalogs()
    overlay = seed_overlay(controls, HIPAA)

    report = _merge(overlay, [_mapping("SI-3")])
    apply_overlays(controls, [overlay])

    row = next(r for r in overlay.requirements[RID] if r.control == "SI-3")
    assert row.status == PROPOSED
    assert row.sources == ["model"]
    assert row.proposed_by["model"] == "fake-model"
    assert row.proposed_by["prompt"].startswith("crosswalk.propose v1 ")
    assert report.proposed == 1
    assert "SI-3" not in build_crosswalk(controls)


def test_a_confirmed_published_pair_gains_its_evidence_and_stays_accepted():
    overlay = seed_overlay(_catalogs(), HIPAA)

    report = _merge(overlay, [_mapping("AT-2", "superset")])

    (row,) = overlay.requirements[RID]
    assert row.status == ACCEPTED
    # The suggestion waits for review; coverage reads `relationship`.
    assert row.relationship == "unspecified"
    assert row.proposed_relationship == "superset"
    assert row.flags == [RELATIONSHIP_PROPOSED]
    assert row.needs_review
    assert row.sources == ["published", "model"]
    assert row.evidence == {"requirement": "reporting malicious software", "control": "report it"}
    assert report.confirmed == 1


def test_an_unconfirmed_published_pair_is_flagged_and_still_accepted():
    """A flag asks a person to look. Unmapping is theirs to do."""
    controls = _catalogs()
    overlay = seed_overlay(controls, HIPAA)

    report = _merge(overlay, [])
    apply_overlays(controls, [overlay])

    (row,) = overlay.requirements[RID]
    assert row.status == ACCEPTED
    assert row.flags == [NOT_CONFIRMED]
    assert row.needs_review
    assert report.not_confirmed == 1
    assert RID in build_crosswalk(controls)["AT-2"]["hipaa"]


def test_a_published_pair_that_was_never_a_candidate_is_not_flagged():
    overlay = seed_overlay(_catalogs(), HIPAA)
    _merge(overlay, [], candidates=("SI-3",))
    assert overlay.requirements[RID][0].flags == []


def test_a_rerun_clears_a_flag_the_model_no_longer_raises():
    overlay = seed_overlay(_catalogs(), HIPAA)
    _merge(overlay, [])
    assert overlay.requirements[RID][0].flags == [NOT_CONFIRMED]
    _merge(overlay, [_mapping("AT-2")])
    assert NOT_CONFIRMED not in overlay.requirements[RID][0].flags


def test_a_reviewed_row_is_left_exactly_as_it_was():
    overlay = parse_overlay(
        {
            "framework": HIPAA,
            "requirements": {
                RID: [
                    {
                        "control": "AT-2",
                        "relationship": "equal",
                        "status": "accepted",
                        "reviewed_by": {"who": "ryan", "date": "2026-09-16"},
                    }
                ]
            },
        }
    )
    before = overlay.requirements[RID][0].as_record()

    report = _merge(overlay, [])

    assert overlay.requirements[RID][0].as_record() == before
    assert report.left_reviewed == 1


def test_a_rejected_pair_is_not_proposed_again():
    overlay = seed_overlay(_catalogs(), HIPAA)
    overlay.requirements[RID].append(
        MappingRow(control="SI-3", status=REJECTED, reviewed_by={"who": "ryan"})
    )

    report = _merge(overlay, [_mapping("SI-3")])

    rows = [r for r in overlay.requirements[RID] if r.control == "SI-3"]
    assert len(rows) == 1 and rows[0].status == REJECTED
    assert report.rejected_again == 1
    assert report.proposed == 0


def test_a_requirement_the_overlay_does_not_list_is_seeded_before_recording():
    """Otherwise recording one proposal would unmap the published pairs."""
    controls = _catalogs()
    overlay = parse_overlay({"framework": HIPAA, "requirements": {}})

    _merge(overlay, [_mapping("SI-3")])
    apply_overlays(controls, [overlay])

    assert RID in build_crosswalk(controls)["AT-2"]["hipaa"]


def test_an_errored_proposal_changes_nothing():
    overlay = seed_overlay(_catalogs(), HIPAA)
    before = [r.as_record() for r in overlay.requirements[RID]]

    report = merge(
        overlay, [Proposal(RID, ["AT-2"], error="timeout")], _catalogs(), model="fake-model"
    )

    assert [r.as_record() for r in overlay.requirements[RID]] == before
    assert report.errors == [f"{RID}: timeout"]


def test_a_pair_rejected_by_editing_the_file_is_left_alone():
    """The file is YAML, so a person may reject a pair without any review command."""
    overlay = parse_overlay(
        {
            "framework": HIPAA,
            "requirements": {RID: [{"control": "AT-2", "status": "rejected"}]},
        }
    )

    _merge(overlay, [_mapping("AT-2")])
    _merge(overlay, [])

    (row,) = overlay.requirements[RID]
    assert row.status == REJECTED
    assert row.sources == [] and row.evidence == {} and row.flags == []
    assert not row.needs_review
