"""A per-organization crosswalk file, applied over a catalog's published mapping.

The published HIPAA-to-800-53 crosswalk is a list of pairs. It has no
relationship type and no rationale, several requirements map to twenty
controls or more, and it is stored inside the catalog's `controls.json` —
so re-running `etl-hipaa` wipes it, and an organization that disagrees with
a pair has nowhere to say so that survives the next ETL.

An overlay is that somewhere. One YAML file per framework, in the
organization's repository next to its parameter ledger:

    framework: HIPAA Security Rule
    anchor: nist
    requirements:
      164.308(a)(5)(ii)(B):
        - control: SI-3
          relationship: subset
          status: accepted
          sources: [cprt, model]
          evidence:
            requirement: guarding against, detecting, and reporting malicious software
            control: implement malicious code protection mechanisms
          rationale: SI-3 is the detection half; reporting is IR-6.
          proposed_by: {model: glm-5.3-flash, prompt: crosswalk.propose v1 1a2b3c4d5e6f}
          reviewed_by: {who: ryan, date: 2026-09-17}

**Only accepted rows reach the pipeline.** A requirement the overlay lists
has its mapping *replaced* by that requirement's accepted rows — a rejected
pair is gone, an added one is present, a proposed one waits. A requirement
the overlay does not list keeps the catalog's mapping, so a partial overlay
is a safe one: reviewing ten requirements does not unmap the other sixty.

**Stale is reported, not dropped.** A requirement id or a control id the
catalog no longer has is listed by `check_overlay`, the same way the
parameter ledger treats a key that matches nothing: a catalog revision
surfaces as a question rather than a lost decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from policyforge.mapping.crosswalk import NIST_ANCHOR

DEFAULT_OVERLAY_DIR = Path("config/crosswalks")

#: NIST's OLIR relationship vocabulary (IR 8278A), read from the mapped
#: framework's side: `subset` means the requirement is narrower and the
#: control covers all of it. `unspecified` is what a published pair with no
#: stated relationship becomes when seeded — honest about what is not known.
#:
#: `source-untyped` is **this project's**, not IR 8278A's, and named so it
#: cannot be read as NIST's (80's ruling on #408): the pair's own source
#: publishes it with no relationship AND says its mapping is incomplete,
#: as NIST does for CSF 2.0's OLIR 186 (`comprehensive: No`). A catalog's
#: manifest declares it (`crosswalk_relationship`), `crosswalk seed` writes
#: it, and coverage reads it as partial: a person decides whether the pair
#: is enough. `unspecified` is unchanged, so nothing that existed moves.
RELATIONSHIPS = ("equal", "subset", "superset", "intersects", "unspecified", "source-untyped")

PROPOSED = "proposed"
ACCEPTED = "accepted"
REJECTED = "rejected"
STATUSES = (PROPOSED, ACCEPTED, REJECTED)


class OverlayError(ValueError):
    """An overlay file that cannot be read as one."""


#: Catalogs that state **conditions** rather than controls, and the sentence
#: each refusal prints.
#:
#: The crosswalk is 800-53-anchored by construction: an overlay maps a
#: framework's requirements onto `NIST_ANCHOR`, and every row asserts *this
#: requirement and that control are about the same obligation*. For a catalog
#: whose entries are conditions of an exception, there is no such obligation
#: to be the same as, so the mapping cannot be right — not "is unreviewed",
#: but has no true form.
#:
#: Refusing rather than seeding an empty overlay, because the empty one is
#: the more dangerous artefact. `seed` against 45 CFR 171 produced 76 rows
#: reading `0 published pairs, 76 with none` and exited 0, which says *no
#: mappings found* — indistinguishable from a catalog whose crosswalk nobody
#: has published yet, and it hands `crosswalk propose` a ready-made worklist
#: to fill in with a model's guesses.
#:
#: Keyed on the framework name as the catalog declares it. Matching is
#: case- and space-insensitive, because the name arrives from `--framework`
#: typed by a person.
NOT_CROSSWALK_ANCHORABLE: dict[str, str] = {
    # Keyed on BOTH names this catalog has had. It now declares
    # "Information Blocking" so that a citation to it is a legal tag --
    # a name beginning with a digit is not one -- and matching here is by
    # word overlap, so the old spelling shares no word with the new and
    # would have stopped refusing. Renaming the catalog silently re-opened
    # the defect this dict exists to close; both keys, and a test.
    "Information Blocking": (
        "Information Blocking (45 CFR Part 171) states the conditions under which a "
        "practice is NOT information blocking. Its entries are conditions of an "
        "exception, not controls to implement, so there is nothing for an 800-53 "
        "control to correspond to. Mapping them would assert something neither "
        "document says: that qualifying for an exception is evidence a safeguard "
        "exists. Cite it instead -- `[Information Blocking 171.203(a)]` -- to show a "
        "practice qualifies; do not map it. See "
        "data/frameworks/cfr-171-information-blocking/README.md."
    ),
    "45 CFR 171": (
        "45 CFR 171 states the conditions under which a practice is NOT information "
        "blocking. Its entries are conditions of an exception, not controls to "
        "implement, so there is nothing for an 800-53 control to correspond to. "
        "Mapping them would assert something neither document says: that qualifying "
        "for an exception is evidence a safeguard exists. Cite it instead -- "
        "`[Information Blocking 171.203(a)]` -- to show a practice qualifies; do "
        "not map it. See "
        "data/frameworks/cfr-171-information-blocking/README.md."
    ),
    # Added when the AI RMF catalog landed, because the catalog arrived
    # already documented as un-crosswalkable -- README, module docstring,
    # command help and changelog all said so -- and `/coverage` still
    # printed "no published crosswalk yet, run `crosswalk seed`" against
    # it. **Every piece of prose we had was right and the one line the
    # user actually reads was wrong**, which is this dict's whole job.
    #
    # Found by running `/coverage` with the new catalog installed rather
    # than by reading the code: the `reason is None` branch does not omit
    # a note, it prints the *other* one, so the row stayed well-formed
    # and confidently said the opposite of the documentation.
    "NIST AI RMF": (
        'The NIST AI RMF Core states outcomes, not obligations -- "the risks are '
        'understood and managed" rather than "the organization shall". NIST puts '
        "the actions in the separately versioned, explicitly voluntary Playbook. A "
        "crosswalk entry from a subcategory to an 800-53 control would assert that "
        "implementing the control ACHIEVES the outcome, which is precisely the claim "
        "NIST declined to make when it split the Playbook out. Cite it instead -- "
        "`[NIST AI RMF Govern 1.1]` -- and be aware that a citation here can be "
        "fully traceable and still commit nobody to anything. See "
        "data/frameworks/nist-ai-rmf/README.md."
    ),
    # Added with the Playbook catalog (#177), for the reason the Core's entry
    # gives and one more: its entries are NIST's VOLUNTARY suggestions toward
    # an outcome, so mapping one to an 800-53 control would assert an
    # equivalence NIST has not published, between a suggestion and an
    # obligation. `/coverage` otherwise printed "seed one" for it.
    "NIST AI RMF Playbook": (
        "The NIST AI RMF Playbook lists NIST's voluntary suggested actions toward "
        "each AI RMF outcome -- suggestions, not obligations, and NIST says so. A "
        "crosswalk entry from a suggested action to an 800-53 control would assert "
        "an equivalence NIST has not published, between a suggestion and a "
        "requirement. Cite it instead -- `[NIST AI RMF Playbook Govern 1.1 Action "
        "1]` -- as something NIST suggests, never requires. See "
        "data/frameworks/nist-ai-rmf-playbook/README.md."
    ),
}

#: The catalogs that state OUTCOMES or SUGGESTIONS, not obligations, by
#: normalized framework key (#341, 80's ruling). Their reasons are the two AI
#: RMF entries in the table above: the Core states outcomes ("the risks are
#: understood"), the Playbook NIST's voluntary actions toward them.
#:
#: **Read by the Playbook gate** (`content/deontic.py`). A sentence citing the
#: Playbook is exempt from the gate only if it also cites a source that
#: states obligations, because that source's strength rule then governs it.
#: A Core citation is not one: before this, a "NIST suggests" sentence
#: tagged `[... Playbook Map 1.6 Action 1 | NIST AI RMF Map 2.1]` escaped the
#: gate as if the Core were binding (glm wrote three in #301's run).
#:
#: **Not every refusal above is here.** Information Blocking is refused as a
#: crosswalk anchor for being conditions of an exception, a different reason;
#: the ruling named exactly these two. A test holds this set inside the two
#: reason tables, so a catalog cannot be non-binding without a written reason.
#:
#: **And not every member is refused.** CSF 2.0 states outcomes as the Core
#: does, but NIST publishes its mapping to 800-53, so it can be seeded and its
#: reason lives in `OUTCOME_CATALOGS` below instead (80, on #408: `check` must
#: not treat a CSF citation as supporting an obligation on its own any more
#: than an AI RMF Core citation).
NON_BINDING_FRAMEWORKS: frozenset[str] = frozenset(
    {"nist-ai-rmf", "nist-ai-rmf-playbook", "nist-csf"}
)

#: Non-binding catalogs that CAN anchor a crosswalk, with the reason each is
#: non-binding, by normalized framework key. The refusal table above holds
#: the reasons for the rest.
OUTCOME_CATALOGS: dict[str, str] = {
    "nist-csf": (
        'NIST CSF 2.0 states outcomes ("the organizational mission is understood"), '
        "not obligations, as the AI RMF Core does; a citation to it commits nobody to "
        "anything on its own. See data/frameworks/nist-csf-2-0/README.md."
    ),
}


class NotAnchorableError(OverlayError):
    """Seeding was refused because the catalog states conditions, not controls.

    A distinct type because `seed_overlay` has two refusals and a reader
    cannot safely tell them apart by message alone. 1d's review probe
    passed `[]` as controls, hit the *empty catalog* error, and read it as
    this one — nearly reporting that `45 CFR 164` was being refused when it
    was not. Two failures sharing an exception type means the only thing
    separating them is whoever is reading, which is the arrangement this
    project keeps finding at the bottom of a wrong answer.
    """


def _canonical(name: str) -> str:
    return " ".join(name.split()).casefold()


def _refusal_reason(framework: str) -> str | None:
    """Why `framework` cannot anchor a crosswalk, or None if it can."""
    wanted = _canonical(framework)
    for name, reason in NOT_CROSSWALK_ANCHORABLE.items():
        if _canonical(name) == wanted:
            return reason
    return None


#: Frameworks whose SOURCE publishes a mapping to 800-53 that this release
#: does not ingest. A third reason a coverage row can read zero, and the one
#: the report used to call "no published crosswalk yet" — which was false.
#:
#: **This is NOT a refusal, and must never be merged into
#: `NOT_CROSSWALK_ANCHORABLE`.** That table makes `seed_overlay` refuse and
#: makes `/coverage` print "not mapped by design". Both would be false here:
#: these frameworks SHOULD be mapped, and their publisher already did it.
#: Seeding one by hand stays ALLOWED — the tool does not refuse it — but the
#: report advises against it: a hand-made mapping competes with the source's,
#: and the source's is the one to use. (Product ruling, 80, on #260.)
#:
#: **Enumerated, because it cannot be derived.** Whether a source publishes a
#: crosswalk is a fact about the source that only reading it establishes;
#: nothing in the produced catalog records it until an ingest keeps the
#: links. Each entry therefore names what was measured, so the claim can be
#: re-checked rather than trusted.
#:
#: Keyed by declared framework name through `_canonical`, the same matching as
#: the refusal table, which has already been re-opened once by a catalog
#: rename. `test_every_upstream_crosswalk_names_a_shipped_catalog` fails if a
#: key stops matching, so a rename cannot silently restore the false message.
PUBLISHED_UPSTREAM: dict[str, str] = {
    # EMPTY since #259, and that is a measurement, not an omission. Its one
    # entry was "NIST 800-171": NIST's rev 3 OSCAL links all 97 requirements
    # to 800-53 (157 links, as `rel="reference"` to back-matter), and this
    # release did not read them. `oscal_loader` now does, so 800-171 carries
    # NIST's mapping as its `source_crosswalk` and its row is a
    # crosswalk-carrying one. The table stays for the next catalog whose
    # source publishes a mapping PolicyForge does not yet read.
}


def _upstream_reason(framework: str) -> str | None:
    """Why `framework` reads zero when its source publishes the mapping, or None.

    Checked AFTER `_refusal_reason`: a framework that cannot anchor a
    crosswalk is refused whatever its source says.
    """
    wanted = _canonical(framework)
    for name, reason in PUBLISHED_UPSTREAM.items():
        if _canonical(name) == wanted:
            return reason
    return None


#: Frameworks someone SEARCHED for a published 800-53 mapping and found none.
#: The fourth kind of zero: seeding by hand is the right advice, and the report
#: may say "found" because a search was actually made. (Product ruling, 80, on
#: #264, corrected there after `ba` measured that the generic branch prints for
#: every catalog in no table -- including BYOC ones nobody searched for.)
#:
#: **The value is the search record, and it is never printed.** A date and a
#: list of sources in CLI output rot; here they sit beside the claim so it can
#: be re-checked rather than trusted. A negative search result is only as good
#: as its denominator, so each record says what was NOT searched too.
#:
#: Same matching and the same rename guard as `PUBLISHED_UPSTREAM`
#: (`test_every_searched_framework_names_a_shipped_catalog`): if a key stops
#: matching, the row falls to the generic branch, which claims no search.
SEARCHED_NONE_FOUND: dict[str, str] = {
    "Substance Use Disorder Records": (
        # 42 CFR Part 2. Searched by `policyforge-f8` (5b) from about 02:30 UTC
        # on 2026-09-24; the full record, with its contamination notes, is on
        # #264. Searched: 42 CFR 2.16 text (eCFR, point-in-time 2026-09-17);
        # the 2024 final rule 89 FR 12472 and the 2020 rules; the HITRUST CSF
        # v11.7.0 authoritative-sources list; NIST SP 800-66r2; site searches
        # of nist.gov, csrc.nist.gov, hhs.gov, samhsa.gov, healthit.gov and
        # 405d.hhs.gov. The published HIPAA crosswalks map the Security Rule,
        # which 2.16 does not incorporate, so they do not reach Part 2
        # transitively.
        #
        # Then, on 2026-09-26 00:23-00:26 UTC, `policyforge-f8` ENUMERATED
        # NIST OLIR and CPRT in full, where a formal mapping would be
        # registered, through the undocumented JSON routes their own pages
        # call: OLIR's 102 informative references (every status; no paging)
        # and CPRT's 197 frameworks, 211 framework versions (5 frameworks
        # list none), each listing's sha256 on #279. The counts were
        # re-measured on the same snapshot hashes by policyforge-1d (both) and
        # policyforge-ba (CPRT); #279's first report said 216 versions.
        # A self-tested pattern (42 CFR / Part 2 / substance / SAMHSA / SUD)
        # found 0 hits in either. Its stated limits, kept here because
        # dropping them would overstate the search: catalog METADATA only,
        # not every dataset's elements (a Part 2 element inside a dataset
        # named for something else would not show); only OLIR and CPRT.
        "searched 2026-09-24 UTC (#264) and 2026-09-26 UTC (#279) by policyforge-f8; "
        "NIST OLIR and CPRT catalogs enumerated in full; their datasets' elements "
        "not enumerated; record on #264, #279"
    ),
    # 45 CFR 164 Subparts D and E (#409). Searched by `policyforge-f8` (5b) on
    # 2026-09-26 06:14-06:35 UTC; the full record is on #409. Enumerated: NIST
    # OLIR's live catalog (102 entries; positive control CSF entry 186 found)
    # and CPRT's framework list (211 versions). Every HIPAA entry in either
    # maps the Security Rule: `HIPAA-Sec-Rule-800-53-5.1.1`'s 279 reference
    # elements cite 164.308-316 only, and SP 800-66r2 names 164.410, 504 and
    # 508 only inside quoted 164.314 text. NOT searched: HHS (hhs.gov returns
    # 403 to a scripted client), third parties (HITRUST is licensed and never
    # ingested; vendor crosswalks), and the NIST Privacy Framework's own
    # crosswalks, which map that framework, not the Privacy Rule.
    "HIPAA Privacy Rule": (
        "searched 2026-09-26 UTC (#409) by policyforge-f8; NIST OLIR and CPRT "
        "enumerated, every HIPAA entry maps the Security Rule; HHS and third "
        "parties not enumerated; record on #409"
    ),
    "HIPAA Breach Notification Rule": (
        "searched 2026-09-26 UTC (#409) by policyforge-f8; NIST OLIR and CPRT "
        "enumerated, every HIPAA entry maps the Security Rule; HHS and third "
        "parties not enumerated; record on #409"
    ),
}


def _searched_none_found(framework: str) -> str | None:
    """The search record for `framework` if one found no mapping, or None.

    Checked AFTER `_refusal_reason` and `_upstream_reason`: a refusal or a
    publisher's own mapping outranks a search that came back empty.
    """
    wanted = _canonical(framework)
    for name, record in SEARCHED_NONE_FOUND.items():
        if _canonical(name) == wanted:
            return record
    return None


def _tokens(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.casefold()))


def _selects(controls, typed: str, declared: str) -> bool:
    """Does `typed` name the catalog that declares itself `declared`?

    True when every distinguishing word of the declared name appears in the
    typed one, so `Information Blocking (45 CFR Part 171)` reaches
    `45 CFR 171` while `45 CFR 164` and `45 CFR 1710` do not — `164` and
    `1710` are different tokens, not prefixes of `171`.

    Only consulted when the typed name matched no catalog exactly, so a
    name that already works is never reinterpreted. A typed name that
    matches nothing at all still falls through to the empty-catalog error,
    which names what *is* loaded rather than implying the mapping is
    merely unpublished.

    **"Every distinguishing word appears" reads unbounded, and is not.**
    The rule is gated on `declared`, which comes from the loaded catalogs —
    so a name only reaches this test when the catalog that declares it is
    actually in front of the command. `Guidance on 45 CFR 160 164 171`
    carries all of `{45, cfr, 171}` and refuses **when 171 is loaded**;
    with 171 absent, the same string gets the empty-catalog error instead.
    Both directions err toward refusing rather than seeding, which is the
    safe direction for this guard, and `test_the_token_match_is_gated_on_
    the_loaded_catalog` holds it. Stated because a future reader asked to
    simplify this would otherwise see an unbounded substring rule.
    """
    if any(_canonical(name) == _canonical(typed) for name in _declared_frameworks(controls)):
        return False
    return _tokens(declared) <= _tokens(typed)


def _declared_frameworks(controls) -> list[str]:
    """Every framework name the loaded catalogs declare, in first-seen order."""
    seen: dict[str, None] = {}
    for control in controls:
        name = getattr(control, "framework", "") or ""
        if name:
            seen.setdefault(name, None)
    return list(seen)


def printable(text) -> str:
    """`text` with control characters replaced by spaces, tabs kept.

    Quotes come from a model and rationales from a hand-edited file, and
    `review` prints both to a terminal. A quote carrying an escape sequence
    passes word-based grounding untouched — the checker compares words, not
    bytes — so the characters are removed where text enters the overlay rather
    than trusted to have been caught upstream.
    """
    return "".join(
        ch if ch == "\t" or (ch.isprintable() and ch not in "\x7f") else " " for ch in str(text)
    ).strip()


@dataclass
class MappingRow:
    control: str
    relationship: str = "unspecified"
    status: str = PROPOSED
    sources: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)
    rationale: str = ""
    proposed_by: dict[str, str] = field(default_factory=dict)
    reviewed_by: dict[str, str] = field(default_factory=dict)
    #: Machine-set notes for a reviewer, such as `not-confirmed-by-model`.
    #: Never change what reaches the pipeline; only `status` does.
    flags: list[str] = field(default_factory=list)
    #: A relationship a model suggested. Held apart from `relationship`,
    #: which coverage reads: only `review` promotes it, so a model reply can
    #: never change a report before a person has seen it.
    proposed_relationship: str = ""

    @property
    def needs_review(self) -> bool:
        return not self.reviewed_by and (self.status == PROPOSED or bool(self.flags))

    def as_record(self) -> dict:
        record: dict = {
            "control": self.control,
            "relationship": self.relationship,
            "status": self.status,
        }
        for name in (
            "proposed_relationship",
            "sources",
            "flags",
            "evidence",
            "rationale",
            "proposed_by",
            "reviewed_by",
        ):
            value = getattr(self, name)
            if value:
                record[name] = value
        return record


@dataclass
class Overlay:
    framework: str
    anchor: str = NIST_ANCHOR
    requirements: dict[str, list[MappingRow]] = field(default_factory=dict)
    path: Path | None = None

    def accepted(self, requirement_id: str) -> list[MappingRow]:
        return [r for r in self.requirements.get(requirement_id, []) if r.status == ACCEPTED]

    def as_record(self) -> dict:
        return {
            "framework": self.framework,
            "anchor": self.anchor,
            "requirements": {
                rid: [row.as_record() for row in rows] for rid, rows in self.requirements.items()
            },
        }


def _string_map(value, where: str) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise OverlayError(f"{where} must be a mapping, not {type(value).__name__}.")
    return {printable(k): printable(v) for k, v in value.items()}


def parse_overlay(data, *, path: Path | None = None) -> Overlay:
    """An `Overlay` from parsed YAML, refusing anything it cannot mean.

    Strict where a typo would change what reaches a document: a misspelled
    status (`acepted`) would otherwise read as not-accepted and silently
    unmap a control, which is exactly the quiet failure this file exists to
    stop. Everything else about a row is optional.
    """
    label = str(path) if path else "overlay"
    if not isinstance(data, dict) or not data.get("framework"):
        raise OverlayError(f"{label}: needs a top-level `framework:` naming the catalog it maps.")
    raw = data.get("requirements") or {}
    if not isinstance(raw, dict):
        raise OverlayError(f"{label}: `requirements:` must map requirement ids to lists of rows.")

    overlay = Overlay(
        framework=printable(data["framework"]),
        anchor=printable(data.get("anchor") or NIST_ANCHOR),
        path=path,
    )
    for requirement_id, rows in raw.items():
        rid = printable(requirement_id)
        if rows is None:
            overlay.requirements[rid] = []
            continue
        if not isinstance(rows, list):
            raise OverlayError(f"{label}: {rid} must be a list of rows.")
        parsed = []
        seen: dict[str, int] = {}
        for index, row in enumerate(rows, start=1):
            where = f"{label}: {rid} row {index}"
            if not isinstance(row, dict) or not row.get("control"):
                raise OverlayError(f"{where} needs a `control:`.")
            # Upper-cased so `si-3` is SI-3 rather than a control nobody has.
            control = printable(row["control"]).upper()
            if control in seen:
                raise OverlayError(
                    f"{where}: {control} is already row {seen[control]} for {rid}. One "
                    "requirement names a control once; two rows could accept and reject "
                    "the same pair."
                )
            seen[control] = index
            relationship = str(row.get("relationship") or "unspecified")
            proposed_relationship = str(row.get("proposed_relationship") or "")
            status = str(row.get("status") or PROPOSED)
            for name, value in (
                ("relationship", relationship),
                ("proposed_relationship", proposed_relationship or "unspecified"),
            ):
                if value not in RELATIONSHIPS:
                    allowed = ", ".join(RELATIONSHIPS)
                    raise OverlayError(f"{where}: {name} {value!r} is not one of {allowed}.")
            if status not in STATUSES:
                raise OverlayError(
                    f"{where}: status {status!r} is not one of {', '.join(STATUSES)}."
                )
            sources = row.get("sources") or []
            if isinstance(sources, str):
                sources = [sources]
            flags = row.get("flags") or []
            if isinstance(flags, str):
                flags = [flags]
            parsed.append(
                MappingRow(
                    control=control,
                    relationship=relationship,
                    status=status,
                    sources=[printable(s) for s in sources],
                    evidence=_string_map(row.get("evidence"), f"{where} evidence"),
                    rationale=printable(row.get("rationale") or ""),
                    proposed_by=_string_map(row.get("proposed_by"), f"{where} proposed_by"),
                    reviewed_by=_string_map(row.get("reviewed_by"), f"{where} reviewed_by"),
                    flags=[printable(f) for f in flags],
                    proposed_relationship=proposed_relationship,
                )
            )
        overlay.requirements[rid] = parsed
    return overlay


def load_overlay(path: Path) -> Overlay:
    import yaml

    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise OverlayError(f"{path}: not valid YAML ({exc}).") from exc
    return parse_overlay(data, path=Path(path))


def overlay_files(directory: Path = DEFAULT_OVERLAY_DIR) -> list[Path]:
    """The overlay files in `directory`, `.yaml` and `.yml` alike, in name order."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file())


