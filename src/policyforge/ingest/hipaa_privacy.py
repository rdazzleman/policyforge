"""HIPAA Subparts D and E: the Breach Notification and Privacy Rules (#409).

**eCFR's own per-paragraph citations are the ground truth** (80's ruling 2 on
#409). The Security Rule's parser derives citations from the paragraph tokens
in eCFR's XML. On these subparts it mis-cites or merges 118 of 793 Privacy
Rule paragraphs, 2 of them onto a real paragraph id (5b's measurement).
eCFR's renderer prints every paragraph inside `<div id="p-164.502(a)(5)(i)(A)(1)">`,
so this reads the id eCFR itself assigns and derives nothing.

**Two instruments, two jobs.** The renderer gives the structure: section,
paragraph id, heading. The versioner XML, which the Security Rule's loader
already reads, guards the words. Each section's full text from the renderer
must equal the XML's, whitespace aside. That is compared per section,
because the XML packs `(a) Standard - (1) General rule...` into one `<P>`
where the renderer splits `(a)` and `(a)(1)`; compared per paragraph, 198
of 1,051 "disagree" when nothing is wrong (ba, measured on #409).

**What is skipped, and why.** Definitions (term-keyed ids, such as
`164.402(Breach)` and its sub-paragraphs) are not requirements. The Security
loader skips its Definitions section for the same reason. Source notes
(`[65 FR 82802 ...]`) are not text of the rule.

**What eCFR leaves without an id.** In §164.404(d), eCFR prints `(d)(1)(i)`
inside `(d)(1)`'s own text, and `(d)(1)(ii)`, `(d)(2)(i)` and `(d)(2)(ii)`
as unnumbered paragraphs. They are kept inside the paragraph eCFR puts them
in, and a citation to one resolves to its section, the unit the catalog
declares (`family: section`, #423), as `satisfies` reports it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from policyforge.ingest.schema import Control, ControlEnhancement

TITLE = 45
PART = "164"
#: The eCFR date both catalogs are pinned to, as the Security Rule's is.
PIN_DATE = "2026-09-17"
#: The day before the 2024 reproductive-health rule took effect in eCFR's
#: point-in-time view: the source of the pre-rule wording that binds again
#: where the rule's revision was vacated (80's ruling 1 on #409).
PRE_RULE_DATE = "2024-04-25"

_RENDERER = "https://www.ecfr.gov/api/renderer/v1/content/enhanced"

#: (framework name as tags write it, the subpart's first and last section).
BREACH = ("HIPAA Breach Notification Rule", 400, 414)
PRIVACY = ("HIPAA Privacy Rule", 500, 535)

_SECTION_ID = re.compile(r"^164\.(\d+)$")
#: A paragraph id is the section plus one or more `(token)` groups, each a
#: paragraph designation: a letter, a number or a roman numeral. A term-keyed
#: definition (`164.402(Breach)`, `164.501(Health%20care%20operations)(1)`)
#: has a word in its first group and is not a requirement.
_PARAGRAPH_ID = re.compile(r"^164\.\d+(?:\((?:[a-z]{1,2}|[0-9]{1,2}|[ivxl]{1,6}|[A-Z])\))+$")
_SOURCE_NOTE = re.compile(r"^\s*\[")


def renderer_url(date: str, subpart: str = "E") -> str:
    return f"{_RENDERER}/{date}/title-{TITLE}?part={PART}&subpart={subpart}"


def xml_url(date: str, subpart: str) -> str:
    from policyforge.ingest.ecfr import source_url

    return f"{source_url(date, title=TITLE, part=PART)}&subpart={subpart}"


def fetch(date: str, subpart: str) -> tuple[str, str]:
    """(renderer HTML, versioner XML) for one subpart of Part 164 at `date`.

    Not refused by hash, unlike CSF 2.0's files: eCFR serves each date's text
    as it stood, and its HTML names the date in every link, so a new date is
    always new bytes. What refuses is the content: the extent, the vacated
    set by name, and the renderer's words against the XML's.
    """
    import requests

    params = {"part": PART, "subpart": subpart}
    rendered = requests.get(f"{_RENDERER}/{date}/title-{TITLE}", params=params, timeout=120)
    rendered.raise_for_status()
    xml = requests.get(
        f"https://www.ecfr.gov/api/versioner/v1/full/{date}/title-{TITLE}.xml",
        params=params,
        timeout=120,
    )
    xml.raise_for_status()
    return rendered.content.decode("utf-8"), xml.content.decode("utf-8")


class HipaaPrivacyError(ValueError):
    """eCFR's text does not have the shape these catalogs are pinned to.
    Nothing is written."""


# ---- a small tree from the renderer's HTML ---------------------------------


@dataclass
class _Node:
    tag: str
    attrs: dict
    children: list = field(default_factory=list)
    parent: _Node | None = None

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def iter(self, tag: str):
        for child in self.children:
            if isinstance(child, _Node):
                if child.tag == tag:
                    yield child
                yield from child.iter(tag)


_VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "col", "area", "source"}


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self._open = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), parent=self._open)
        self._open.children.append(node)
        if tag not in _VOID:
            self._open = node

    def handle_endtag(self, tag):
        node = self._open
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self._open = node.parent

    def handle_data(self, data):
        self._open.children.append(data)


def _tree(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


# ---- sections and paragraphs -----------------------------------------------


@dataclass
class Paragraph:
    #: eCFR's own citation, or "" for text eCFR gives no id.
    pid: str
    text: str
    heading: str = ""
    term: bool = False


@dataclass
class Section:
    sid: str
    heading: str
    paragraphs: list[Paragraph] = field(default_factory=list)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _heading_of(p: _Node) -> str:
    for em in p.iter("em"):
        if "paragraph-heading" in (em.attrs.get("class") or ""):
            return _clean(em.text()).rstrip(".").rstrip("—").strip()
    return ""


def sections(html: str, first: int, last: int) -> list[Section]:
    """The renderer's sections `164.<first>`..`164.<last>`, in document order."""
    found = []
    for div in _tree(html).iter("div"):
        if "section" not in (div.attrs.get("class") or "").split():
            continue
        match = _SECTION_ID.match(div.attrs.get("id") or "")
        if not match or not first <= int(match.group(1)) <= last:
            continue
        heading = next((_clean(h.text()) for h in div.iter("h4")), "")
        section = Section(div.attrs["id"], heading)
        for p in div.iter("p"):
            owner = p.parent
            pid = ""
            if owner.tag == "div" and (owner.attrs.get("id") or "").startswith("p-"):
                first_p = next(owner.iter("p"), None)
                if first_p is p:
                    pid = owner.attrs["id"][2:]
            text = _clean(p.text())
            if not text or _SOURCE_NOTE.match(text):
                continue
            section.paragraphs.append(
                Paragraph(pid, text, _heading_of(p), term=p.attrs.get("data-term") == "true")
            )
        found.append(section)
    return found


