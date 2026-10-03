"""zardoz retrieval tests (milestone M2) — finding the right passage.

Retrieval is tested apart from answering on purpose: "did it find the right
section?" and "did it answer well from that section?" are different failures
with different fixes. Everything here is pure and offline, so ranking can be
regression-tested rather than eyeballed.

The cases that matter most are the *refusals*. A retriever that always
returns its least-bad chunk is how a grounded-answers-only tool starts
inventing things — the model gets handed irrelevant context, is asked a
question, and obliges.
"""

from __future__ import annotations

import pytest

from policyforge.zardoz.corpus import CONFLUENCE, MARKDOWN, SUPPORTING, TRUSTED, Corpus
from policyforge.zardoz.corpus import CorpusDocument as Doc
from policyforge.zardoz.retrieve import (
    build_index,
    chunk_document,
    extract_control_ids,
    normalize,
    tokenize,
)

ACCESS_STANDARD = """# Access Control Standard

## 1. Purpose

This Standard governs how accounts are provisioned and reviewed.

## 4. Policy

### 4.1 Account Review

Account entitlements must be recertified quarterly by the system owner.
Terminated accounts are disabled within 24 hours. [NIST AC-2 | HIPAA 164.308(a)(4)]

### 4.2 Privileged Access

Administrative credentials require hardware multi-factor authentication.
[NIST AC-6(5)]
"""

BACKUP_STANDARD = """# Backup and Restore Standard

## 4. Policy

### 4.1 Restore Testing

Restore drills happen twice a year against production snapshots.
[NIST CP-9]
"""

RUNBOOK = """# Laptop Encryption Runbook

Turn on FileVault. Escrow the recovery key in the vault.
"""


def _corpus():
    return Corpus(
        documents=[
            Doc(
                doc_id="standards-access-control",
                title="Access Control Standard",
                space="",
                confidence=TRUSTED,
                source=MARKDOWN,
                path="standards/access-control.md",
                tier="standard",
                topic="Access Review",
                owner="IAM Engineering",
                body=ACCESS_STANDARD,
            ),
            Doc(
                doc_id="standards-backup",
                title="Backup and Restore Standard",
                space="",
                confidence=TRUSTED,
                source=MARKDOWN,
                path="standards/backup.md",
                tier="standard",
                owner="Platform",
                body=BACKUP_STANDARD,
            ),
            Doc(
                doc_id="eng-laptop-encryption-runbook",
                title="Laptop Encryption Runbook",
                space="ENG",
                confidence=SUPPORTING,
                source=CONFLUENCE,
                webui_url="https://x/wiki/laptop",
                body=RUNBOOK,
            ),
        ],
        synced_at="2026-08-31T00:00:00+00:00",
    )


# --------------------------------------------------------------------------
# Tokenizing
# --------------------------------------------------------------------------


def test_plurals_fold_so_a_question_meets_its_own_document():
    assert normalize("reviews") == "review"
    assert normalize("accounts") == "account"


def test_a_word_that_merely_ends_in_s_is_not_treated_as_a_plural():
    """`access` is the most common term in this corpus, and `acces` would
    match nothing. Porter keeps it because it ends in a double s."""
    assert normalize("access") == "access"


def test_folding_is_symmetric_rather_than_pretty():
    """The stems are not words, and do not need to be. What matters is that
    every form of one idea lands on the same one, since the same folding is
    applied to the question and to the document.

    `status` becomes `statu` and `policy` becomes `polici`, which is the
    surprise the old plural-only rule was written to avoid. It was the wrong
    thing to avoid: `policy` and `policies` did not meet under that rule,
    and they do under this one."""
    for word, inflections in [
        ("policy", ["policies"]),
        ("status", ["status"]),
        ("review", ["reviews", "reviewed", "reviewing"]),
        ("recertify", ["recertified", "recertification"]),
        ("approve", ["approved", "approval"]),
    ]:
        stems = {normalize(form) for form in [word, *inflections]}
        assert len(stems) == 1, f"{word}: {stems}"


