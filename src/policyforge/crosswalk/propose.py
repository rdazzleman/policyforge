"""A model's reading of each requirement against 800-53, for a person to review.

This does not decide anything. It reads one requirement and a short list of
candidate controls (`candidates.py`) and says which of them address it, how,
and with which words of each text. Those become notes on the organization's
overlay: a published pair the model could quote a basis for gains the quote;
one it could not is flagged; a pair nobody published is added as proposed.
Only a person moves a row's status.

**Why it cannot decide.** Measured on the 75 HIPAA requirements before this
was built (see MEASUREMENTS.md): two models confirmed 31% and 41% of NIST's
published pairs while being shown every one of them, and agreed with each
other on 58% of what they asserted. Reading the disagreements, the models
were not simply wrong. NIST's mapping links a requirement to controls that
*support* it — IR-5 and IR-6 for reviewing system activity, AC-4 for access
authorization — where the models map controls that *carry the obligation*.
Those are different questions, and which one an organization wants answered
is its decision. The value of this step is that it puts the difference, with
the words that justify each side, in front of the person making it.

**The model is not told which pairs were published.** Its candidates are
sorted by id with the published ones among them. Told which ones NIST chose,
agreeing with NIST is the easy answer, and a confirmation would say nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from policyforge.crosswalk.candidates import CatalogEntry, WordIndex, candidates_for
from policyforge.crosswalk.grounding import grounded, is_whole_text
from policyforge.crosswalk.overlay import (
    ACCEPTED,
    PROPOSED,
    REJECTED,
    MappingRow,
    Overlay,
    printable,
    published_pairs,
)
from policyforge.llm.base import SchemaReplyError, TruncatedResponse
from policyforge.llm.prompts import REGISTRY, Prompt, register

#: The relationships a model may assert. `unspecified` is for published pairs
#: whose relationship nobody stated, never an answer.
ASSERTABLE = ("equal", "subset", "superset", "intersects")

#: On a published pair the model was shown and gave no quotable basis for.
#: Cleared and recomputed on every run for rows nobody has reviewed.
NOT_CONFIRMED = "not-confirmed-by-model"

#: On an accepted row whose recorded relationship differs from the one the
#: model suggested. Coverage reads `relationship`, so the suggestion waits in
#: `proposed_relationship` until a person promotes it.
RELATIONSHIP_PROPOSED = "relationship-proposed-by-model"

#: Flags this module sets, cleared and recomputed on every run for a row
#: nobody has reviewed.
MODEL_FLAGS = (NOT_CONFIRMED, RELATIONSHIP_PROPOSED)

PROPOSE_PROMPT = register(
    Prompt(
        name="crosswalk.propose",
        version=1,
        text="""You map one requirement from a compliance framework to the \
NIST SP 800-53 Rev. 5 controls that address it.

You are given the REQUIREMENT and a list of CANDIDATE controls. Most \
candidates are not related; they were found by word overlap. Return only the \
candidates that genuinely address the requirement, each with:

- relationship, read from the requirement's side:
  - equal: the two require the same thing.
  - subset: the requirement is narrower; the control fully covers it.
  - superset: the requirement is broader; the control covers only part.
  - intersects: they overlap, and each requires something the other does not.
- requirement_quote: words copied exactly from the requirement text that the
  control addresses.
- control_quote: words copied exactly from the control text that address them.

Rules:
1. Judge the texts, not what you remember about published crosswalks. A shared
   word ("audit", "access", "review") is not a relationship; the obligation has
   to be the same kind of obligation.
2. Quotes must be copied from the text given, at least four words each where
   the text is that long. A mapping you cannot quote is a mapping you should
   not make.
3. Returning no candidates is correct when none address the requirement, and
   some requirements (definitions, compliance dates) are not controls at all.
4. A requirement listed under a parent standard is part of that standard, and
   is read in its context: a specification under a training standard is about
   what the workforce is trained on.
5. A control ending in -1 (AC-1, PS-1) is that family's policy and procedures
   control. It addresses a requirement to have policies or procedures for that
   family's subject.
