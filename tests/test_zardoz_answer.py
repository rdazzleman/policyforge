"""zardoz answering tests (milestone M3) — grounded, or absent.

No test calls a model. The provider interface is one method, so a fake that
returns a scripted string exercises every path that matters, and the paths
that matter are mostly the *checks* — what happens when the model cites a
passage that was never supplied, or quotes text that is not in the source.

Those checks are the point of the module. A prompt asking a model to cite
its claims is a request; verifying the citations afterwards is the only
part that holds when the request is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from policyforge.zardoz.answer import (
    REFUSAL_SENTINEL,
    _visible,
    answer_question,
    build_prompt,
    check_answer,
    undisclosed_placeholders,
    ungrounded_values,
)
from policyforge.zardoz.corpus import SUPPORTING, TRUSTED, Corpus
from policyforge.zardoz.corpus import CorpusDocument as Doc
from policyforge.zardoz.retrieve import build_index

ACCESS_STANDARD = """# Access Control Standard

## 4. Policy

### 4.1 Account Review

Account entitlements must be recertified quarterly by the system owner.
Terminated accounts are disabled within 24 hours. [NIST AC-2]
"""

RUNBOOK = """# Offboarding Runbook

Revoke the badge, then close the Okta account.
"""


@dataclass
class FakeResponse:
    text: str
    model: str = "fake"


class FakeProvider:
    """Returns a scripted answer and records what it was asked."""

    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        self.calls.append(
            {
                "system": system,
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return FakeResponse(self.reply)

    def check(self) -> bool:
        return True


class ExplodingProvider:
    def generate(self, **kwargs):
        raise RuntimeError("connection reset")

    def check(self) -> bool:
        return False


def _corpus():
    return Corpus(
        documents=[
            Doc(
                doc_id="standards-access-control",
                title="Access Control Standard",
                space="",
                confidence=TRUSTED,
                source="markdown",
                path="standards/access-control.md",
                tier="standard",
                topic="Access Review",
                owner="IAM Engineering",
                body=ACCESS_STANDARD,
            ),
            Doc(
                doc_id="eng-offboarding-runbook",
                title="Offboarding Runbook",
                space="ENG",
                confidence=SUPPORTING,
                webui_url="https://x/wiki/offboarding",
                body=RUNBOOK,
            ),
        ]
    )


def _passages(query="how often are accounts recertified?"):
    return build_index(_corpus()).search(query)


# --------------------------------------------------------------------------
# The model is not always called
# --------------------------------------------------------------------------


def test_no_passages_means_the_model_is_never_asked():
    """A model handed a question and no context answers it from what access
    control standards usually say. That is the failure this whole package
    exists to prevent, and it arrives sounding entirely plausible."""
    provider = FakeProvider("Accounts are reviewed annually.")

    answer = answer_question("what is our vacation policy?", [], provider)

    assert provider.calls == [], "the provider was called with nothing to ground on"
    assert answer.refused
    assert "Nothing in the synced documents" in answer.text


def test_the_refusal_token_becomes_a_plain_refusal():
    provider = FakeProvider(REFUSAL_SENTINEL)

    answer = answer_question("what is the RTO?", _passages(), provider)

    assert answer.refused
    assert REFUSAL_SENTINEL not in answer.text
    assert "do not answer that" in answer.text
    assert answer.passages, "the closest passages are still offered"


@pytest.mark.parametrize(
    "reply",
    [
        f"`{REFUSAL_SENTINEL}`",
        f"{REFUSAL_SENTINEL}.",
        f'"{REFUSAL_SENTINEL}"',
        f"\n{REFUSAL_SENTINEL}\n",
    ],
)
def test_the_sentinel_in_its_usual_wrapping_is_still_a_refusal(reply):
    """Models return the bare token in backticks or with a full stop often
    enough that treating those as answers would show a reader the token."""
    answer = answer_question("what is the RTO?", _passages(), FakeProvider(reply))

    assert answer.refused


def test_a_partial_answer_that_names_its_gap_keeps_the_part_it_answered():
    """Rule 4 asks for the supported half to be answered and the gap named.
    A model naming the gap with the sentinel used to have the whole reply
    thrown away as a refusal — the cited half with it."""
    reply = (
        "Account entitlements are recertified quarterly by the system owner [1]. "
        f"Who approves exceptions: {REFUSAL_SENTINEL}."
    )
    answer = answer_question("how often, and who approves?", _passages(), FakeProvider(reply))

    assert not answer.refused
    assert "quarterly" in answer.text
    assert answer.cited == [1]
    assert any(REFUSAL_SENTINEL in w for w in answer.warnings), "the gap is reported, not hidden"


def test_answering_is_deterministic():
    """The same question over the same documents must not give two accounts
    of what the organization requires."""
    provider = FakeProvider("Quarterly. [1]")

    answer_question("how often?", _passages(), provider)

    assert provider.calls[0]["temperature"] == 0.0


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_passages_are_numbered_as_the_answer_will_cite_them():
    prompt = build_prompt("how often?", _passages())

    assert "[1] Access Control Standard § 4. Policy > 4.1 Account Review" in prompt
    assert "recertified quarterly" in prompt


def test_the_prompt_says_which_passages_nobody_owns():
    """A requirement nobody is accountable for is a different kind of fact
    from one a named team owns, and the answer has to be able to say so."""
    prompt = build_prompt("how is offboarding done?", _passages("revoke badge okta account"))

    assert "supporting (no declared owner)" in prompt


# --------------------------------------------------------------------------
# Verifying what came back
# --------------------------------------------------------------------------


def test_a_well_formed_answer_passes_its_checks():
    passages = _passages()

    cited, warnings = check_answer("Entitlements are recertified quarterly. [1]", passages)

    assert cited == [1]
    assert warnings == []


def test_a_citation_to_a_passage_that_was_never_supplied_is_caught():
    """The model is told to cite; it is not trusted to have cited something
    real. A marker pointing at nothing is a fabricated source."""
    passages = _passages()

    cited, warnings = check_answer("Reviews happen quarterly. [9]", passages)

    assert cited == [9]
    assert any("never" in w and "[9]" in w for w in warnings)


def test_an_answer_with_no_citations_at_all_is_flagged():
    passages = _passages()

    _, warnings = check_answer("Accounts are reviewed every quarter.", passages)

    assert any("without citing" in w for w in warnings)


def test_a_quotation_that_is_not_verbatim_is_caught():
    """A quotation is what somebody pastes into a ticket or shows an
    assessor. If it isn't in the document, that is the most damaging thing
    this tool could emit."""
    passages = _passages()

    _, warnings = check_answer(
        'The Standard says "entitlements shall be recertified twice yearly" [1]', passages
    )

    assert any("appears in no passage" in w for w in warnings)


def test_a_verbatim_quotation_passes():
    passages = _passages()

    _, warnings = check_answer(
        'It says "Account entitlements must be recertified quarterly" [1]', passages
    )

    assert warnings == []


def test_short_quoted_phrases_are_not_treated_as_quotations():
    """Models quote for mention as well as quotation — the "Owner" field, a
    "trusted" document. Flagging those trains the reader to ignore the
    warning that matters."""
    passages = _passages()

    _, warnings = check_answer('The "owner" recertifies them quarterly. [1]', passages)

    assert warnings == []


def test_whitespace_differences_do_not_make_a_quotation_wrong():
    """The passage wraps mid-sentence; the answer will not."""
    passages = _passages()

    _, warnings = check_answer('It says "recertified quarterly by the system owner" [1]', passages)

    assert warnings == []


def test_warnings_survive_onto_the_answer():
    provider = FakeProvider("Reviews are annual. [7]")

    answer = answer_question("how often?", _passages(), provider)

    assert not answer.is_grounded
    assert answer.warnings


# --------------------------------------------------------------------------
# In the shell
# --------------------------------------------------------------------------


def _state(provider=None, note=""):
    from policyforge.zardoz.shell import ShellState

    return ShellState(corpus=_corpus(), provider=provider, provider_note=note)


def test_an_answer_is_shown_with_the_sources_it_cited():
    from policyforge.zardoz.shell import dispatch

    provider = FakeProvider("Entitlements are recertified quarterly. [1]")

    output = dispatch("how often are accounts recertified?", _state(provider))

    assert "recertified quarterly. [1]" in output
    assert "Sources:" in output
    assert "[1] Access Control Standard § 4. Policy > 4.1 Account Review" in output


def test_a_failed_check_is_shown_above_the_answer_not_below_it():
    """An integrity problem is only useful if the reader sees it before they
    believe the sentence it is about."""
    from policyforge.zardoz.shell import dispatch

    provider = FakeProvider("Reviews are annual. [7]")

    output = dispatch("how often are accounts recertified?", _state(provider))

    assert output.startswith("!!")
    assert output.index("did not pass its own checks") < output.index("Reviews are annual")


def test_with_no_model_configured_the_passages_are_shown_instead():
    """Retrieval is offline. An API key should not be the price of searching
    your own documents."""
    from policyforge.zardoz.shell import dispatch

    output = dispatch("how often are accounts recertified?", _state(note="No config/config.yaml"))

    assert "No config/config.yaml" in output
    assert "4.1 Account Review" in output
    assert "recertified quarterly" in output


def test_a_provider_failure_does_not_end_the_session():
    from policyforge.zardoz.shell import ShellState, dispatch

    state = ShellState(corpus=_corpus(), provider=ExplodingProvider())

    output = dispatch("how often are accounts recertified?", state)

    assert "could not be reached" in output
    assert "connection reset" in output
    assert state.running, "the shell keeps going"
    assert "4.1 Account Review" in output, "the passages are still offered"


def test_sources_shows_the_full_text_behind_the_last_answer():
    from policyforge.zardoz.shell import dispatch

    state = _state(FakeProvider("Recertified quarterly. [1]"))
    dispatch("how often are accounts recertified?", state)

    output = dispatch("/sources", state)

    assert "Terminated accounts are disabled within 24 hours" in output


def test_sources_before_any_question_says_so():
    from policyforge.zardoz.shell import dispatch

    assert "ask a question first" in dispatch("/sources", _state(FakeProvider()))


def test_a_refusal_still_offers_the_closest_passages():
    from policyforge.zardoz.shell import dispatch

    provider = FakeProvider(REFUSAL_SENTINEL)

    output = dispatch("how often are accounts recertified?", _state(provider))

    assert "do not answer that" in output
    assert "Closest passages:" in output


@pytest.mark.parametrize("reply", ["", "   \n  "])
def test_an_empty_reply_is_treated_as_ungrounded(reply):
    provider = FakeProvider(reply)

    answer = answer_question("how often?", _passages(), provider)

    assert answer.warnings, "an empty answer cites nothing and must say so"


def test_the_shell_opens_with_no_api_key_at_all(tmp_path, monkeypatch):
    """Running without a model is a supported mode, not a crash.

    `AnthropicProvider` raises RuntimeError for a missing key, and the shell
    caught only ValueError — so `policyforge zardoz` died on launch for
    anyone without a key. Invisible for as long as everybody working on it
    had one, which is exactly how this class of bug survives.
    """
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        cli_mod,
        "load_config",
        lambda: {
            "llm": {"provider": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"}
        },
    )

    result = CliRunner().invoke(
        cli_mod.cli,
        ["zardoz", "--no-art", "--corpus-dir", str(tmp_path / "absent")],
        input="/quit\n",
    )

    assert result.exit_code == 0, result.output
    assert "No model configured" in result.output
    assert "passages, not prose" in result.output


# --------------------------------------------------------------------------
# Quoting from a real generated document
# --------------------------------------------------------------------------


def _emphasised_passages():
    """A passage written the way `generate` actually writes one."""
    from policyforge.zardoz.corpus import TRUSTED, Corpus
    from policyforge.zardoz.corpus import CorpusDocument as Doc
    from policyforge.zardoz.retrieve import build_index

    body = (
        "# Media Standard\n\n## 4.2 Documentation Retention\n\n"
        "IT Asset Management shall retain such documentation for **6 years** "
        "from the date of its creation or the date it was last in effect.\n"
        "[HIPAA 164.316(b)(1)]\n\n"
        "## 4.3 Access Restriction\n\nAccess is restricted to authorized staff.\n"
    )
    corpus = Corpus(
        documents=[Doc(doc_id="1", title="Media Standard", space="", confidence=TRUSTED, body=body)]
    )
    return build_index(corpus).search("how long is documentation retained?")


def test_a_faithful_quote_that_drops_markdown_is_not_a_fabrication():
    """A generated Standard writes "**6 years**"; a model quoting that
    sentence into prose drops the asterisks, which is correct. Reporting it
    as a fabricated quotation is a false positive on the one check people
    most need to trust — and a check that cries wolf teaches them to scroll
    past the time it catches a real invention."""
    passages = _emphasised_passages()

    _, warnings = check_answer(
        'It says "retain such documentation for 6 years from the date of its creation" [1].',
        passages,
    )

    assert warnings == []


def test_a_quote_that_keeps_the_markdown_also_passes():
    passages = _emphasised_passages()

    _, warnings = check_answer(
        'It says "retain such documentation for **6 years** from the date" [1].', passages
    )

    assert warnings == []


def test_stripping_markup_does_not_blind_the_check_to_a_real_fabrication():
    """The whole point of relaxing the comparison is that it must not relax
    what the check is for."""
    passages = _emphasised_passages()

    _, warnings = check_answer(
        'It says "retain such documentation for 3 years from the date of its creation" [1].',
        passages,
    )

    assert warnings
    assert "appears in no passage" in warnings[0]


def test_prose_between_two_short_quotes_is_not_read_as_a_quotation():
    """A short quote fails the length test, and a pattern that filtered
    length inside the brackets would then resume at its closing mark and
    match the ordinary prose running to the next quotation — reporting words
    nobody quoted as a fabricated quote."""
    passages = _emphasised_passages()

    _, warnings = check_answer(
        'Retain it for "6 years" from the date of its creation or the date it '
        'was last in effect, and log it in the "transport log" afterwards. [1]',
        passages,
    )

    assert warnings == []


def test_a_long_fabricated_quote_beside_a_short_real_one_is_still_caught():
    passages = _emphasised_passages()

    _, warnings = check_answer(
        'Retain for "6 years", and note that "every removal is authorised by '
        'the deputy director in writing" [1].',
        passages,
    )

    assert warnings
    assert "deputy director" in warnings[0]


def test_punctuation_inside_the_quotation_marks_is_not_a_fabrication():
    """Putting the comma inside the quotes is a typographic convention, not
    a change to what the document says."""
    passages = _emphasised_passages()

    _, warnings = check_answer(
        'It says "retain such documentation for 6 years from the date," and then goes on. [1]',
        passages,
    )

    assert warnings == []


def test_trimming_punctuation_does_not_admit_a_changed_quotation():
    passages = _emphasised_passages()

    _, warnings = check_answer(
        'It says "retain such documentation for 6 decades from the date," [1]', passages
    )

    assert warnings


def test_a_quote_lowercased_to_fit_the_sentence_is_not_a_fabrication():
    """A source sentence starts with a capital; a model embedding it
    mid-answer lowercases it. That edits the sentence into its own prose, it
    does not change what the document requires."""
    passages = _emphasised_passages()

    _, warnings = check_answer(
        'The rule is that "it asset management shall retain such documentation '
        'for 6 years" here. [1]',
        passages,
    )

    assert warnings == []


def test_an_interval_no_passage_states_is_flagged():
    """The citation and quotation checks both work on things the model
    marked. An invented frequency is marked as nothing at all — it sits in
    ordinary prose beside a real citation and inherits its authority."""
    passages = _emphasised_passages()

    _, warnings = check_answer("Records are reviewed quarterly [1].", passages)

    assert warnings
    assert "quarterly" in warnings[0]


def test_an_interval_the_passage_does_state_is_not_flagged():
    passages = _emphasised_passages()

    _, warnings = check_answer("Documentation is retained for 6 years [1].", passages)

    assert warnings == []


def test_saying_a_value_is_unset_is_not_stating_one():
    passages = _emphasised_passages()

    _, warnings = check_answer(
        "The frequency is an unfilled placeholder and has not been set [1].", passages
    )

    assert warnings == []


def test_echoing_the_question_s_own_value_to_deny_it_is_not_an_invention():
    """Asked "why do we review accounts annually?", the honest answer says
    the documents do not say annually and gives the real figure. Flagging
    that would punish exactly the behaviour rule 5 asks for."""
    passages = _emphasised_passages()

    _, warnings = check_answer(
        "The documents do not say annually; retention is 6 years [1].",
        passages,
        question="why do we review accounts annually?",
    )

    assert warnings == []


def test_a_value_in_neither_the_question_nor_a_passage_is_still_caught():
    passages = _emphasised_passages()

    _, warnings = check_answer(
        "Records are reviewed quarterly [1].",
        passages,
        question="how often are sanitization records reviewed?",
    )

    assert warnings


def test_an_empty_answer_is_reported_as_empty_not_as_uncited():
    """ "Makes claims without citing any passage" is the wrong finding for
    text that makes no claims. An empty reply is still a problem — a
    truncation, or a provider returning nothing — and naming the right one
    is what lets a reader act on it."""
    cited, warnings = check_answer("", _passages())

    assert cited == []
    assert warnings == ["the model returned an empty answer"]


def test_an_uncited_answer_that_does_make_claims_still_says_so():
    _, warnings = check_answer("Accounts are reviewed quarterly.", _passages())

    assert "makes claims without citing any passage" in warnings


# --------------------------------------------------------------------------
# Typography — the fourth variant of the same false positive
# --------------------------------------------------------------------------


def _quoting(source: str, quoted: str, query: str = "recertified accounts"):
    """An answer quoting `quoted` from a passage that says `source`."""
    corpus = Corpus(
        documents=[
            Doc(
                doc_id="1",
                title="Access Control Standard",
                space="",
                confidence=TRUSTED,
                source="markdown",
                body=f"# Access Control Standard\n\n## 4.1 Review\n\n{source}\n",
            )
        ]
    )
    passages = build_index(corpus).search(query, limit=4)
    assert passages, "the probe retrieved nothing, so it tests nothing"
    return check_answer(f'The Standard says "{quoted}" [1].', passages, "")


SOURCE = "Accounts are recertified quarterly per the organization's schedule."


def test_a_curly_apostrophe_is_not_a_fabricated_quotation():
    """Models normalise punctuation constantly. An apostrophe copied out of
    a document comes back curly, and a quotation is not fabricated because
    its apostrophe has a different code point — the fourth variant of a
    false positive that teaches readers to ignore this warning."""
    _, warnings = _quoting(SOURCE, "recertified quarterly per the organization\u2019s schedule")

    assert warnings == []


@pytest.mark.parametrize("dash", ["-", "\u2010", "\u2013", "\u2014", "\u2212"])
def test_any_dash_matches_any_other(dash):
    _, warnings = _quoting(
        "Restore drills run twice a year - once per half.",
        f"Restore drills run twice a year {dash} once per half",
        query="restore drills",
    )

    assert warnings == []


def test_a_zero_width_character_does_not_break_a_quotation():
    """Invisible by definition, so a reader comparing the two strings by eye
    would call them identical."""
    _, warnings = _quoting(SOURCE, "recertified\u200b quarterly per the organization's schedule")

    assert warnings == []


def test_folding_typography_does_not_let_a_real_fabrication_through():
    """The fold changes how characters are drawn, never which words are
    there. Swapping quarterly for monthly is still caught."""
    _, warnings = _quoting(SOURCE, "recertified monthly per the organization\u2019s schedule")

    assert any("appears in no passage" in w for w in warnings)


# --------------------------------------------------------------------------
# True, cited, and still misleading
#
# Every check above compares an answer against its sources and passes
# anything faithful to them. These two ask what the answer failed to say
# *about* its sources — the gap a local 14B walked straight into, producing
# "Media removal requests are logged in the [Ticketing System] [1]" and an
# unowned-source answer with no provenance, both of which passed every
# other check in the module.
# --------------------------------------------------------------------------


def _unowned_passages():
    """Passages where at least one comes from the document nobody owns."""
    passages = _passages("revoke badge okta account")
    assert any(not p.document.is_trusted for p in passages), "fixture no longer has an unowned doc"
    return passages


def _number_of_unowned(passages):
    return next(n for n, p in enumerate(passages, start=1) if not p.document.is_trusted)


def test_a_reproduced_placeholder_is_not_an_answer():
    """`generate` leaves an unfilled role as `[Ticketing System]` on
    purpose. Copied into an answer it names a system that does not exist,
    while being verbatim and correctly cited."""
    passages = _passages()

    _, warnings = check_answer("Requests are logged in the [Ticketing System]. [1]", passages)

    assert any("placeholder" in w for w in warnings)


def test_an_undecided_parameter_reproduced_verbatim_is_caught():
    passages = _passages()

    _, warnings = check_answer(
        "Reviews happen [Assignment: organization-defined frequency]. [1]", passages
    )

    assert any("placeholder" in w for w in warnings)


def test_a_placeholder_the_answer_calls_a_placeholder_is_not_flagged():
    """The false positive this module keeps guarding against. An answer that
    already says the value is unfilled has done the right thing, and warning
    on it teaches the reader to scroll past the warning that matters."""
    passages = _passages()

    _, warnings = check_answer(
        "The documents name only a generic [Ticketing System] placeholder, "
        "not a specific system. [1]",
        passages,
    )

    assert warnings == []


def test_a_citation_marker_is_not_mistaken_for_a_placeholder():
    passages = _passages()

    _, warnings = check_answer("Entitlements are recertified quarterly. [1]", passages)

    assert warnings == []


def test_a_markdown_link_is_not_mistaken_for_a_placeholder():
    passages = _passages()

    _, warnings = check_answer(
        "Entitlements are recertified quarterly, per [the standard](https://example.test/ac). [1]",
        passages,
    )

    assert warnings == []


def test_a_lowercase_aside_is_not_mistaken_for_a_placeholder():
    passages = _passages()

    _, warnings = check_answer("Entitlements are recertified quarterly [see below]. [1]", passages)

    assert warnings == []


def test_an_answer_built_on_unowned_content_must_say_so():
    """A supporting document is real content nobody has declared ownership
    of. Answers may draw on it; presented without that, the claim reads as
    governed policy somebody is accountable for."""
    passages = _unowned_passages()
    n = _number_of_unowned(passages)

    _, warnings = check_answer(f"The Okta account is closed during offboarding. [{n}]", passages)

    assert any("unowned" in w for w in warnings)


def test_an_answer_that_credits_the_unowned_source_passes():
    passages = _unowned_passages()
    n = _number_of_unowned(passages)

    _, warnings = check_answer(
        f"A supporting runbook nobody owns says the Okta account is closed "
        f"during offboarding. [{n}]",
        passages,
    )

    assert not any("unowned" in w for w in warnings)


def test_citing_only_owned_documents_needs_no_provenance_note():
    passages = _passages()
    owned = [n for n, p in enumerate(passages, start=1) if p.document.is_trusted]

    _, warnings = check_answer(f"Entitlements are recertified quarterly. [{owned[0]}]", passages)

    assert warnings == []


# --------------------------------------------------------------------------
# Real answers, from real models, that this check got wrong
#
# The placeholder rule shipped with a vocabulary of exact phrases and
# promptly flagged three correct answers across two models: it carried
# "does not specify" and met "The documents **do** not specify", and it had
# no word for an answer that explains the brackets instead of naming them.
# Those false positives inflated two eval runs before anyone read the
# output. The strings below are what the models actually wrote.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        # deepseek-v4-pro, explaining the brackets rather than naming them.
        'Requests are logged in "[Ticketing System]" [1], but the brackets '
        "indicate the organization has not assigned one.",
        'Reviewed at an "[Assignment: organization-defined frequency]" [1], '
        "meaning the organization has not set one.",
        # deepseek-v4-flash, plural subject: "documents DO not specify".
        "The documents do not specify a frequency for reviewing media "
        "sanitization records. Passage [1] states they are reviewed "
        '"[Assignment: organization-defined frequency]".',
        "The documents do not specify how often media sanitization records "
        "are reviewed. Passage [1] states the review occurs at an "
        '"[Assignment: organization-defined frequency]".',
    ],
)
def test_an_answer_that_explains_the_placeholder_is_not_flagged(answer):
    assert undisclosed_placeholders(answer) == []


@pytest.mark.parametrize(
    "answer",
    [
        "Media removal requests are logged in the [Ticketing System] [1].",
        "Reviews happen [Assignment: organization-defined frequency]. [1]",
    ],
)
def test_a_placeholder_presented_as_the_answer_is_still_flagged(answer):
    """Widening the vocabulary must not blunt the check it belongs to."""
    assert undisclosed_placeholders(answer) != []


# --------------------------------------------------------------------------
# Two false positives that were depressing every model's answering score
#
# Neither was a model error. Both were found by reading the failures of a
# run rather than its rate, which is the only way this kind of bug surfaces:
# the score simply looks a few points lower than it should, on every model
# at once, and nothing points at the checker.
# --------------------------------------------------------------------------


def test_a_narrow_no_break_space_is_not_an_invented_figure():
    """A model writing "6 years" with U+202F was reported as inventing a
    figure the passage states in so many words — the check firing hardest
    on the output that took most care over its typography."""
    haystack = "documentation shall be retained for 6 years"

    # Spelled as a code point for the reason _TYPOGRAPHY gives: a test
    # about a character nobody can see should not be written in it.
    assert ungrounded_values("Retained for 6\u202fyears. [1]", haystack) == []


def test_a_real_invention_is_still_caught_after_normalising():
    haystack = "documentation shall be retained for 6 years"

    assert ungrounded_values("Reviewed quarterly. [1]", haystack) == ["quarterly"]


def test_a_bracketed_case_marker_is_not_a_fabricated_quotation():
    """ "[t]erminated accounts..." quotes a source that opens "Terminated".
    Altering the case and marking it is the convention for quoting into a
    sentence; reading it as invention punishes careful quoting."""
    passages = _passages()
    quoted = passages[0].chunk.text.strip().split(".")[0]
    answer = f'The Standard says "[{quoted[0].lower()}]{quoted[1:]}". [1]'

    _, warnings = check_answer(answer, passages)

    assert not [w for w in warnings if "appears in no passage" in w]


def test_a_case_marker_does_not_swallow_a_placeholder():
    """One letter only. "[Ticketing System]" is several words and must
    survive, or the placeholder check goes blind."""
    assert _visible("logged in [Ticketing System]") == "logged in [Ticketing System]"


def test_a_case_marker_does_not_eat_citation_markers():
    assert _visible("recertified quarterly [1]") == "recertified quarterly [1]"


# --------------------------------------------------------------------------
# Routing through a schema, where one can be enforced
# --------------------------------------------------------------------------


class _SchemaRouter:
    """A provider whose schema path returns a fixed analysis."""

    def __init__(self, analysis="history", can=True, fail=False):
        self.analysis = analysis
        self.can = can
        self.fail = fail
        self.json_calls = 0
        self.prose_calls = 0

    def supports_schema(self):
        return self.can

    def generate_json(self, **kwargs):
        import json

        self.json_calls += 1
        if self.fail:
            raise RuntimeError("no schema for you")
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=json.dumps({"analysis": self.analysis}), model="fake")

    def generate(self, **kwargs):
        self.prose_calls += 1
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text="documents", model="fake")

    def check(self):
        return True


def test_routing_uses_a_schema_when_the_model_can_be_held_to_one():
    from policyforge.zardoz.skills import route

    provider = _SchemaRouter(analysis="history")

    assert route("what versions have been recorded?", provider) == "history"
    # One call, not two. `route` asks only which analysis; filling that
    # analysis's arguments is a second, separate call made by
    # `route_with_arguments`, and it is separate precisely so it cannot
    # affect the decision this call makes — measured, see MEASUREMENTS.md
    # epoch 14.
    assert (provider.json_calls, provider.prose_calls) == (1, 0)


def test_routing_falls_back_to_prose_when_the_model_cannot():
    """Most local models cannot enforce a schema, and a router that only
    worked on hosted ones would be worse than the one already here."""
    from policyforge.zardoz.skills import route

    provider = _SchemaRouter(can=False)

    assert route("anything", provider) == "documents"
    assert (provider.json_calls, provider.prose_calls) == (0, 1)


def test_a_failed_schema_call_falls_through_rather_than_failing_the_route():
    """A failed optimisation must leave the caller where it started."""
    from policyforge.zardoz.skills import route

    provider = _SchemaRouter(fail=True)

    assert route("anything", provider) == "documents"
    assert (provider.json_calls, provider.prose_calls) == (1, 1)


def test_a_cut_off_answer_is_named_as_such_and_not_shown():
    """Not "could not be reached": the model answered, and the answer ran past
    its budget twice. Half an answer with the citations at the cut-off end
    would read as a whole one, so it stays unshown and the passages stand."""
    from policyforge.llm.base import LLMResponse
    from policyforge.zardoz.shell import ShellState, dispatch

    class CutProvider:
        def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
            return LLMResponse(
                text="Accounts are recertified [1] and also", model="m", stop_reason="length"
            )

        def check(self):
            return True

    state = ShellState(corpus=_corpus(), provider=CutProvider())

    output = dispatch("how often are accounts recertified?", state)

    assert "cut off at" in output
    assert "not shown" in output
    assert "could not be reached" not in output
    assert "and also" not in output, "the partial answer must not appear"
    assert "4.1 Account Review" in output, "the passages are still offered"
    assert state.running


# --------------------------------------------------------------------------
# Entailment, when it is switched on
# --------------------------------------------------------------------------


class RecordingEntailer:
    """Judges every claim the way it was told to, and counts the asking."""

    def __init__(self, label):
        self.label = label
        self.calls = []

    def entails(self, premise, hypothesis):
        from policyforge.entail import Verdict

        self.calls.append((premise, hypothesis))
        return Verdict(label=self.label, reason="the passage names a different team")


def test_no_entailer_means_no_judging_and_no_warning():
    """The default. Off is off: nothing is asked, and nothing is said about
    a check that did not run, because a warning nobody can act on is noise."""
    from policyforge.entail import ENTAILED
    from policyforge.zardoz.answer import answer_question

    judge = RecordingEntailer(ENTAILED)
    provider = FakeProvider("Accounts are recertified quarterly. [1]")

    answer = answer_question("how often are accounts recertified?", _passages(), provider)

    assert judge.calls == []
    assert not [w for w in answer.warnings if "entail" in w or "does not support" in w]


def test_an_unsupported_claim_is_reported_as_a_warning():
    """The failure no other check on this path can see: the marker points at a
    real passage, the quotation is verbatim, and the passage does not carry the
    claim built around it."""
    from policyforge.entail import NEUTRAL
    from policyforge.zardoz.answer import answer_question

    judge = RecordingEntailer(NEUTRAL)
    provider = FakeProvider("The Security Officer approves every exception. [1]")

    answer = answer_question("who approves exceptions?", _passages(), provider, entailer=judge)

    assert judge.calls, "a cited sentence should have been judged"
    unsupported = [w for w in answer.warnings if "does not support" in w]
    assert len(unsupported) == 1
    assert "Security Officer" in unsupported[0]
    assert "the passage names a different team" in unsupported[0]
    assert answer.text.startswith("The Security Officer"), "the answer itself is untouched"


def test_a_supported_claim_produces_no_entailment_warning():
    from policyforge.entail import ENTAILED
    from policyforge.zardoz.answer import answer_question

    judge = RecordingEntailer(ENTAILED)
    provider = FakeProvider("Accounts are recertified quarterly. [1]")

    answer = answer_question(
        "how often are accounts recertified?", _passages(), provider, entailer=judge
    )

    assert judge.calls, "the judge is still asked"
    assert not [w for w in answer.warnings if "does not support" in w]


def test_a_judge_that_cannot_run_is_reported_and_the_answer_survives():
    """`LLMEntailer` refuses rather than degrades when its provider cannot be
    held to a schema. Losing the answer to that would be the wrong trade, and
    so would swallowing it: a check nobody can tell did not happen is worse
    than one that is plainly off."""
    from policyforge.zardoz.answer import answer_question

    class Refusing:
        def entails(self, premise, hypothesis):
            raise RuntimeError("cannot be held to a schema")

    provider = FakeProvider("Accounts are recertified quarterly. [1]")

    answer = answer_question(
        "how often are accounts recertified?", _passages(), provider, entailer=Refusing()
    )

    assert answer.text == "Accounts are recertified quarterly. [1]"
    assert [w for w in answer.warnings if "entailment check did not run" in w]
    assert [w for w in answer.warnings if "cannot be held to a schema" in w]


class ScriptingEntailer:
    """Judges by what the passage says, and counts the asking.

    `RecordingEntailer` answers every question the same way, which cannot
    express two passages disagreeing with each other.
    """

    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = []

    def entails(self, premise, hypothesis):
        from policyforge.entail import Verdict

        self.calls.append((premise, hypothesis))
        return Verdict(label=self.verdicts[premise], reason="scripted")


def _judged_passages(*texts):
    """Passage-shaped stand-ins. `_entailment_warnings` reads `chunk.text`
    and nothing else, and building a corpus that retrieves two passages for
    one question would put the retriever inside a test about the judge."""
    from types import SimpleNamespace

    return [SimpleNamespace(chunk=SimpleNamespace(text=text)) for text in texts]


def test_the_judge_is_asked_once_per_citation_and_not_twice():
    """Two findings are read off one pass of judging. A refactor that gives
    each its own call site doubles the cost of the entailment path — which is
    opt-in precisely because every citation costs a model call — and nothing
    else here would notice: both findings would still be right, just billed
    twice, and the only evidence would be on the bill."""
    from policyforge.entail import ENTAILED
    from policyforge.zardoz.answer import _entailment_warnings

    judge = ScriptingEntailer({"carries it": ENTAILED, "denies it": ENTAILED})

    _entailment_warnings(
        "A claim. [1][2] Another claim. [1]",
        _judged_passages("carries it", "denies it"),
        judge,
    )

    assert len(judge.calls) == 3, "three citations, three judgements"


def test_passages_that_disagree_are_reported_under_their_own_heading():
    """Not as an unsupported claim: the sentence *is* carried, by a source
    another cited source denies. The separate prefix is also what lets the
    eval harness count the two apart."""
    from policyforge.entail import CONTRADICTED, ENTAILED
    from policyforge.zardoz.answer import (
        CONFLICTING_PASSAGES_PREFIX,
        UNSUPPORTED_PREFIX,
        _entailment_warnings,
    )

    judge = ScriptingEntailer({"carries it": ENTAILED, "denies it": CONTRADICTED})

    warnings = _entailment_warnings(
        "Accounts are recertified quarterly. [1][2]",
        _judged_passages("carries it", "denies it"),
        judge,
    )

    assert len(warnings) == 1
    assert warnings[0].startswith(CONFLICTING_PASSAGES_PREFIX)
    assert not warnings[0].startswith(UNSUPPORTED_PREFIX)
    assert "[1]" in warnings[0] and "[2]" in warnings[0]


def test_the_shell_passes_its_judge_and_builds_one_only_when_asked():
    """The flag is `entail.answering`, and it lives in the block that already
    holds the judge's model, so there is one place to look."""
    from policyforge.entail import NEUTRAL
    from policyforge.zardoz.shell import ShellState, dispatch

    judge = RecordingEntailer(NEUTRAL)
    state = ShellState(
        corpus=_corpus(),
        provider=FakeProvider("Accounts are recertified quarterly. [1]"),
        entailer=judge,
    )

    output = dispatch("how often are accounts recertified?", state)

    assert judge.calls, "the shell must hand its judge to the answering path"
    assert "does not support" in output