def test_distinct_ideas_keep_distinct_stems():
    """Aggressive folding is only safe while words that mean different
    things stay apart."""
    for a, b in [("access", "audit"), ("policy", "procedure"), ("backup", "badge")]:
        assert normalize(a) != normalize(b)


def test_function_words_are_dropped_but_domain_words_are_not():
    """Domain noise is left to IDF: "policy" is worthless in a corpus of
    policies and meaningful in one query out of fifty, and hard-coding it
    into a stoplist breaks that query silently."""
    assert tokenize("what is the policy") == [normalize("policy")]


def test_identifiers_never_become_bare_terms():
    """`MP-6` used to tokenize to `mp`, which matched a section citing
    `MP-1` — a claimed match on a control the document does not mention."""
    assert "mp" not in tokenize("what does MP-6 require?")
    assert tokenize("what does MP-6 require?") == [normalize("require")]


def test_control_identifiers_are_extracted_from_prose_and_from_tags():
    assert extract_control_ids("see [NIST AC-2 | HIPAA 164.308(a)(4)]") == [
        "AC-2",
        "164.308(a)(4)",
    ]
    assert extract_control_ids("ac-2 and AC-2 again") == ["AC-2"]
    assert extract_control_ids("enhancement MP-6(1) applies") == ["MP-6(1)"]


def test_a_section_number_is_not_a_regulatory_citation():
    """`10.1 Scope` is a heading, not 45 CFR anything."""
    assert extract_control_ids("## 10.1 Scope") == []


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def test_each_heading_becomes_its_own_citable_chunk():
    chunks = chunk_document(_corpus().documents[0])

    sections = [chunk.section for chunk in chunks]
    assert "4. Policy > 4.1 Account Review" in sections
    assert "4. Policy > 4.2 Privileged Access" in sections


def test_an_h1_restating_the_title_is_not_repeated_in_the_path():
    """Otherwise every citation reads "Access Control Standard § Access
    Control Standard > 4.1", and the repetition is pure noise."""
    chunks = chunk_document(_corpus().documents[0])

    assert all(not chunk.section.startswith("Access Control Standard") for chunk in chunks)


def test_a_chunk_knows_the_controls_its_text_cites():
    chunks = chunk_document(_corpus().documents[0])

    review = next(c for c in chunks if "Account Review" in c.section)
    assert review.control_ids == ["AC-2", "164.308(a)(4)"]


def test_headings_inside_a_code_fence_are_not_headings():
    document = Doc(
        doc_id="d",
        title="T",
        space="",
        confidence=TRUSTED,
        body="# T\n\n## Steps\n\n```sh\n# rotate the key\nvault rotate\n```\n",
    )

    chunks = chunk_document(document)

    assert [c.section for c in chunks] == ["Steps"]
    assert "vault rotate" in chunks[0].text


def test_text_before_the_first_heading_is_kept():
    """A document's opening paragraph is frequently its scope statement."""
    document = Doc(
        doc_id="d",
        title="T",
        space="",
        confidence=TRUSTED,
        body="This applies to all staff.\n\n## Details\n\nMore.\n",
    )

    chunks = chunk_document(document)

    assert chunks[0].section == ""
    assert "all staff" in chunks[0].text


def test_a_heading_level_jump_does_not_leave_stale_ancestors():
    document = Doc(
        doc_id="d",
        title="T",
        space="",
        confidence=TRUSTED,
        body="## A\n\ntext a\n\n#### A1\n\ntext a1\n\n## B\n\ntext b\n",
    )

    chunks = chunk_document(document)

    assert next(c for c in chunks if c.text == "text b").section == "B"


def test_a_very_long_section_is_split_on_paragraph_boundaries():
    paragraph = "Requirements text. " * 40
    document = Doc(
        doc_id="d",
        title="T",
        space="",
        confidence=TRUSTED,
        body="## Long\n\n" + "\n\n".join([paragraph] * 6),
    )

    chunks = chunk_document(document)

    assert len(chunks) > 1
    assert all(c.section == "Long" for c in chunks), "every piece keeps its citation"


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def test_the_right_section_comes_first():
    results = build_index(_corpus()).search("how often are accounts recertified?")

    assert results
    assert results[0].chunk.section == "4. Policy > 4.1 Account Review"
    assert "quarterly" in results[0].chunk.text


