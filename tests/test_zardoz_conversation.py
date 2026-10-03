"""zardoz conversation tests (milestone M4) — follow-up questions.

The case that matters is the chain: a question that is perfectly clear to a
human reading the exchange and meaningless on its own. "who owns that?" has
one content word, and retrieved literally it earns an honest refusal that
helps nobody.

The other case that matters is the false positive — a standalone question
mistaken for a follow-up and silently rewritten against whatever preceded
it. That one is worse, because the user gets a confident answer to a
question they did not ask, so the resolved question is always shown.
"""

from __future__ import annotations

from dataclasses import dataclass

from policyforge.zardoz.conversation import (
    Conversation,
    Turn,
    looks_like_a_follow_up,
    resolve_offline,
    resolve_question,
)
from policyforge.zardoz.corpus import TRUSTED, Corpus
from policyforge.zardoz.corpus import CorpusDocument as Doc
from policyforge.zardoz.shell import ShellState, dispatch

ACCESS_STANDARD = """# Access Control Standard

## 4. Policy

### 4.1 Account Review

Account entitlements are recertified quarterly by the system owner.
[NIST AC-2]

### 4.2 Privileged Access

Administrative credentials require hardware tokens.
[NIST AC-6]
"""

BACKUP_STANDARD = """# Backup and Restore Standard

## 4. Policy

### 4.1 Restore Testing

Restore drills happen twice a year against production snapshots.
[NIST CP-9]
"""


@dataclass
class FakeResponse:
    text: str
    model: str = "fake"


