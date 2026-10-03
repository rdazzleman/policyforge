"""The NIST AI RMF Playbook -- NIST's suggested actions, per Core subcategory.

**The Playbook is VOLUNTARY, and that is the first thing to know about
citing it.** NIST: *"The Playbook is neither a checklist nor set of steps to
be followed in its entirety. Playbook suggestions are voluntary."* A
document citing an action here may say NIST *suggests* it and never that
NIST *requires* it (80's ruling on #177). The catalog README says so first.

**Why this catalog exists.** The AI RMF Core (`ai_rmf.py`) states outcomes,
not obligations, and NIST deliberately put the actions in this separately
versioned Playbook instead. Decomposing an outcome into actions ourselves
would assert something NIST withheld; ingesting NIST's own suggestions is
the one route to actions that invents nothing (#177).

**Shape.** One Control per Core subcategory (`Govern 1.1` ...), titled with
OUR label ("Suggested actions for GOVERN 1.1"), and one enhancement per
suggested action (`Govern 1.1 Action 3`). No outcome wording is carried: the
export restates each outcome, and in 30 of 72 its wording differs from the
AI RMF 1.0 Core's, and in 8 more only its capitalisation (observed on #177;
the split is 9b's, on #304), so the outcome is read from
`nist-ai-rmf` under the same id (80's ruling). The action NUMBER is ours -- its position in
NIST's list -- because NIST does not number them; the README says so. Every
Control's id must be a subcategory the shipped Core carries: an action with
no subcategory is refused, not filed.

**What counts as an action** is a TOP-LEVEL item of a subcategory's
Suggested Actions list -- the definition both of `policyforge-f8`'s
instruments applied on #177 (Markdown indentation, and NIST's own rendered
DOM), which agreed on every subcategory. Nested sub-items are enumerations
inside an action and stay part of its text. A line before the first item is
a lead-in ("Organizational management can:"), kept as the subcategory's
discussion. A column-0 line after an item that is not itself an item --
MANAGE 2.2's malformed `-Establish ...`, which repeats the line above it --
is a CommonMark lazy continuation of that item, and is kept in its text
exactly as NIST's renderer shows it. Accepting it as an item gives 460,
which is the number to distrust.

**Pinned, twice.** NIST publishes no revision number for the Playbook (its
FAQ: *"there will not be a 'final version'"*), so the revision this catalog
asserts is the SHA-256 of the export itself, and a different export is
REFUSED, not parsed: a catalog pinned to a revision refuses to ingest a
different one (standing ruling). The extent is pinned too, per subcategory,
to an independent count -- not to anything this parser produced.
"""

from __future__ import annotations

import hashlib
import json
import re

from policyforge.ingest.schema import Control, ControlEnhancement

#: NIST's machine-readable export, linked from the AIRC Playbook page. A
#: fixed constant, like the Core's: the safety argument for parsing it rests
#: on the bytes coming from a host we named.
SOURCE_URL = "https://airc.nist.gov/docs/playbook.json"

FRAMEWORK = "NIST AI RMF Playbook"
CORE_VERSION = "1.0"

#: The export this catalog is pinned to: its SHA-256 and the `Last-Modified`
#: date AIRC served it with. Measured by `policyforge-f8` on #177 (accessed
#: 2026-09-24) and re-measured by `policyforge-ba` from a fresh fetch the
#: same day: identical bytes.
SOURCE_SHA256 = "aecbee3d3c8820816d295b11d10fb61324b17c25c2ff39ee95e2aa5654555bba"
EXPORT_DATE = "2026-06-11"
FRAMEWORK_VERSION = f"AIRC export {EXPORT_DATE} (AI RMF {CORE_VERSION})"