def test_naming_a_control_finds_the_section_that_cites_it():
    results = build_index(_corpus()).search("what does AC-6(5) require?")

    assert results[0].matched_controls == ["AC-6(5)"]
    assert results[0].chunk.section == "4. Policy > 4.2 Privileged Access"


def test_a_control_the_corpus_does_not_cite_is_not_answered_from_a_neighbour():
    """AC-2 and AC-3 are different controls to an assessor, and a retriever
    that treats them as similar is worse than one that says nothing."""
    results = build_index(_corpus()).search("what does AC-3 require?")

    assert results == []


# ---- A-02: digits-and-dot tokens that only look like identifiers ----------

NUMBERED_STANDARD = """# Backup and Restore Standard

## 4. Policy

### 4.2 Privileged Access

Administrative credentials require hardware multi-factor authentication.
[NIST AC-6(5)]

### 4.12 Restore Testing

Restore drills happen twice a year against production snapshots.
[NIST CP-9]
"""


def _numbered():
    return build_index(
        Corpus(
            documents=[
                Doc(
                    doc_id="standards-backup",
                    title="Backup and Restore Standard",
                    space="",
                    confidence=TRUSTED,
                    body=NUMBERED_STANDARD,
                )
            ]
        )
    )


def test_a_two_digit_section_number_is_not_a_hitrust_gate():
    """ "section 4.2" worked and "section 4.12" returned nothing: 4.12 is
    HITRUST-shaped, nothing cites it, and naming an uncited control was an
    immediate empty result. Any generated Standard with ten or more
    subsections could not be asked about its own later sections."""
    results = _numbered().search("what does section 4.12 say about restore testing?")

    assert results
    assert "4.12 Restore Testing" in results[0].chunk.section


@pytest.mark.parametrize(
    "question",
    [
        "restore testing from 192.168.1.10",
        "restore testing since version 1.06",
        "restore testing budget of 100.00",
    ],
)
def test_addresses_versions_and_amounts_are_not_gates_either(question):
    """The three other strings the review found extracted as identifiers."""
    results = _numbered().search(question)

    assert results
    assert "Restore Testing" in results[0].chunk.section


def test_a_cued_identifier_nothing_cites_is_still_an_honest_empty_result():
    """The gate is kept where it was right: somebody who writes HITRUST
    01.a is naming a control, and a corpus that never cites it has nothing
    to say about it."""
    assert _numbered().search("what does HITRUST 01.a require for restore testing?") == []


def test_an_identifier_the_corpus_cites_still_counts_without_a_cue():
    results = _numbered().search("what does CP-9 require?")

    assert results
    assert results[0].matched_controls == ["CP-9"]


def test_a_question_the_documents_do_not_cover_returns_nothing():
    index = build_index(_corpus())

    assert index.search("what is our vacation policy?") == []
    assert index.search("who approves expense reports?") == []


def test_an_empty_query_is_not_a_search():
    assert build_index(_corpus()).search("what is the?") == []


def test_a_passage_says_why_it_matched():
    """Every score in this module has to be explainable by naming what hit,
    or ranking cannot be iterated on."""
    results = build_index(_corpus()).search("restore drills")

    assert "restore" in results[0].matched_terms
    assert results[0].score > 0


def test_an_owned_document_wins_a_tie_with_an_unowned_one():
    corpus = Corpus(
        documents=[
            Doc(
                doc_id="unowned",
                title="Shared Text",
                space="ENG",
                confidence=SUPPORTING,
                body="## Rotation\n\nCredentials rotate every ninety days.\n",
            ),
            Doc(
                doc_id="owned",
                title="Shared Text",
                space="ENG",
                confidence=TRUSTED,
                owner="Platform",
                body="## Rotation\n\nCredentials rotate every ninety days.\n",
            ),
        ]
    )

    results = build_index(corpus).search("credential rotation")

    assert results[0].document.doc_id == "owned"
    assert results[0].is_trusted