6. Reply with the JSON object only, including when the list is empty.""",
    )
)


@dataclass
class Requirement:
    requirement_id: str
    title: str
    text: str
    #: The standard an implementation specification sits under, as text, or "".
    parent: str = ""


@dataclass
class ProposedMapping:
    control: str
    relationship: str
    requirement_quote: str
    control_quote: str


@dataclass
class Proposal:
    requirement_id: str
    candidates: list[str]
    mappings: list[ProposedMapping] = field(default_factory=list)
    #: Returned by the model and refused: an id not on the list, a relationship
    #: not in the vocabulary, or a quote the text does not contain.
    refused: list[dict] = field(default_factory=list)
    error: str = ""
    #: The model's reply was cut off at its budget twice, so this requirement
    #: has no proposal rather than a partial one. Reported apart from other
    #: failures: it is the one a larger budget would fix.
    truncated: bool = False
    #: Rows that named no control on the candidate list — usually no `control`
    #: key at all, which the schema requires and a provider may not enforce.
    nameless: int = 0


def requirements_of(controls, framework: str, vacated: dict | None = None) -> list[Requirement]:
    """Each requirement of `framework`, as a model is asked about it.

    Text a court vacated is not asked about (#409): a catalog whose manifest
    marks paragraphs `vacated:` is read through `vacated.for_generation`, as
    synthesis reads it, so no mapping is proposed for text that binds nobody,
    and a revised paragraph is proposed from the wording that binds.
    `vacated` defaults to the manifests on disk.
    """
    from policyforge.mapping.crosswalk import normalize_framework

    if vacated is None:
        from policyforge.frameworks.registry import config_or_defaults
        from policyforge.frameworks.vacated import declared_vacated

        vacated = declared_vacated(config_or_defaults("vacated paragraphs were read"))
    wanted = framework.casefold()
    found = []
    for control in controls:
        if control.framework.casefold() != wanted:
            continue
        status = vacated.get(normalize_framework(control.framework))
        if status is not None:
            from policyforge.frameworks.vacated import for_generation

            control = for_generation(control, status)
            if control is None:
                continue
        found.append(
            Requirement(control.control_id, control.title, control.control_statement or "")
        )
        parent = f"{control.control_id} — {control.title}: {control.control_statement or ''}"
        for enhancement in control.enhancements:
            found.append(
                Requirement(
                    enhancement.enhancement_id,
                    enhancement.title,
                    enhancement.description or "",
                    parent=parent,
                )
            )
    return found


def _schema(candidates: list[str]) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "mappings",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "mappings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "control": {"type": "string", "enum": candidates or ["none"]},
                                "relationship": {"type": "string", "enum": list(ASSERTABLE)},
                                "requirement_quote": {"type": "string"},
                                "control_quote": {"type": "string"},
                            },
                            "required": [
                                "control",
                                "relationship",
                                "requirement_quote",
                                "control_quote",
                            ],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["mappings"],
                "additionalProperties": False,
            },
        },
    }


def _named_nothing(rows, candidates) -> bool:
    """Whether a non-empty reply named no candidate control anywhere.

    An empty list is a real answer — the model was asked to return nothing
    when nothing addresses the requirement — so only a reply with rows in it
    can be unusable this way.
    """
    return bool(rows) and not any(
        isinstance(row, dict) and row.get("control") in candidates for row in rows
    )


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_reply(text: str) -> list:
    """The rows in a reply, from the schema's object or a common near miss.

    Measured on glm-5.3-flash: 13 of 75 replies held their rows in the wrong
    wrapper — 9 inside a markdown code fence, 4 as a bare list — and were
    lost as errors, since neither is the schema's object. The rows
    themselves are still validated one by one, so reading past the wrapper
    admits nothing the schema would have kept out.
    """
    fenced = _FENCE.match(text)
    data = json.loads(fenced.group(1) if fenced else text)
    if isinstance(data, dict):
        data = data.get("mappings", [])
    if not isinstance(data, list):
        raise ValueError("reply holds no list of mappings")
    return data


def _render(requirement: Requirement, framework: str, candidates, entries) -> str:
    catalog = "\n\n".join(f"{i} — {entries[i].title}\n{entries[i].text[:900]}" for i in candidates)
    context = f"UNDER THE STANDARD\n\n{requirement.parent}\n\n" if requirement.parent else ""
    return (
        f"{context}REQUIREMENT ({framework})\n\n"
        f"{requirement.requirement_id} — {requirement.title}\n{requirement.text}\n\n"
        f"CANDIDATES\n\n{catalog}"
    )


def propose_for(
    requirement: Requirement,
    *,
    framework: str,
    published: list[str],
    entries: dict[str, CatalogEntry],
    index: WordIndex,
    provider,
) -> Proposal:
    from policyforge.llm import effort

    candidates = candidates_for(
        f"{requirement.title} {requirement.text}",
        published=published,
        entries=entries,
        index=index,
    )
    proposal = Proposal(requirement.requirement_id, candidates)
    rows = None
    unusable = None
    # Two attempts, and only for a reply that could not be read at all. On
    # the full HIPAA run glm-5.3-flash answered 6 of 75 requirements with its
    # reasoning in prose ("Let me analyze the requirement carefully…") and no
    # rows anywhere in it; one failure in twelve is a requirement nobody gets
    # a proposal for. A timeout or a refused request is not retried.
    for _attempt in range(2):
        try:
            response = effort.call_json(
                provider,
                effort=effort.MAPPING,
                system=PROPOSE_PROMPT,
                prompt=_render(requirement, framework, candidates, entries),
                schema=_schema(candidates),
                temperature=0.0,
                max_tokens=6000,
            )
            rows = parse_reply(response.text)
            if _named_nothing(rows, candidates):
                # Every row left out the control it was about, so there is
                # nothing to map and nothing to guess from. glm-5.3-flash
                # answered the encryption specification this way on three
                # runs and two direct calls, with relationship and both
                # quotes present and `control` absent, which the schema
                # requires and OpenRouter does not enforce. Read as an
                # unusable reply and asked once more, like an unreadable one.
                unusable = rows
                rows = None
                proposal.error = "every row named no control on the candidate list"
                continue
        except TruncatedResponse as exc:
            # Never retried here. `effort.call_json` has already asked again
            # at a larger budget, so a second attempt would buy a third
            # billed call and the same cut-off reply. The requirement keeps
            # whatever the overlay already says, and the run names it.
            proposal.error = str(exc)[:300]
            proposal.truncated = True
            return proposal
        except SchemaReplyError as exc:
            try:
                rows = parse_reply(exc.text)
            except ValueError:
                proposal.error = str(exc)[:300]
        except ValueError as exc:
            proposal.error = f"{type(exc).__name__}: {exc}"[:300]
        except Exception as exc:  # noqa: BLE001 - one requirement's failure must not end the run
            # Provider SDKs raise their own types (LiteLLM's rate-limit error
            # is not a RuntimeError), and a 75-call run that stops on the
            # first one loses the rest. Recorded, not retried.
            proposal.error = f"{type(exc).__name__}: {exc}"[:300]
            return proposal
        if rows is not None:
            proposal.error = ""
            break
    if rows is None:
        # Nothing usable, but what was refused is still worth reporting.
        if unusable is not None:
            _read_rows(unusable, proposal, requirement, entries, candidates)
        return proposal

    _read_rows(rows, proposal, requirement, entries, candidates)
    return proposal


def _read_rows(rows, proposal, requirement, entries, candidates) -> None:
    """Keep the rows that name a candidate and quote both texts; refuse the rest."""
    requirement_words = f"{requirement.title} {requirement.text} {requirement.parent}"
    seen = set()
    for row in rows:
        control = row.get("control") if isinstance(row, dict) else None
        requirement_quote = printable(row.get("requirement_quote", "")) if control else ""
        control_quote = printable(row.get("control_quote", "")) if control else ""
        valid = (
            control in candidates
            and control not in seen
            and row.get("relationship") in ASSERTABLE
            # A quote of four words or more from anywhere in the requirement,
            # its title or its parent; or, for a specification shorter than
            # that, the whole of the specification's own text and no less.
            and (
                grounded(requirement_quote, requirement_words, heading=requirement.title)
                or is_whole_text(requirement_quote, requirement.text)
            )
            and grounded(
                control_quote,
                f"{entries[control].title} {entries[control].text}",
                heading=entries[control].title.split(" | ")[-1],
            )
        )
        if not valid:
            proposal.refused.append(row if isinstance(row, dict) else {"row": row})
            if control not in candidates:
                proposal.nameless += 1
            continue
        seen.add(control)
        proposal.mappings.append(
            ProposedMapping(
                control=control,
                relationship=row["relationship"],
                requirement_quote=requirement_quote,
                control_quote=control_quote,
            )
        )


@dataclass
class MergeReport:
    confirmed: int = 0
    not_confirmed: int = 0
    proposed: int = 0
    #: Rows a person has already decided, left exactly as they were.
    left_reviewed: int = 0
    #: Pairs a person rejected, which the model named again. Not re-proposed.
    rejected_again: int = 0
    #: Requirements whose reply was cut off at its budget, listed apart from
    #: other failures because a larger budget is the fix.
    truncated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def merge(
    overlay: Overlay,
    proposals: list[Proposal],
    controls,
    *,
    model: str,
    today: date | None = None,
    keep_quotes: bool = True,
) -> MergeReport:
    """Record `proposals` on `overlay`, in place. Never changes a decision.

    A requirement the overlay does not list yet is first seeded from the
    published mapping, so recording a proposal on it does not unmap the
    published pairs the model happened not to confirm.

    Nothing here changes what reaches the pipeline. `status` is left alone,
    and a suggested relationship goes to `proposed_relationship`, never
    `relationship`, which coverage reads.

    `keep_quotes=False` records a digest of each quote instead of its words,
    for a licensed catalog: the overlay lives in the organization's
    repository, and requirement text written into it would be licensed
    content in a file nobody classified.
    """
    prompt = REGISTRY["crosswalk.propose"]
    proposed_by = {
        "model": model,
        "prompt": prompt.label,
        "date": (today or date.today()).isoformat(),
    }
    published = published_pairs(controls, overlay.framework, overlay.anchor)
    report = MergeReport()

    for proposal in proposals:
        rid = proposal.requirement_id
        if proposal.error:
            report.errors.append(f"{rid}: {proposal.error}")
            if proposal.truncated:
                report.truncated.append(rid)
            continue
        rows = overlay.requirements.get(rid)
        if rows is None:
            rows = [
                MappingRow(control=c, status=ACCEPTED, sources=["published"])
                for c in published.get(rid, [])
            ]
            overlay.requirements[rid] = rows
        by_control = {row.control: row for row in rows}
        asserted = {m.control: m for m in proposal.mappings}

        for row in rows:
            if row.reviewed_by:
                report.left_reviewed += 1
                continue
            row.flags = [flag for flag in row.flags if flag not in MODEL_FLAGS]
            mapping = asserted.get(row.control)
            if mapping is None:
                # Shown to the model and not asserted. Only meaningful for a
                # row that is live or waiting; a control that was never a
                # candidate was not judged at all.
                if row.control in proposal.candidates and row.status != REJECTED:
                    row.flags.append(NOT_CONFIRMED)
                    report.not_confirmed += 1
                continue
            if row.status == REJECTED:
                continue
            _annotate(row, mapping, proposed_by, keep_quotes)
            if row.status == ACCEPTED and mapping.relationship != row.relationship:
                row.flags.append(RELATIONSHIP_PROPOSED)
            report.confirmed += row.status == ACCEPTED

        for control, mapping in asserted.items():
            existing = by_control.get(control)
            if existing is not None:
                # Counted where it stands, not again as new: a second run over
                # the same proposals must not report them as fresh.
                report.rejected_again += existing.status == REJECTED
                continue
            row = MappingRow(control=control, status=PROPOSED, sources=["model"])
            _annotate(row, mapping, proposed_by, keep_quotes)
            rows.append(row)
            report.proposed += 1
    return report


def _digest(text: str) -> str:
    """A short, unsalted SHA-256 of `text`: enough to tell whether a quote
    changed between runs, not a secret. A specification of three or four
    words, quoted whole, could be recovered by hashing guesses, which is
    acceptable because the digest's job is keeping licensed prose out of a
    tracked file, not hiding which requirement a row is about — its id is in
    the file beside it."""
    import hashlib

    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _annotate(
    row: MappingRow, mapping: ProposedMapping, proposed_by: dict, keep_quotes: bool
) -> None:
    row.proposed_relationship = mapping.relationship
    if keep_quotes:
        row.evidence = {
            "requirement": mapping.requirement_quote,
            "control": mapping.control_quote,
        }
    else:
        row.evidence = {
            "requirement": _digest(mapping.requirement_quote),
            "control": _digest(mapping.control_quote),
            "withheld": "licensed catalog: quotes verified, not stored",
        }
    if "model" not in row.sources:
        row.sources.append("model")
    row.proposed_by = dict(proposed_by)