def overlay_digests(directory: Path = DEFAULT_OVERLAY_DIR) -> dict[str, str]:
    """{file name: digest} for every overlay in `directory`.

    What `map` records beside the crosswalk it builds, and what `synthesize`
    compares against, so a crosswalk built before an overlay was added,
    edited or deleted is recognised as stale. Content, not modification time:
    a timestamp can tie, and a deleted file has none.

    **Line endings are normalised first, and this hashed raw bytes until
    2026-09-21.** `ingest/provenance.content_digest` exists because raw
    hashing made every stamp recorded on Windows a mismatch in CI — the
    same file, byte for byte apart from `\\r\\n`. This site bypassed it.

    `.gitattributes` pins only `*.md` to LF, so a `config/crosswalks/*.yaml`
    gets whatever `core.autocrlf` decides. A team that commits its overlays
    **and** the provenance file beside the crosswalk therefore records one
    digest on Windows and computes another on Linux — measured, not
    inferred: the same overlay gives `edba2eba…` with LF and `154707dc…`
    with CRLF, while `content_digest` gives `edba2eba…` for both.

    **The consequence is a hard failure, not a warning.** `synthesize`
    raises `"<path> was not built from the crosswalk overlays now in
    config/crosswalks/"` and tells the operator to rebuild — blaming the
    overlays for a checkout difference, on a crosswalk that is correct.
    """
    from policyforge.ingest.provenance import content_digest

    return {p.name: content_digest(p.read_bytes()) for p in overlay_files(directory)}