def test_results_are_capped_and_ordered_deterministically():
    index = build_index(_corpus())

    first = index.search("access control account review", limit=2)
    again = index.search("access control account review", limit=2)

    assert len(first) <= 2
    assert [p.chunk.section for p in first] == [p.chunk.section for p in again]


# --------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------


def test_a_markdown_citation_points_at_the_file():
    results = build_index(_corpus()).search("account recertification")

    assert results[0].citation == (
        "Access Control Standard § 4. Policy > 4.1 Account Review (standards/access-control.md)"
    )


def test_a_confluence_citation_points_at_the_page():
    results = build_index(_corpus()).search("filevault escrow")

    assert "https://x/wiki/laptop" in results[0].citation


# --------------------------------------------------------------------------
# In the shell
# --------------------------------------------------------------------------


def test_a_question_returns_passages_with_citations():
    from policyforge.zardoz.shell import ShellState, dispatch

    output = dispatch("how often are accounts recertified?", ShellState(corpus=_corpus()))

    assert "4.1 Account Review" in output
    assert "standards/access-control.md" in output
    assert "quarterly" in output


def test_a_question_the_corpus_cannot_answer_says_so_plainly():
    from policyforge.zardoz.shell import ShellState, dispatch

    output = dispatch("what is our vacation policy?", ShellState(corpus=_corpus()))

    assert "Nothing in the synced documents" in output
    assert "may not be synced" in output


def test_an_unowned_passage_is_flagged_as_such():
    from policyforge.zardoz.shell import ShellState, dispatch

    output = dispatch("filevault recovery key escrow", ShellState(corpus=_corpus()))

    assert "supporting" in output


def test_reloading_rebuilds_the_index_over_the_new_corpus(tmp_path):
    """An index that outlived its corpus would answer from documents that
    are no longer there, which is the one failure a re-sync exists to fix."""
    from policyforge.zardoz.corpus import write_corpus
    from policyforge.zardoz.shell import ShellState, dispatch

    state = ShellState(corpus=_corpus(), corpus_dir=tmp_path)
    assert dispatch("account recertification", state) != ""
    assert state.index is not None

    write_corpus(
        [
            Doc(
                doc_id="only",
                title="Backup and Restore Standard",
                space="",
                confidence=TRUSTED,
                source=MARKDOWN,
                path="standards/backup.md",
                owner="Platform",
                body=BACKUP_STANDARD,
            )
        ],
        corpus_dir=tmp_path,
    )
    dispatch("/reload", state)

    assert dispatch("account recertification", state).startswith("Nothing in the synced")
    assert "Restore drills" in dispatch("restore drills", state)


# --------------------------------------------------------------------------
# The behaviour table
# --------------------------------------------------------------------------


def test_answers_and_refusals_across_a_realistic_question_set():
    """One table, because ranking regressions show up as a *shift* in where
    the answer/refuse line falls, and a single case moving is easy to miss
    when each lives in its own test.

    The refusals are the half that matters. Every one of them is a question
    a real person would ask of a real policy set, and every one is about
    something these documents do not cover.
    """
    index = build_index(_corpus())
    expected = [
        # (should it answer?, question, the section it should land on)
        (True, "how often are accounts recertified?", "4. Policy > 4.1 Account Review"),
        (True, "when are terminated accounts disabled?", "4. Policy > 4.1 Account Review"),
        (True, "do admins need MFA?", "4. Policy > 4.2 Privileged Access"),
        (True, "what does AC-2 cover?", "4. Policy > 4.1 Account Review"),
        (True, "how often do we test restores?", "4. Policy > 4.1 Restore Testing"),
        (True, "how is the laptop recovery key escrowed?", ""),
        (False, "what is our vacation policy?", None),
        (False, "who approves expense reports?", None),
        (False, "what is the incident severity matrix?", None),
        (False, "what does AC-3 require?", None),
    ]

    wrong = []
    for should_answer, question, section in expected:
        results = index.search(question)
        if bool(results) is not should_answer:
            wrong.append(f"{question!r}: expected {'a hit' if should_answer else 'nothing'}")
        elif results and section is not None and results[0].chunk.section != section:
            wrong.append(f"{question!r}: landed on {results[0].chunk.section!r}, want {section!r}")

    assert not wrong, "\n".join(wrong)