#: Suggested actions per subcategory at the pinned export, from
#: `policyforge-f8`'s INSTRUMENT B on #177: `<li>` elements directly inside
#: the first `<ul>` after each Suggested Actions heading on NIST's rendered
#: AIRC pages -- NIST's own Markdown renderer deciding what nests. It agreed
#: with instrument A (column-0 bullets in the export) on all 72, and with a
#: third run by ba on the fetched bytes. **Not produced by this parser**: a
#: count taken from our own parse is not independent (#173). The total is
#: the sum of this table, never a separate literal.
EXPECTED_ACTIONS: dict[str, int] = {
    "Govern 1.1": 3, "Govern 1.2": 15, "Govern 1.3": 5, "Govern 1.4": 7,
    "Govern 1.5": 7, "Govern 1.6": 4, "Govern 1.7": 4, "Govern 2.1": 6,
    "Govern 2.2": 6, "Govern 2.3": 2, "Govern 3.1": 5, "Govern 3.2": 8,
    "Govern 4.1": 5, "Govern 4.2": 4, "Govern 4.3": 5, "Govern 5.1": 3,
    "Govern 5.2": 5, "Govern 6.1": 4, "Govern 6.2": 2,
    "Map 1.1": 13, "Map 1.2": 2, "Map 1.3": 5, "Map 1.4": 3, "Map 1.5": 6,
    "Map 1.6": 8, "Map 2.1": 1, "Map 2.2": 6, "Map 2.3": 16, "Map 3.1": 6,
    "Map 3.2": 2, "Map 3.3": 2, "Map 3.4": 10, "Map 3.5": 6, "Map 4.1": 4,
    "Map 4.2": 4, "Map 5.1": 4, "Map 5.2": 7,
    "Measure 1.1": 12, "Measure 1.2": 8, "Measure 1.3": 8, "Measure 2.1": 3,
    "Measure 2.2": 8, "Measure 2.3": 9, "Measure 2.4": 7, "Measure 2.5": 15,
    "Measure 2.6": 7, "Measure 2.7": 10, "Measure 2.8": 6, "Measure 2.9": 11,
    "Measure 2.10": 8, "Measure 2.11": 24, "Measure 2.12": 6, "Measure 2.13": 4,
    "Measure 3.1": 6, "Measure 3.2": 3, "Measure 3.3": 6, "Measure 4.1": 6,
    "Measure 4.2": 6, "Measure 4.3": 6,
    "Manage 1.1": 5, "Manage 1.2": 3, "Manage 1.3": 5, "Manage 1.4": 3,
    "Manage 2.1": 6, "Manage 2.2": 4, "Manage 2.3": 7, "Manage 2.4": 8,
    "Manage 3.1": 10, "Manage 3.2": 5, "Manage 4.1": 9, "Manage 4.2": 5,
    "Manage 4.3": 5,
}  # fmt: skip

FUNCTIONS = ("Govern", "Map", "Measure", "Manage")
_FUNCTION_ABBR = {"Govern": "GV", "Map": "MP", "Measure": "MS", "Manage": "MG"}

#: NIST's title, `GOVERN 1.1`. The function word is CAPTURED, not
#: enumerated, so an unknown function reaches the guard that names it rather
#: than failing to match -- the lesson recorded on the Core's `_ROW`.
_TITLE = re.compile(r"^([A-Z]+) (\d+\.\d+)$")

#: A top-level item: a bullet in column 0 followed by a space (CommonMark).
_ITEM = re.compile(r"^[-*] ")


class AiRmfPlaybookError(RuntimeError):
    """The export is not the one this catalog is pinned to, or it parsed to
    something the pinned Playbook cannot be. Raised, never returned: a
    Playbook short by ten actions has failed in a way no caller can see."""


def check_pinned_source(raw: bytes) -> None:
    """Refuse any export but the pinned one, before parsing a byte of it."""
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_SHA256:
        raise AiRmfPlaybookError(
            f"the Playbook export is sha256:{digest[:16]}..., and this catalog is "
            f"pinned to sha256:{SOURCE_SHA256[:16]}... ({FRAMEWORK_VERSION}). NIST "
            "re-publishes the Playbook without a revision number, so a different "
            "export is a different revision: its README, pin and expected counts "
            "must be reviewed and re-pinned by a person, not absorbed by the ETL. "
            "On the scheduled drift job, this failure is the job working."
        )


def _subcategory_id(title: str) -> str:
    match = _TITLE.match(title.strip())
    if not match:
        raise AiRmfPlaybookError(f"not a subcategory title: {title!r}")
    function = match.group(1).capitalize()
    if function not in FUNCTIONS:
        raise AiRmfPlaybookError(f"unrecognised AI RMF function in {title!r}")
    return f"{function} {match.group(2)}"


def _actions(section: str, subcategory: str) -> tuple[list[str], list[str]]:
    """(lead-in lines, actions) from one Suggested Actions section."""
    lead_in: list[str] = []
    actions: list[list[str]] = []
    for line in section.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        if _ITEM.match(line):
            actions.append([line[2:].strip()])
        elif not actions:
            if line[0] in " \t":
                raise AiRmfPlaybookError(
                    f"{subcategory}: an indented line before any suggested action: "
                    f"{line.strip()[:60]!r}"
                )
            lead_in.append(line.strip())
        else:
            # A nested sub-item (indented) or a lazy continuation (column 0,
            # not an item): part of the action above it, as NIST renders it.
            actions[-1].append(line.strip())
    return lead_in, ["\n".join(parts) for parts in actions]


