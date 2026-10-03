"""Whether a generated document's obligations trace back to its synthesis.

The two findings here are different kinds of claim and the tests are written
to keep them apart: an uncited obligation is a **fact** about the document
and may gate an exit code; a cited obligation its premise does not carry is a
model's **opinion** and may not. Nothing in this file consults a model.
"""

from __future__ import annotations

from policyforge.content.grounding import (
    Claim,
    claims,
    judgeable,
    parse_synthesis,
    premises_for,
    unanchored,
    ungrounded,
)

SYNTHESIS = """\
- Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]
- Privileged access shall be separately approved. [NIST 800-53 AC-6]
"""


def _document(*sections: str) -> str:
    return "\n\n".join(sections) + "\n"


# --------------------------------------------------------------------------
# Reading the two sides
# --------------------------------------------------------------------------


def test_a_synthesis_is_read_one_requirement_per_bullet():
    found = parse_synthesis(SYNTHESIS)

    assert [r.tags for r in found] == [("[NIST 800-53 AC-2]",), ("[NIST 800-53 AC-6]",)]


def test_a_requirement_containing_a_full_stop_stays_whole():
    """Parsed by bullet, not by sentence. A requirement may legitimately
    contain a full stop, and a fragment of one grounds nothing."""
    found = parse_synthesis("- Review accounts. Then approve them. [NIST 800-53 AC-2]\n")

    assert len(found) == 1
    assert "Then approve them" in found[0].text


def test_only_binding_sentences_become_claims():
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]",
        "This section describes account review. [NIST 800-53 AC-2]",
    )

    found = claims(body)

    assert len(found) == 1, "a statement of fact is not a claim to ground"
    assert "shall be reviewed" in found[0].text


# --------------------------------------------------------------------------
# The join — the tag as an index, not as the assertion
# --------------------------------------------------------------------------


def test_a_claim_is_joined_to_every_requirement_sharing_a_tag():
    """A document sentence may merge two requirements and cite both. An
    exact tag-set match would miss that and report a grounded sentence as
    ungrounded, which is the direction that gets a check ignored."""
    requirements = parse_synthesis(SYNTHESIS)
    claim = Claim(text="…", line=1, tags=("[NIST 800-53 AC-2]", "[NIST 800-53 AC-6]"))

    assert len(premises_for(claim, requirements)) == 2


def test_a_claim_citing_something_the_synthesis_lacks_has_no_premise():
    requirements = parse_synthesis(SYNTHESIS)
    claim = Claim(text="…", line=1, tags=("[NIST 800-53 AU-6]",))

    assert premises_for(claim, requirements) == []


# --------------------------------------------------------------------------
# Unanchored: the fact, and the half that may gate
# --------------------------------------------------------------------------


def test_an_uncited_obligation_beside_cited_ones_is_reported():
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]",
        "Reviewers shall retain evidence of each review for seven years.",
    )

    found = unanchored(body)

    assert len(found) == 1
    assert "seven years" in found[0].claim.text


def test_boilerplate_in_a_section_that_cites_nothing_is_not_reported():
    """**The half that gets skipped, and the one that decides whether this
    check survives contact with a real tree.** A Standard's own enforcement
    clause binds, carries no framework tag, and is entirely correct. A rule
    that reported it would be wrong on every document in the corpus."""
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]",
        "## 2. Compliance and Enforcement",
        "Conformance with this Standard is mandatory.",
        "Exceptions must be approved in writing.",
    )

    assert unanchored(body) == []


def test_a_document_that_cites_nothing_anywhere_reports_nothing_here():
    """`check.py:_check_uncited` owns that case and states it as a fact about
    the whole document. Two checks reporting one defect teaches people to
    read neither."""
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed quarterly.",
        "Reviewers shall retain evidence.",
    )

    assert unanchored(body) == []


# --------------------------------------------------------------------------
# Cost, knowable before anything is spent
# --------------------------------------------------------------------------


def test_the_number_of_model_calls_is_countable_without_making_one():
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]",
        "Privileged access shall be separately approved. [NIST 800-53 AC-6]",
        "Reviewers shall retain evidence of each review.",
    )

    pairs = judgeable(body, SYNTHESIS)

    assert len(pairs) == 2, "one per cited claim with a premise; the uncited one is free"


def test_a_cited_claim_with_no_premise_costs_nothing_and_is_not_judged():
    """Nothing to judge it against, so asking a model would be asking about
    the empty set. `_check_citations` already reports a tag mismatch."""
    body = _document(
        "## 1. Logging",
        "Logs shall be retained for one year. [NIST 800-53 AU-11]",
    )

    assert judgeable(body, SYNTHESIS) == []


# --------------------------------------------------------------------------
# Ungrounded: the opinion, and the half that must never gate
# --------------------------------------------------------------------------


class ScriptedEntailer:
    """Answers by premise text, and counts the asking."""

    def __init__(self, supports=True):
        self.supports = supports
        self.calls: list[tuple[str, str]] = []

    def entails(self, premise, hypothesis):
        from policyforge.entail import ENTAILED, NEUTRAL, Verdict

        self.calls.append((premise, hypothesis))
        label = ENTAILED if self.supports else NEUTRAL
        return Verdict(label=label, reason="scripted")


def test_a_carried_obligation_produces_no_finding():
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]",
    )

    assert ungrounded(body, SYNTHESIS, ScriptedEntailer(supports=True)) == []