class ScriptedProvider:
    """Replies in order, recording every prompt it was given."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        self.prompts.append(prompt)
        return FakeResponse(self.replies.pop(0) if self.replies else "")

    def check(self) -> bool:
        return True


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
                topic="Access Review",
                owner="IAM Engineering",
                body=ACCESS_STANDARD,
            ),
            Doc(
                doc_id="standards-backup",
                title="Backup and Restore Standard",
                space="",
                confidence=TRUSTED,
                source="markdown",
                path="standards/backup.md",
                topic="Backup",
                owner="Platform",
                body=BACKUP_STANDARD,
            ),
        ]
    )


def _with_history(subject="what is our access review cadence?", answer="Quarterly. [1]"):
    return Conversation(turns=[Turn(question=subject, resolved=subject, answer=answer)])


# --------------------------------------------------------------------------
# Spotting a follow-up
# --------------------------------------------------------------------------


def test_a_word_pointing_backwards_marks_a_follow_up():
    for question in ("who owns that?", "does it satisfy the HIPAA citation?", "what about those?"):
        assert looks_like_a_follow_up(question), question


def test_a_question_with_no_subject_of_its_own_marks_a_follow_up():
    assert looks_like_a_follow_up("how often?")


def test_a_short_standalone_question_is_not_a_follow_up():
    """Two-word questions are overwhelmingly standalone in this domain, and
    rewriting one against whatever preceded it answers something else."""
    for question in ("account recertification", "backup retention", "access review cadence"):
        assert not looks_like_a_follow_up(question), question


def test_a_full_question_is_not_a_follow_up():
    assert not looks_like_a_follow_up("what is our access review cadence?")


# --------------------------------------------------------------------------
# Resolving
# --------------------------------------------------------------------------


def test_the_first_question_is_never_rewritten():
    resolved, rewritten = resolve_question("who owns that?", Conversation(), provider=None)

    assert resolved == "who owns that?"
    assert not rewritten


def test_a_standalone_question_passes_through_untouched():
    provider = ScriptedProvider("something else entirely")

    resolved, rewritten = resolve_question(
        "what is our backup retention period?", _with_history(), provider
    )

    assert resolved == "what is our backup retention period?"
    assert not rewritten
    assert provider.prompts == [], "the rewriter was not even called"


def test_a_follow_up_is_rewritten_against_the_exchange():
    provider = ScriptedProvider("who owns the access review cadence?")

    resolved, rewritten = resolve_question("who owns that?", _with_history(), provider)

    assert resolved == "who owns the access review cadence?"
    assert rewritten
    assert "access review cadence" in provider.prompts[0], "the exchange was supplied"


def test_a_rewriter_that_returns_nothing_falls_back_to_the_question():
    """A refusal the user can understand beats a search for something they
    never asked about."""
    resolved, rewritten = resolve_question("who owns that?", _with_history(), ScriptedProvider(""))

    assert resolved == "who owns that?"
    assert not rewritten


def test_a_rewriter_that_writes_an_essay_is_ignored():
    provider = ScriptedProvider("Certainly! " + "The access review process " * 40)

    resolved, rewritten = resolve_question("who owns that?", _with_history(), provider)

    assert resolved == "who owns that?"
    assert not rewritten


def test_without_a_model_the_previous_subject_is_carried_along():
    resolved = resolve_offline("who owns that?", _with_history())

    assert "who owns that" in resolved
    assert "access review cadence" in resolved


def test_the_offline_fallback_does_not_repeat_the_question_back():
    """Appending a subject the question already contains shows a rewrite
    that changed nothing."""
    conversation = _with_history(subject="account recertification")

    assert resolve_offline("account recertification", conversation) == "account recertification"


# --------------------------------------------------------------------------
# Keeping the turns
# --------------------------------------------------------------------------


def test_a_conversation_keeps_turns_in_order_and_can_be_cleared():
    conversation = Conversation()
    conversation.add(Turn(question="first"))
    conversation.add(Turn(question="second"))

    assert len(conversation) == 2
    assert conversation.last.question == "second"
    assert [t.question for t in conversation.recent(1)] == ["second"]

    conversation.clear()
    assert len(conversation) == 0
    assert conversation.last is None


def test_a_turn_knows_whether_it_was_rewritten():
    assert Turn(question="who owns that?", resolved="who owns the cadence?").was_rewritten
    assert not Turn(question="same", resolved="same").was_rewritten


# --------------------------------------------------------------------------
# In the shell
# --------------------------------------------------------------------------


def test_a_follow_up_reaches_the_section_the_first_question_found():
    """The whole point. Asked cold, "who owns that?" finds nothing."""
    state = ShellState(corpus=_corpus())
    cold = dispatch("who owns that?", state)
    assert "Nothing in the synced documents" in cold

    state = ShellState(corpus=_corpus())
    dispatch("how often are accounts recertified?", state)
    warm = dispatch("who owns that?", state)

    assert "Nothing in the synced documents" not in warm
    assert "4.1 Account Review" in warm


def test_a_rewritten_question_is_always_shown():
    """A good guess about intent is indistinguishable from a bad one once
    the answer is written, so the reader is told what was searched for."""
    state = ShellState(corpus=_corpus())
    dispatch("how often are accounts recertified?", state)

    output = dispatch("who owns that?", state)

    assert output.startswith("(reading that as:")


def test_a_question_that_was_not_rewritten_shows_no_note():
    state = ShellState(corpus=_corpus())

    output = dispatch("how often are accounts recertified?", state)

    assert not output.startswith("(reading that as:")


def test_every_turn_is_recorded():
    state = ShellState(corpus=_corpus())

    dispatch("how often are accounts recertified?", state)
    dispatch("who owns that?", state)

    assert len(state.conversation) == 2
    assert state.conversation.last.was_rewritten


def test_forget_drops_the_context_so_the_next_question_stands_alone():
    state = ShellState(corpus=_corpus())
    dispatch("how often are accounts recertified?", state)

    assert "Forgotten 1 turn" in dispatch("/forget", state)
    assert len(state.conversation) == 0
    assert "Nothing in the synced documents" in dispatch("who owns that?", state)


def test_forget_before_anything_was_asked_says_so():
    assert "Nothing to forget" in dispatch("/forget", ShellState(corpus=_corpus()))


def test_the_answer_is_written_from_the_resolved_question():
    """The model must be asked what the user meant, not what they typed."""
    provider = ScriptedProvider(
        "who owns the quarterly account recertification?",
        "IAM Engineering owns it. [1]",
    )
    state = ShellState(corpus=_corpus(), provider=provider)
    state.conversation.add(
        Turn(
            question="cadence?", resolved="how often are accounts recertified", answer="Quarterly."
        )
    )

    dispatch("who owns that?", state)

    assert "who owns the quarterly account recertification?" in provider.prompts[-1]
