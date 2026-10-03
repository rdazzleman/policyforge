"""Does the cited passage actually support the claim?

`zardoz/answer.py` verifies everything about an answer that can be decided
by looking: the citation points at a passage that exists, the quotation is
verbatim, no interval appears that no passage states, no placeholder is
presented as a value, no unowned source goes undisclosed. All of it is a
pattern or a comparison, and all of it is exact.

None of it can answer the remaining question. Given a passage that says
*"IT Asset Management shall generate and retain Certificates of
Destruction"*, the sentence *"The Security Officer approves Certificates of
Destruction. [1]"* cites correctly, quotes nothing, invents no interval and
names no placeholder — and is wrong about both the actor and the act. Every
deterministic check passes it. That is the gap, and it is the failure mode
an assessor would notice first.

**This is a model judging a model, and that is a real cost.**
`evals/runner.py` refuses it for grading, with a reason that holds: *"A
grader that itself needed a model would have the failure mode it exists to
detect."* Today's runs bear it out — the deterministic checks caught
sonnet-5 fabricating a quotation. So three lines are drawn here and none of
them should be crossed casually:

1. **`check_answer` is not touched.** It stays pure, exact, and dependency
   free. Its warnings remain facts.
2. **These findings are opinions, and are labelled as such.** Mixing a
   model's judgement into a list of verified facts would devalue the facts,
   which are the more useful half.
3. **This is never an eval grader.** The harness keeps deciding with
   patterns, or it stops being able to measure the thing it exists for.

The strongest version of this uses a dedicated NLI cross-encoder —
DeBERTa-MNLI and its relatives — which is small, fast, and crucially from a
different model family than whatever wrote the answer. That independence is
most of the value. It needs PyTorch, which on this project's Windows/AMD
target means CPU-only and a heavy dependency, so `llm_entailer.py` ships
first and the interface leaves room for the better one.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

#: The passage supports the claim.
ENTAILED = "entailed"
#: The passage neither supports nor contradicts it — the common failure.
#: A claim about an actor the passage never mentions lands here.
NEUTRAL = "neutral"
#: The passage says otherwise. Rarer and worse.
CONTRADICTED = "contradicted"

_CITATION_RE = re.compile(r"\[(\d+)\]")

#: Where one claim ends and the next begins: a run of `.!?` that is either
#: at the end of the text, or followed by whitespace and something that
#: starts a sentence — a capital, a quote, a bracket, or the `[n]` marker of
#: a trailing citation.
#:
#: It splits on *fewer* things than a full stop, on purpose. The rule this
#: replaced was `[^.!?]+(?:[.!?]+|$)`, which broke at every full stop,
#: including the ones inside `Section 4.2`, `164.308(a)(3)`, `45 C.F.R.`,
#: `Rev. 5`, `e.g.` and `99.9%`. Each break cost two things and only one of
#: them was visible: the judge was handed a fragment like `2 of the Standard`
#: and reported it unsupported, which shows up as a false finding — and the
#: claim the fragment was carved out of was never judged at all, which shows
#: up as nothing. **A claim that is never examined looks exactly like a claim
#: that passed.** Measured over a real run: of 43 findings whose text could be
#: parsed, 19 were fragments.
#:
#: So the failure direction is chosen. Under-splitting merges two sentences
#: into one unit, and the claim is still judged, just in a coarser chunk.
#: Over-splitting drops claims silently. Fewer, whole claims beats more,
#: broken ones. A legal reference is the case to design against rather than
#: a decimal: `45 C.F.R. 164.312` is how every HIPAA citation is written, so
#: a rule that special-cased digit-dot-digit would leave the framework this
#: tool exists to handle still broken while looking fixed. `_segments` adds
#: one exception to this pattern, for an initial followed by a capital.
_BOUNDARY_RE = re.compile(r"""[.!?]+(?=\s+["'\[(A-Z]|\s*\Z)""")


def _ends_an_initial(text: str, stop: int) -> bool:
    """Whether the stop at `stop` closes a one-letter capital token.

    `U.S.`, `D.C.`, `J. Smith`: the letter before the stop is a capital and
    the character before *it* is not a letter, so the token is a single
    initial rather than a word. `CISO.` and `IAM.` are words and are not
    matched, so a sentence ending in an acronym still ends.
    """
    if stop == 0 or not (text[stop - 1].isalpha() and text[stop - 1].isupper()):
        return False
    return stop - 1 == 0 or not text[stop - 2].isalpha()


def _starts_with_a_capital(text: str, after: int) -> bool:
    """Whether what follows the stop opens with a capital letter, as opposed
    to a quote, a bracket, a `[n]` citation, or the end of the text."""
    rest = text[after:].lstrip()
    return bool(rest) and rest[0].isalpha() and rest[0].isupper()


def _segments(text: str) -> list[str]:
    """`text` cut at claim boundaries, each piece keeping its terminator."""
    pieces: list[str] = []
    start = 0
    for match in _BOUNDARY_RE.finditer(text):
        # "The U.S. Department of Health and Human Services enforces it":
        # `S.` followed by a space and a capital reads as a sentence start
        # by every other rule here, and that phrase is in essentially every
        # HIPAA document. An initial followed by a capital is not a
        # boundary. The cost is a sentence genuinely ending in a one-letter
        # token — "signed by J. The auditor then…" — which does not occur
        # in compliance prose, and which fails by merging rather than by
        # dropping, so it cannot lose a claim.
        if _ends_an_initial(text, match.start()) and _starts_with_a_capital(text, match.end()):
            continue
        pieces.append(text[start : match.end()])
        start = match.end()
    pieces.append(text[start:])
    return [piece for piece in (p.strip() for p in pieces) if piece]


@dataclass(frozen=True)
class Verdict:
    """One judgement, and why."""

    label: str
    #: Free text from the judge. Kept because "the passage names IT Asset
    #: Management, not the Security Officer" is what makes a finding
    #: actionable, and a bare label is not.
    reason: str = ""

    @property
    def supports(self) -> bool:
        return self.label == ENTAILED


@dataclass(frozen=True)
class Unsupported:
    """A sentence whose cited passages do not carry it."""

    sentence: str
    cited: list[int]
    verdict: Verdict

    def __str__(self) -> str:
        markers = "".join(f"[{n}]" for n in self.cited)
        detail = f" — {self.verdict.reason}" if self.verdict.reason else ""
        return f"{self.verdict.label} by {markers}: {self.sentence!r}{detail}"


@dataclass(frozen=True)
class Conflict:
    """A sentence whose own cited passages disagree with each other.

    Not an unsupported claim, and deliberately a separate type. One cited
    passage carries the sentence and another contradicts it, so the answer
    is supported — by a source that another source it also cites denies.
    Reporting that through `Unsupported` would state a falsehood about
    which document said what, and the reader's next question is *which two
    disagree*, which a claim-level finding cannot answer.

    This is arguably the most useful thing the entailment path can say. It
    is also the thing it said nothing about until now: `unsupported_claims`
    skips a sentence the moment anything supports it, so a supported-and-
    contradicted sentence produced no finding at all — and an absence reads
    as agreement.
    """

    sentence: str
    #: The passage that carries the sentence, and the one that denies it.
    #: Both, because "they disagree" without naming them sends a reader to
    #: read every cited passage to find out which.
    supported_by: int
    contradicted_by: int
    verdict: Verdict

    def __str__(self) -> str:
        detail = f" — {self.verdict.reason}" if self.verdict.reason else ""
        return (
            f"[{self.supported_by}] carries it and [{self.contradicted_by}] "
            f"contradicts it: {self.sentence!r}{detail}"
        )


class Entailer(ABC):
    """Judges whether a premise supports a hypothesis."""

    @abstractmethod
    def entails(self, premise: str, hypothesis: str) -> Verdict:
        raise NotImplementedError

    @abstractmethod
    def check(self) -> bool:
        """Cheap round-trip."""
        raise NotImplementedError


def cited_sentences(text: str, count: int) -> list[tuple[str, list[int]]]:
    """(sentence, cited passage numbers) for each sentence that cites one.

    The citation follows the full stop — "…destruction. [1]" — so a naive
    split puts the marker at the head of the *next* sentence and leaves the
    claim uncited. Judged that way the model is handed "[1]" and nothing
    else, and replies, reasonably, that no claim was supplied. The same
    trailing-citation shape bit `content/deontic.py`; it is a property of
    how this project's answers are written, not a quirk of one module.
    """
    pairs: list[tuple[str, list[int]]] = []
    for segment in _segments(text or ""):
        leading: list[int] = []
        while True:
            found = _CITATION_RE.match(segment)
            if not found:
                break
            leading.append(int(found.group(1)))
            # lstrip() with no argument covers every whitespace form,
            # which avoids spelling control characters in a literal.
            segment = segment[found.end() :].lstrip().lstrip(".;:|")

        if leading and pairs:
            sentence, cited = pairs[-1]
            pairs[-1] = (sentence, list(dict.fromkeys(cited + leading)))

        if not segment:
            continue
        inline = [int(n) for n in _CITATION_RE.findall(segment)]
        pairs.append((" ".join(_CITATION_RE.sub("", segment).split()), list(dict.fromkeys(inline))))

    return [
        (sentence, [n for n in cited if 1 <= n <= count])
        for sentence, cited in pairs
        if sentence and [n for n in cited if 1 <= n <= count]
    ]


def unsupported_claims(
    text: str,
    passages: list,
    entailer: Entailer,
    *,
    _judged: list[tuple[str, list[int], list[Verdict]]] | None = None,
) -> list[Unsupported]:
    """Cited sentences that their own passages do not support.

    Only sentences carrying a citation are judged. One with no citation is
    already `check_answer`'s business — it reports "makes claims without
    citing any passage" — and judging it here would duplicate a finding
    that is a fact with one that is an opinion.

    A sentence citing several passages is supported if *any* of them
    carries it, because an answer is allowed to draw one claim from two
    documents and citing both is the behaviour the prompt asks for.
    """
    judged = _judged if _judged is not None else judge_cited_sentences(text, passages, entailer)
    findings: list[Unsupported] = []
    for sentence, cited, verdicts in judged:
        if any(v.supports for v in verdicts):
            continue
        # Report the most specific complaint available: a contradiction is
        # a stronger finding than "says nothing about it", and burying it
        # behind a neutral verdict from a second passage would hide it.
        worst = next((v for v in verdicts if v.label == CONTRADICTED), verdicts[0])
        findings.append(Unsupported(sentence=sentence, cited=cited, verdict=worst))
    return findings


def judge_cited_sentences(
    text: str, passages: list, entailer: Entailer
) -> list[tuple[str, list[int], list[Verdict]]]:
    """Every cited sentence, its citations, and a verdict for each of them.

    **Eager on purpose, and the reason is a measurement rather than a
    preference.** Judging only until something supports the sentence would
    be cheaper and would make `conflicting_passages` below impossible to
    measure: a conflict would be found when the contradicting passage
    happened to be cited first and missed when it was cited second, so a
    conflict rate would track how the answering model orders its citations
    rather than how often sources disagree. The confound and the signal
    would be the same number, so no care at analysis time recovers it.

    One pass because both readers want the same verdicts. Asking the judge
    the same question twice to keep two call sites tidy would double the
    cost of the one check on this path that is opt-in precisely because it
    costs a model call per cited sentence.
    """
    return [
        (sentence, cited, [entailer.entails(passages[n - 1].chunk.text, sentence) for n in cited])
        for sentence, cited in cited_sentences(text, len(passages))
    ]


def conflicting_passages(
    text: str,
    passages: list,
    entailer: Entailer,
    *,
    _judged: list[tuple[str, list[int], list[Verdict]]] | None = None,
) -> list[Conflict]:
    """Sentences whose own cited passages disagree with each other.

    The sibling of `unsupported_claims`, and not a widening of it: a
    conflict is not an unsupported claim. The sentence *is* carried — by a
    passage the answer cites alongside another that denies it. Reporting it
    as unsupported would state a falsehood about which document said what.

    Nothing reported this before. `unsupported_claims` returns early the
    moment any passage supports the sentence, so a supported-and-
    contradicted sentence produced no finding — and it produced that
    absence in the direction that looks clean, since fewer findings read as
    the documents agreeing more than they do.

    The first supporting and first contradicting passage are named. Where
    several of each exist the rest are the same disagreement restated, and
    a reader who wants them has the citation markers.
    """
    judged = _judged if _judged is not None else judge_cited_sentences(text, passages, entailer)
    conflicts: list[Conflict] = []
    for sentence, cited, verdicts in judged:
        pairs = list(zip(cited, verdicts, strict=True))
        support = next((n for n, v in pairs if v.supports), None)
        against = next((n for n, v in pairs if v.label == CONTRADICTED), None)
        if support is None or against is None:
            continue
        conflicts.append(
            Conflict(
                sentence=sentence,
                supported_by=support,
                contradicted_by=against,
                verdict=next(v for n, v in pairs if n == against),
            )
        )
    return conflicts


def get_entailer(config: dict) -> Entailer | None:
    """Build the configured entailer, or None when there isn't one.

    None is the normal case. This is an addition, and one with a cost worth
    opting into deliberately rather than inheriting.
    """
    block = config.get("entail")
    if not block:
        return None

    provider = block.get("provider", "llm")
    if provider == "llm":
        from .llm_entailer import LLMEntailer, entailment_provider

        model = block.get("model")
        return LLMEntailer(
            model=model,
            max_tokens=block.get("max_tokens", 2000),
            # Left to LLMEntailer to refuse a missing model, with the reason
            # it has to be a different one from the answerer's.
            provider=entailment_provider(block, config) if model else None,
        )

    raise ValueError(f"Unknown entail.provider '{provider}'. Supported: llm.")
