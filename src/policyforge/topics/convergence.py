"""The convergence table: which areas a security programme's reference
structures agree on (22, part 3; 5b's research in 5921, adopted by 80 in 5925
and 5927).

**What it is for.** The table chooses *default topics* for `policyforge init`.
No document ever cites it: a citation converts an organisational choice into a
claim about its source, and a count of how many structures name an area is not
a claim any of them makes.

**What each cell carries.** Its value (the structure's own name for the area),
whether the match is partial (parentheses in 5921), the source it was read
from, and whether that reading was verified. 5921's "Limits" section is the
record of what could be: the 800-53 families and CSF 2.0 categories were read
from the shipped catalogs; CIS v8.1 from the controls list; the HITRUST domain
names from a vendor page and the ISO/IEC 27002:2022 5.1 topic list from general
knowledge (paywalled), both **unverified**. The scoring below uses the
unverified columns as they are, and `UNVERIFIED` says which they are, so a
reader can see how much of a verdict rests on them.

**The bar** (5925): an area is core when at least 3 of the 5 structures name
it at top level. A full match counts 1 and a partial match one half (80's
ruling on 22, comment 6445), so `bar = 3.0`.
"""

from __future__ import annotations

from dataclasses import dataclass

FRAMEWORKS = ("800-53", "csf", "hitrust", "cis", "iso")

#: Where each column was read from, and whether that reading was verified
#: (5921, "Limits"). Cell-level `verified` follows its column.
SOURCES: dict[str, tuple[str, bool]] = {
    "800-53": ("NIST SP 800-53 Rev 5 control families, from the shipped catalog (6c8ff8d6)", True),
    "csf": ("NIST CSF 2.0 categories (CSWP 29), from the shipped catalog (6c8ff8d6)", True),
    "cis": ("CIS Controls v8.1 list, cisecurity.org/controls/cis-controls-list", True),
    "hitrust": ("HITRUST's 19 assessment domains, a vendor page (barradvisory.com)", False),
    "iso": (
        "ISO/IEC 27002:2022 clause 5.1 topic-specific policy examples, general knowledge",
        False,
    ),
}
UNVERIFIED = tuple(f for f, (_, ok) in SOURCES.items() if not ok)

BAR = 3.0
CEILING = 25
FULL, PARTIAL = 1.0, 0.5


@dataclass(frozen=True)
class Cell:
    """One structure's name for an area, or nothing."""

    text: str = ""
    partial: bool = False

    @property
    def named(self) -> bool:
        return bool(self.text)

    @property
    def weight(self) -> float:
        if not self.named:
            return 0.0
        return PARTIAL if self.partial else FULL


@dataclass(frozen=True)
class Area:
    """One row of the table, and the topics that implement it."""

    id: str
    name: str
    cells: dict[str, Cell]
    #: The pool topics (names in `topics.example.yaml`) that implement the
    #: area when it is on, in the order they are listed.
    topics: tuple[str, ...]
    #: The fact that turns a conditional area on (a `ScopeFacts` property),
    #: or "" for an area the bar decides.
    condition: str = ""
    #: Where this area's topics fold when the ceiling forces a merge, or when
    #: the area is below the bar: the topic that takes their anchors, and
    #: keeps accountability (5927).
    merge_into: str = ""

    @property
    def score(self) -> float:
        """How many structures name it, partial matches half."""
        return sum(cell.weight for cell in self.cells.values())

    @property
    def meets_bar(self) -> bool:
        return self.score >= BAR

    def unverified_cells(self) -> list[str]:
        """The structures this area's score counts that were not verified."""
        return [f for f in UNVERIFIED if self.cells[f].named]


def _row(row: str, *, partial: tuple[str, ...] = ()) -> dict[str, Cell]:
    """A table row from 'a | b | c | d | e': one text per structure, `-` for
    none. A structure listed in `partial` is a parenthesised match."""
    parts = [p.strip() for p in row.split("|")]
    if len(parts) != len(FRAMEWORKS):
        raise ValueError(f"a table row has one cell per structure: {row!r}")
    return {
        f: Cell("" if text == "-" else text, partial=f in partial)
        for f, text in zip(FRAMEWORKS, parts, strict=True)
    }


# ---- the table, 5921, in its order (ties in a merge go to the later row) ----------

