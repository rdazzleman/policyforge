"""One input adapter: parse NIST 800-53 control notes out of a markdown
source using the specific frontmatter + heading shape this project started
from (an Obsidian vault) into the common Control schema.

This is one adapter among possible others, not a dependency — nothing about
this format is Obsidian-specific at runtime; it's just markdown with YAML
frontmatter and `[[wikilink]]`-style cross-references, which Obsidian
happens to be a convenient tool for authoring. Point this loader at any
directory of markdown files in the same shape (e.g. exported from a
different editor entirely) and it works identically. A different source
format (spreadsheet, OSCAL JSON, plain YAML) would get its own loader in
this package, implementing the same `list[Control]` output contract.

NIST 800-53 control text is a US federal government work — public domain —
so this loader is safe to point at bundled, redistributable output. It is
NOT safe to point at a vault's HITRUST or GovRAMP control notes and bundle
the result; see README's licensing table. That's why `parse_control_file`
strips non-public-domain crosswalk columns by default (see
`keep_crosswalk_columns`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from .schema import Control, ControlEnhancement

# Columns in the "Cross-Framework Mappings" table that are safe to retain in
# bundled/public output by default. FedRAMP mappings for NIST 800-53 are
# themselves published as part of a federal program (public domain).
# HITRUST / GovRAMP / ISO / PCI-DSS columns are NOT included by default —
# even ID-level correspondence drawn from a licensed crosswalk table is a
# grey area worth avoiding until confirmed. Override with
# `keep_crosswalk_columns` if you've verified it's safe for your use case.
_DEFAULT_SAFE_CROSSWALK_COLUMNS = {"fedramp"}

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_ENHANCEMENT_ROW_RE = re.compile(
    r"^\|\s*\*{0,2}(?P<id>[A-Z]{2}-\d+\(\d+\))\*{0,2}\s*\|\s*"
    r"(?P<title>[^|]*)\|\s*(?P<baseline>[^|]*)\|\s*(?P<desc>[^|]*)\|\s*$",
    re.MULTILINE,
)
_CROSSWALK_ROW_RE = re.compile(
    r"^\|\s*(?P<framework>[^|]+?)\s*\|\s*(?P<equiv>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)
_RELATED_ID_RE = re.compile(r"\[\[([A-Z]{2}-\d+)\]\]")


def _sections(body: str) -> dict[str, str]:
    """Split the markdown body into {heading: content} on '## ' headings."""
    matches = list(_SECTION_RE.finditer(body))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        heading = m.group(1).strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        # Each section in this vault format is followed by a "---" horizontal
        # rule before the next heading; strip it so it doesn't leak into the
        # captured text.
        content = re.sub(r"\n?-{3,}\s*$", "", content).strip()
        sections[heading] = content
    return sections


def _parse_enhancements(text: str) -> list[ControlEnhancement]:
    out = []
    for m in _ENHANCEMENT_ROW_RE.finditer(text):
        eid = m.group("id").strip()
        title = m.group("title").strip()
        if not title or title.lower() == "title":
            continue  # header row
        out.append(
            ControlEnhancement(
                enhancement_id=eid,
                title=title,
                baseline=m.group("baseline").strip(),
                description=m.group("desc").strip(),
            )
        )
    return out


def _parse_crosswalk(text: str, keep_columns: set[str]) -> dict[str, str]:
    out = {}
    for m in _CROSSWALK_ROW_RE.finditer(text):
        framework = m.group("framework").strip()
        equiv = m.group("equiv").strip()
        key = framework.lower()
        if key in ("framework", "---", "") or set(framework) <= {"-"}:
            continue
        # normalize keys like "HITRUST CSF" -> "hitrust", "FedRAMP" -> "fedramp".
        # Through the shared rule rather than by first word here: this was a
        # private copy, and a private copy of a naming rule is how the NIST
        # family came to share one key. `fedramp` — the only column kept by
        # default — normalizes identically either way, so nothing moves today.
        from policyforge.mapping.crosswalk import normalize_framework

        norm = normalize_framework(key)
        if norm in keep_columns and equiv:
            out[norm] = equiv
    return out


def parse_control_file(
    path: Path,
    *,
    keep_crosswalk_columns: set[str] = _DEFAULT_SAFE_CROSSWALK_COLUMNS,
) -> Control:
    post = frontmatter.load(path)
    fm = post.metadata
    body = post.content
    sections = _sections(body)

    related = []
    if "related controls" in sections:
        related = _RELATED_ID_RE.findall(sections["related controls"])

    enhancements = []
    if "control enhancements" in sections:
        enhancements = _parse_enhancements(sections["control enhancements"])

    crosswalk = {}
    if "cross-framework mappings" in sections:
        crosswalk = _parse_crosswalk(sections["cross-framework mappings"], keep_crosswalk_columns)

    control_statement = sections.get("control statement", "").strip("> \n")
    discussion = sections.get("discussion", "")

    _require_some_control_content(
        path,
        title=fm.get("title", ""),
        control_statement=control_statement,
        discussion=discussion,
        enhancements=enhancements,
    )

    return Control(
        control_id=fm.get("control_id", path.stem),
        title=fm.get("title", ""),
        framework=fm.get("framework", "NIST 800-53"),
        framework_version=fm.get("version", "Rev 5"),
        family=fm.get("family"),
        family_abbr=fm.get("family_abbr"),
        baseline=fm.get("baseline"),
        control_statement=control_statement,
        discussion=discussion,
        enhancements=enhancements,
        related_controls=related,
        source_crosswalk=crosswalk,
        source_path=str(path),
    )


def _require_some_control_content(
    path: Path,
    *,
    title: str,
    control_statement: str,
    discussion: str,
    enhancements: list,
) -> None:
    """Refuse a note that carries nothing a control could be built from.

    **Every field in `parse_control_file` has a default, so before this
    the function could not fail.** An empty `.md` file produced a valid
    `Control` whose ID was the *filename*, with an empty title and an
    empty statement, and `load_vault_controls`'s broad `except` had
    nothing to catch — its comment promised a reported failure on a path
    that was never taken. `etl-vault` then printed `Parsed 7 controls`
    and exited 0 for five real notes and two empty files. See #220.

    **Emptiness, never thinness.** The refusal is that *all four* carry
    nothing; any one of them is enough to pass. A length or
    well-formedness threshold would be wrong, and this is measured rather
    than assumed: the thinnest control statement in the four catalogs
    this project ships is ten characters — HIPAA § 164.308(a)(5)(ii)
    reads exactly `Implement:`, a real control whose substance lives in
    its children. A guard that refuses emptiness tends to refuse thinness
    too, and thin-but-real is common.

    **The gap this leaves, stated rather than left to be discovered.** A
    note with a title and no control statement still parses. That is a
    weaker malformation — the note says *something* — and refusing it
    would rest on an inference this function cannot support: no control
    in the shipped catalogs has an empty statement, but those catalogs
    come from OSCAL and the eCFR, not from a vault, so they are evidence
    about a different parser. If vault notes do lose statements in
    practice, that is a second finding with its own measurement, not an
    argument for widening this one today.
    """
    if title.strip() or control_statement.strip() or discussion.strip() or enhancements:
        return
    raise ValueError(
        "carries no title, control statement, discussion or enhancements — "
        "an empty or unparseable note, not a control"
    )


@dataclass
class VaultLoadReport:
    """What `load_vault_controls` **attempted**, not only what it produced.

    The old signature returned `list[Control]`, so ten notes parsed and
    twenty notes with ten failures were the same value. The failures went
    to stdout as `WARN:` lines that nothing read and no exit code
    reflected.

    **A returned count is the fix, not a louder warning.** `mdformat
    --check` with no paths exits 0 saying *"No files have been passed
    in"* — the same defect one layer out, found by 1d on #221: the
    absence of input and the absence of a problem sharing one value. A
    caller that cannot tell them apart reports the wrong one however
    loud the log is.
    """

    controls: list[Control] = field(default_factory=list)
    #: (path, why) for every note that did not become a control.
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    #: Every *.md file found, whatever became of it.
    attempted: int = 0


def load_vault_controls(
    controls_dir: Path,
    *,
    keep_crosswalk_columns: set[str] = _DEFAULT_SAFE_CROSSWALK_COLUMNS,
) -> VaultLoadReport:
    """Parse every *.md control note in a directory (non-recursive).

    Returns a `VaultLoadReport` rather than a bare list — see that class
    for why what was *attempted* is part of the answer.
    """
    report = VaultLoadReport()
    for path in sorted(controls_dir.glob("*.md")):
        report.attempted += 1
        try:
            report.controls.append(
                parse_control_file(path, keep_crosswalk_columns=keep_crosswalk_columns)
            )
        # Deliberately broad: one malformed note must not abort the whole
        # batch, so the failure is reported and parsing continues.
        except Exception as exc:  # noqa: BLE001
            report.unreadable.append((str(path), str(exc)))
            print(f"WARN: failed to parse {path}: {exc}")
    _require_every_note_accounted(report)
    return report


def _require_every_note_accounted(report: VaultLoadReport) -> None:
    """Every note found became a control or was reported unreadable.

    The same conservation shape as `hipaa_crosswalk_loader`'s
    `_require_nothing_dropped`, and here for the same reason: `unreadable`
    is empty for a healthy vault, so a test asserting it is empty stays
    green if the line that appends to it is deleted. This one cannot —
    `attempted` is incremented before the `try`, independently of what
    the body then does with the note.
    """
    accounted = len(report.controls) + len(report.unreadable)
    if accounted != report.attempted:
        raise ValueError(
            f"{report.attempted} control note(s) were found; {len(report.controls)} "
            f"parsed and {len(report.unreadable)} were reported unreadable, "
            f"accounting for {accounted}"
        )