def section_text(section: Section) -> str:
    """Every character of the section's rule text, whitespace removed: the
    unit the XML guard compares (see the module docstring)."""
    return re.sub(r"\s+", "", "".join(p.text for p in section.paragraphs))


def xml_section_texts(xml: str, first: int, last: int) -> dict[str, str]:
    """{section id: its text from the versioner XML, whitespace removed}.

    Read with patterns, as the Security loader reads the same file
    (`hipaa_loader._SECTION_RE`), not with an XML parser: the standard
    library's is open to entity expansion, and this project's SAST refuses
    it. The unit is every `<P>` in the section, tags removed and entities
    decoded.
    """
    import html

    texts = {}
    for match in _XML_SECTION.finditer(xml):
        number = int(match.group("id").split(".")[1])
        if not first <= number <= last:
            continue
        paragraphs = [
            html.unescape(re.sub(r"<[^>]+>", "", p))
            for p in _XML_PARAGRAPH.findall(match.group("body"))
        ]
        texts[match.group("id")] = re.sub(r"\s+", "", "".join(paragraphs))
    return texts


_XML_SECTION = re.compile(
    r'<DIV8 N="(?P<id>164\.\d+)" TYPE="SECTION"[^>]*>(?P<body>.*?)</DIV8>', re.DOTALL
)
_XML_PARAGRAPH = re.compile(r"<P>(.*?)</P>", re.DOTALL)