def test_asking_about_a_control_also_reaches_its_enhancements():
    """Nobody enumerates AC-2(1) through AC-2(13) to ask about AC-2, which
    is the same rule `coverage.py` uses to decide what a topic claims."""
    results = build_index(_corpus()).search("what does AC-6 require?")

    assert results
    assert results[0].matched_controls == ["AC-6(5)"]


def test_asking_about_an_enhancement_does_not_reach_the_parent():
    """The narrower question is not answered by the broader citation."""
    corpus = Corpus(
        documents=[
            Doc(
                doc_id="d",
                title="Access Control Standard",
                space="",
                confidence=TRUSTED,
                body="## Accounts\n\nEntitlements are recertified. [NIST AC-2]\n",
            )
        ]
    )

    assert build_index(corpus).search("AC-2(7)") == []


def test_an_acronym_finds_its_spelled_out_form():
    """Questions use "MFA"; Standards say "multi-factor authentication"."""
    results = build_index(_corpus()).search("do admins need MFA?")

    assert results
    assert results[0].chunk.section == "4. Policy > 4.2 Privileged Access"


def test_the_expansion_works_in_both_directions():
    corpus = Corpus(
        documents=[
            Doc(
                doc_id="d",
                title="Authentication Standard",
                space="",
                confidence=TRUSTED,
                body="## Login\n\nAll staff must enrol in MFA before first login.\n",
            )
        ]
    )

    results = build_index(corpus).search("multi-factor authentication enrolment")

    assert results, "a page that only says MFA is still found"


def test_a_passage_only_reports_citations_its_own_text_carries():
    """A document titled "AC-2 Account Management Standard" once made every
    one of its sections report `[cites AC-2]`, including ones that said
    nothing about it. A fabricated citation is the one output this package
    must never produce."""
    corpus = Corpus(
        documents=[
            Doc(
                doc_id="d",
                title="AC-2 Account Management Standard",
                space="",
                confidence=TRUSTED,
                body=(
                    "## 1. Scope\n\nThis applies to every production system.\n\n"
                    "## 2. Review\n\nEntitlements are recertified quarterly. [NIST AC-2]\n"
                ),
            )
        ]
    )

    for passage in build_index(corpus).search("AC-2"):
        for control in passage.matched_controls:
            assert control in passage.chunk.text, (
                f"{passage.chunk.section!r} claims to cite {control} but does not"
            )


def test_an_acronym_expansion_does_not_smuggle_in_stopwords():
    """`sso` expands to "single sign on", and an unfiltered "on" is a query
    term no document can match — the document side drops it — so it only
    drags the coverage ratio down."""
    assert "on" not in tokenize("SSO")

    corpus = Corpus(
        documents=[
            Doc(
                doc_id="d",
                title="Authentication Standard",
                space="",
                confidence=TRUSTED,
                body="## Login\n\nUse single sign-on for all internal applications.\n",
            )
        ]
    )

    assert build_index(corpus).search("SSO"), "a question about SSO finds single sign-on"


# --------------------------------------------------------------------------
# Paraphrase: closing the gap between the question and the document
# --------------------------------------------------------------------------


PRIVILEGED_STANDARD = """# Privileged Access Standard

## 4.1 Review Cadence

Privileged entitlements undergo a quarterly recertification by the service
owner. [NIST AC-6]
"""


def _one_passage_corpus():
    """A corpus big enough that one passage is a sliver of it.

    The size matters as much as the hit count. Thinness is judged against
    the corpus, so a document set with only a section or two never counts as
    thin however few passages a question reaches.
    """
    sections = "\n".join(
        f"## 4.{n} Section {n}\n\nUnrelated requirement number {n} about audit logging.\n"
        for n in range(1, 10)
    )
    return Corpus(
        documents=[
            Doc(
                doc_id="standards-backup",
                title="Backup Standard",
                space="",
                confidence=TRUSTED,
                source=MARKDOWN,
                owner="Platform",
                body=(
                    "# Backup Standard\n\n"
                    "## 3.1 Tape Retention\n\n"
                    "Backup tapes are kept for seven years. [NIST CP-9]\n\n" + sections
                ),
            )
        ]
    )


