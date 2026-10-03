"""Making a follow-up question mean what it obviously means.

This is the milestone that makes Zardoz a conversation rather than a search
box with a prompt. The questions people actually have about a policy set
arrive in chains — *what's our access review cadence?*, then *who owns
that?*, then *does it satisfy the HIPAA citation?* — and only the first of
those can be answered on its own. "who owns that?" retrieved literally is
one content word against a corpus of governance documents: it finds
nothing, and the honest refusal it earns is useless, because the question
was perfectly clear to any human reading the exchange.

Resolution happens before retrieval rather than inside answering, and that
ordering is the whole design. Retrieval is keyword scoring; it has no
mechanism for "that" and never will. Handing the answering model the
conversation and hoping it works out which passages *would* have been
relevant is the alternative, and it fails silently — the model answers from
whatever it was given, and nobody can tell that the right section was never
fetched. Rewriting the question first means the failure is visible in the
one place a reader can check it.

**A rewritten question is always shown.** Zardoz resolving "who owns that?"
into "who owns the access review cadence requirement?" is a guess about
intent, and a good one is indistinguishable from a bad one once the answer
is written. So the resolved question is printed above the answer: if the
guess was wrong, the reader sees a plausible answer to a question they did
not ask, which is exactly the situation the rest of this package exists to
make visible rather than to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from policyforge.llm.prompts import Prompt, register

from .budgets import RESOLUTION_TOKENS
from .retrieve import Passage, tokenize

#: Words that point at something said earlier rather than naming it. Checked
#: against the raw question, not the tokenized form, because retrieval's
#: stopword list drops most of them — they carry no topical meaning, which
#: is precisely why their presence signals a question that cannot stand
#: alone.
_ANAPHORA_WORDS = """
    it its that this these those they them their there
    same one ones above below former latter
    """
_ANAPHORA = frozenset(_ANAPHORA_WORDS.split())

#: A question with no more content words than this is elliptical — "how
#: often?", "and backups?" — and leans on the previous turn even when it
#: contains no pronoun at all.
#:
#: One, not two. Two-word questions are overwhelmingly standalone in this
#: domain ("account recertification", "backup retention", "access review"),
#: and treating them as follow-ups meant a perfectly clear question got
#: silently rewritten against whatever preceded it.
_ELLIPTICAL_TERMS = 1

#: How many previous turns are offered to the rewriter. Two is enough for
#: the chains people actually produce and short enough that the model cannot
#: drift onto an older subject that has since been dropped.
CONTEXT_TURNS = 2

_WORD_RE = re.compile(r"[a-z']+")

RESOLVE_SYSTEM_PROMPT = register(
    Prompt(
        name="zardoz.resolve",
        version=1,
        text="""You rewrite follow-up questions into standalone \
ones, for a search over an organization's security policy documents.

You are given the recent exchange and a new question. Return the new
question rewritten so it can be understood with no other context, and
nothing else — no preamble, no explanation, no quotation marks.

Rules:

1. Substitute only what the conversation actually establishes. "who owns
   that?" after a question about access review cadence becomes "who owns
   the access review cadence?" — not "who owns access control", which is a
   broader subject nobody raised.
2. Carry the subject, never the answer. "who owns that?", after being told
   the cadence is quarterly, becomes "who owns the access review cadence?"
   and not "who owns the quarterly access review cadence?" — the frequency
   was the previous answer, it is already known, and putting it in a search
   query narrows retrieval on a detail the user did not ask about. Take the
   subject from the earlier question, not the facts from the earlier reply.
3. Keep the user's own words wherever they still work. You are resolving
   references, not improving phrasing, and a rewrite that swaps the user's
   terms for synonyms will retrieve different documents.
4. If the question already stands alone, return it exactly as given. Most
   questions do.
5. Never answer the question, and never add facts. You are producing a
   search query, and a detail you supplied rather than the user is a detail
   the documents will be searched for and may well contain — giving a
   confident answer to a question nobody asked.
6. Keep control identifiers (AC-2, 164.312(a)(1)) exactly as written. This
   applies when the identifier is what you are carrying forward, not only
   when it is already in the new question: "who owns that?", after a
   question about AC-6(5), becomes "who owns AC-6(5)?". AC-6(5) must not
   become AC-6, and must not be dropped — retrieval scores these by exact
   equality, so a lost or truncated identifier searches for the wrong
   control while the answer still looks right.