def provenance_path(crosswalk_path: Path) -> Path:
    """Where `map` records which overlays a crosswalk was built from.

    Beside the crosswalk rather than inside it: every reader of crosswalk.json
    takes its top-level keys to be 800-53 control ids.
    """
    crosswalk_path = Path(crosswalk_path)
    return crosswalk_path.with_name(f"{crosswalk_path.stem}.overlays.json")


def load_overlays(directory: Path = DEFAULT_OVERLAY_DIR) -> list[Overlay]:
    """Every overlay in `directory`. A missing directory is no overlays.

    Two files for one framework are refused rather than applied in name
    order: the later one would silently replace the earlier one's decisions
    for every requirement both list.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []
    overlays = [load_overlay(p) for p in overlay_files(directory)]
    first: dict[str, Overlay] = {}
    for overlay in overlays:
        key = _framework_key(overlay.framework)
        if key in first:
            raise OverlayError(
                f"{first[key].path} and {overlay.path} both map {overlay.framework!r}. "
                "Keep one file per framework."
            )
        first[key] = overlay
    return overlays


def dump_overlay(overlay: Overlay) -> str:
    import yaml

    return yaml.safe_dump(overlay.as_record(), sort_keys=False, allow_unicode=True, width=100)


def _requirement_targets(controls, framework: str) -> dict:
    wanted = _framework_key(framework)
    targets = {}
    for control in controls:
        if _framework_key(control.framework) != wanted:
            continue
        targets[control.control_id] = control
        for enhancement in control.enhancements:
            targets[enhancement.enhancement_id] = enhancement
    return targets


def _anchor_keys(crosswalk: dict, anchor: str) -> list[str]:
    """The keys in a `source_crosswalk` that name the anchor framework.

    Catalogs do not agree on the key: the HIPAA and NIST loaders write
    `nist`, and FedRAMP, GovRAMP and ARC-AMPE write `NIST 800-53`. Reading
    only `nist` seeded those catalogs with no pairs at all, and a rejection
    left the `NIST 800-53` pair in place.
    """
    from policyforge.mapping.crosswalk import normalize_framework

    return [key for key in crosswalk if normalize_framework(key) == anchor]


def _framework_key(name: str) -> str:
    """A framework name compared the way a person reads it: case and spacing aside."""
    return " ".join(str(name).split()).casefold()


#: How close an overlay's framework name must be to a loaded catalog's name to
#: be read as a misspelling of it. "HIPPA Security Rule" against "HIPAA Security
#: Rule" is 0.95; "FedRAMP" against "ARC-AMPE" is 0.53, and against "GovRAMP"
#: 0.57. A shortened name — "HIPAA" for "HIPAA Security Rule", 0.42 — is caught
#: separately, by its words all appearing in the catalog's name.
TYPO_SIMILARITY = 0.8


def _check_framework_is_loaded(controls, overlay: Overlay) -> None:
    """Refuse an overlay whose framework name looks like a misspelled loaded catalog.

    A framework the loaded catalogs do not include is normal — most commands
    load 800-53 alone — so that on its own is not an error. Two signals
    together make a typo: the name is close to a loaded catalog's name, and
    the requirement ids it lists belong to that catalog. Either alone is not
    enough. FedRAMP, GovRAMP and ARC-AMPE all number their requirements with
    800-53 ids, so ids alone would read a FedRAMP overlay as a misspelled
    800-53 or ARC-AMPE one whenever FedRAMP is not loaded — the review
    reproduced exactly that — and the anchor catalog is never a candidate.
    """
    from difflib import SequenceMatcher

    from policyforge.mapping.crosswalk import normalize_framework

    if _requirement_targets(controls, overlay.framework):
        return
    wanted = _framework_key(overlay.framework)
    listed = set(overlay.requirements)
    owners: set[str] = set()
    for control in controls:
        if normalize_framework(control.framework) == overlay.anchor:
            continue
        if control.control_id in listed or any(
            e.enhancement_id in listed for e in control.enhancements
        ):
            owners.add(control.framework)

    def resembles(name: str) -> bool:
        key = _framework_key(name)
        return SequenceMatcher(None, wanted, key).ratio() >= TYPO_SIMILARITY or set(
            wanted.split()
        ) <= set(key.split())

    names = sorted(name for name in owners if resembles(name))
    if names:
        raise OverlayError(
            f"{overlay.path or 'overlay'}: framework {overlay.framework!r} matches no loaded "
            f"catalog, but reads like {', '.join(repr(n) for n in names)}, whose requirement "
            "ids it lists. Correct the `framework:` line; until then none of its decisions apply."
        )


def apply_overlays(controls, overlays: list[Overlay]) -> int:
    """Replace each listed requirement's mapping with its accepted rows, in place.

    Returns how many requirements were rewritten. The value is written in the
    free-text form `mapping/crosswalk.py` already reads ("SI-3, IR-6"), so
    nothing downstream needs to know an overlay exists.
    """
    rewritten = 0
    for overlay in overlays:
        _check_framework_is_loaded(controls, overlay)
        targets = _requirement_targets(controls, overlay.framework)
        for requirement_id in overlay.requirements:
            target = targets.get(requirement_id)
            if target is None:
                continue
            for key in _anchor_keys(target.source_crosswalk, overlay.anchor):
                target.source_crosswalk.pop(key)
            ids = [row.control for row in overlay.accepted(requirement_id)]
            if ids:
                target.source_crosswalk[overlay.anchor] = ", ".join(ids)
            rewritten += 1
    return rewritten


def published_pairs(controls, framework: str, anchor: str = NIST_ANCHOR) -> dict[str, list[str]]:
    """The catalog's own mapping for `framework`, before any overlay."""
    from policyforge.mapping.crosswalk import _extract_ids

    pairs = {}
    for rid, target in _requirement_targets(controls, framework).items():
        ids: dict[str, None] = {}
        for key in _anchor_keys(target.source_crosswalk, anchor):
            ids.update(dict.fromkeys(_extract_ids(target.source_crosswalk[key])))
        pairs[rid] = list(ids)
    return pairs