def _paraphrase_corpus():
    return Corpus(
        documents=[
            Doc(
                doc_id="standards-privileged",
                title="Privileged Access Standard",
                space="",
                confidence=TRUSTED,
                source=MARKDOWN,
                path="standards/privileged.md",
                owner="IAM Engineering",
                body=PRIVILEGED_STANDARD,
            ),
            Doc(
                doc_id="standards-backup",
                title="Backup and Restore Standard",
                space="",
                confidence=TRUSTED,
                source=MARKDOWN,
                path="standards/backup.md",
                owner="Platform",
                body=BACKUP_STANDARD,
            ),
        ]
    )


def test_the_paraphrase_case_misses_without_expansion():
    """The documented gap: not one content word overlaps, and the honest
    refusal that earns is wrong, because the document answers completely."""
    index = build_index(_paraphrase_corpus())

    assert index.search("how often do we check who has admin?") == []


def test_expansion_reaches_the_document_the_question_missed():
    index = build_index(_paraphrase_corpus())

    results = index.search(
        "how often do we check who has admin?",
        expansion="privileged access, recertification, entitlements",
    )

    assert results
    assert results[0].chunk.section == "4.1 Review Cadence"


def test_a_guessed_term_is_reported_apart_from_what_was_typed():
    """ "matched cadence" and "matched cadence, which we guessed you meant"
    are different claims about the evidence."""
    index = build_index(_paraphrase_corpus())

    results = index.search("who has admin?", expansion="privileged entitlements")

    assert "privileged" in results[0].matched_expansions
    assert "privileged" not in results[0].matched_terms


def test_expansion_never_outranks_the_words_somebody_chose():
    """An expansion is a guess at vocabulary, and a guess must not beat the
    user's own words."""
    index = build_index(_paraphrase_corpus())

    typed = index.search("privileged recertification")[0]
    guessed = index.search("who has admin?", expansion="privileged recertification")[0]

    assert typed.chunk.section == guessed.chunk.section
    assert typed.score > guessed.score


def test_expansion_cannot_rescue_a_question_the_corpus_does_not_cover():
    """The refusal has to survive the recovery path, or it was never real."""
    index = build_index(_paraphrase_corpus())

    assert (
        index.search(
            "who approves expense reports?",
            expansion="reimbursement, receipts, finance approval",
        )
        == []
    )


def test_a_question_with_no_terms_at_all_is_still_not_a_search():
    assert build_index(_paraphrase_corpus()).search("what is the?", expansion="privileged") == []


def test_parse_expansion_keeps_terms_and_drops_prose():
    from policyforge.zardoz.paraphrase import parse_expansion

    assert parse_expansion("privileged access, entitlements, recertification") == [
        "privileged access",
        "entitlements",
        "recertification",
    ]
    assert parse_expansion("Certainly, here are the terms a standard would use for this idea") == []


def test_parse_expansion_deduplicates_and_caps():
    from policyforge.zardoz.paraphrase import parse_expansion

    assert parse_expansion("access, Access, ACCESS") == ["access"]
    assert len(parse_expansion(", ".join(f"term{n}" for n in range(30)), max_terms=5)) == 5


def test_expansion_is_skipped_entirely_without_a_model():
    from policyforge.zardoz.paraphrase import expand_query

    assert expand_query("how often do we check who has admin?", None) == ""


def test_a_provider_failure_yields_no_expansion_rather_than_an_error():
    """Expansion is a recovery path taken after a refusal; failing here
    should leave the user with the refusal they had."""
    from policyforge.zardoz.paraphrase import expand_query

    class Exploding:
        def generate(self, **kwargs):
            raise RuntimeError("nope")

    assert expand_query("anything", Exploding()) == ""