def require_text_agrees(found: list[Section], xml_texts: dict[str, str]) -> None:
    """Refuse unless the renderer and the XML hold the same sections with the
    same words (ba on #409: 27 of 27 at the pin; one changed word is caught)."""
    ids = [s.sid for s in found]
    if sorted(ids) != sorted(xml_texts):
        raise HipaaPrivacyError(
            f"The renderer and the XML hold different sections: renderer-only "
            f"{sorted(set(ids) - set(xml_texts))}, XML-only {sorted(set(xml_texts) - set(ids))}."
        )
    differ = [s.sid for s in found if section_text(s) != xml_texts[s.sid]]
    if differ:
        raise HipaaPrivacyError(
            f"eCFR's renderer and its XML disagree on the text of {differ}. Refusing: "
            "the renderer's ids would be attached to words the XML does not print."
        )


# ---- the catalog -------------------------------------------------------------

_LEADING_TOKEN = re.compile(r"^(?:\(\s*[^)\s]{1,6}\s*\)\s*)+")


def _body(paragraph: Paragraph) -> str:
    """The paragraph's text without its designation and heading."""
    text = _LEADING_TOKEN.sub("", paragraph.text, count=1)
    if paragraph.heading and text.startswith(paragraph.heading):
        text = text[len(paragraph.heading) :].lstrip(" .—").strip()
    return text


def to_controls(found: list[Section], framework: str, version: str) -> list[Control]:
    """One Control per section, its framing text as the statement, and one
    enhancement per paragraph eCFR gives an id, under that id.

    Text eCFR gives no id is kept with the paragraph before it (as eCFR
    renders it), or, before the first numbered paragraph, in the statement.
    A term-keyed definition and every paragraph under it is skipped.
    """
    controls = []
    for section in found:
        match = re.match(r"^§?\s*164\.\d+\s+(.*?)\.?$", section.heading)
        title = match.group(1) if match else section.heading
        control = Control(
            control_id=section.sid,
            title=title,
            framework=framework,
            framework_version=version,
            source_path=f"https://www.ecfr.gov/on/{version}/title-45/section-{section.sid}",
        )
        in_definition = False
        for paragraph in section.paragraphs:
            if paragraph.term:
                in_definition = True
                continue
            if paragraph.pid and not _PARAGRAPH_ID.match(paragraph.pid):
                in_definition = True  # a definition's own sub-paragraph
                continue
            if paragraph.pid:
                in_definition = False
                control.enhancements.append(
                    ControlEnhancement(
                        enhancement_id=paragraph.pid,
                        title=paragraph.heading,
                        baseline="",
                        description=_body(paragraph),
                    )
                )
            elif in_definition:
                continue
            elif control.enhancements:
                last = control.enhancements[-1]
                last.description = f"{last.description} {paragraph.text}".strip()
            else:
                control.control_statement = f"{control.control_statement} {paragraph.text}".strip()
        # **Nothing emitted is empty**, the Security loader's rule
        # (`_drop_empty_enhancements`), applied after the section is complete
        # for the same reason: a heading-only container (`164.512(c)`,
        # "Standard: Disclosures about victims of abuse...") delegates to its
        # children, and citing it cites a title. A citation to one still
        # resolves, to its section, through its descendants (#423).
        # `[Reserved]` paragraphs carry no rule either.
        control.enhancements = [
            e
            for e in control.enhancements
            if e.description.strip() and not _RESERVED.match(e.description)
        ]
        controls.append(control)
    return controls