def seed_overlay(
    controls, framework: str, anchor: str = NIST_ANCHOR, declared: dict[str, str] | None = None
) -> Overlay:
    """An overlay holding exactly the published mapping, every pair accepted.

    Applying a seeded overlay changes nothing, which is the point: it is the
    starting file an organization edits, and until somebody does, the
    pipeline behaves as it did before overlays existed.

    Refuses outright for a framework in `NOT_CROSSWALK_ANCHORABLE`, where
    the mapping this file exists to hold has no true form. The refusal is
    here rather than in the CLI so that every caller meets it.

    Keyed on what the loaded catalog **declares**, not only on what was
    typed. This repository calls the same catalog two things — its
    `controls.json` says `45 CFR 171` and its `framework.yaml` says
    `Information Blocking (45 CFR Part 171)` — and `framework.yaml` is
    where a person looks up what a catalog is called, so the un-guarded
    spelling was the one a careful user was most likely to type. Matching
    the typed string alone let it through to "No requirements found", which
    is a politer spelling of exactly the reading this guard exists to
    destroy. Adding the second name as a key would fix today and leave the
    third spelling unguarded; taking the name from the data cannot drift.
    """
    reason = _refusal_reason(framework)
    named = framework
    if reason is None:
        # The typed name matched nothing in the table. Ask the catalogs
        # themselves whether the thing being seeded is one of these.
        for loaded in _declared_frameworks(controls):
            declared_reason = _refusal_reason(loaded)
            if declared_reason is not None and _selects(controls, framework, loaded):
                reason, named = declared_reason, loaded
                break
    if reason is not None:
        raise NotAnchorableError(f"{named} cannot anchor a crosswalk. {reason}")

    overlay = Overlay(framework=framework, anchor=anchor)
    # The relationship the source declares for its published pairs, so that
    # seeding changes nothing (80's ruling on #408): a CSF pair seeded as
    # `unspecified` would read full, where the same pair unseeded reads
    # partial. A source declaring nothing seeds `unspecified`, as before.
    from policyforge.frameworks.registry import (
        config_or_defaults,
        declared_crosswalk_relationships,
    )
    from policyforge.mapping.crosswalk import normalize_framework

    if declared is None:
        declared = declared_crosswalk_relationships(config_or_defaults("the crosswalk was seeded"))
    # Read from the catalogs `published_pairs` selects, the same population
    # the rows come from, rather than by matching the typed name again.
    wanted = _framework_key(framework)
    selected = {
        normalize_framework(c.framework) for c in controls if _framework_key(c.framework) == wanted
    }
    relationships = {declared[key] for key in selected if key in declared}
    if len(relationships) > 1:
        raise OverlayError(
            f"The catalogs named {framework!r} declare different crosswalk relationships "
            f"({', '.join(sorted(relationships))}); seed them separately."
        )
    relationship = relationships.pop() if relationships else "unspecified"
    for rid, ids in published_pairs(controls, framework, anchor).items():
        overlay.requirements[rid] = [
            MappingRow(control=i, relationship=relationship, status=ACCEPTED, sources=["published"])
            for i in ids
        ]
    if not overlay.requirements:
        # Name what IS loaded. "No requirements found" alone reads as "this
        # catalog has no published mapping yet", which is a claim about the
        # crosswalk rather than about the name being wrong — and the two
        # want opposite responses from the reader.
        available = _declared_frameworks(controls)
        if available:
            listed = ", ".join(
                f"{name!r}" + (" (cannot anchor a crosswalk)" if _refusal_reason(name) else "")
                for name in available
            )
            detail = f"The catalogs you loaded declare: {listed}."
        else:
            detail = "No catalogs were loaded."
        raise OverlayError(
            f"No catalog here declares the framework {framework!r}, so there is nothing "
            f"to seed. {detail}"
        )
    return overlay


