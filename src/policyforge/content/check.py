"""The gate a pull request has to pass before anything reaches the wiki.

Everything here is local and offline. That is the point: these are the
mistakes worth catching *before* a publish, and a check that needed
credentials could not run on a fork's pull request, which is exactly where
you want it running.

The failures it looks for are the ones that are invisible in review. A
reviewer reading a diff sees the prose change and not that two files now
claim the same Confluence page, that a link points at a file somebody
renamed, or that a rewrite dropped the `[NIST AC-2 | HIPAA 164.308(a)(3)(i)]`
tag that was the document's only traceability back to the control it
implements. Each of those survives review comfortably and then fails at
publish time, or worse, does not fail at all.

Errors and warnings are separated because they need different answers. Two
documents pointing at one page will publish one over the other and lose
work, so it stops the build. A document with no declared owner is a gap
worth seeing on every run and not worth blocking a merge over — a repo
mid-migration is full of them, and a gate that cannot be satisfied gets
switched off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .deontic import (
    NONE,
    playbook_obligations,
    playbook_tagged_headings,
    unresolved_playbook_parts,
    weakened_citations,
)
from .tags import source_tags
from .tree import LEGACY_TIER_DIRS, ContentDocument, load_content_tree

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    """One problem, and where to go and look at it."""

    path: str
    message: str
    severity: str = ERROR

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class CheckReport:
    documents: int = 0
    findings: list[Finding] = field(default_factory=list)
    #: Which model wrote which document, where the document says so. Not a
    #: finding in either direction: a stamp is not a problem, and its
    #: absence is not either — a hand-written document has none, and so does
    #: one pulled back from Confluence, which cannot carry frontmatter
    #: through storage format. Reported because "which documents did that
    #: model touch" is the question asked after a model is found to be
    #: weakening requirements, and it should be answerable from a checkout.
    attribution: str = ""

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def format_report(self) -> str:
        lines = [f"Checked {self.documents} document(s)."]
        if self.errors:
            lines += ["", f"{len(self.errors)} error(s):"]
            lines += [f"  {finding}" for finding in self.errors]
        if self.warnings:
            lines += ["", f"{len(self.warnings)} warning(s):"]
            lines += [f"  {finding}" for finding in self.warnings]
        if self.ok and not self.warnings:
            lines.append("Nothing to report.")
        elif self.ok:
            lines += ["", "No errors — safe to publish."]
        if self.attribution:
            lines += ["", self.attribution]
        return "\n".join(lines)


def _check_page_claims(documents: list[ContentDocument]) -> list[Finding]:
    """No two files may publish to the same page.

    Left alone this is silent data loss: both documents publish, the second
    overwrites the first, and the repo still contains two files that each
    look like the source of truth for that page. Whichever one happened to
    run second wins, which is not a rule anybody chose.
    """
    claims: dict[tuple[str, str, str], str] = {}
    findings: list[Finding] = []
    for doc in documents:
        # Keyed by kind as well as by place and title, because a page is only
        # the same page within one store: `SEC/Access Review` in Confluence
        # and `Access Review` on a wiki are two destinations, and a tree
        # publishing to both would otherwise report a collision that is not
        # one. Within a kind the rule is unchanged.
        for kind, target in (doc.targets or {}).items():
            if not isinstance(target, dict):
                continue
            where = str(target.get("space") or target.get("repository") or "")
            title = str(target.get("title") or doc.title or "")
            if not title:
                continue
            # A wiki block may name no repository, taking it from config; two
            # such files still collide with each other, so they key on the
            # empty location together rather than being skipped.
            if kind == "confluence" and not where:
                continue
            key = (kind, where.lower(), title.lower())
            first = claims.get(key)
            if first is not None:
                place = f"{where}/{title!r}" if where else f"{title!r}"
                findings.append(
                    Finding(
                        doc.relative_path,
                        f"publishes to {place}, which {first} already claims — one of "
                        "them would silently overwrite the other",
                    )
                )
                continue
            claims[key] = doc.relative_path
    return findings


def _check_references(documents: list[ContentDocument], root: Path) -> list[Finding]:
    """Every local link must point at a file that exists.

    A renamed document leaves working prose behind a broken link, and in a
    governance set the links are load-bearing: a Standard pointing at its
    Procedure is how a reader gets from the requirement to the steps.
    """
    findings: list[Finding] = []
    for doc in documents:
        for reference in doc.references:
            # Wikilinks name a title rather than a path; resolve those
            # against the set of document titles instead of the filesystem.
            if reference.endswith(".md"):
                target = (doc.path.parent / reference).resolve()
                if not target.exists():
                    findings.append(
                        Finding(doc.relative_path, f"links to {reference!r}, which does not exist")
                    )
            elif not any(
                other.title.lower() == reference.lower() or other.slug.lower() == reference.lower()
                for other in documents
            ):
                findings.append(
                    Finding(
                        doc.relative_path,
                        f"links to [[{reference}]], which matches no document",
                        WARNING,
                    )
                )
    return findings


def _check_target_alias(documents: list[ContentDocument]) -> list[Finding]:
    """`confluence:` at the top level and `targets.confluence` must agree.

    The top-level block is the older spelling and is read as the alias of
    the newer one. A file carrying both with different contents would
    publish to whichever the reader happened to prefer, which is a page
    nobody chose; reported rather than resolved.
    """
    findings: list[Finding] = []
    for doc in documents:
        legacy = doc.metadata.get("confluence")
        general = doc.metadata.get("targets")
        if not isinstance(legacy, dict) or not isinstance(general, dict):
            continue
        inner = general.get("confluence")
        if isinstance(inner, dict) and inner != legacy:
            findings.append(
                Finding(
                    doc.relative_path,
                    "declares `confluence:` and `targets.confluence:` with different "
                    "contents — keep one, or make them agree",
                )
            )
    return findings


def _check_publishable(documents: list[ContentDocument]) -> list[Finding]:
    findings: list[Finding] = []
    for doc in documents:
        block = doc.confluence
        if block and not doc.space:
            findings.append(
                Finding(
                    doc.relative_path,
                    "declares a `confluence:` block with no `space:`, so it cannot be "
                    "published anywhere",
                )
            )
        if not doc.tier:
            legacy = next(
                (
                    part
                    for part in PurePosixPath(doc.relative_path).parts[:-1]
                    if part.lower() in LEGACY_TIER_DIRS
                ),
                None,
            )
            findings.append(
                Finding(
                    doc.relative_path,
                    # A directory an earlier `generate` wrote (#411): say where
                    # it belongs, not only that it is unrecognised.
                    f"is under {legacy}/, where `generate` wrote Policies before #411 — "
                    f"move it to {LEGACY_TIER_DIRS[legacy.lower()]}/"
                    if legacy
                    else "has no tier — put it under policies/, standards/ or procedures/, "
                    "or set `tier:` in its frontmatter",
                    WARNING,
                )
            )
        if not doc.owner:
            findings.append(
                Finding(
                    doc.relative_path,
                    "has no owner, so an answer drawn from it cannot say who is accountable",
                    WARNING,
                )
            )
    return findings


def _check_citations(documents: list[ContentDocument], synthesis_dir: Path) -> list[Finding]:
    """Framework tags in the synthesis must survive into the document.

    The generated document is prose written from a synthesis; the synthesis
    is the requirement list with its provenance attached. A tag that exists
    in one and not the other means the traceability an assessor needs was
    dropped somewhere between them, which is a compliance defect rather than
    a formatting one — and it is invisible in a diff of the prose.
    """
    from policyforge.content.tags import source_tags

    if not synthesis_dir.exists():
        return []

    findings: list[Finding] = []
    for doc in documents:
        # A Policy drops framework/control citations by design: "that
        # traceability lives in the Standard" (README; `policy_writer`). Once
        # `check` could read a generated Policy as one (#411), this rule was
        # still warning on the citations it deliberately leaves out.
        if doc.tier == "policy":
            continue
        source = synthesis_dir / f"{doc.slug}.md"
        if not source.exists():
            continue
        expected = set(source_tags(source.read_text(encoding="utf-8")))
        present = set(source_tags(doc.body))
        missing = sorted(expected - present)
        if missing:
            shown = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
            findings.append(
                Finding(
                    doc.relative_path,
                    f"is missing {len(missing)} citation(s) its synthesis carries: {shown}",
                    WARNING,
                )
            )
    return findings


#: Tiers whose sentences are supposed to bind. A Policy states principles
#: and may legitimately say "should"; a Standard is what an assessor asks
#: for evidence against, and a Procedure is a set of steps somebody
#: follows.
_BINDING_TIERS = frozenset({"standard", "procedure"})


def _check_uncited(documents: list[ContentDocument]) -> list[Finding]:
    """A Standard or Procedure that cites no framework requirement at all.

    The tag is the whole traceability story: it is how a document says
    which control it answers for, and how `satisfies`, `drift` and the
    edit path find it. A Standard with no tag anywhere is not a document
    with weak traceability, it is a document with none — and the two ways
    that happens are both worth seeing. Either the generation was cut off
    part-way, or the citations were written and then lost in an edit.

    **Tier-scoped, and the scoping is the whole check.** A Policy states
    what the organization intends and cites nothing by design: in the
    bundled 20-topic starter set all nineteen Policies carry no tag, so an
    unscoped version of this rule would report nineteen documents that are
    exactly as they should be, and a check that is wrong nineteen times out
    of twenty-one gets switched off in a week. Scoped to Standards and
    Procedures it reports two documents in that same set, and both are real:
    one Standard truncated mid-sentence at 1,066 bytes, and one full-length
    Procedure whose tags are simply absent. Found by policyforge-b5 while
    reconciling the citation measurement.

    A warning rather than an error, for the reason the tier and owner rules
    are warnings: a hand-written Standard that has not been mapped to a
    framework yet is a normal thing to have in a tree mid-migration, and a
    gate that cannot be satisfied gets turned off rather than fixed.
    """
    findings: list[Finding] = []
    for doc in documents:
        if doc.tier not in _BINDING_TIERS or source_tags(doc.body):
            continue
        findings.append(
            Finding(
                doc.relative_path,
                f"is a {doc.tier} that cites no framework requirement, so nothing "
                "traces it back to a control — check whether it was cut short or "
                "whether its citations were dropped",
                WARNING,
            )
        )
    return findings


def _check_requirement_strength(documents: list[ContentDocument]) -> list[Finding]:
    """A cited requirement that does not bind.

    Warnings rather than errors: the source controls are written in
    obligation language, so rendering one as "teams should consider" or as
    a bare statement of fact promises less than the framework the citation
    claims to satisfy — but whether that is wrong is a judgement about the
    document, not a breach of a rule, and blocking a publish over it would
    be the wrong trade. See `content/deontic.py` for why only *cited*
    sentences are reported.

    **In a Procedure, a cited step with no modality is not reported** (80's
    ruling on #412, after #363): "Security Operations distributes the plan"
    carries out an obligation the Standard above it states, which is how a
    step is meant to read. **A step that says "should" or "may" still is**,
    in a Procedure as in a Standard: it weakens a mandated requirement. The
    same sentence with no modality in a Standard is still reported too.
    """
    findings: list[Finding] = []
    for doc in documents:
        if doc.tier not in _BINDING_TIERS:
            continue
        for statement in weakened_citations(doc.body):
            # A Procedure step is an instruction carrying out an obligation
            # the Standard above it states (#363): "Security Operations
            # distributes the plan" is how a step is meant to read, so a
            # cited step with no modality is not "a requirement stated as
            # fact" there (80's ruling on #412; 266 such warnings over the
            # 45 generated Procedures). A step weakened to "should" or "may"
            # is still reported, in every tier.
            if doc.tier == "procedure" and statement.modality == NONE:
                continue
            how = (
                f"as a {statement.modality}"
                if statement.modality != NONE
                else "as a statement of fact, with no obligation"
            )
            findings.append(
                Finding(
                    doc.relative_path,
                    f"line {statement.line}: "
                    + ("heading " if statement.heading else "")
                    + f"states a cited requirement {how} "
                    f'— "{statement.text[:70]}"',
                    WARNING,
                )
            )
    return findings


def _check_playbook_obligations(
    documents: list[ContentDocument], org_actors: tuple[str, ...] = ()
) -> list[Finding]:
    """A sentence citing only the NIST AI RMF Playbook, not framed as NIST's (#300).

    **An error, not a warning** (80's ruling). The sibling checks warn so that
    a tree mid-migration is not blocked, but this one cannot be tripped by an
    unmapped document: only by one that cites the Playbook and turns its
    voluntary suggestion into an obligation -- asserting what NIST withheld,
    which the project's own ruling forbids. Every tier, since the claim is
    wrong wherever it is made.
    """
    findings: list[Finding] = []
    for doc in documents:
        for statement in playbook_obligations(doc.body, org_actors):
            findings.append(
                Finding(
                    doc.relative_path,
                    f"line {statement.line}: cites the NIST AI RMF Playbook, which is "
                    "voluntary, but is not framed as NIST's -- make NIST or the Playbook its "
                    'subject ("NIST suggests ..."), never "NIST requires", '
                    "and state any requirement the organization adopts in its own sentence "
                    f'without the Playbook tag — "{statement.text[:70]}"',
                    ERROR,
                )
            )
        for line, heading in playbook_tagged_headings(doc.body):
            findings.append(
                Finding(
                    doc.relative_path,
                    f"line {line}: a heading cites the NIST AI RMF Playbook, which makes every "
                    "step beneath it read as NIST's instruction -- put the Playbook citation "
                    f'on a "NIST suggests ..." sentence inside the section — "{heading[:70]}"',
                    ERROR,
                )
            )
        # An invented shorthand id, on its own terms (#340, 80's ruling (a)).
        for line, citation in unresolved_playbook_parts(doc.body):
            findings.append(
                Finding(
                    doc.relative_path,
                    f"line {line}: `{citation}` -- no such NIST AI RMF Playbook subcategory "
                    "or action. A part of a Playbook tag that names no framework on disk "
                    "inherits the Playbook, so its id must be one the Playbook publishes; if "
                    "it cites another framework, name it in full, or add that catalog.",
                    ERROR,
                )
            )
    return findings


def _check_unanchored(documents: list[ContentDocument]) -> list[Finding]:
    """An obligation that binds while citing nothing, beside ones that cite.

    **A fact, and the half of #196 that may reach an exit code.** No model is
    consulted: either the sentence carries a tag or it does not, and the
    answer is the same twice. The companion judgement — whether a *cited*
    obligation is actually carried by the requirements it cites — is a
    model's opinion and is deliberately **not** a `Finding`, because
    `--strict` promotes warnings to a failing exit and that would put a
    verdict which can differ between runs behind the same number as a
    malformed document.

    Scoped by section rather than by document: a Standard's own enforcement
    clause binds, cites nothing, and is correct. See
    `content/grounding.unanchored` for why that scoping is the whole check.

    A warning rather than an error, matching `_check_uncited` and
    `_check_requirement_strength`: a tree mid-migration legitimately has
    documents whose citations are not all in place yet, and a gate that
    cannot be satisfied gets turned off rather than fixed.
    """
    from .grounding import unanchored

    findings: list[Finding] = []
    for doc in documents:
        if doc.tier not in _BINDING_TIERS:
            continue
        for finding in unanchored(doc.body):
            findings.append(
                Finding(
                    doc.relative_path,
                    f"line {finding.claim.line}: "
                    + (
                        "heading states an obligation but cites nothing"
                        if finding.claim.heading
                        else "binds but cites nothing"
                    )
                    + ", in a section "
                    f'that cites — "{finding.claim.text[:70]}"',
                    WARNING,
                )
            )
    return findings


def _check_vacated_citations(documents: list[ContentDocument], vacated=None) -> list[Finding]:
    """A citation to text a court vacated that the catalog's source still
    prints (80's ruling on #409), named with the judgment.

    **Warns on text that exists only because of the vacated rule** (80, on
    #409): the `vacated` kind, and a `vacated-revision` id the pre-rule text
    does not have at all (`164.502(g)(5)(i)(A)(1)`), which is pointed at its
    root's pre-rule wording. Any other `vacated-revision` citation is VALID
    and is not flagged: the paragraph binds, in its pre-rule wording, which
    the catalog quotes and generation uses.

    A warning, not an error: this is the project's reading of the judgment,
    not legal advice, and a document may cite vacated text on purpose, to say
    it no longer applies.
    """
    from policyforge.topics.satisfies import parse_citations

    if vacated is None:
        from policyforge.frameworks.registry import config_or_defaults
        from policyforge.frameworks.vacated import declared_vacated

        vacated = declared_vacated(config_or_defaults("vacated paragraphs were read"))
    if not vacated:
        return []
    from policyforge.frameworks.vacated import VACATED
    from policyforge.mapping.crosswalk import normalize_framework

    findings: list[Finding] = []
    for doc in documents:
        for framework, requirement_id, _, section in parse_citations(doc.body):
            status = vacated.get(normalize_framework(framework))
            if status is None:
                continue
            where = f" (in {section!r})" if section else ""
            if status.status(requirement_id) == VACATED:
                message = (
                    f"cites {framework} {requirement_id}{where}, text vacated by "
                    f"{status.judgment}. eCFR still prints it, but it binds nobody; see the "
                    "catalog's README."
                )
            elif requirement_id in status.added_under_revision:
                root = status.added_under_revision[requirement_id]
                message = (
                    f"cites {framework} {requirement_id}{where}, text added by the vacated 2024 "
                    f"rule ({status.judgment}); the binding text is {root}'s pre-rule wording, "
                    "quoted in the catalog's framework.yaml."
                )
            else:
                continue
            findings.append(Finding(doc.relative_path, message, WARNING))
    return findings


#: Directories the unmarked-catalog scan never enters: version control,
#: environments, caches, and `local_content/`, where a licensed catalog
#: belongs and is read as licensed.
_SKIP_DIRS = frozenset({"local_content", "node_modules", "__pycache__", "venv", "env"})


def _check_unmarked_licensed_catalogs(project: Path, config: dict | None) -> list[Finding]:
    """A licensed BYOC catalog saved outside local_content/ before #459 (b)
    (80's ruling): warned, naming the file. Only files named `controls.json`,
    the name the ETLs document, are read: a scan of every JSON file would
    flag this project's own test fixtures.

    `config` is the project's, because it is what makes a location licensed
    (`frameworks.search_paths` and the manifests there): classified without
    it, a catalog the user's own configuration holds as licensed would be
    told its boundary settings had not been applied (9b on #10)."""
    import os

    from policyforge.llm.boundary import UNMARKED_WARNING, unmarked_licensed_catalog

    findings: list[Finding] = []
    for directory, subdirs, files in os.walk(project):
        subdirs[:] = [d for d in subdirs if not d.startswith(".") and d not in _SKIP_DIRS]
        if "controls.json" in files:
            path = Path(directory) / "controls.json"
            relative = path.relative_to(project)
            if unmarked_licensed_catalog(relative, config):
                findings.append(Finding(str(relative), UNMARKED_WARNING, WARNING))
    return findings


def check_tree(
    root: Path,
    *,
    synthesis_dir: Path | None = None,
    org_actors: tuple[str, ...] = (),
    config: dict | None = None,
) -> CheckReport:
    """Run every local check over a content tree.

    `config` is the project config, read by the unmarked-licensed-catalog
    scan to classify files as the boundary does; without it that scan sees
    only the default locations.

    `org_actors` are the names the organization acts under, from its config
    (`org.context.org_actors`): the Playbook check refuses one opening a
    clause after "NIST suggests ..." (#323). Without them only the generic
    actors are caught.
    """
    documents, problems = load_content_tree(root)
    report = CheckReport(documents=len(documents))

    report.findings.extend(Finding(path, reason) for path, reason in problems)
    report.findings.extend(_check_target_alias(documents))
    report.findings.extend(_check_page_claims(documents))
    report.findings.extend(_check_references(documents, root))
    report.findings.extend(_check_publishable(documents))
    report.findings.extend(_check_uncited(documents))
    report.findings.extend(_check_unanchored(documents))
    report.findings.extend(_check_requirement_strength(documents))
    report.findings.extend(_check_playbook_obligations(documents, org_actors))
    report.findings.extend(_check_vacated_citations(documents))
    report.findings.extend(_check_unmarked_licensed_catalogs(Path.cwd(), config))
    if synthesis_dir is not None:
        report.findings.extend(_check_citations(documents, synthesis_dir))

    from .provenance import attribution_summary

    report.attribution = attribution_summary(documents)
    return report