AREAS: tuple[Area, ...] = (
    Area(
        "access",
        "Access control and identity",
        _row("AC, IA | PR.AA | Access Control; Password Mgmt | 5, 6 | yes"),
        ("Identity Lifecycle & Access Review", "Authentication & Credential Management"),
    ),
    Area(
        "network",
        "Network security",
        _row("SC | PR.IR | Network; Transmission; Wireless | 12, 13 | yes"),
        ("Network & Boundary Protection",),
    ),
    Area(
        "incident",
        "Incident response",
        _row("IR | RS.*, RC.* | Incident Mgmt | 17 | yes"),
        ("Incident Response",),
    ),
    Area(
        "config",
        "Configuration and change",
        _row("CM | PR.PS | Configuration Mgmt | 4 | yes"),
        ("Change & Configuration Management",),
    ),
    Area(
        "backup",
        "Backup and continuity",
        _row("CP | PR.IR, RC.RP | BC/DR | 11 | yes"),
        ("Backup, Restore & Contingency",),
    ),
    Area(
        "data_protection",
        "Data protection and classification",
        _row("PT | PR.DS | Data Protection & Privacy | 3 | yes"),
        ("Data Protection",),
    ),
    Area(
        "logging",
        "Logging and monitoring",
        _row("AU | DE.CM, DE.AE | Audit Logging & Monitoring | 8 | -"),
        ("Logging, Monitoring & Alerting",),
    ),
    Area(
        "endpoint",
        "Endpoint and malware",
        _row("- | PR.PS | Endpoint; Mobile Device | 9, 10 | yes"),
        ("Endpoint Protection & Device Controls",),
    ),
    Area(
        "supplier",
        "Supplier and third-party risk",
        _row("SR | GV.SC | Third-Party Assurance | 15 | -"),
        ("Business Associate & Vendor Risk",),
    ),
    Area(
        "awareness",
        "Awareness and training",
        _row("AT | PR.AT | Education, Training & Awareness | 14 | -"),
        ("Workforce Security Training",),
    ),
    Area(
        "governance",
        "Governance and programme",
        _row(
            "PL, PM | GV.OC/RR/PO/OV | Information Protection Program | - | the top policy",
            partial=("iso",),
        ),
        ("Security Program Governance",),
    ),
    Area(
        "risk",
        "Risk management",
        _row("RA, CA | GV.RM, ID.RA | Risk Management | - | -"),
        ("Risk Assessment & Authorization",),
    ),
    Area("asset", "Asset management", _row("- | ID.AM | - | 1, 2 | yes"), ("Asset Management",)),
    Area(
        "vulnerability",
        "Vulnerability management",
        _row("- | ID.RA | Vulnerability Mgmt | 7 | yes", partial=("csf",)),
        ("Vulnerability & Patch Management",),
    ),
    Area(
        "cryptography",
        "Cryptography",
        _row("- | PR.DS | Transmission | - | yes", partial=("csf", "hitrust")),
        ("Encryption & Key Management",),
        merge_into="Data Protection",
    ),
    Area(
        "secure_development",
        "Secure development",
        _row("SA | PR.PS | - | 16 | yes", partial=("csf",)),
        ("Secure Development Lifecycle",),
        condition="builds_software",
        merge_into="Change & Configuration Management",
    ),
    Area(
        "physical",
        "Physical",
        _row("PE | - | Physical Environment | - | yes"),
        ("Physical & Environmental Security",),
        condition="own_facilities",
        merge_into="Media Handling & Disposal",
    ),
    Area(
        "media",
        "Media handling",
        _row("MP | PR.DS | Portable Media | - | handling", partial=("csf", "iso")),
        ("Media Handling & Disposal",),
    ),
    Area(
        "personnel",
        "Personnel security",
        _row("PS | GV.RR | - | - | -", partial=("csf",)),
        ("Personnel Security",),
        condition="has_assessed_baseline",
        merge_into="Workforce Security Training",
    ),
    Area(
        "maintenance",
        "Maintenance",
        _row("MA | - | - | - | -"),
        ("System Maintenance",),
        condition="has_assessed_baseline",
        merge_into="Change & Configuration Management",
    ),
    Area(
        "remote_access",
        "Remote access",
        _row("AC-17 | - | Wireless | - | -", partial=("800-53", "hitrust")),
        ("Remote & Third-Party Access",),
        merge_into="Identity Lifecycle & Access Review",
    ),
    # Not rows of 5921's table: 5925 splits Privacy out of Data Protection when
    # the organisation handles PII or PHI, and the PHI rules add their two
    # topics. Scored as the data-protection row they come from (n = 5).
    Area(
        "privacy",
        "Privacy",
        _row("PT | PR.DS | Data Protection & Privacy | 3 | yes"),
        ("Privacy",),
        condition="handles_personal_data",
        merge_into="Data Protection",
    ),
    Area(
        "phi_rules",
        "PHI rules (HIPAA Privacy and Breach Notification)",
        _row("PT | PR.DS | Data Protection & Privacy | 3 | yes"),
        ("HIPAA Privacy Practices", "Breach Notification"),
        condition="handles_phi",
        merge_into="Data Protection",
    ),
)

BY_ID = {a.id: a for a in AREAS}


@dataclass(frozen=True)
class AIBlock:
    """The AI topics by the organisation's role (5925, rule 4)."""

    #: ISO/IEC 42001 Annex A and the AI RMF functions are the sources; the
    #: Annex A titles are unverified table data and are never cited.
    user: tuple[str, ...] = (
        "AI Governance & Accountability",
        "Responsible Use of AI",
        "Third-Party AI & Data Supply Chain",
    )
    builder: tuple[str, ...] = (
        "AI Governance & Accountability",
        "AI System Inventory & Impact Assessment",
        "AI Evaluation & Measurement",
        "AI Risk Treatment & Incident Response",
        "Third-Party AI & Data Supply Chain",
    )
    #: Optional on top of a builder's five; the first to go at the ceiling.
    extras: tuple[str, ...] = ("Data for AI Systems", "Transparency to Affected People")


AI = AIBlock()

__all__ = [
    "AI", "AREAS", "BAR", "BY_ID", "CEILING", "FRAMEWORKS", "SOURCES", "UNVERIFIED",
    "Area", "Cell",
]  # fmt: skip