# --------------------------------------------------------------------------
# The entailment switch
# --------------------------------------------------------------------------


def _session(tmp_path, config):
    from policyforge.zardoz.startup import open_session

    topics = tmp_path / "topics.yaml"
    topics.write_text("topics: []\n", encoding="utf-8")
    return open_session(
        topics_path=topics,
        corpus_dir=tmp_path / "corpus",
        config=config,
        provider_factory=lambda cfg: object(),
    )


def test_no_judge_is_built_unless_answering_is_switched_on(tmp_path):
    """An `entail:` block alone configures the judge; it does not enable it.
    The cost is a model call per cited sentence, so it is opted into."""
    session = _session(tmp_path, {"entail": {"model": "openrouter/some/model"}})

    assert session.state.entailer is None
    assert not [n for n in session.notes if "entailment" in n]


def test_answering_on_without_a_block_to_build_from_says_so(tmp_path):
    """Silently answering as though the check had run and found nothing is the
    one outcome worth refusing."""
    session = _session(tmp_path, {"entail": {"answering": True}})

    assert session.state.entailer is None
    assert [n for n in session.notes if "could not be built" in n or "no entail block" in n]


def test_a_judge_that_cannot_be_built_is_reported_not_raised(tmp_path):
    """`LLMEntailer` refuses a missing model, because it must be a different
    one from the answerer's. That must not stop the shell opening."""
    session = _session(tmp_path, {"entail": {"answering": True, "provider": "llm"}})

    assert session.state.entailer is None
    assert [n for n in session.notes if "entailment" in n]
