"""Repair a Standard's Playbook sentences, or refuse to hand it back (#301).

A Standard for an AI topic carries one "NIST suggests ..." sentence per Core
subcategory its topic anchors, citing the Playbook actions it summarises.
Measured on production models (#301), a first draft can fail that in two
ways a reader cannot see without the check: a sentence the Playbook gate
refuses (sonnet paraphrased NIST's "will need to be ... decommissioned" as
"must be decommissioned"), and a subcategory with no sentence at all (glm
left out Map 5.1). **80's ruling: regenerate what fails, with bounded
retries, and if it still fails, refuse loudly rather than write a document
that `policyforge check` will reject.**

**Generic over the gate.** A problem is whatever the gate reports --
`deontic.playbook_obligations` and `deontic.playbook_tagged_headings` -- plus
a subcategory with no sentence or the wrong action count. Nothing here
re-implements a gate rule, so a rule added to the gate (#337, #340, #341)
becomes something this repairs without a change here.

**What it changes, and what it cannot.** A failing sentence is replaced by
line, and only when every Playbook citation on that line is for the one
subcategory being repaired; a line mixing subcategories is left and
reported. A missing sentence is appended under one closing section, not
placed beside related text. A Playbook tag on a heading is removed from the
heading without a model call, since the rule is simply that headings carry
none. Every other line of the draft is returned byte-for-byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Tries per failing subcategory, the first included. Bounded so a model
#: that cannot write the sentence costs a known number of calls.
REPAIR_ATTEMPTS = 3
#: One sentence, never a document. Room for a reasoning model to think
#: before answering; a reply that needs more is not a sentence.
REPAIR_TOKENS = 2048
#: Where appended sentences go, when a subcategory had none.
MISSING_SECTION = "## NIST AI RMF Playbook suggestions"

_PLAYBOOK_PART = re.compile(r"NIST AI RMF Playbook ([A-Za-z]+ \d+\.\d+) Action (\d+)")
_AMONG = re.compile(r"among its (\d+) actions? for ([A-Za-z]+ \d+\.\d+)")
_TAG = re.compile(r"\[[^\[\]]*\]")


class PlaybookRepairFailed(ValueError):
    """The Standard still fails its Playbook check after bounded repair."""

    def __init__(self, problems: list[PlaybookProblem]):
        self.problems = problems
        lines = "\n".join(f"  - {p.describe()}" for p in problems)
        super().__init__(
            f"Refusing to write this Standard: {len(problems)} Playbook problem(s) remain "
            f"after {REPAIR_ATTEMPTS} attempt(s) each.\n{lines}\n"
            "  `policyforge check` would reject it. Regenerate, or draft with a model "
            "that writes these sentences as NIST's suggestions."
        )


@dataclass(frozen=True)
class PlaybookProblem:
    kind: str  # "obligation", "heading", "missing", "count", "lost"
    subcategory: str
    line: int | None = None
    text: str = ""

    def describe(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        return {
            "obligation": f"{where}{self.subcategory}: not framed as NIST's -- {self.text[:90]}",
            "heading": f"{where}a heading carries a Playbook tag",
            "missing": f"{self.subcategory}: no Playbook sentence",
            "count": f"{where}{self.subcategory}: {self.text}",
            "lost": f"{where}the repair would remove a cited obligation -- {self.text[:90]}",
        }[self.kind]


def _subcategories_cited(parts: list[str]) -> set[str]:
    # Every match, not the first: a merged tag, or a whole line, can name
    # several subcategories, and reading only the first made a line mixing
    # two look safe to replace (caught by the mixed-line test).
    return {m.group(1) for part in parts for m in _PLAYBOOK_PART.finditer(part)}


def playbook_problems(
    document: str, entries: list[dict], org_actors: tuple[str, ...] = ()
) -> list[PlaybookProblem]:
    """Everything wrong with `document`'s Playbook sentences, for `entries`
    (the `TopicContext.playbook` block it was drafted from)."""
    from policyforge.content import deontic

    problems: list[PlaybookProblem] = []
    for line, text in deontic.playbook_tagged_headings(document):
        problems.append(PlaybookProblem("heading", "", line, text))
    for statement in deontic.playbook_obligations(document, org_actors):
        subs = _subcategories_cited(list(statement.citations)) or {""}
        for sub in sorted(subs):
            problems.append(PlaybookProblem("obligation", sub, statement.line, statement.text))

    expected = {e["subcategory"]: len(e["actions"]) for e in entries}
    seen: dict[str, int] = {}
    for statement in deontic.analyze(document):
        if not statement.cites_the_playbook:
            continue
        for sub in _subcategories_cited(list(statement.citations)):
            seen.setdefault(sub, statement.line)
        for count, sub in _AMONG.findall(statement.text):
            if sub in expected and int(count) != expected[sub]:
                problems.append(
                    PlaybookProblem(
                        "count",
                        sub,
                        statement.line,
                        f"says {count} actions, NIST lists {expected[sub]}",
                    )
                )
    for sub in expected:
        if sub not in seen:
            problems.append(PlaybookProblem("missing", sub))
    return problems


def _entry(entries: list[dict], sub: str) -> dict | None:
    return next((e for e in entries if e["subcategory"] == sub), None)


def _acceptable(candidate: str, entry: dict, org_actors: tuple[str, ...]) -> bool:
    """The candidate is one line, passes the gate, cites only this
    subcategory's real actions, and states NIST's count."""
    from policyforge.content import deontic

    if not candidate or "\n" in candidate.strip():
        return False
    text = candidate.strip() + "\n"
    if deontic.playbook_obligations(text, org_actors) or deontic.playbook_tagged_headings(text):
        return False
    ids = {a["id"] for a in entry["actions"]}
    cited = {f"{m.group(1)} Action {m.group(2)}" for m in _PLAYBOOK_PART.finditer(text)}
    if not cited or not cited <= ids:
        return False
    return any(
        sub == entry["subcategory"] and int(n) == len(entry["actions"])
        for n, sub in _AMONG.findall(text)
    )


def _draft_sentence(provider, entry: dict, failed: str) -> str:
    from policyforge.generate.policy_writer import (
        PLAYBOOK_MERGED_TAG_EXAMPLE,
        PLAYBOOK_SENTENCE_FORM,
        TopicContext,
        _render_playbook,
    )
    from policyforge.llm import effort

    block = _render_playbook(TopicContext(playbook=[entry]))
    system = (
        "Write exactly ONE sentence, on one line, and nothing else: no heading, "
        "no list marker, no commentary. It presents NIST's voluntary suggestions "
        f'in this form: "{PLAYBOOK_SENTENCE_FORM}", with N the number of actions '
        "given. NIST is the subject. Restate only what the cited actions say, in "
        'NIST\'s words where you can; never "must", "shall", "will", "is required '
        'to" or any obligation, and never the organization as the one acting. '
        "Cite every action you draw on with its tag; several may share one tag, "
        f"separated by |, but every part names the framework in full: "
        f"{PLAYBOOK_MERGED_TAG_EXAMPLE}."
    )
    prompt = f"{block}\n\n" + (
        f"This sentence was refused, so do not repeat its wording:\n{failed}\n\n" if failed else ""
    )
    prompt += "Write the one sentence."
    response = effort.call(
        provider, system=system, prompt=prompt, temperature=0.2, max_tokens=REPAIR_TOKENS
    )
    return (getattr(response, "text", "") or "").strip()


#: A line's indentation and list marker (`- `, `* `, `1. `), kept on repair.
_MARKER_RE = re.compile(r"^[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?")


def _words(text: str) -> str:
    """Text as compared for "is this the whole line": no citation tags, no
    markdown emphasis or backticks, no list marker, whitespace collapsed."""
    text = _TAG.sub(" ", text)
    text = re.sub(r"\*\*|__|\*|`", "", text)
    return " ".join(_MARKER_RE.sub("", text.strip()).split())


def _is_whole_line(document: str, index: int) -> bool:
    """Whether the failing statement on line `index` is ALL of that line.

    **Replacing a line replaces everything on it** (1d on #359): with a
    Playbook sentence and "The owner must review access quarterly [NIST
    800-53 AC-2]." on one line, a line-level repair deleted the AC-2
    requirement and returned success. So a line is replaced only when exactly
    one statement starts on it and that statement's words are the line's
    words. Anything else is left as written and reported, as a line mixing
    subcategories already is.
    """
    from policyforge.content import deontic

    starting = [s for s in deontic.analyze(document) if s.line == index + 1]
    if len(starting) != 1:
        return False
    line = document.split("\n")[index]
    return _words(starting[0].text) == _words(line)


def _lost_obligations(before: str, after: str, *, flagged: set[str]) -> list[PlaybookProblem]:
    """**Conservation** (80's ruling on #359): every cited obligation in the
    draft that the gate did not flag is still in the repaired Standard.

    A repair may replace only the failing sentence's own text. The isolation
    check in `repair_standard` is meant to guarantee that; this checks the
    result, so a repair path added later cannot delete a requirement and
    report success either. The unit is a statement's words (citations, list
    markers and emphasis set aside), read by `analyze` on both sides.

    **The exemption is the flagged STATEMENT, by its words, never its line.**
    Keyed by line, it exempted exactly 1d's case: the 800-53 obligation
    sharing the flagged sentence's line went unchecked (caught by the
    conservation test with isolation switched off).
    """
    from policyforge.content import deontic

    kept = {_words(s.text) for s in deontic.analyze(after)}
    return [
        PlaybookProblem("lost", "", s.line, s.text)
        for s in deontic.analyze(before)
        if s.cited and s.binds and _words(s.text) not in flagged and _words(s.text) not in kept
    ]


def _only_citations(line: str) -> bool:
    return bool(line.strip()) and not _TAG.sub("", line).strip(" \t.;:|")


def _strip_playbook_tags(heading: str) -> str:
    def keep(match: re.Match) -> str:
        parts = [p.strip() for p in match.group(0)[1:-1].split("|")]
        kept = [p for p in parts if not p.startswith("NIST AI RMF Playbook")]
        return f"[{' | '.join(kept)}]" if kept else ""

    return re.sub(r"\s{2,}", " ", _TAG.sub(keep, heading)).rstrip()


def repair_standard(
    document: str, entries: list[dict], provider, org_actors: tuple[str, ...] = ()
) -> str:
    """`document` with its Playbook problems repaired, or `PlaybookRepairFailed`."""
    problems = playbook_problems(document, entries, org_actors)
    if not problems:
        return document

    lines = document.split("\n")
    appended: list[str] = []
    unrepaired: list[PlaybookProblem] = []

    for problem in [p for p in problems if p.kind == "heading"]:
        lines[problem.line - 1] = _strip_playbook_tags(lines[problem.line - 1])

    targets: dict[str, PlaybookProblem] = {}
    for problem in problems:
        if problem.kind != "heading":
            targets.setdefault(problem.subcategory, problem)

    for sub, problem in targets.items():
        entry = _entry(entries, sub)
        if entry is None:
            unrepaired.append(problem)
            continue
        index = problem.line - 1 if problem.line else None
        if index is not None and _subcategories_cited([lines[index]]) - {sub}:
            unrepaired.append(problem)  # the line mixes subcategories
            continue
        if index is not None and not _is_whole_line(document, index):
            unrepaired.append(problem)  # the line carries another sentence
            continue
        failed = lines[index].strip() if index is not None else ""
        sentence = ""
        for _ in range(REPAIR_ATTEMPTS):
            candidate = _draft_sentence(provider, entry, failed)
            if _acceptable(candidate, entry, org_actors):
                sentence = candidate
                break
            failed = candidate or failed
        if not sentence:
            unrepaired.append(problem)
            continue
        if index is None:
            appended.append(sentence)
        else:
            # The line's own indentation and list marker are kept: a list
            # item stays a list item (1d on #359).
            marker = _MARKER_RE.match(lines[index])
            lines[index] = (marker.group(0) if marker else "") + sentence
            if index + 1 < len(lines) and _only_citations(lines[index + 1]):
                lines[index + 1] = ""

    if appended:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", MISSING_SECTION]
        for sentence in appended:
            lines += ["", sentence]
        lines.append("")
    repaired = "\n".join(lines)

    remaining = playbook_problems(repaired, entries, org_actors)
    if remaining or unrepaired:
        raise PlaybookRepairFailed(remaining or unrepaired)
    flagged = {_words(p.text) for p in problems if p.kind == "obligation"}
    lost = _lost_obligations(document, repaired, flagged=flagged)
    if lost:
        raise PlaybookRepairFailed(lost)
    return repaired