@dataclass
class OverlayCheck:
    unknown_requirements: list[str] = field(default_factory=list)
    unknown_controls: list[tuple[str, str]] = field(default_factory=list)
    #: Published pairs for a listed requirement that the overlay neither
    #: accepts nor rejects — usually a catalog update the organization has not
    #: looked at yet, since a listed requirement's mapping is the overlay's.
    unreviewed_published: list[tuple[str, str]] = field(default_factory=list)
    proposed: int = 0

    @property
    def is_clean(self) -> bool:
        return not (self.unknown_requirements or self.unknown_controls or self.unreviewed_published)


def check_overlay(overlay: Overlay, controls) -> OverlayCheck:
    """What in `overlay` no longer matches the catalogs, before it is applied."""
    targets = _requirement_targets(controls, overlay.framework)
    published = published_pairs(controls, overlay.framework, overlay.anchor)
    from policyforge.mapping.crosswalk import normalize_framework

    anchor_ids = set()
    for control in controls:
        if normalize_framework(control.framework) == overlay.anchor:
            anchor_ids.add(control.control_id)
            anchor_ids.update(e.enhancement_id for e in control.enhancements)

    check = OverlayCheck()
    for rid, rows in overlay.requirements.items():
        if rid not in targets:
            check.unknown_requirements.append(rid)
            continue
        named = {row.control for row in rows}
        check.unreviewed_published.extend(
            (rid, c) for c in published.get(rid, []) if c not in named
        )
        for row in rows:
            if row.needs_review:
                check.proposed += 1
            if anchor_ids and row.control not in anchor_ids:
                check.unknown_controls.append((rid, row.control))
    return check