def parse_playbook(raw: bytes, core_subcategories: set[str]) -> list[Control]:
    """Parse the pinned export into one Control per Core subcategory.

    `core_subcategories` is the shipped Core's subcategory ids. It is
    required rather than defaulted, because the check it enables -- every
    Playbook entry serves a subcategory the Core actually has, and every
    subcategory has an entry -- is the one this catalog exists to keep.
    """
    check_pinned_source(raw)
    entries = json.loads(raw.decode("utf-8"))
    if not isinstance(entries, list):
        raise AiRmfPlaybookError("the export is not a list of subcategories")

    by_id: dict[str, dict] = {}
    for entry in entries:
        subcategory = _subcategory_id(entry.get("title", ""))
        if subcategory in by_id:
            raise AiRmfPlaybookError(f"{subcategory} appears twice in the export")
        if (entry.get("type") or "").capitalize() != subcategory.split()[0]:
            raise AiRmfPlaybookError(
                f"{subcategory}: type {entry.get('type')!r} disagrees with its title"
            )
        by_id[subcategory] = entry

    unserved = sorted(set(by_id) - core_subcategories)
    if unserved:
        raise AiRmfPlaybookError(
            f"entries for subcategories the Core does not have: {unserved}. An "
            "action must serve a Core subcategory; these are refused, not filed."
        )
    uncovered = sorted(core_subcategories - set(by_id))
    if uncovered:
        raise AiRmfPlaybookError(f"Core subcategories with no Playbook entry: {uncovered}")

    parsed = {
        sub: _actions(entry.get("section_actions") or "", sub) for sub, entry in by_id.items()
    }
    empty = sorted(sub for sub, (_, actions) in parsed.items() if not all(actions))
    if empty:
        raise AiRmfPlaybookError(f"empty suggested action(s) in {empty}")
    counts = {sub: len(actions) for sub, (_, actions) in parsed.items()}
    if counts != EXPECTED_ACTIONS:
        wrong = {
            sub: (counts.get(sub), EXPECTED_ACTIONS.get(sub))
            for sub in sorted(set(counts) | set(EXPECTED_ACTIONS))
            if counts.get(sub) != EXPECTED_ACTIONS.get(sub)
        }
        raise AiRmfPlaybookError(
            f"suggested actions per subcategory (parsed, pinned) differ: {wrong}. "
            f"The pinned export has {sum(EXPECTED_ACTIONS.values())}; a short or long "
            "parse is refused whole, never shipped partial."
        )

    def order(subcategory: str) -> list[int]:
        function, number = subcategory.split()
        return [FUNCTIONS.index(function), *(int(part) for part in number.split("."))]

    controls = []
    for subcategory in sorted(by_id, key=order):
        entry = by_id[subcategory]
        lead_in, actions = parsed[subcategory]
        function = subcategory.split()[0]
        controls.append(
            Control(
                control_id=subcategory,
                # A LABEL, ours, not NIST's outcome wording (80's ruling on
                # #177). The export's `description` restates each outcome,
                # and in 30 of 72 subcategories its wording differs from the
                # AI RMF 1.0 Core's (8 more differ only in capitalisation);
                # carrying it would give one outcome two NIST-attributed
                # wordings under two valid citations. The
                # outcome lives in `nist-ai-rmf`, under this same id.
                title=f"Suggested actions for {entry['title'].strip()}",
                framework=FRAMEWORK,
                framework_version=FRAMEWORK_VERSION,
                family=function,
                family_abbr=_FUNCTION_ABBR[function],
                control_statement="",
                # The lead-in, where NIST wrote one: the actions complete its
                # sentence ("... should be designed to:"). Kept apart rather
                # than prefixed to each action, which would put words in
                # NIST's mouth the export does not repeat.
                discussion=" ".join(lead_in),
                enhancements=[
                    ControlEnhancement(
                        enhancement_id=f"{subcategory} Action {index}",
                        title="",
                        baseline="",
                        description=action,
                    )
                    for index, action in enumerate(actions, start=1)
                ],
                source_path=SOURCE_URL,
            )
        )
    return controls


def fetch_playbook(url: str = SOURCE_URL) -> tuple[bytes, str]:
    """(the export's bytes, the `Last-Modified` AIRC served it with).

    `requests`, not `urllib`, for the reason `ai_rmf.fetch_core_html` gives:
    it supports no `file://` scheme, so only the host needs checking.
    """
    import requests

    if not url.startswith("https://airc.nist.gov/"):
        raise ValueError(
            f"refusing to fetch the AI RMF Playbook from {url!r}: this parser's "
            "safety argument rests on the host being NIST's."
        )
    response = requests.get(url, headers={"User-Agent": "policyforge"}, timeout=60)
    response.raise_for_status()
    return response.content, response.headers.get("Last-Modified", "")
