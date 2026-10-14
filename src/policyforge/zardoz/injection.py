"""Passages that read as instructions to the answerer rather than as evidence.

Zardoz answers from text it retrieved out of a document corpus, and
`zardoz.supporting_space` deliberately admits pages nobody has declared
ownership of. That text goes into the same request as the rules governing
how it should be used. For a tool whose output an assessor may rely on, a
passage read as instruction rather than as evidence means somebody is told
something false about their own control posture — and told it in the voice
of their own policy set.

`answer.py` holds the structural half of the answer: every passage is
fenced with a per-request token no document can contain, so the model is
told unambiguously where quoted material starts and stops. This module is
the other half, and it is a *report on the corpus*, not a check on the
answer. The distinction matters. A warning at sync time arrives when
somebody can still go and look at the page; the same warning attached to an
answer arrives too late to act on and trains its reader to click past it.

**Why this is not an imperative detector.** The obvious approach — flag
commanding language — is useless on this corpus and would be actively
harmful. A security policy set is imperative from end to end: "Accounts
must be recertified quarterly", "Do not share credentials", "Revoke the
badge, then close the account". Those are the content. A detector firing on
them would report every document in the corpus, which is the same as
reporting nothing.

What separates an injected instruction from a requirement is not mood but
*audience*. A requirement addresses the organization's staff. An injection
addresses whoever is reading the prompt, and to do that it has to reach for
vocabulary a policy document has no reason to use: the prompt itself, the
rules, the assistant, the model, or this system's own control tokens. That
is what is matched here.

**This is a heuristic and is documented as one.** It catches the phrasings
somebody reaches for first, not every phrasing that could work; a patient
attacker writes around any word list, and a list long enough to stop one
would flag ordinary prose. It is worth having anyway, because the realistic
case is not a patient attacker — it is a runbook somebody pasted an
LLM conversation into, or a page written to steer a chatbot the team was
experimenting with last year. The fence in `answer.py` is the part that
holds; this is the part that tells you to go and look.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: The sentinel `answer.py` asks for when the passages do not answer the
#: question. Imported rather than repeated so the two cannot drift: a
#: document containing it verbatim can push an answer into a refusal, or be
#: read as one arriving from the corpus.
from .answer import REFUSAL_SENTINEL


@dataclass(frozen=True)
class Finding:
    """One stretch of text that addresses the reader rather than the reader's org."""

    #: Which rule matched, for grouping and for explaining the report.
    kind: str
    #: The matched text, trimmed. Shown so somebody can judge it rather than
    #: take this module's word for it — every one of these is a heuristic
    #: hit, and some will be innocent.
    excerpt: str
    #: 1-indexed line within the text that was scanned.
    line: int
    #: The frontmatter field the text came from (#329), or "" for the title
    #: and the body.
    field: str = ""

    def __str__(self) -> str:
        where = self.field or f"line {self.line}"
        return f"{where}: {self.kind} — {self.excerpt!r}"