def accepted_rows(overlays: list[Overlay]) -> dict[tuple[str, str, str], MappingRow]:
    """(framework, requirement id, anchor id) -> the row that accepted the pair.

    Keyed by the normalized framework name `mapping/crosswalk.py` files a
    requirement under, so a report can look a pair up by the same key it
    reached the requirement with.

    Only accepted rows, because only those are what `apply_overlays` writes
    into the crosswalk: a rejected pair is not reachable downstream and must
    not be discoverable through this either.
    """
    from policyforge.mapping.crosswalk import normalize_framework

    found: dict[tuple[str, str, str], MappingRow] = {}
    for overlay in overlays:
        framework = normalize_framework(overlay.framework)
        for requirement_id, rows in overlay.requirements.items():
            for row in rows:
                if row.status == ACCEPTED:
                    found[(framework, requirement_id, row.control)] = row
    return found


def accepted_relationships(overlays: list[Overlay]) -> dict[tuple[str, str, str], str]:
    """(framework, requirement id, anchor id) -> relationship, for accepted rows.

    The relationship half of `accepted_rows`, which is all `coverage` needs.
    Both are keyed the same way, deliberately: two reports disagreeing about
    which pair a key names would be a hard bug to see.
    """
    return {key: row.relationship for key, row in accepted_rows(overlays).items()}