def test_an_uncarried_obligation_is_reported_with_what_it_was_judged_against():
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed daily by the CISO. [NIST 800-53 AC-2]",
    )

    found = ungrounded(body, SYNTHESIS, ScriptedEntailer(supports=False))

    assert len(found) == 1
    assert found[0].reason == "scripted"
    assert found[0].premises, "the finding must carry the premises, not just a verdict"
    assert "[NIST 800-53 AC-2]" in found[0].premises[0].tags


def test_premises_are_judged_together_not_one_at_a_time():
    """A generated sentence may merge two requirements and cite both — the
    generator is told to. Judging each premise alone would report a faithful
    merge as ungrounded, which is the direction that gets a check ignored."""
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed quarterly and privileged access separately "
        "approved. [NIST 800-53 AC-2] [NIST 800-53 AC-6]",
    )
    entailer = ScriptedEntailer(supports=True)

    ungrounded(body, SYNTHESIS, entailer)

    assert len(entailer.calls) == 1, "one call for the claim, not one per premise"
    premise = entailer.calls[0][0]
    assert "reviewed quarterly" in premise and "separately approved" in premise


def test_the_judge_is_asked_once_per_cited_claim_and_not_once_per_premise():
    """The cost the caller was shown must be the cost incurred. A refactor
    that judges per premise multiplies the bill by the crosswalk's width and
    every other test here still passes."""
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]",
        "Privileged access shall be separately approved. [NIST 800-53 AC-6]",
        "Reviewers shall retain evidence of each review.",
    )
    entailer = ScriptedEntailer(supports=True)

    ungrounded(body, SYNTHESIS, entailer)

    assert len(entailer.calls) == len(judgeable(body, SYNTHESIS)) == 2


def test_an_uncited_obligation_costs_nothing_and_is_not_an_opinion():
    """It is reported by `unanchored` as a fact. Asking a model about a
    sentence with no premise would be asking about the empty set, and would
    turn a free deterministic finding into a paid uncertain one."""
    body = _document(
        "## 1. Access",
        "Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]",
        "Reviewers shall retain evidence of each review.",
    )
    entailer = ScriptedEntailer(supports=True)

    ungrounded(body, SYNTHESIS, entailer)

    assert len(entailer.calls) == 1, "the uncited obligation was never judged"
    assert len(unanchored(body)) == 1, "and it is reported as a fact instead"


# --------------------------------------------------------------------------
# Dotted identifiers — the defect that used to suppress this gate
# --------------------------------------------------------------------------


def test_a_dotted_citation_does_not_lend_its_tag_to_the_sentence_after_it():
    """Regression for #205/#206, and the reason this gate reported nothing
    on HIPAA, 800-171, both CFRs and the AI RMF — the four catalogue
    families whose identifiers all contain a dot.

    `deontic` split sentences on `[.!?]`, cutting through `164.308(a)(3)(i)`,
    so the fragments landed on the same line as the cited sentence and an
    uncited obligation beside it inherited that line's tag. The gate then
    reported **nothing**, which is indistinguishable from a clean document.
    Measured on the #164 probe: 0 unanchored before the fix, 2 after —
    predicted by simulation before the fix existed and confirmed against it."""
    body = _document(
        "## 1. Workforce",
        "Access shall be reviewed quarterly. [HIPAA Security Rule 164.308(a)(3)(i)]",
        "Reviewers shall retain evidence of each review for seven years.",
    )

    found = unanchored(body)

    assert len(found) == 1, "the second obligation cites nothing and must be reported"
    assert "seven years" in found[0].claim.text


# --------------------------------------------------------------------------
# The exit code carries facts, not opinions
# --------------------------------------------------------------------------


def _tree(tmp_path, body: str, synthesis: str):
    """A minimal content tree and its synthesis, as `check` reads them."""
    content = tmp_path / "docs"
    content.mkdir()
    (content / "access-control-standard.md").write_text(
        "---\ntitle: Access Control Standard\ntier: standard\nowner: IAM Engineering\n---\n\n"
        + body,
        encoding="utf-8",
        newline="\n",
    )
    out = tmp_path / "synthesis"
    out.mkdir()
    (out / "access-control-standard.md").write_text(synthesis, encoding="utf-8", newline="\n")
    return content, out


def test_an_ungrounded_verdict_cannot_change_the_exit_code(tmp_path, monkeypatch):
    """**The product ruling on #196, held to the command.** A model's verdict
    can differ between runs on identical input; a malformed document cannot.
    `--strict` promotes warnings to a failing exit, so an opinion recorded as
    a warning would reach the exit code by that route — which is why
    `ungrounded` findings are not `Finding`s at all.

    Asserted with a judge that calls **everything** ungrounded, so if the
    verdict could move the exit code, this is the run where it would.
    """
    from click.testing import CliRunner

    from policyforge.cli import cli

    body = "## 1. Access\n\nAccounts shall be reviewed quarterly. [NIST 800-53 AC-2]\n"
    # Exactly the requirement the document cites: a synthesis carrying more
    # would make `_check_citations` fire a real warning, which `--strict`
    # promotes -- and this test would then pass or fail for that reason
    # rather than for the verdict it is about.
    one = "- Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]\n"
    content, synthesis = _tree(tmp_path, body, one)

    monkeypatch.setattr(
        "policyforge.entail.get_entailer", lambda config: ScriptedEntailer(supports=False)
    )

    result = CliRunner().invoke(
        cli,
        [
            "check",
            "--content-dir",
            str(content),
            "--synthesis-dir",
            str(synthesis),
            "--entail",
            "--strict",
        ],
    )

    assert "1 cited obligation(s) to judge" in result.output, "the cost is stated first"
    assert "do not affect the exit code" in result.output
    assert result.exit_code == 0, "an opinion must not fail the build, even under --strict"