_RESERVED = re.compile(r"^[\W\d()a-z-]*\[Reserved\]\W*$")


#: The two catalog directories under `data/frameworks/`.
BREACH_DIR = "hipaa-breach-notification-rule"
PRIVACY_DIR = "hipaa-privacy-rule"

#: (sections, paragraphs) at `PIN_DATE`, Definitions sections excluded (ba on
#: #409). Counted from eCFR's own ids, and the renderer's words agree with
#: the XML's in every section. Exact in both directions: fewer loses
#: requirements, more means the text changed; either is read by a person.
BREACH_EXTENT = (7, 31)
PRIVACY_EXTENT = (18, 863)


def require_extent(controls: list[Control], extent: tuple[int, int], name: str) -> None:
    found = (len(controls), sum(len(c.enhancements) for c in controls))
    if found != extent:
        raise HipaaPrivacyError(
            f"{name} parsed as {found[0]} sections and {found[1]} paragraphs, not the pinned "
            f"{extent[0]} and {extent[1]}. eCFR's text has changed; read it before re-pinning."
        )


def without_definitions(found: list[Section]) -> list[Section]:
    """Drop a section headed "Definitions", as the Security loader does: its
    terms are not requirements (§§164.402 and 164.501)."""
    return [s for s in found if not re.search(r"\bDefinitions\.?$", s.heading)]


# ---- what Purl v. HHS vacated (80's rulings 1 and 2 on #409) ------------------

#: The judgment, as the README and every warning name it.
JUDGMENT = (
    "Purl v. HHS, No. 2:24-cv-228-Z (N.D. Tex.), Amended Judgment (ECF 114), "
    "2025-07-03; appeal No. 25-10743 dismissed 2025-09-10"
)
#: The rule it vacated.
RULE = (
    "HIPAA Privacy Rule To Support Reproductive Health Care Privacy, "
    "89 FR 32976 (FR Doc. 2024-08503)"
)

#: Each amendment FR 2024-08503 makes to Subpart E, read from its amendatory
#: instructions, with the scope the instruction names. `subtree` is the
#: paragraph and everything under it; `self` the paragraph alone (a revised
#: "introductory text"); `statement` the section's unnumbered opening text;
#: `heading` only the heading of a paragraph whose body the rule left alone.
#: §164.520 is omitted except (b)(1)(ii)(F)-(H): the judgment keeps the rest.
ADDED = (
    ("164.502(a)(5)(iii)", "subtree"),
    ("164.509", "section"),
    ("164.512(c)(3)", "subtree"),
    ("164.520(b)(1)(ii)(F)", "subtree"),
    ("164.520(b)(1)(ii)(G)", "subtree"),
    ("164.520(b)(1)(ii)(H)", "subtree"),
    ("164.535", "section"),
)
REVISED = (
    ("164.502(a)(1)(vi)", "subtree"),
    ("164.502(g)(5)", "subtree"),
    ("164.512", "statement"),
    ("164.512(c)", "heading"),
    ("164.512(f)(1)(ii)(C)", "self"),
)
#: Pinned by two instruments on #409: eCFR at 2024-04-25 against the pin,
#: paired by citation and text, and the rule's amendatory instructions.
#: Counted in the SOURCE's units: every paragraph eCFR gives an id, plus a
#: section's unnumbered opening text. A re-ingest that finds any other
#: count refuses, so eCFR finally removing the text is a loud failure.
EXTENT_VACATED = 45
EXTENT_REVISED = 14


def section_id(pid: str) -> str:
    return pid.split("(")[0]


def _under(pid: str, root: str) -> bool:
    return pid == root or pid.startswith(root + "(")