def relationships_for(
    controls, overlays: list[Overlay], declared: dict[str, str] | None = None
) -> dict[tuple[str, str, str], str]:
    """Every pair's relationship, as coverage reads it: the ONE table (#408).

    A catalog whose manifest declares a `crosswalk_relationship` gives it to
    every pair of its published crosswalk (CSF 2.0: `source-untyped`, since
    NIST's mapping is untyped and not comprehensive). An accepted overlay row
    then overrides it, since that is an organisation's reviewed decision.
    A catalog that declares nothing contributes nothing, so its pairs read as
    they always have. `declared` defaults to the manifests on disk.
    """
    from policyforge.mapping.crosswalk import build_crosswalk

    if declared is None:
        from policyforge.frameworks.registry import (
            config_or_defaults,
            declared_crosswalk_relationships,
        )

        declared = declared_crosswalk_relationships(
            config_or_defaults("crosswalk relationships were read")
        )
    table: dict[tuple[str, str, str], str] = {}
    for nist_id, mapped in build_crosswalk(controls).items():
        for framework, requirement_ids in mapped.items():
            relationship = declared.get(framework)
            if relationship:
                for requirement_id in requirement_ids:
                    table[(framework, requirement_id, nist_id)] = relationship
    table.update(accepted_relationships(overlays))
    return table