#: Trimmed to a length that shows the phrasing without reprinting a page.
_EXCERPT_CHARS = 90

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        # The classic. Reaching past the current turn to cancel what came
        # before it is something no policy document has any reason to do.
        "countermands earlier instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass|skip)\b[^.!?\n]{0,40}?"
            r"\b(?:instruction|instructions|rule|rules|prompt|prompts|directive|"
            r"directives|guideline|guidelines|constraint|constraints|"
            r"everything above|all of the above|the above|prior|previous|preceding)\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Naming the machinery. A document describing its reader's system
        # prompt is describing something outside its own subject matter.
        #
        # Second person is load-bearing here. "The rules governing privileged
        # access", "follow the instructions in the runbook" and "the
        # guidelines below" are ordinary policy prose, and an earlier version
        # of this pattern that accepted a bare `the` reported all three.
        #
        # So is the choice of nouns. `your training` and `your configuration`
        # were in this list until a sweep found them: annual security
        # awareness training and a device configuration baseline are two of
        # the most common things a policy set addresses staff about, and
        # second person is exactly how a runbook addresses them. What is left
        # are the nouns that name the machinery and have no policy meaning
        # pointed at a reader.
        "refers to the prompt or the model's own rules",
        re.compile(
            r"\b(?:your\s+(?:system\s+)?(?:prompt|instructions|directives|persona)"
            r"|(?:the|this|its)\s+system\s+prompt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Role reassignment.
        "reassigns the reader's role",
        re.compile(
            r"\b(?:you\s+are\s+(?:now|actually|really)|from\s+now\s+on|"
            r"act\s+as\s+(?:a|an|the)\b|pretend\s+(?:to\s+be|that\s+you|you)|"
            r"roleplay|role-play|behave\s+as\s+(?:a|an|if))\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Dictating the output. Required to be sentence-initial or to follow
        # an explicit address, so that "the Standard does not say that
        # reviews are annual" — ordinary prose — is not a hit.
        "dictates what the reader should output",
        re.compile(
            r"(?:^|(?<=[.!?]\s)|(?<=[.!?]\n)|\bplease\s+|\byou\s+(?:must|should|will)\s+)"
            r"(?:reply|respond|answer|output|print|return|state|say|write|report)\s+"
            r"(?:with|only|exactly|that|the\s+following|as\s+follows)\b",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        # Addressing the reader as software. A policy speaks to people — and
        # it may well *mention* an AI vendor, an assistant product or a
        # chatbot the company runs, so naming one is not the signal. Being
        # told what to be, or addressed in the second person as one, is.
        "addresses the reader as a model or assistant",
        re.compile(
            r"\b(?:you\s+are\s+(?:an?\s+)?(?:helpful\s+|honest\s+)*"
            r"(?:ai|a\.i\.|language\s+model|llm|chatbot|chat\s+bot|assistant|copilot)"
            r"|as\s+an?\s+(?:ai|language\s+model|llm|chatbot|assistant)\s*,\s*you"
            r"|dear\s+(?:ai|assistant|model|chatbot))\b",
            re.IGNORECASE,
        ),
    ),
    (
        # Directing the model as reader (#369). An injection that names the
        # reader as a model or assistant in third person or in an editor's note
        # and directs what it says or does. Both conditions together, so text
        # that only mentions AI ("paste nothing into an AI assistant", "the
        # model risk committee") is not flagged. A policy sentence that does
        # both ("The AI must not answer questions about salaries") IS flagged:
        # it reads to the model as an instruction, and is reported, not
        # blocked (1d measured 7 of 12 such sentences on #369's PR; 0 of 138
        # and 1 of 303 findings on two real corpora).
        "directs the model or assistant as reader",
        re.compile(
            r"\b(?:"
            r"(?:to\s+)?(?:the\s+|an?\s+)?(?:ai\s+assistant|language\s+model|llm|chatbot)"
            r"|to\s+the\s+(?:assistant|ai|model)"
            r"|the\s+(?:assistant|ai|model)"
            r"|(?:to\s+)?zardoz"
            r")\b"
            r"(?:"
            r"\s*[:]\s*(?:(?:please|now|always)\s+)?"
            r"\b(?:should|must|disregard|say|print|reply|answer|respond|tell)\b"
            r"|"
            r"\s+(?:"
            r"(?:should|must)\s+(?:\w+\s+){0,2}?"
            r"(?:disregard|say|print|reply|answer|respond|tell)"
            r"|(?:disregard|say|print|reply|answer|respond|tell)"
            r")\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        # Suppressing the guarantees. Citations and quotation fidelity are
        # what make an answer checkable; text asking for them to be dropped
        # is asking for the checks to stop.
        #
        # At most two words between the negation and its object, rather than
        # a span of characters. A character bridge crossed clause boundaries
        # and stitched unrelated halves together: "never supplied, no
        # citations" and "stop matching the catalog they cite" were both
        # reported, and neither asks anybody to do anything.
        # Imperative or second person, like the output rule below. "Staff
        # should never cite internal ticket numbers in public postmortems" is
        # a real policy sentence and was reported until this boundary went
        # in; what distinguishes it is not the words but that it has a
        # third-person subject. An injection has to address the reader.
        "asks for citations or grounding to be dropped",
        re.compile(
            r"(?:^|(?<=[.!?:;]\s)|(?<=\n)|\byou\s+|\bplease\s+)"
            r"(?:do\s+not|don't|never|no\s+need\s+to|stop)\s+(?:\w+\s+){0,2}"
            r"(?:cite|citing|citation|citations|quote|quoting)\b",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        # Speaking for the operator. Added on measurement: on the edit path,
        # a line labelled "Revised operator instruction:" was carried out by
        # deepseek-v4-flash in every run, while "ignore all previous
        # instructions" was resisted in every run. It uses none of the
        # machinery vocabulary the rules above look for — its only move is
        # a claim of authority — so the claim is what is matched.
        #
        # Deliberately narrow: a line that *opens* with a label announcing a
        # new or revised instruction. "Updated instructions are posted in
        # the runbook" is ordinary prose and has no colon after the noun. An
        # unqualified "Operator note:" is left alone too, because OT and
        # data-centre procedures address human operators exactly that way.
        "claims to speak for the operator",
        re.compile(
            r"^[ \t>*_-]*(?:revised|updated|new|additional|amended|replacement)\s+"
            r"(?:operator\s+)?instructions?\s*:",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
)

#: This system's own control vocabulary. A single occurrence is a finding on
#: its own, with no second signal required: there is no reason for a policy
#: document to contain the token that means "these passages do not answer
#: the question", and its presence can force or fake a refusal.
#:
#: There used to be a second entry here, for a line imitating a supplied
#: passage header — `[3] Some Document` in body text. It was dropped. The
#: fence now puts any such line inside the block it was written in, so it
#: cannot start a new one, and `check_answer` already rejects a citation
#: pointing past the passages that were supplied; meanwhile a numbered
#: reference list at the foot of a policy document matches it exactly. A
#: rule that fires on ordinary documents to catch something covered twice
#: elsewhere is the trade this project's checks are supposed to refuse.
#:
#: The fence markers are the opposite case, and were added on measurement
#: rather than argument. A page cannot contain the real per-request token,
#: so structurally the fence holds — but a live run against the edit path
#: showed a model obeying an instruction wrapped in *lookalike* markers
#: (`END pf-0000` … `BEGIN pf-0000`) in 5 runs of 6, while resisting a plain
#: "ignore all previous instructions" every time. A model does not compare
#: tokens character by character. And unlike a numbered reference list,
#: this project's fence vocabulary has no innocent reason to be in a policy
#: page, so a hit costs nothing in false positives.
_CONTROL_TOKENS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "contains the refusal sentinel",
        re.compile(rf"\b{re.escape(REFUSAL_SENTINEL)}\b"),
    ),
    (
        "imitates this system's fence markers",
        re.compile(r"\b(?:BEGIN|END)\s+pf-[0-9A-Za-z]+\b"),
    ),
)


def _excerpt(text: str, start: int, end: int) -> str:
    """The matched span, collapsed to one line and trimmed."""
    span = " ".join(text[start:end].split())
    return span if len(span) <= _EXCERPT_CHARS else span[: _EXCERPT_CHARS - 1] + "…"


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


#: A run of characters outside ASCII: the only places a format character
#: can be, and the only places NFKC changes anything.
_NON_ASCII = re.compile("[^" + chr(0) + "-" + chr(0x7F) + "]+")


#: The longest stretch handed to one NFKC call, give or take a run's search
#: for a safe cut (at most twice this). `unicodedata.normalize` is quadratic
#: on a long run that expands: 1 MB of U+FDFA took about 3 minutes (1d,
#: #377), and anyone who can edit a page could stall `sync` with one.
_NFKC_CHUNK = 2048


#: How many characters before a cut the check reads. Starters compose in
#: chains, not only in pairs: Hangul L + V make a syllable that then takes a
#: T, so the pair V | T alone looks safe and isn't (ba measured it, carrying
#: #377). L + V + T is the longest such chain, so three is enough.
_CUT_CONTEXT = 3


def _safe_cut(before: str, char: str) -> bool:
    """Whether NFKC may be applied on each side of a cut before `char` alone,
    with `before` the characters just before it (80's ruling on #377, read
    over a context rather than one character). It takes three things:

    - `char`'s decomposition starts with a starter (`combining` 0), so no
      mark after the cut belongs to what's before it. That covers `char`
      being a mark itself, and U+0F73, which has class 0 but decomposes into
      marks that could reorder past what's before it into text the check
      never sees (ba, carrying #377). A separate test of `char`'s own class
      was subsumed: an arm dropping it changed no result;
    - NFKC of `before` + `char` is NFKC of each, which rejects anything that
      composes across the cut (Hangul L V | T, `e` + a combining form). With
      a starter first on the right, nothing reaches further back than the
      chain the context holds.
    """
    lead = unicodedata.normalize("NFKD", char)[:1]
    if lead and unicodedata.combining(lead):
        return False
    whole = unicodedata.normalize("NFKC", before + char)
    return whole == unicodedata.normalize("NFKC", before) + unicodedata.normalize("NFKC", char)


def _nfkc_pieces(given: str) -> list[tuple[str, bool]]:
    """`given` cut into pieces NFKC can take one at a time, each paired with
    whether to normalise it. A run with no safe cut within twice the bound
    is handed back whole and un-normalised from there on, and `scan` reports
    it (the ruling's loud fallback): never silently, never in one huge call."""
    pieces: list[tuple[str, bool]] = []
    start = 0
    while len(given) - start > _NFKC_CHUNK:
        window = range(start + _NFKC_CHUNK, min(len(given), start + 2 * _NFKC_CHUNK))
        cut = next((i for i in window if _safe_cut(given[i - _CUT_CONTEXT : i], given[i])), None)
        if cut is None:
            pieces.append((given[start:], False))
            return pieces
        pieces.append((given[start:cut], True))
        start = cut
    pieces.append((given[start:], True))
    return pieces


def _normalise(text: str) -> tuple[str, list[int], list[tuple[int, int]]]:
    """Remove Unicode Cf (format) characters, then apply NFKC (#368).

    Returns (normalised_text, norm_to_orig, too_long) where norm_to_orig[i]
    is the index in `text` corresponding to index i in `normalised_text`, and
    too_long lists the (start, end) of each stretch in `text` left
    un-normalised because no safe cut was found (#377).
    """
    if text.isascii():  # no format characters, and nothing NFKC changes
        return text, list(range(len(text) + 1)), []

    # Format characters are all outside ASCII, so only the non-ASCII runs
    # are looked at, one character at a time; ASCII is copied in bulk.
    pieces: list[str] = []
    step1_to_orig: list[int] = []
    pos = 0
    for run in _NON_ASCII.finditer(text):
        pieces.append(text[pos : run.start()])
        step1_to_orig.extend(range(pos, run.start()))
        for j in range(run.start(), run.end()):
            if unicodedata.category(text[j]) != "Cf":
                pieces.append(text[j])
                step1_to_orig.append(j)
        pos = run.end()
    pieces.append(text[pos:])
    step1_to_orig.extend(range(pos, len(text)))
    step1_to_orig.append(len(text))
    step1_text = "".join(pieces)

    # Linear, segment by segment (9b on #368's PR: a `difflib` alignment of
    # the whole page was quadratic on varied prose, 12.6 s for 260 KB with one
    # non-breaking space). NFKC decomposes each character alone, and no
    # composition has an ASCII character as its second element, so nothing
    # reaches across the start of an ASCII character. Each run of non-ASCII
    # characters is normalised with the one ASCII character before it, which
    # can compose with it (`e` + U+0301); everything else is ASCII, which NFKC
    # leaves alone, and maps one to one. Inside a segment, an ASCII character
    # that composed with nothing maps to itself and the rest of the output
    # maps proportionally into the run, so no output character after a
    # newline maps onto the newline (a property test holds the line count).
    out: list[str] = []
    norm_to_step1: list[int] = []
    too_long: list[tuple[int, int]] = []
    pos = 0
    for run in _NON_ASCII.finditer(step1_text):
        # The ASCII character before the run, if any, goes with it.
        start = run.start() - 1 if run.start() > pos else run.start()
        out.append(step1_text[pos:start])
        norm_to_step1.extend(range(pos, start))
        offset = start
        # A long run goes to NFKC a piece at a time, cut only where nothing
        # composes across the cut (#377); a short one is one piece.
        for piece, normalise in _nfkc_pieces(step1_text[start : run.end()]):
            segment = unicodedata.normalize("NFKC", piece) if normalise else piece
            if not normalise:
                # From the run itself, not the ASCII character leading it: a
                # newline there would put the report a line early.
                first = offset + 1 if offset == start < run.start() else offset
                too_long.append((first, offset + len(piece)))
            out.append(segment)
            if offset == start < run.start() and segment[:1] == piece[0]:
                # The ASCII character came through unchanged, so it composed
                # with nothing: it maps to itself, and only the rest maps into
                # the run. Mapped proportionally over the whole segment
                # instead, an expanding run after a newline put its first
                # characters back on the newline, one line early (1d on
                # #368's PR).
                norm_to_step1.append(offset)
                rest, rest_start = len(segment) - 1, offset + 1
            else:
                rest, rest_start = len(segment), offset
            width = offset + len(piece) - rest_start
            norm_to_step1.extend(rest_start + k * width // rest for k in range(rest))
            offset += len(piece)
        pos = run.end()
    out.append(step1_text[pos:])
    norm_to_step1.extend(range(pos, len(step1_text)))
    norm_to_step1.append(len(step1_text))
    norm_text = "".join(out)

    norm_to_orig = [step1_to_orig[idx] for idx in norm_to_step1]
    spans = [(step1_to_orig[a], step1_to_orig[b]) for a, b in too_long]
    return norm_text, norm_to_orig, spans


def scan(text: str) -> list[Finding]:
    """Every stretch of `text` that addresses its reader rather than its org.

    Findings are returned in the order they appear, deduplicated by the
    exact span so that two rules matching the same words report once.
    """
    findings: list[Finding] = []
    seen: set[tuple[int, int]] = set()

    norm_text, norm_to_orig, too_long = _normalise(text)

    # Loud, not silent (80's ruling on #377): a stretch left un-normalised
    # is a finding of its own, named and counted, since a split instruction
    # inside it would not be read the way a model reads it.
    for start, end in too_long:
        findings.append(
            Finding(
                kind=f"a run of {end - start} characters too long to normalise safely",
                excerpt=_excerpt(text, start, end),
                line=_line_of(text, start),
            )
        )

    for kind, pattern in (*_RULES, *_CONTROL_TOKENS):
        for match in pattern.finditer(norm_text):
            orig_start = norm_to_orig[match.start()]
            orig_end = norm_to_orig[match.end()]
            span = (orig_start, orig_end)
            if span in seen:
                continue
            seen.add(span)
            findings.append(
                Finding(
                    kind=kind,
                    excerpt=_excerpt(text, *span),
                    line=_line_of(text, orig_start),
                )
            )

    return sorted(findings, key=lambda f: (f.line, f.excerpt))


def scan_document(title: str, body: str, fields=()) -> list[Finding]:
    """Scan a document's title and its frontmatter as well as its body.

    The title is written by whoever wrote the page and carries the same
    trust as its text, so a check that read only the body would miss the
    one line a reader is most likely to skim past. Reported at line 0, so
    it sorts above the body and does not claim a line the body has.

    `fields` is `(name, value)` pairs, every frontmatter string the answer
    can show (#329): `owner` is printed inside the answer fence, so a line
    planted there reached the model unscanned. Each finding is named by its
    field, at line 0. The same scanner, so what counts as a finding is
    unchanged.
    """
    findings = [Finding(f.kind, f.excerpt, 0) for f in scan(title)]
    for name, value in fields:
        findings += [Finding(f.kind, f.excerpt, 0, name) for f in scan(value)]
    return findings + scan(body)