def _units(section: Section, root: str, scope: str) -> list[tuple[str, str]]:
    """(unit id, text) for what `root`/`scope` covers in `section`. The
    statement's unit id is the section id."""
    numbered = [p for p in section.paragraphs if p.pid and not p.term]
    statement = " ".join(p.text for p in section.paragraphs if not p.pid and not p.term)
    if scope == "section":
        units = [(p.pid, p.text) for p in numbered]
        if not units or statement:
            units.insert(0, (section.sid, statement))
        return [u for u in units if u[1]]
    if scope == "statement":
        return [(section.sid, statement)] if statement else []
    if scope in ("self", "heading"):
        return [(p.pid, p.text) for p in numbered if p.pid == root]
    return [(p.pid, p.text) for p in numbered if _under(p.pid, root)]


def vacated_status(pin: list[Section], pre_rule: list[Section], *, pre_rule_sha256: str) -> dict:
    """The `vacated:` block for the Privacy Rule's `framework.yaml`.

    `added` ids are vacated and nothing binds in their place. `revised` ids
    carry the rule's vacated wording; the wording that binds again is the
    pre-rule text at their root, quoted from eCFR at `PRE_RULE_DATE` as an
    attributed annotation, never spliced into the catalog (80's ruling 1).
    """
    by_sid = {s.sid: s for s in pin}
    pre_by_sid = {s.sid: s for s in pre_rule}

    def section_of(root: str, table: dict) -> Section:
        sid = root.split("(")[0]
        if sid not in table:
            raise HipaaPrivacyError(
                f"{sid} is not in eCFR's text; the vacated set no longer applies."
            )
        return table[sid]

    added: list[str] = []
    for root, scope in ADDED:
        units = _units(section_of(root, by_sid), root, scope)
        if not units:
            raise HipaaPrivacyError(f"{root} ({scope}) matched nothing in eCFR's text.")
        added += [uid for uid, _ in units]
    revised: dict[str, dict] = {}
    pre_ids = {p.pid for s in pre_rule for p in s.paragraphs if p.pid}
    for root, scope in REVISED:
        units = _units(section_of(root, by_sid), root, scope)
        before = _units(section_of(root, pre_by_sid), root, scope)
        if not units or not before:
            raise HipaaPrivacyError(f"{root} ({scope}) matched nothing in one of the two texts.")
        revised[root] = {
            "scope": scope,
            "ids": [uid for uid, _ in units],
            # Ids under a revised root that eCFR's pre-rule text does not have
            # at all: the rule's own paragraphs. Generated as part of the
            # root's pre-rule entry, like the rest; but a citation to one
            # cites text that exists only because of the vacated rule, so
            # `check` warns on it (80, on #409).
            "added_ids": [
                uid for uid, _ in units if uid not in pre_ids and uid != section_id(root)
            ],
            "pre_rule_text": " ".join(text for _, text in before),
        }
    n_revised = sum(len(r["ids"]) for r in revised.values())
    if (len(added), n_revised) != (EXTENT_VACATED, EXTENT_REVISED):
        raise HipaaPrivacyError(
            f"The 2024 rule's text in eCFR is {len(added)} vacated and {n_revised} revised "
            f"unit(s), not the pinned {EXTENT_VACATED} and {EXTENT_REVISED}. eCFR has changed "
            "the text Purl v. HHS vacated; read the change before re-pinning."
        )
    return {
        "judgment": JUDGMENT,
        "rule": RULE,
        "note": (
            "This project's reading of the judgment, not legal advice. The catalog carries "
            "eCFR's text as eCFR prints it; these ids are not used for generation, and the "
            "revised ones are generated from the pre-rule wording quoted here."
        ),
        "pre_rule_source": {
            "url": renderer_url(PRE_RULE_DATE),
            "sha256": pre_rule_sha256,
        },
        "added": sorted(added),
        # Whole sections the rule added: a citation to the bare section, or to
        # any paragraph of it, cites vacated text.
        "sections": sorted(root for root, scope in ADDED if scope == "section"),
        "revised": revised,
    }