def test_the_shell_does_not_expand_when_the_exact_words_already_worked():
    """Precision first: a guess mixed into a search that was working can
    only make it worse, and it costs a request."""
    from dataclasses import dataclass as _dataclass

    from policyforge.zardoz.shell import ShellState, dispatch

    @_dataclass
    class R:
        text: str
        model: str = "fake"

    class Recording:
        def __init__(self):
            self.systems = []

        def generate(self, *, system, **kwargs):
            self.systems.append(system)
            return R("documents" if "rewrite" in system.lower() else "Quarterly. [1]")

    corpus = _paraphrase_corpus()
    query = "privileged recertification cadence"
    index = build_index(corpus)
    # One passage out of two chunks is the corpus, not a sliver of it, so
    # there is nothing a second opinion on vocabulary could add.
    assert len(index.search(query, limit=4)) * 4 > len(index), (
        "this case only says anything while the result is good coverage"
    )

    provider = Recording()
    dispatch(query, ShellState(corpus=corpus, provider=provider))

    # Asserted on which prompts ran rather than on how many, so adding a
    # routing or resolution call does not look like an expansion.
    assert not any("vocabulary" in system.lower() for system in provider.systems), (
        "the exact words worked, so no expansion should have been requested"
    )


def test_the_shell_does_expand_when_the_exact_words_found_only_one_passage():
    """A single passage is the shape that hides a disagreement: the document
    that contradicts it is simply absent, and nothing in the answer can say
    so. Measured on the real corpus — asked how often account
    recertification happens, retrieval returned the Procedure saying
    annually and never reached the Standard saying quarterly."""
    from dataclasses import dataclass as _dataclass

    from policyforge.zardoz.shell import ShellState, dispatch

    @_dataclass
    class R:
        text: str
        model: str = "fake"

    class Recording:
        def __init__(self):
            self.systems = []

        def generate(self, *, system, **kwargs):
            self.systems.append(system)
            return R("documents" if "rewrite" in system.lower() else "Quarterly. [1]")

    corpus = _one_passage_corpus()
    query = "how long are backup tapes kept?"
    index = build_index(corpus)
    passages = index.search(query, limit=4)
    assert len(passages) == 1 and len(passages) * 4 <= len(index), (
        f"the case assumes a sliver: {len(passages)} of {len(index)} chunks"
    )

    provider = Recording()
    dispatch(query, ShellState(corpus=corpus, provider=provider))

    assert any("vocabulary" in system.lower() for system in provider.systems), (
        "one passage is thin, so a second look at the vocabulary was worth a call"
    )


def test_one_long_term_does_not_discard_a_usable_expansion():
    """A term of art can legitimately run to five or six words. Refusing the
    whole list for one of them returned an empty expansion for questions the
    model had answered perfectly well."""
    from policyforge.zardoz.paraphrase import parse_expansion

    terms = parse_expansion(
        "privileged access, entitlement review, user access certification and re-validation, audit"
    )

    assert terms == ["privileged access", "entitlement review", "audit"]


def test_a_mostly_sentence_shaped_reply_is_still_refused_whole():
    from policyforge.zardoz.paraphrase import parse_expansion

    assert parse_expansion("Certainly, here are the terms a standard would use") == []
    assert parse_expansion("The documents refer to privileged access review cadence") == []


# --------------------------------------------------------------------------
# The stemmer
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word, expected",
    [
        ("caresses", "caress"),
        ("ponies", "poni"),
        ("ties", "ti"),
        ("cats", "cat"),
        ("feed", "feed"),
        ("agreed", "agre"),
        ("plastered", "plaster"),
        ("motoring", "motor"),
        ("hopping", "hop"),
        ("failing", "fail"),
        ("filing", "file"),
        ("happy", "happi"),
        ("relational", "relat"),
        ("conditional", "condit"),
        ("rational", "ration"),
        ("callousness", "callous"),
        ("hopeful", "hope"),
        ("goodness", "good"),
        ("revival", "reviv"),
        ("allowance", "allow"),
        ("adjustment", "adjust"),
        ("adoption", "adopt"),
        ("communism", "commun"),
        ("activate", "activ"),
        ("effective", "effect"),
        ("roll", "roll"),
    ],
)
def test_the_stemmer_agrees_with_porters_published_vocabulary(word, expected):
    """Written out rather than depended on, so it is worth proving it is
    actually the algorithm and not an approximation of it. These are Porter's
    own worked examples."""
    assert normalize(word) == expected


