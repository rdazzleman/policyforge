"""NIST AI Risk Management Framework 1.0 — the Core, as a control catalog.

**This catalog states OUTCOMES where every other catalog here states
obligations, and that difference is the whole reason to read this note
before citing it.**

800-53 says *the organization shall*. HIPAA says *a covered entity must*.
The AI RMF Core says things like *"the risks ... are understood and
managed"* — a state of the world a governance programme is supposed to
reach, with no sentence anywhere naming who does what. NIST publishes the
*actions* separately, in the Playbook, which is explicitly voluntary and
versioned apart from the Framework.

Two consequences, both of which bite in practice:

- **A generated document citing an AI RMF subcategory is traceable and can
  still be hollow.** `satisfies` resolves the citation, the tag is legal,
  the gate is green — and the sentence it anchors may restate the outcome
  rather than commit anyone to anything. Traceability and substance come
  apart here in a way they do not for 800-53, and no check in this project
  currently distinguishes them.
- **`satisfies` resolves an AI RMF citation and then stops, because there
  is no crosswalk.** That is not an omission to be fixed quietly: a
  crosswalk from an outcome to a control asserts that implementing the
  control *achieves* the outcome, which is exactly the claim NIST declined
  to make when it split the Playbook out. See the catalog README.

**Source is AIRC, not CPRT.** CPRT does not carry the AI RMF Core. This was
resolved by checking both; a future session should not re-litigate it.

**The declared name must be citable, which means it must not begin with a
digit.** `content/tags.SOURCE_TAG_RE` builds a framework name from
capital-initial words, so a name like `1.0 AI RMF` would not be a tag at
all — it would be prose, and a document citing this catalog would report
nothing cited while `satisfies --strict` exited 0. Two CFR catalogs shipped
in exactly that state. `NIST AI RMF` is capital-initial and safe, and
`FRAMEWORK_ALIASES` could not have rescued it otherwise: the alias table is
downstream of the pattern that failed to match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from policyforge.ingest.schema import Control, ControlEnhancement

#: AIRC's rendering of the Core. A fixed constant, deliberately: the parser
#: below is a regex over untrusted markup, and the argument that this is
#: safe rests on the bytes coming from a host we named rather than one an
#: operator or an attacker chooses. If this ever takes a supplied URL, that
#: argument lapses and has to be made again.
SOURCE_URL = "https://airc.nist.gov/airmf-resources/airmf/5-sec-core/"

FRAMEWORK = "NIST AI RMF"
FRAMEWORK_VERSION = "1.0"

#: The four functions, in the order NIST presents them. Used to validate
#: rather than to find: the parser keys on identifier *shape*, so an
#: unexpected function name surfaces as a parse error instead of being
#: silently skipped by a hard-coded list.
FUNCTIONS = ("Govern", "Map", "Measure", "Manage")

#: **The Core's expected shape is PINNED IN THE CATALOG, not here** (#189):
#: `shape:` in `data/frameworks/nist-ai-rmf/framework.yaml`, beside the
#: revision's other pins (`source_ref`, `content_sha256`), so a new revision
#: is a data edit plus a README update rather than a code change.
#:
#: **Parsing MORE than NIST publishes is as much a defect as parsing
#: fewer**, and the structural checks do not catch it: contiguity accepts
#: an *extension*, so an invented `Govern 7` yields 20 categories with
#: every other assertion satisfied. policyforge-9b raised this against the
#: widened row pattern and it was already true of the narrow one.
#:
#: Pinned as an exact pair rather than a floor. A page that yields a
#: different shape is either a restyle or a new revision of the Framework,
#: and **both need a person**: the first is a parser bug, the second means
#: this catalog's pin, its README and its provenance stamp are all stale.
#: Neither should be absorbed silently by a loader.
#:
#: **The ETL cannot rewrite it.** `provenance.record_source_provenance` owns
#: only `PROVENANCE_KEYS` and preserves every other key untouched, so a
#: re-run leaves `shape:` as a person wrote it -- a guard updated by the run
#: it guards would be "warn and proceed" in disguise.
SHAPE_KEY = "shape"


def expected_shape(framework_yaml) -> tuple[int, int]:
    """(categories, subcategories) as pinned in a catalog's `framework.yaml`.

    Refuses, loudly, when the pin is missing or malformed: a parse with no
    expected shape would accept any shape, and that is the failure the pin
    exists to prevent.
    """
    from pathlib import Path

    import yaml

    path = Path(framework_yaml)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    pin = (data or {}).get(SHAPE_KEY) if isinstance(data, dict) else None
    if not isinstance(pin, dict):
        raise AiRmfParseError(
            f"no `{SHAPE_KEY}:` pin in {path}. The AI RMF catalog's expected shape "
            "lives there (categories, subcategories); without it a parse of any size "
            "would be accepted."
        )
    categories, subcategories = pin.get("categories"), pin.get("subcategories")
    if not all(
        isinstance(n, int) and not isinstance(n, bool) and n > 0
        for n in (categories, subcategories)
    ):
        raise AiRmfParseError(
            f"`{SHAPE_KEY}:` in {path} must give positive integer `categories` and "
            f"`subcategories`; it reads {pin!r}."
        )
    return categories, subcategories


#: Abbreviations NIST uses in the Playbook and in its own crosswalks.
_FUNCTION_ABBR = {"Govern": "GV", "Map": "MP", "Measure": "MS", "Manage": "MG"}

#: **Keyed on the identifier's shape, not on a CSS class.** Class names are
#: AIRC's presentation and change without notice; `Govern 1.1` is the
#: document's own grammar and changes only when the Framework does. A
#: class-keyed parser would return zero rows on a restyle and report
#: success — the silent-empty failure this project keeps finding. The
#: structural assertions in `parse_ai_rmf` are what turn that into a crash.
#: **The function word is captured, not enumerated, and that is the whole
#: point.** It used to read `(?:Govern|Map|Measure|Manage)`, which made the
#: `unrecognised AI RMF function` guard below **unreachable** -- the regex
#: could not produce a name the guard would reject, so a fifth NIST
#: function did not raise, it **vanished**: the row failed to match at all
#: and the reader got a confusing complaint about category numbering
#: instead.
#:
#: Found by policyforge-ba, constructively: deleting each guard in turn and
#: recording which test noticed. Two noticed nothing, and this one could
#: not have. Enumerating a vocabulary inside a pattern turns "I do not
#: recognise this" into "this does not exist", which is the failure this
#: module is otherwise written against.
_ROW = re.compile(
    r'<span class="[^"]*">\s*'
    r"(?P<id>[A-Z][a-z]+ \d+(?:\.\d+)?)\s*"
    r"</span>\s*:\s*(?P<text>.*?)</th>",
    re.S,
)

_TAGS = re.compile(r"<[^>]+>")

_ENTITIES = {
    "&amp;": "&",
    "&nbsp;": " ",
    "&lt;": "<",
    "&gt;": ">",
    "&#39;": "'",
    "&quot;": '"',
    "&rsquo;": "\u2019",
    "&lsquo;": "\u2018",
    "&rdquo;": "\u201d",
    "&ldquo;": "\u201c",
    "&mdash;": "\u2014",
    "&ndash;": "\u2013",
}


class AiRmfParseError(RuntimeError):
    """The page parsed to something the Framework cannot be.

    Raised rather than returned, and raised on *structure* rather than on
    emptiness alone, because the failure this guards against is a restyle
    that yields a well-formed catalog with the wrong contents. A loader
    that returns nine categories when the Framework has nineteen has not
    failed in any way the caller can see.
    """


def _clean(raw: str) -> str:
    text = _TAGS.sub(" ", raw)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class _Row:
    identifier: str
    text: str

    @property
    def function(self) -> str:
        return self.identifier.split()[0]

    @property
    def is_subcategory(self) -> bool:
        return "." in self.identifier


def _rows(html: str) -> list[_Row]:
    return [_Row(match.group("id"), _clean(match.group("text"))) for match in _ROW.finditer(html)]


def _sort_key(row: _Row) -> list[int]:
    """Function order first, then numeric.

    **The function index has to lead, or the catalog interleaves.** Sorting
    on the number alone yields `Govern 1, Map 1, Measure 1, Manage 1,
    Govern 2, ...` — every row present, every count correct, and an order
    the Framework does not have. The first version of this did exactly
    that and all three count assertions passed, which is the reminder that
    a check counting rows says nothing about their arrangement.
    """
    function, number = row.identifier.split(maxsplit=1)
    return [FUNCTIONS.index(function), *(int(part) for part in number.split("."))]


def parse_ai_rmf(html: str, *, expected_shape: tuple[int, int]) -> list[Control]:
    """Parse AIRC's Core page into one Control per category.

    **A category is the Control and its subcategories are the
    enhancements**, which is the shape 800-53 uses for a control and its
    enhancements and the shape `part2_loader` uses for a section and its
    paragraphs. The alternative — 72 flat controls — would lose the only
    grouping the Framework actually defines, and a crosswalk built on it
    could not express "this maps to Govern 1 as a whole".
    """
    rows = _rows(html)
    if not rows:
        raise AiRmfParseError(
            f"no AI RMF rows matched at {SOURCE_URL}. The page structure "
            f"changed, or the fetch returned something that is not the Core. "
            f"This is a parser failure, not an empty framework."
        )

    empty = [row.identifier for row in rows if not row.text]
    if empty:
        raise AiRmfParseError(f"rows parsed with no text: {empty}")

    unknown = sorted({row.function for row in rows} - set(FUNCTIONS))
    if unknown:
        raise AiRmfParseError(f"unrecognised AI RMF function(s): {unknown}")

    categories = [row for row in rows if not row.is_subcategory]
    subcategories = [row for row in rows if row.is_subcategory]

    by_category: dict[str, list[_Row]] = {row.identifier: [] for row in categories}
    orphans = []
    for row in subcategories:
        parent = row.identifier.rsplit(".", 1)[0]
        if parent not in by_category:
            orphans.append(row.identifier)
            continue
        by_category[parent].append(row)
    if orphans:
        raise AiRmfParseError(
            f"subcategory with no parent category: {orphans}. Either a "
            f"category row failed to parse or the numbering changed."
        )

    # Contiguity is the check that catches a *partial* parse, which is the
    # failure that otherwise looks exactly like success. Govern 1, 2, 4 is
    # not a smaller framework; it is three rows and a missing one.
    for function in FUNCTIONS:
        numbers = sorted(
            int(row.identifier.split()[1]) for row in categories if row.function == function
        )
        if numbers != list(range(1, len(numbers) + 1)):
            raise AiRmfParseError(
                f"{function} categories are not contiguous from 1: {numbers}. "
                f"A category row is missing."
            )

    shape = (len(categories), len(subcategories))
    if shape != tuple(expected_shape):
        raise AiRmfParseError(
            f"the Core parsed to {shape[0]} categories and {shape[1]} "
            f"subcategories; revision {FRAMEWORK_VERSION} is pinned at "
            f"{expected_shape[0]} and {expected_shape[1]} (framework.yaml). Either the page "
            f"changed shape, or NIST has revised the Framework -- in which "
            f"case this catalog's pin, README and provenance stamp are all "
            f"stale and a person has to say so."
        )

    controls = []
    for row in sorted(categories, key=_sort_key):
        controls.append(
            Control(
                control_id=row.identifier,
                title=row.text,
                framework=FRAMEWORK,
                framework_version=FRAMEWORK_VERSION,
                family=row.function,
                family_abbr=_FUNCTION_ABBR[row.function],
                # `control_statement` is left empty and the outcome lives in
                # `title`, because the Framework gives a category one
                # sentence and that sentence is its name. Copying it into
                # both fields would let a generator quote the same words
                # twice and read as two sources agreeing.
                control_statement="",
                enhancements=[
                    ControlEnhancement(
                        enhancement_id=sub.identifier,
                        title="",
                        baseline="",
                        description=sub.text,
                    )
                    for sub in sorted(by_category[row.identifier], key=_sort_key)
                ],
                source_path=SOURCE_URL,
            )
        )
    return controls


def fetch_core_html(url: str = SOURCE_URL) -> str:
    """Fetch AIRC's Core page.

    Separated from `parse_ai_rmf` so every test in this project runs against
    a fixture and none reaches the network — the same split as
    `part2_loader`.

    **`requests`, not `urllib`, and the reason is a property rather than a
    convention.** `urllib` honours `file://`, so a `urlopen` over a
    non-constant URL can be walked into reading a local path; `requests`
    supports no such scheme, which removes the class instead of guarding
    against it. The host check below then only has to enforce *which*
    remote host, which is a much weaker thing to have to get right.
    """
    import requests

    if not url.startswith("https://airc.nist.gov/"):
        raise ValueError(
            f"refusing to fetch the AI RMF Core from {url!r}: this parser's "
            f"safety argument rests on the host being NIST's."
        )
    response = requests.get(url, headers={"User-Agent": "policyforge"}, timeout=60)
    response.raise_for_status()
    return response.text