7. Never return a fragment. The result is a question somebody could have
   typed; "who owns" is not one.""",
    )
)


@dataclass
class Turn:
    """One question and what came back."""

    question: str
    #: What retrieval actually ran on. Differs from `question` only when the
    #: question was a follow-up that had to be resolved.
    resolved: str = ""
    passages: list[Passage] = field(default_factory=list)
    answer: str = ""

    @property
    def was_rewritten(self) -> bool:
        return bool(self.resolved) and self.resolved.strip() != self.question.strip()

    @property
    def subject(self) -> str:
        """The standalone form of what was asked."""
        return self.resolved or self.question


@dataclass
class Conversation:
    """The turns so far, oldest first."""

    turns: list[Turn] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.turns)

    @property
    def last(self) -> Turn | None:
        return self.turns[-1] if self.turns else None

    def recent(self, count: int = CONTEXT_TURNS) -> list[Turn]:
        return self.turns[-count:] if count > 0 else []

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)

    def clear(self) -> None:
        self.turns.clear()


def looks_like_a_follow_up(question: str) -> bool:
    """Whether this question leans on the ones before it.

    Two signals, either sufficient. A word that points at something said
    earlier, or too few content words to stand up alone — "how often?" names
    no subject at all, and neither does "and for contractors?".

    Deliberately generous: resolving a question that did not need it costs
    one model call and returns the question unchanged, while failing to
    resolve one that did costs the user a refusal to a question they asked
    perfectly clearly.
    """
    words = set(_WORD_RE.findall(question.lower()))
    if words & _ANAPHORA:
        return True
    return len(tokenize(question)) <= _ELLIPTICAL_TERMS


def _transcript(turns: list[Turn]) -> str:
    blocks = []
    for turn in turns:
        blocks.append(f"Q: {turn.subject}")
        if turn.answer:
            blocks.append(f"A: {turn.answer.strip()}")
    return "\n".join(blocks)


def resolve_offline(question: str, conversation: Conversation) -> str:
    """Resolve without a model, by carrying the previous subject along.

    Concatenation rather than substitution: with no model there is no way to
    work out *which* words "that" stands for, but retrieval is a bag of
    terms, so appending the previous question's subject puts the search back
    in the right neighbourhood. Cruder than a rewrite and honest about it —
    the caller shows the result either way, so the reader can see that a
    machine guessed and what it guessed.
    """
    previous = conversation.last
    if previous is None:
        return question

    # Nothing to carry if the question already contains everything the
    # previous subject would contribute — appending it would just repeat
    # the user's own words back at them and show a rewrite that changed
    # nothing.
    carried = set(tokenize(previous.subject)) - set(tokenize(question))
    if not carried:
        return question
    return f"{question.rstrip('?').strip()} — {previous.subject.rstrip('?').strip()}"


def resolve_question(question: str, conversation: Conversation, provider=None) -> tuple[str, bool]:
    """Turn a follow-up into a standalone question.

    Returns `(resolved question, whether it was rewritten)`. The flag is what
    the shell uses to decide whether to show its work: a question that came
    back unchanged needs no explanation, and a rewritten one always does.
    """
    if not conversation.turns or not looks_like_a_follow_up(question):
        return question, False

    if provider is None:
        resolved = resolve_offline(question, conversation)
        return resolved, resolved.strip() != question.strip()

    prompt = (
        f"RECENT EXCHANGE\n\n{_transcript(conversation.recent())}\n\n"
        f"NEW QUESTION\n\n{question.strip()}\n\n"
        "Rewrite the new question so it stands alone."
    )
    from policyforge.llm import effort

    response = effort.call(
        provider,
        effort=effort.RESOLUTION,
        system=RESOLVE_SYSTEM_PROMPT,
        prompt=prompt,
        temperature=0.0,
        max_tokens=RESOLUTION_TOKENS,
    )
    resolved = response.text.strip().strip('"').strip()

    # A rewriter that returns nothing, or an essay, has not done the job.
    # Falling back to the original question means the user gets a refusal
    # they can understand rather than a search for something they never
    # asked about.
    if not resolved or len(resolved) > max(300, len(question) * 6):
        return question, False

    return resolved, resolved.strip() != question.strip()