@pytest.mark.parametrize(
    "noun, verb",
    [
        ("recertification", "recertified"),
        ("review", "reviewed"),
        ("authorization", "authorized"),
        ("encryption", "encrypted"),
        ("approval", "approved"),
        ("disposal", "disposed"),
        ("termination", "terminated"),
        ("sanitization", "sanitized"),
        ("monitoring", "monitored"),
        ("classification", "classified"),
    ],
)
def test_the_noun_and_the_verb_of_one_idea_meet(noun, verb):
    """Every one of these missed under plural-only folding. The question
    asks with the noun and the document is written with the verb, or the
    other way round, and neither finds the other."""
    assert normalize(noun) == normalize(verb)


def test_an_irregular_pair_is_still_missed_and_that_is_expected():
    """No suffix stripper folds retention onto retain. Recorded so the gap
    is known rather than discovered again — expansion is what covers it."""
    assert normalize("retention") != normalize("retain")


def test_the_ify_family_is_folded_beyond_porter():
    """Porter's step 1c turns "classified" into "classifi" while
    "classification" reduces to "classif". Only after f, so Porter's own
    results for words like "ponies" are untouched."""
    assert normalize("classification") == normalize("classified")
    assert normalize("ponies") == "poni"


def test_a_passage_explains_itself_in_the_words_that_were_typed():
    """Matching happens on stems and the explanation is read by a person. A
    passage reporting that it matched "restor" and "privileg" has stopped
    explaining itself, and naming which terms hit is why this scorer was
    chosen over embeddings."""
    from policyforge.zardoz.retrieve import surface_forms

    forms = surface_forms("how often are privileged accounts reviewed?")

    assert forms[normalize("reviewed")] == "reviewed"
    assert forms[normalize("privileged")] == "privileged"


def test_the_natural_question_reaches_both_sides_of_a_contradiction():
    """The failure that motivated stemming, pinned.

    Two documents disagree about how often accounts are recertified. The
    Standard writes it as "recertified", the Procedure as "recertification",
    and under plural-only folding a question using either word reached only
    one of them — so the answer was confident, cited, and half the truth,
    with nothing to indicate the other half existed.

    Retrieval finding *something* is also why nothing recovered: query
    expansion only ran on a total miss.
    """
    standard = Doc(
        doc_id="standards-access-control",
        title="Access Control Standard",
        space="",
        confidence=TRUSTED,
        source=MARKDOWN,
        owner="IAM Engineering",
        body=(
            "# Access Control Standard\n\n## 4.1 Account Review\n\n"
            "Account entitlements are recertified quarterly. [NIST AC-2]\n"
        ),
    )
    procedure = Doc(
        doc_id="procedures-access-review",
        title="Access Review Procedure",
        space="",
        confidence=TRUSTED,
        source=MARKDOWN,
        owner="IAM Engineering",
        body=(
            "# Access Review Procedure\n\n## 3. Cadence\n\n"
            "Account recertification runs annually, at the start of the fiscal year.\n"
        ),
    )
    # Enough other material that the specificity gate can tell a term about
    # recertification from one about everything. In a corpus of two chunks
    # every shared term is in 100% of it and the gate has nothing to work
    # with — a small-corpus degeneracy, not a property of the question.
    filler = [
        Doc(
            doc_id=f"filler-{n}",
            title=f"Filler Standard {n}",
            space="",
            confidence=TRUSTED,
            source=MARKDOWN,
            body=f"# Filler Standard {n}\n\n## 4.1 Scope\n\nUnrelated logging duty {n}.\n",
        )
        for n in range(6)
    ]

    passages = build_index(Corpus(documents=[standard, procedure, *filler])).search(
        "how often is account recertification performed?", limit=4
    )

    assert {p.document.title for p in passages} == {
        "Access Control Standard",
        "Access Review Procedure",
    }, "a question using one form must reach the document written in the other"
