"""The analyses Zardoz can run, as distinct from the documents it can read.

Until now Zardoz's whole world was the synced corpus: ask it something, it
finds passages and answers from them. But half the questions people have
about a compliance programme are not answerable from any document, because
they are questions *about* the programme rather than about its prose. Which
controls does nobody own. What did the catalog change last month. How many
organization-defined values are still undecided. Nothing in a Standard says
any of that; it falls out of set arithmetic over the registry, the catalogs
and the ledger.

Those computations already exist as CLI commands. What was missing was a way
to reach them from the place people are actually asking the question.

**The model routes; the report speaks.** A skill is chosen by the model —
that is a judgement about intent, which is what models are for — and then
its output is printed *verbatim*. The model never reads a result and tells
you about it. That division is the whole safety property here: a paraphrase
of "14 orphaned controls" can become "mostly in the audit family" with
nothing to check it against, and a compliance answer nobody can check is
worth less than no answer. Routing can be wrong in a way you can see, since
the chosen skill is always named. Reporting cannot be wrong at all, because
no model touches it.

Every skill is read-only. That is not a convention here — the package-wide
import guard makes the Confluence publish path unreachable from any module
in this directory, and a test walks the AST to prove it. A skill that wanted
to publish could not be written in this file.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from policyforge.llm import effort
from policyforge.llm.base import SchemaReplyError, TruncatedResponse
from policyforge.llm.prompts import Prompt, register

from .budgets import ROUTING_TOKENS

#: What a router returns when the question is about the documents rather
#: than about the programme. The common case, and the default.
NO_SKILL = "documents"


@dataclass
class Skill:
    """One analysis, and the questions it is the right answer to."""

    name: str
    summary: str
    #: Plain-language description of what this answers, shown to the router.
    #: Written as the questions a person would ask, because that is what the
    #: router is matching against.
    answers: str
    run: Callable[..., str]
    #: Named when the skill cannot run, so the shell can say what is missing
    #: rather than printing an empty report.
    needs: str = ""
    #: What this analysis can be narrowed by, as JSON Schema properties.
    #:
    #: Routing used to return a name and nothing else, and the shell ran the
    #: skill with no arguments at all. So "which controls are orphaned in the
    #: moderate baseline?" routed correctly to `coverage` and then reported
    #: on all 1,408 in-scope requirements, under a heading that said "scope:
    #: all controls". The scope in the question was not misread — it was
    #: never carried. A confidently wrong answer to a narrower question than
    #: the one asked is exactly what this project exists to avoid, and it was
    #: happening on the routed path only: `/coverage moderate` typed by hand
    #: has always worked.
    #:
    #: Declared per skill rather than sniffed from the question, so the model
    #: fills a typed field and the parsing is the API's job.
    arguments: dict = field(default_factory=dict)
    #: The order `run` expects its positional words in. Only matters for a
    #: skill taking more than one argument.
    argument_order: tuple[str, ...] = ()

    def as_args(self, values: dict | None) -> list[str]:
        """Typed arguments, in the positional form `run` already parses.

        Every skill reads `args: list[str]` and always has. Converting here
        rather than rewriting ten run functions keeps this change to the
        routing path, which is the only place that was broken — and keeps
        `/coverage moderate` typed by hand working through exactly the same
        code as the routed question.
        """
        if not values:
            return []
        order = self.argument_order or tuple(self.arguments)
        words: list[str] = []
        for key in order:
            value = values.get(key)
            if value is None or value == "":
                continue
            words.extend(str(value).split())
        return words


def _catalog_paths(state) -> list[str]:
    """The catalog files this shell is answering from.

    Split out of `_controls` so a report can name them. A suggested
    command that does not say which catalogs to load is not performable
    from the CLI's own default context — see `_zero_row_reasons`.
    """
    from policyforge.frameworks.registry import discover

    if state.controls_paths:
        return [str(p) for p in state.controls_paths]
    return [str(f.controls_path) for f in discover(state.config) if f.has_controls]


def _controls(state):
    """The control catalogs on disk, discovered rather than configured.

    A question about coverage should not require somebody to have passed
    `--controls` when they opened the shell. The framework registry already
    knows what is on disk, and using it means the shell answers with whatever
    the repository actually holds.
    """
    from policyforge.ingest.schema import load_controls

    paths = _catalog_paths(state)

    controls = []
    for path in paths:
        try:
            controls.extend(load_controls(Path(path)))
        except (OSError, ValueError):
            continue
    # The organization's reviewed crosswalk, as the CLI applies it. Not
    # caught: an unreadable overlay is reported by `run_skill` as the
    # analysis failing, rather than answered from the published mapping.
    from policyforge.crosswalk.overlay import apply_overlays, load_overlays

    apply_overlays(controls, load_overlays())
    return controls


def _coverage(state, args: list[str]) -> str:
    from policyforge.crosswalk.overlay import load_overlays, relationships_for
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.coverage import (
        analyze_coverage,
        format_report,
        scope_label,
        split_by_adoption,
        unadopted_note,
    )

    controls = _controls(state)
    if not controls:
        return "No control catalogs on disk. Run `policyforge etl-oscal` first."
    if not state.topics:
        return (
            "No topic registry loaded, so there is nothing to measure coverage "
            "against. Copy config/topics.example.yaml to config/topics.yaml."
        )

    # analyze_coverage takes controls already narrowed: "orphaned" is only
    # meaningful relative to a stated scope, so the filtering is the
    # caller's job and the scope has to be named in the report.
    baseline = next((a for a in args if a.lower() in ("low", "moderate", "high")), None)
    if baseline:
        controls = [c for c in controls if c.baseline and baseline in c.baseline.lower()]
        if not controls:
            return f"No controls tagged for the {baseline} baseline in the loaded catalogs."

    # Split the way `_addresses` already does. Handing every catalog to a
    # parameter named `nist_controls` made a HIPAA or CFR requirement an
    # orphan by construction — no topic anchors to its ids — so the
    # denominator grew with every catalog installed and the headline fell
    # while the numerator never moved: 1657 -> 1754 in scope, 814 owned
    # both sides, 49% -> 46%. Nothing about the programme had changed.
    #
    # **And in scope is what the registry ANCHORS, not what it COULD
    # anchor.** Those read the same and are not: the first bundled
    # catalog that is anchorable but OPTIONAL makes them diverge, and a
    # user who has not adopted it loses six points for work they never
    # took on. `unadopted` is named in the report rather than dropped,
    # so the exclusion cannot become a place for a framework to hide.
    nist, unadopted, other = split_by_adoption(state.topics, controls)
    if not nist:
        return (
            "No topic anchors any catalog on disk, so there is nothing to "
            "measure coverage against. The catalogs here are: "
            + ", ".join(sorted({c.framework for c in controls}))
            + "."
        )
    from policyforge.frameworks.registry import config_or_defaults
    from policyforge.frameworks.vacated import declared_vacated

    report = analyze_coverage(
        state.topics,
        nist,
        vacated=declared_vacated(config_or_defaults("vacated paragraphs were read")),
        scope=scope_label(nist, baseline),
        other_controls=other,
        crosswalk=build_crosswalk(controls),
        # **Passed, because the CLI passes it and they answer one question.**
        # Without it every lookup returns None, None is not in
        # PARTIAL_RELATIONSHIPS, and a mapping an organisation reviewed and
        # recorded as `superset` or `intersects` counts as FULL coverage
        # here while counting as partial in `policyforge coverage`.
        #
        # Measured before fixing, with every HIPAA mapping recorded as
        # superset: the shell said 65 of 74 covered and the CLI said 0.
        # Two views of one registry, disagreeing about what the
        # organisation's own reviewed decision means.
        #
        # `_controls` already applies the overlays to the controls; this is
        # the same overlays read for their relationships rather than their
        # pairs, so the shell is not reading anything the CLI does not.
        relationships=relationships_for(controls, load_overlays()),
    )
    return "\n".join(
        [
            format_report(report),
            *_zero_row_reasons(controls, report, _catalog_paths(state)),
            *unadopted_note(unadopted),
        ]
    )


def _parameters(state, args: list[str]) -> str:
    from policyforge.parameters.ledger import build_report, load_ledger

    controls = _controls(state)
    if not controls:
        return "No control catalogs on disk. Run `policyforge etl-oscal` first."

    baseline = next((a for a in args if a.lower() in ("low", "moderate", "high")), None)
    if baseline:
        controls = [c for c in controls if c.baseline and baseline in c.baseline.lower()]

    decisions = load_ledger(state.parameters_path)
    report = build_report(controls, decisions)
    # Grouped by default: a thousand parameters listed one per line is not an
    # answer to anything somebody asked out loud.
    return report.format_report(group="all" not in args)


def _drift(state, args: list[str]) -> str:
    from policyforge.frameworks.drift import analyze_drift, load_previous
    from policyforge.ingest.schema import load_controls
    from policyforge.parameters.ledger import load_ledger

    from .corpus import DEFAULT_CONTENT_DIR

    paths = list(state.controls_paths)
    if not paths:
        from policyforge.frameworks.registry import discover

        paths = [f.controls_path for f in discover(state.config) if f.has_controls]
    if not paths:
        return "No control catalogs on disk, so there is nothing to compare."

    blocks = []
    for path in paths:
        previous = load_previous(Path(path))
        if previous is None:
            blocks.append(f"{Path(path).parent.name}: no committed version to compare against.")
            continue
        report = analyze_drift(
            previous,
            load_controls(Path(path)),
            topics=state.topics,
            content_root=state.content_dir or DEFAULT_CONTENT_DIR,
            decisions=load_ledger(state.parameters_path),
        )
        blocks.append(f"{Path(path).parent.name}:\n{report.format_report()}")
    return "\n\n".join(blocks)


def _history(state, args: list[str]) -> str:
    from policyforge.history.version_store import load_history

    if not args:
        return (
            "Which document? Try `/history standard access-control` — the tier "
            "and the filename stem `generate` used."
        )
    tier, name = (args[0], args[1]) if len(args) > 1 else ("standard", args[0])
    try:
        versions = load_history(state.history_dir, f"{tier}/{name}")
    except (OSError, ValueError) as exc:
        return f"Could not read history for {tier}/{name}: {exc}"
    if not versions:
        return (
            f"No recorded history for {tier}/{name} in {state.history_dir}. "
            "History is written by `generate` and by the edit commands."
        )

    lines = [f"{len(versions)} recorded version(s) of {tier}/{name}:"]
    for record in versions:
        lines.append(
            f"  v{record.version}  {record.timestamp}  {record.source}  "
            f"(+{record.lines_added}/-{record.lines_removed})"
        )
    return "\n".join(lines)


def _check(state, args: list[str]) -> str:
    from policyforge.content.check import check_tree

    from .corpus import DEFAULT_CONTENT_DIR

    root = Path(state.content_dir or DEFAULT_CONTENT_DIR)
    if not root.exists():
        return f"No content tree at {root}, so there is nothing to check."
    from policyforge.org.context import org_actors

    config = state.config or {}
    return check_tree(root, org_actors=org_actors(config), config=config).format_report()


def _catalogs_used(controls) -> str:
    """One line naming the catalogs the answer was computed against.

    **The answer to "does this satisfy HIPAA" depends on which catalogs
    are loaded, and the shell and the CLI load different ones.** A shell
    session falls back to `discover()` and sees every bundled catalog,
    including both NIST ones — the configuration in which a bare
    `[NIST AC-2]` is *correctly* unresolvable. Someone running
    `policyforge satisfies --controls ...` names a narrower set and gets a
    different count of citations resolving to nothing, for the same
    documents. Neither is wrong and it reads as a bug.

    Naming the configuration is what turns that into information. It is
    this project's own rule from the prompt-comparison pre-registration —
    *an unknown count quoted without its catalog configuration means
    nothing* — arriving where a user meets it first.

    Derived from the controls actually loaded rather than from the paths
    asked for, because `_controls` skips a catalog it cannot read and
    carries on. Listing what was requested would name a catalog that
    contributed nothing.
    """
    from policyforge.mapping.crosswalk import normalize_framework

    counted: dict[str, int] = {}
    for control in controls:
        counted[control.framework] = counted.get(control.framework, 0) + 1
    if not counted:
        return "Answered against no catalogs."
    named = ", ".join(
        f"{name} ({count})" for name, count in sorted(counted.items(), key=lambda kv: kv[0])
    )
    keys = {normalize_framework(name) for name in counted}
    line = f"Answered against {len(counted)} catalog(s): {named}."
    if len(keys) < len(counted):
        line += " Some share a framework key, so their requirement ids are pooled."
    return line


def _satisfies(state, args: list[str]) -> str:
    """Which requirements a document cites, and which resolve to nothing.

    The assessor's direction of travel, and the opposite of `addresses`:
    that one starts from a requirement and finds the document, this one
    starts from the documents and finds what they can prove.
    """
    from policyforge.content.tree import load_content_tree
    from policyforge.crosswalk.overlay import accepted_rows, load_overlays
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.satisfies import build_report, format_report

    from .corpus import DEFAULT_CONTENT_DIR, slugify

    root = Path(state.content_dir or DEFAULT_CONTENT_DIR)
    if not root.exists():
        return f"No content tree at {root}, so there is nothing to check citations in."

    controls = _controls(state)
    if not controls:
        return "No control catalogs on disk. Run `policyforge etl-oscal` first."

    documents, _problems = load_content_tree(root)
    if not documents:
        return f"No documents in {root}, so there is nothing to check."

    wanted = " ".join(args).strip()
    if wanted:
        # By frontmatter topic or by filename slug, as the CLI matches:
        # `generate` names a file for the topic's slug and adds the
        # frontmatter `topic:` only when the document is published, so
        # matching frontmatter alone finds nothing in a freshly generated
        # tree -- which is when this report is most worth running.
        slug = slugify(wanted)
        selected = [d for d in documents if slugify(d.topic) == slug or slugify(d.slug) == slug]
        if not selected:
            return (
                f"No document in {root} belongs to {wanted!r}. Documents are matched "
                f"on their frontmatter topic or their filename slug."
            )
    else:
        selected = list(documents)

    evidences = build_report(
        selected,
        controls=controls,
        crosswalk=build_crosswalk(controls),
        provenance=accepted_rows(load_overlays()),
        topics=state.topics or (),
    )

    unknown = sum(len(e.unknown) for e in evidences)
    header = [_catalogs_used(controls)]
    if unknown:
        header.append(
            f"{unknown} citation(s) resolve to nothing in those catalogs — "
            f"`satisfies --strict` would exit non-zero."
        )
    return "\n".join([*header, "", format_report(evidences)])


def _frameworks(state, args: list[str]) -> str:
    from policyforge.frameworks.registry import check_licences

    report = check_licences(state.config)
    if not report.frameworks:
        return "No framework catalogs on disk."
    return report.format_report()


def _hitrust(state, args: list[str]) -> str:
    """What a loaded HITRUST catalog contains, and how far its crosswalk reaches.

    Distinct from `frameworks`, which answers "is a catalog on disk and may
    we hold it". This answers "what is in it" — how many control references
    and requirement statements, which levels the library states them at, and
    which of the eighty-odd authoritative sources HITRUST maps to are ones
    this repository actually has a catalog for.

    The last part is the useful one for planning. HITRUST has already
    reconciled its requirements against NIST 800-53, the HIPAA Security
    Rule, ARC-AMPE and FedRAMP; knowing how many edges that gives you is
    what decides whether a mapping run is worth doing.
    """
    from policyforge.ingest.levelled import FRAMEWORK, summarize
    from policyforge.mapping.crosswalk import requirement_crosswalk

    controls = _controls(state)
    catalog = [c for c in controls if c.framework == FRAMEWORK or c.requirements]
    if not catalog:
        return (
            "No HITRUST catalog on disk. HITRUST CSF is licensed content that "
            "this project never bundles, so you supply your own MyCSF export:\n"
            "  policyforge etl-hitrust --export local_content/hitrust/<export>.csv\n"
            "That prints what the export contains. Add `--out` to write a "
            "controls.json, but only into a repository whose licence permits "
            "holding it."
        )

    lines = [summarize(catalog).format_report(), ""]

    versions = sorted({c.framework_version for c in catalog if c.framework_version})
    if versions:
        lines.append(f"  catalog version: {', '.join(versions)}")

    levels = summarize(catalog).levels
    lines.append("")
    lines.append("  most-stated levels:")
    for level, count in levels.most_common(8):
        lines.append(f"    {level[:38].ljust(38)}  {count}")

    # Which mapped frameworks this repository can actually resolve against.
    held = {f.id for f in _frameworks_on_disk(state)}
    reach: dict[str, int] = {}
    for mappings in requirement_crosswalk(catalog).values():
        for framework, identifiers in mappings.items():
            reach[framework] = reach.get(framework, 0) + len(identifiers)
    matched = [(f, n) for f, n in sorted(reach.items(), key=lambda kv: -kv[1]) if _held(f, held)]
    lines.append("")
    if matched:
        lines.append("  mapped to catalogs this repository holds:")
        for framework, count in matched:
            lines.append(f"    {framework.ljust(38)}  {count} identifiers")
    else:
        lines.append(
            "  none of the mapped authoritative sources match a catalog on "
            "disk, so no crosswalk can be resolved yet. Run `policyforge "
            "etl-oscal` to bring in NIST 800-53."
        )
    return "\n".join(lines)


def _frameworks_on_disk(state):
    from policyforge.frameworks.registry import discover

    return discover(state.config)


def _held(framework: str, held: set[str]) -> bool:
    """Whether a mapped framework key names a catalog on disk.

    Matched loosely because the two vocabularies are written by different
    people: HITRUST says "NIST SP 800-53 r5", which normalizes to `nist`,
    while the directory is `nist-800-53-r5`.
    """
    return any(framework in identifier or identifier.startswith(framework) for identifier in held)


def _roles(state, args: list[str]) -> str:
    from policyforge.org.roles import TEAM_ROLES, VENDOR_ROLES

    lines = []
    for title, roles in (("Tool", VENDOR_ROLES), ("Team", TEAM_ROLES)):
        lines.append(f"{title} roles ({len(roles)}):")
        width = min(max(len(k) for k in roles), 28)
        lines += [f"  {k.ljust(width)}  {r.placeholder}" for k, r in roles.items()]
        lines.append("")
    lines.append("Assign them under `org.vendors` / `org.teams` in config.yaml.")
    return "\n".join(lines)


def _bundle(state, args: list[str]) -> str:
    """Everything one team answers for.

    The team lead's question, which the coverage report answers only by
    being read whole and filtered by eye.
    """
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.bundles import team_bundle

    if not state.topics:
        return (
            "No topic registry loaded, so there is nobody to build a bundle for. "
            "Copy config/topics.example.yaml to config/topics.yaml."
        )

    owner = " ".join(args).strip()
    if not owner:
        owners = sorted({t.owner for t in state.topics})
        return (
            "Name a team. Owners in the registry: " + ", ".join(owners) + "\n"
            "  e.g. /bundle Security Engineering"
        )

    controls = _controls(state)
    if not controls:
        return "No control catalogs on disk. Run `policyforge etl-oscal` first."

    nist = [c for c in controls if _is_nist_control(c)]
    return team_bundle(state.topics, nist, owner, crosswalk=build_crosswalk(controls)).render()


def _addresses(state, args: list[str]) -> str:
    """Where one requirement is answered, and by whom.

    The assessor's direction of travel: they name a citation, often not a
    NIST one, and want the document.
    """
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.bundles import requirement_view

    if not state.topics:
        return (
            "No topic registry loaded, so nothing can be said about who answers "
            "for a requirement. Copy config/topics.example.yaml to config/topics.yaml."
        )

    requirement = " ".join(args).strip()
    if not requirement:
        return "Name a requirement — e.g. /addresses AC-2, or /addresses 164.308(a)(1)(i)."

    controls = _controls(state)
    if not controls:
        return "No control catalogs on disk. Run `policyforge etl-oscal` first."

    nist = [c for c in controls if _is_nist_control(c)]
    other = [c for c in controls if not _is_nist_control(c)]
    return requirement_view(
        state.topics,
        nist,
        requirement,
        other_controls=other,
        crosswalk=build_crosswalk(controls),
    ).render()


def _first_sentence(text: str) -> str:
    """The opening sentence, by the boundary rule this project already has.

    **Neither `split(".")` nor `split(". ")` survives the house citation
    spelling.** On *"A practice under 45 C.F.R. 171.203(a) qualifies."*
    the first cuts to `45 C`; b5 caught that and proposed the second,
    which cuts to `45 C.F.R` — `C.F.R. 171` carries a stop-space of its
    own, so the one-character fix is still wrong.

    `entail/base.py:_BOUNDARY_RE` requires a capital, quote or bracket
    after the stop. It exists because full-stop splitting cost 19 of 43
    findings there. Imported rather than copied, so there is one
    definition of a sentence boundary — the same reason every reader of a
    source tag shares `SOURCE_TAG_RE`.
    """
    from policyforge.entail.base import _BOUNDARY_RE

    match = _BOUNDARY_RE.search(text)
    return text[: match.end()] if match else text


def _zero_row_reasons(controls, report, catalog_paths=None) -> list[str]:
    """Why each framework reachable through the crosswalk covers nothing.

    **A zero under a heading that reads as a gap is not a finding until it
    carries its cause.** A zero means one of several things, and which ones
    a user sees depends on their topics and overlays as much as on the
    catalogs:

    - `Information Blocking` is refused **by design**. Its entries are
      conditions of an exception, not controls to implement, so mapping
      them would assert something neither document says. That zero is the
      correct answer and `NOT_CROSSWALK_ANCHORABLE` already holds the
      reason as prose.
    - `42 CFR Part 2` seeds a crosswalk normally, and no published mapping
      was found (#264). That zero is work nobody has done -- as far as a
      search that did not enumerate NIST OLIR can say -- and
      `SEARCHED_NONE_FOUND` holds the search record.
    - **A source that publishes a mapping PolicyForge does not yet read**:
      that zero is a gap in PolicyForge, not in the source, and
      `PUBLISHED_UPSTREAM` holds the reason. `NIST 800-171` was the case
      until #259 -- NIST's own OSCAL links all 97 requirements to 800-53 --
      and it now carries that mapping, so it is a crosswalk-carrying
      catalog below. The table is empty today.
    - **A catalog that carries a crosswalk** -- HIPAA, FedRAMP, ARC-AMPE
      -- reads zero for up to three reasons AT ONCE, one per requirement,
      so its row is one counted clause per reason and the counts sum to
      the catalog's whole requirement count (80's ruling on #270):
      *partial* -- the topics own the control, but the organisation's
      overlay records the mapping as `superset`/`intersects`, so whether
      that is enough is a person's call; *unowned* -- mapped to a control
      no topic owns, so the gap is in the topics and a hand-seeded pair
      would compete with the publisher's; *unmapped* -- no mapping in this
      catalog's crosswalk at all (HIPAA's 164.306(a)-(e) among 9), with no
      advice. One sentence spoke for this mixed population three times on
      #270 before it was split. `covered` is the catalog joined to the
      user's topics and overlay, not a fact about the file: the example
      registry anchors so much that none of this showed (1d, on #270).
    - **Any other catalog** -- every BYOC one, and any shipped one added
      later without a crosswalk -- is in none of those tables and carries no
      mapping. Its row states only that. Until #264 this branch printed "no
      published crosswalk yet" for all of them, including the two above,
      which is a claim about the world that no one had checked.

    **This docstring said the opposite until #260**: *"`42 CFR Part 2` and
    `NIST 800-171` seed a crosswalk normally. Nobody has published one."*
    The report then told 800-171 users to rebuild by hand, with `crosswalk
    seed`, a mapping NIST publishes in the very file the catalog is built
    from. The two-way framing — *leave it alone* against *go and map it* —
    had no place for a zero the source had already answered, so the third
    kind was filed under the second.

    Those want different responses — *leave it alone*, *wait for the
    ingest*, *fix the topics*, *decide whether partial is enough*, *go and
    map it*, and *find out, then map it* — and the report prints the same
    number for all of them. The header names no count: it went from two
    kinds to six in a day, and a count in output is a claim that goes stale
    in a place nobody adding a kind thinks to look (80, on #270). The rows
    carry the taxonomy. The reasons are looked up from the declared
    framework name, which is reliable since the two CFR catalogs were
    renamed to be citable.

    **`catalog_paths` is what makes the remedy performable.** Without it
    this printed `crosswalk seed --framework 'NIST 800-171'`, which the
    shell can run because `discover()` loaded nine catalogs, and which
    exits 1 from the CLI's own default context because that context holds
    two. policyforge-80 got exit 1 and policyforge-9b got a clean run from
    the same printed line — **the disagreement between two runs was the
    finding**, and neither run was wrong. A command that works only in the
    context that printed it is a remedy in the same sense that a check
    which cannot fail is a check.

    So the flags are emitted, and they name the catalog that declared the
    framework plus the 800-53 anchor a crosswalk is built against. Both,
    because seeding needs the thing being mapped and the thing it maps to.
    """
    from policyforge.crosswalk.overlay import (
        _refusal_reason,
        _searched_none_found,
        _upstream_reason,
    )

    declared = {}
    # Frameworks whose catalog carries any 800-53 mapping, at either level --
    # NIST's HIPAA crosswalk maps implementation specifications separately
    # from their Standards, so a control-level check alone would miss some.
    for control in controls:
        declared.setdefault(_framework_key(control.framework), control.framework)

    # Per framework, the requirement ids the crosswalk maps to 800-53 -- read
    # from `build_crosswalk`, the instrument coverage itself uses, so the R/N
    # split and the covered count pass through ONE instrument (#278). It
    # takes in all three places a mapping can live: a catalog's own
    # `source_crosswalk`, an 800-53 control naming another framework's ids,
    # and HITRUST's level-scoped requirement mappings ("01.a Level 1").
    # Reading only the first made the other two print "carry no mapping".
    from policyforge.mapping.crosswalk import build_crosswalk

    mapped: dict[str, set[str]] = {}
    for by_framework in build_crosswalk(controls).values():
        for framework_key, ids in by_framework.items():
            mapped.setdefault(framework_key, set()).update(ids)

    path_for = _paths_by_framework(catalog_paths or [])
    anchor_path = path_for.get(_framework_key("NIST 800-53"))

    notes = []
    for framework in report.framework_coverage:
        if framework.covered:
            continue
        name = declared.get(_framework_key(framework.framework), framework.framework)
        reason = _refusal_reason(name)
        if reason is not None:
            notes.append(
                f"  {framework.framework.upper()}: not mapped by design. {_first_sentence(reason)}"
            )
        elif (upstream := _upstream_reason(name)) is not None:
            # The third kind of zero. Printed whole rather than cut to a first
            # sentence: the second sentence -- a gap in PolicyForge, not in the
            # source -- is the point, and no command follows, because the one
            # this branch used to print told users to rebuild by hand what the
            # publisher already publishes. #260.
            notes.append(f"  {framework.framework.upper()}: {upstream}")
        elif framework.partial or _framework_key(framework.framework) in mapped:
            # A catalog that carries a crosswalk, read zero. **One sentence
            # spoke for a mixed population three times** (1d twice, ba once,
            # on #270), so the row is built from clauses -- one per kind of
            # requirement, each with its live count, omitted at zero -- and
            # the three counts sum to every requirement the catalog has:
            #
            #   P  reached only through mappings the organisation recorded as
            #      superset/intersects; whether that is enough is a person's
            #      call. "listed as partial above" holds because `_coverage`,
            #      this function's only production caller, prints
            #      `format_report` first, and that lists them.
            #   R  mapped, but to no control a topic owns: the gap is in the
            #      topics, and a hand-seeded pair would compete with the
            #      publisher's mapping.
            #   N  no mapping in this catalog's crosswalk at all -- HIPAA's
            #      164.306(a)-(e) among them. No advice: whether each could be
            #      mapped is a claim nobody has measured.
            #
            # Product ruling 80, on #270. No command on any clause.
            own = mapped.get(_framework_key(framework.framework), set())
            reach = [r for r in framework.uncovered if r in own]
            none = [r for r in framework.uncovered if r not in own]
            clauses = []
            if framework.partial:
                clauses.append(
                    f"{len(framework.partial)} reach controls your topics own, but only in "
                    "part (listed as partial above); whether that is enough is a person's call."
                )
            if reach:
                clauses.append(
                    f"{len(reach)} map to 800-53 controls none of your topics owns; that gap "
                    "is in your topics, not the mapping, so do not seed them by hand."
                )
            if none:
                clauses.append(f"{len(none)} carry no mapping in this catalog's crosswalk.")
            notes.append(f"  {framework.framework.upper()}: " + " ".join(clauses))
        else:
            # Named for its contract, because the name is what the source
            # scan sees at the interpolation site: a pre-assembled fragment
            # cannot be quoted again there without collapsing it into one
            # argument, so the quoting has to happen here, per value.
            quoted_flags = ""
            own_path = path_for.get(_framework_key(framework.framework))
            for path in (own_path, anchor_path):
                if path and shlex.quote(str(path)) not in quoted_flags:
                    quoted_flags += f" --controls {shlex.quote(str(path))}"
            # Two kinds of zero share the seed advice -- neither catalog carries
            # any mapping -- and differ only in what the row may claim. "found"
            # is reserved for a framework someone actually searched for
            # (`SEARCHED_NONE_FOUND`, whose search record sits beside it --
            # Part 2, #264). Every other catalog, including any BYOC one, gets
            # a fact about the catalog and no claim about the world: nobody
            # searched for it, so nothing was "not found".
            if _searched_none_found(name) is not None:
                state = "no published crosswalk found"
            else:
                state = "this catalog carries no crosswalk"
            notes.append(
                f"  {framework.framework.upper()}: {state} — "
                f"`policyforge crosswalk seed --framework {shlex.quote(name)}"
                f"{quoted_flags}` starts one."
            )
    if not notes:
        return []
    return [
        "",
        "Why those are zero",
        "-" * 60,
        "  A zero here has one of several causes, and they want different responses.",
        "  Each line below names its cause.",
        *notes,
    ]


def _paths_by_framework(catalog_paths) -> dict[str, str]:
    """`{framework key: the catalog file that declares it}`.

    Read from each file's first entry rather than from the directory
    name, because the declared name is what `crosswalk seed --framework`
    matches against and a directory is only a convention. Unreadable
    files are skipped in silence here for the same reason `_controls`
    skips them: this builds a *suggestion*, and a report that failed
    because one catalog was malformed would be worse than one whose
    suggestion is missing a flag.
    """

    paths: dict[str, str] = {}
    for path in catalog_paths:
        try:
            from policyforge.ingest.schema import read_catalog

            entries, _ = read_catalog(Path(path))
            declared = entries[0]["framework"]
        except (OSError, ValueError, LookupError, TypeError):
            continue
        # **Forward slashes, always.** `discover()` hands back
        # `data\frameworks\...` on Windows, and a printed command carrying
        # backslashes is not copy-pasteable into bash, git-bash or any
        # POSIX shell — the backslashes are eaten as escapes and the path
        # arrives as `dataframeworks...`, which exits 2.
        #
        # This was found by running the printed line through a shell rather
        # than reading it, and it is the *same defect this function was
        # being fixed for*, one level down: the first version printed a
        # command that only ran in the context that printed it, and the fix
        # printed one that only ran on the platform that printed it. A
        # remedy inherits the credibility of the finding that prompted it
        # and gets checked less carefully. Click accepts forward slashes on
        # Windows, so this costs nothing.
        paths.setdefault(_framework_key(declared), Path(path).as_posix())
    return paths


def _framework_key(name: str) -> str:
    from policyforge.mapping.crosswalk import normalize_framework

    return normalize_framework(name)


def _is_nist_control(control) -> bool:
    """Whether a control belongs to the catalog the registry anchors on.

    The registry's `nist_controls` are NIST ids, so the two views have to
    know which half of a mixed catalog set is anchorable and which half is
    only reachable through the crosswalk.
    """
    from policyforge.mapping.crosswalk import anchors_a_topic

    return anchors_a_topic(control.framework)


def _boundary(state, args: list[str]) -> str:
    """What may be sent to the configured model, and why.

    **Answered for the provider this shell is talking to**, not for a
    hypothetical one. A user asking *what does this send anywhere* is
    sitting inside a model interface at the time, so an answer describing
    a provider they are not using is worse than no answer: it is
    confidently about the wrong thing.

    That is why the first line names the provider, the model and the
    endpoint rather than opening with the table. Same rule as a citation
    count needing its catalogs — an answer without the configuration it
    describes cannot be checked by the person reading it.
    """
    from policyforge.llm import boundary

    config = state.config or {}
    llm = config.get("llm") or {}
    provider = boundary.classify_provider(llm)

    named = llm.get("provider") or "(none configured)"
    model = llm.get("model") or "(no model set)"
    endpoint = llm.get("base_url") or llm.get("api_base") or "the provider's default endpoint"

    if str(llm.get("provider", "")).strip().lower() == "cascade":
        # A cascade has no model of its own, so naming "the" model would be
        # false where "two, one per half" is true. The classification line
        # below already names both halves; the header has to agree with it.
        halves = []
        for key in ("primary", "escalate_to"):
            half = llm.get(key) or {}
            halves.append(
                f"{key.replace('_', ' ')} {half.get('provider') or '(unset)'}"
                f"/{half.get('model') or '(no model set)'}"
                f" via {half.get('base_url') or half.get('api_base') or 'its default endpoint'}"
            )
        header = "Answering for this shell's configuration: cascade — " + "; ".join(halves) + "."
    else:
        header = (
            f"Answering for this shell's configuration: {named}, model {model}, via {endpoint}."
        )

    lines = [
        header,
        f"Classified as: {provider}",
        "",
        boundary.matrix(config),
    ]
    agreement = boundary.agreement_lines(config, provider)
    if agreement:
        lines += ["", *agreement]

    tightened = {
        content_class: ceiling
        for content_class, ceiling in boundary.ceilings(config).items()
        if ceiling != boundary.DEFAULT_CEILINGS[content_class]
    }
    if tightened:
        lines += ["", "Tightened by config (llm.boundary):"]
        lines += [f"  {k} -> at most {v}" for k, v in sorted(tightened.items())]
    else:
        # Stated rather than left silent: a reader cannot tell "nothing
        # tightened" from "the tightening section failed to render".
        lines += ["", "No ceiling is tightened by config; these are the defaults."]

    return "\n".join(lines)


SKILLS: dict[str, Skill] = {
    "boundary": Skill(
        name="boundary",
        summary="What may be sent to the configured model, and why.",
        answers=(
            "what does this send to a model; can this run offline; is my "
            "licensed content safe; what may leave the machine; which provider "
            "am I configured against; would HITRUST content be sent anywhere; "
            "what is the content boundary. Answered for the provider this "
            "shell is configured with, not in general."
        ),
        run=_boundary,
        arguments={},
    ),
    "bundle": Skill(
        name="bundle",
        summary="Everything one team owns: topics, requirements, documents, cadence.",
        answers=(
            "what is my team responsible for; what does a named team own; which "
            "documents does a team have to keep current; what am I accountable "
            "for; show me the Security team's obligations. Ask for one team by "
            "name — for the whole programme's gaps, that is coverage."
        ),
        run=_bundle,
        arguments={
            "owner": {
                "type": "string",
                "description": "The team name, exactly as the registry spells it.",
            }
        },
    ),
    "satisfies": Skill(
        name="satisfies",
        summary="What a document cites, and which citations resolve to nothing.",
        answers=(
            "does this document satisfy a framework; what does our access "
            "review policy cite; which citations in a document resolve to "
            "nothing; is what we have written traceable to a requirement; can "
            "we evidence HIPAA from our documents; what would an assessor find "
            "if they read this page. Starts from a document and asks what it "
            "proves. Not for finding the document that answers a named "
            'requirement — "where do we address AC-2" starts from the '
            "requirement, which is addresses. Not for how much of a baseline "
            "is owned, which is coverage."
        ),
        run=_satisfies,
        needs="a content tree and at least one control catalog",
        arguments={
            "topic": {
                "type": "string",
                "description": (
                    "One topic or document slug to narrow to, e.g. access-control. "
                    "Omit to report on the whole tree."
                ),
            }
        },
    ),
    "addresses": Skill(
        name="addresses",
        summary="Who answers for one requirement, and which document says so.",
        answers=(
            "where do we address a named control or citation; which document "
            "covers AC-2; who owns 164.308(a)(1)(i); show me where a HIPAA or "
            "HITRUST requirement is answered; which team is responsible for one "
            "specific requirement. Ask about one named requirement — for how "
            "much of a baseline is owned overall, that is coverage. Not for what "
            'a requirement says or requires — "what does AC-2 require?" asks '
            "what a document states, which is a question for the documents. Not "
            'for who owns a document — "who owns the backup standard?" names a '
            "document, not a requirement, and is a question for the documents."
        ),
        run=_addresses,
        arguments={
            "requirement": {
                "type": "string",
                "description": "A requirement id, e.g. AC-2 or 164.308(a)(1)(i).",
            }
        },
    ),
    "coverage": Skill(
        name="coverage",
        summary="Which in-scope controls no topic owns, and which two claim.",
        answers=(
            "which controls nobody owns or is responsible for; orphaned controls; "
            "controls claimed by two teams; gaps in the programme; whether a "
            "baseline is fully covered. Not for whether a document on some "
            'subject exists — "do we have anything covering X" asks what is '
            "written down, which is a question for the documents."
        ),
        run=_coverage,
        arguments={
            "baseline": {
                "type": "string",
                "enum": ["low", "moderate", "high"],
                "description": "NIST baseline to narrow to. Omit unless the question names one.",
            }
        },
    ),
    "parameters": Skill(
        name="parameters",
        summary="Organization-defined values decided and still outstanding.",
        answers=(
            "how many organization-defined parameters are still undecided; which "
            "frequencies or thresholds nobody has chosen; ODP status"
        ),
        run=_parameters,
        arguments={
            "baseline": {
                "type": "string",
                "enum": ["low", "moderate", "high"],
                "description": "NIST baseline to narrow to. Omit unless the question names one.",
            }
        },
    ),
    "drift": Skill(
        name="drift",
        summary="What a framework update changed, and what it reaches.",
        answers=(
            "what changed in the control catalog; whether a framework update "
            "affects us; which documents a catalog revision touches"
        ),
        run=_drift,
    ),
    "history": Skill(
        name="history",
        summary="Recorded versions of one document.",
        answers=(
            "what changed in a specific document over time; a document's version "
            "history; which versions of a document exist, are recorded, or have "
            "been published; when a policy was last revised. Asking what a "
            "named document *says* is a documents question; asking which "
            "versions of that same document exist is this one."
        ),
        run=_history,
        arguments={
            "tier": {
                "type": "string",
                "enum": ["policy", "standard", "procedure"],
                "description": (
                    "Document tier. Defaults to standard when the question does not say."
                ),
            },
            "name": {
                "type": "string",
                "description": "The document's filename stem, e.g. access-control.",
            },
        },
        # `_history` reads args[0] as the tier and args[1] as the name when
        # both are given, so the order is load-bearing rather than cosmetic.
        argument_order=("tier", "name"),
    ),
    "check": Skill(
        name="check",
        summary="Problems in the content tree before anything is published.",
        answers=(
            "whether the document tree is healthy before publishing; broken links "
            "between documents; two files publishing to one page; documents "
            "missing required frontmatter. Not for looking up who owns a "
            "particular document — that is a question for the documents."
        ),
        run=_check,
    ),
    "frameworks": Skill(
        name="frameworks",
        summary="Catalogs on disk and their licence position.",
        answers=(
            "which frameworks are loaded; what version of a catalog is in use; "
            "whether licensed content is committed"
        ),
        run=_frameworks,
    ),
    "hitrust": Skill(
        name="hitrust",
        summary="What the loaded HITRUST catalog holds, and what it maps to.",
        answers=(
            "what is in our HITRUST catalog; how many CSF control references or "
            "requirement statements; which HITRUST levels or regulatory overlays "
            "apply; what HITRUST maps to in 800-53 or HIPAA; CSF crosswalk reach"
        ),
        run=_hitrust,
    ),
    "roles": Skill(
        name="roles",
        summary="Tool and team roles config can assign.",
        answers="what tool or team roles can be configured; what placeholders exist",
        run=_roles,
    ),
}


ROUTER_SYSTEM_PROMPT = register(
    Prompt(
        name="zardoz.route",
        version=1,
        text="""You decide whether a question about an \
organization's security programme should be answered from its documents or \
by running an analysis.

You are given the question and a list of analyses. Reply with exactly one
word: the name of the analysis, or `documents`.

Rules:

1. Prefer `documents`. Most questions are about what a policy says, and the
   documents are the right source for those. An analysis is for questions
   about the *programme* — what is missing, what changed, what nobody has
   decided — which no document states.
2. Never explain, never add punctuation, never answer the question. One
   word.
3. If two could apply, choose the more specific one.
4. If you are unsure, say `documents`. A wrong analysis wastes a turn; a
   question sent to the documents that finds nothing gets an honest refusal,
   which is recoverable.""",
    )
)


ARGUMENT_PROMPT = register(
    Prompt(
        name="zardoz.route.arguments",
        version=1,
        text="""You are given an analysis that has already been chosen, \
and the question it was chosen for. Say how the analysis should be narrowed.

Rules:

1. Only fill a field the question actually names. "which controls are
   orphaned in the moderate baseline" names a baseline; "which controls are
   orphaned" does not. Leave a field out rather than guessing at it.
2. An invented value is worse than an empty one. Left empty, the analysis
   reports on everything and says so in its heading, which a reader can see.
   Filled with a guess, it reports on a slice nobody asked for under a
   heading that looks deliberate — and the reader has no way to tell.
3. Copy values from the question rather than normalising them. A team name
   is matched against the registry exactly as written there, so answer with
   what the question said.
4. Returning nothing at all is a correct and common answer. Most questions
   name no scope.""",
    )
)


#: Fallback routing, used when no model is configured. Deliberately narrow:
#: it fires on the words these analyses are actually about, and anything
#: else goes to the documents. A keyword router that guessed broadly would
#: hijack ordinary questions, which is worse than not routing at all.
_ROUTING_HINTS: dict[str, tuple[str, ...]] = {
    # Stems, not whole phrases. "nobody owns" misses "does nobody own?",
    # which is how the question is actually asked out loud.
    "coverage": (
        "orphan",
        "nobody own",
        "no one own",
        "no-one own",
        "unowned",
        "contested",
        "uncovered",
        "not covered",
        "who owns nothing",
    ),
    "parameters": ("undecided", "organization-defined", "odp", "parameter"),
    # Only the unambiguous phrasings. The tempting stems were measured
    # against ordinary document questions and rejected: "traceable" hijacks
    # "is the encryption section traceable to a decision log?", and
    # "satisfy the" hijacks "does the standard satisfy the auditor's
    # expectations for evidence?". Both are questions for the documents.
    # A missed route costs a fallback to the documents; a hijacked one
    # answers a question nobody asked.
    "satisfies": ("resolve to nothing", "resolves to nothing", "resolving to nothing"),
    "drift": (
        "changed in the catalog",
        "catalog change",
        "catalog changed",
        "framework update",
        "framework changed",
        "new version",
        "version bump",
    ),
    "check": ("broken link", "content tree", "dangling"),
    "frameworks": ("which frameworks", "catalog version", "licence", "license"),
    # "hitrust" alone is deliberately absent: most questions naming HITRUST
    # are about what a document requires, and those belong to the documents.
    # These fire only on questions about the catalog itself.
    "hitrust": (
        "hitrust catalog",
        "csf catalog",
        "hitrust level",
        "csf level",
        "requirement statement",
        "control reference",
        "hitrust overlay",
        "hitrust map",
        "csf map",
    ),
    "roles": ("what roles", "role keys", "which placeholders"),
}


def route_offline(question: str) -> str:
    lowered = question.lower()
    for name, hints in _ROUTING_HINTS.items():
        if any(hint in lowered for hint in hints):
            return name
    return NO_SKILL


def _route_with_schema(question: str, catalog: str, provider) -> str | None:
    """The routing decision as a constrained enum, or None to fall back.

    Returns None rather than raising, so this stays a pure optimisation:
    any failure leaves the caller exactly where it would have been.
    """
    import json

    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "routing",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"analysis": {"type": "string", "enum": [NO_SKILL, *SKILLS]}},
                "required": ["analysis"],
                "additionalProperties": False,
            },
        },
    }
    try:
        # Through the helper, not the provider directly: that is where a
        # cut-off reply is retried once and then refused, and a routing
        # call that silently fell through to prose after a truncation was
        # measured as a route, not as the failure it was.
        response = effort.call_json(
            provider,
            system=ROUTER_SYSTEM_PROMPT,
            prompt=f"ANALYSES\n\n{catalog}\n\nQUESTION\n\n{question.strip()}",
            schema=schema,
            temperature=0.0,
            max_tokens=ROUTING_TOKENS,
        )
        choice = json.loads(response.text).get("analysis")
    except TruncatedResponse:
        raise
    except Exception:  # noqa: BLE001 - a failed optimisation is not a failed route
        return None
    # Belt and braces. `strict` should make an off-enum value impossible,
    # and a schema nobody actually enforced is precisely the case this
    # cannot detect from the inside.
    return choice if choice in SKILLS else NO_SKILL


@dataclass(frozen=True)
class Routed:
    """A routing decision, and how the analysis should be narrowed."""

    skill: str
    arguments: dict = field(default_factory=dict)

    @property
    def ran(self) -> bool:
        return self.skill != NO_SKILL

    def as_args(self) -> list[str]:
        skill = SKILLS.get(self.skill)
        return skill.as_args(self.arguments) if skill else []


def skill_tools() -> list[dict]:
    """Every analysis as a tool definition, generated from the registry.

    Generated here, unlike `mcp/server.py`'s hand-written list, and the
    difference is not an inconsistency. The MCP list is an interface other
    people's agents depend on, where adding an entry should be a visible
    act. This list is what *this* project's own router chooses from, and it
    has to contain every skill or the router cannot reach one — a skill
    missing from it is a feature nobody can route to, which is the bug the
    generated form makes impossible.
    """
    return [
        {
            "name": name,
            "description": skill.answers,
            "input_schema": {
                "type": "object",
                "properties": dict(skill.arguments),
                "required": [],
                "additionalProperties": False,
            },
        }
        for name, skill in SKILLS.items()
    ]


def _fill_arguments(question: str, skill_name: str, provider) -> dict:
    """Ask only how to narrow an analysis already chosen.

    A second call, deliberately, and the reason is measured rather than
    aesthetic. The first attempt did this in one call: routing enum plus
    every skill's arguments flattened into one schema. It filled arguments
    well and it *made routing worse* — `glm-5.3-flash` went from 10/10 to
    9/10 on a live sweep, sending "what is our access review cadence?" (an
    ordinary document question the eval suite covers) to `parameters` with a
    baseline nobody had mentioned. Twenty extra fields in the schema pulled
    the model's attention off the one decision that matters.

    Split in two, the routing call is byte-for-byte the one that measures
    100%, and argument filling cannot touch it. The cost is one extra call,
    and only for a skill that declares arguments at all — six of the ten
    declare none and never make it.

    Returns {} on any failure. Running an analysis unnarrowed is what this
    project did until now; it is a worse answer, not a wrong one, and it is
    the right thing to fall back to.
    """
    import json

    declared = SKILLS[skill_name].arguments
    if not declared:
        return {}

    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "arguments",
            "strict": False,
            "schema": {
                "type": "object",
                "properties": dict(declared),
                "required": [],
                "additionalProperties": False,
            },
        },
    }
    try:
        response = effort.call_json(
            provider,
            system=ARGUMENT_PROMPT,
            prompt=(
                f"ANALYSIS\n\n{skill_name}: {SKILLS[skill_name].answers}\n\n"
                f"QUESTION\n\n{question.strip()}"
            ),
            schema=schema,
            temperature=0.0,
            max_tokens=ROUTING_TOKENS,
        )
        parsed = json.loads(response.text)
    except TruncatedResponse:
        raise
    except Exception:  # noqa: BLE001 - unnarrowed is a worse answer, not a wrong one
        return {}

    if not isinstance(parsed, dict):
        return {}
    return {
        key: value
        for key, value in parsed.items()
        if key in declared and value not in (None, "") and _named_in(question, value)
    }


def _named_in(question: str, value) -> bool:
    """Whether the question actually contains the value the model returned.

    Rule 2 of `ARGUMENT_PROMPT` asks the model not to invent a scope, and
    measurement says asking is not enough. On a live sweep `glm-5.3-flash`
    invented `baseline: moderate` on one question of ten and
    `deepseek-v4-flash` on three — always "moderate", always for a question
    that named no baseline at all. A prompt is a request and a check is a
    guarantee, which is the rule the rest of this project is built on.

    So a value has to be *in* the question to survive. That makes inventing
    one structurally impossible rather than discouraged, and it costs almost
    nothing real: every value worth filling — a baseline, a control id, a
    team name — is something the asker typed.

    Matched case-insensitively over whitespace-collapsed text, so "Moderate"
    against "the moderate baseline" holds, and "the highest impact level"
    still yields "high". The deliberate loss is a value the model knew from
    somewhere other than the question: "what does the IAM team own?" will not
    become `IAM Engineering` unless the question said so. Running unnarrowed
    and asking which team is the better outcome there — a bundle for the
    wrong team is worse than a prompt for the right one.

    Hyphens and underscores are flattened to spaces on both sides, because a
    document's slug is `access-control` and nobody asks about it that way —
    "what versions of the access control standard are recorded?" should yield
    `access-control`, and an exact match would reject it for punctuation the
    asker had no reason to type.
    """
    return bool(_flatten(value)) and _flatten(value) in _flatten(question)


def _flatten(text) -> str:
    """Casefolded, with separators and runs of whitespace reduced to one space."""
    import re

    return " ".join(re.sub(r"[-_/]+", " ", str(text)).split()).casefold()


def route(question: str, provider=None) -> str:
    """Which skill answers this, or `documents`.

    Never raises: a router failure should send the question to the documents,
    which is the honest default and the path that refuses gracefully.

    Deliberately not `route_with_arguments(...).skill`. This returns a name
    and nothing else, so filling arguments to then discard them would be a
    second billed call bought for nothing — which is exactly what it was
    doing until a test that counts calls caught it.
    """
    return _route_by_name(question, provider) if provider is not None else route_offline(question)


def route_with_arguments(question: str, provider=None) -> Routed:
    """`route`, plus the arguments the question named.

    Kept as a separate entry point so `route()` stays exactly what every
    existing caller and the whole routing eval suite expect — a name. The
    suite grades which analysis a question reaches, and that question is
    unchanged by this.
    """
    if provider is None:
        return Routed(route_offline(question))

    # The routing decision first, through the path that measures 100%, and
    # unchanged by anything here.
    skill = _route_by_name(question, provider)
    if skill == NO_SKILL or not SKILLS[skill].arguments:
        return Routed(skill)
    if not getattr(provider, "supports_schema", lambda: False)():
        # Without a schema the model would be asked for arguments in prose
        # and parsed with a regex, which is how the scope got lost in the
        # first place. Unnarrowed is the honest outcome here.
        return Routed(skill)
    return Routed(skill, _fill_arguments(question, skill, provider))


#: The most analyses one question may run. Two, not "as many as it asks for":
#: a question that needs three reports is better asked as two questions, and
#: every extra report is more for a person to read past to find their answer.
MAX_ANALYSES = 2


ALSO_PROMPT = register(
    Prompt(
        name="zardoz.route.also",
        version=1,
        text="""A question about an organization's security programme has \
already been routed to one analysis. You decide whether it also asks a second, \
separate thing that a different analysis answers.

Rules:

1. Most questions ask one thing. Answer `none` unless the question plainly
   asks two separate things — usually joined by "and", or asked as two
   sentences.
2. A detail, a scope or a restatement of the same thing is not a second
   question. "Which controls are orphaned in the moderate baseline?" asks one
   thing.
3. Never add an analysis because it is related or might be useful. Only
   because the question asks for what it reports.
4. If you are unsure, answer `none`. A missing second report is a follow-up
   question; an unwanted one buries the answer that was asked for.""",
    )
)


def _route_also(question: str, first: str, provider) -> str:
    """A second analysis the question separately asks for, or `none`.

    Its own call, after routing, for the reason argument filling is its own
    call: putting more into the routing schema measurably made routing worse
    (docs/measurements/epochs.md epoch 14), and routing is the decision that has to stay
    right. This call cannot change the first analysis — it only chooses among
    the others.

    Measured before it was built, on the routing suite's single-intent
    questions and on compound ones: no false second analysis in 52 runs across
    two models, and every compound question fully routed (docs/measurements/epochs.md
    epoch 15). Failure of any kind is `none` — one report where two were
    asked for is a follow-up question, not a wrong answer.
    """
    import json

    others = [name for name in SKILLS if name != first]
    catalog = "\n".join(f"{name}: {SKILLS[name].answers}" for name in others)
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "also",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"also": {"type": "string", "enum": ["none", *others]}},
                "required": ["also"],
                "additionalProperties": False,
            },
        },
    }
    prompt = (
        f"ALREADY ROUTED TO\n\n{first}: {SKILLS[first].answers}\n\n"
        f"OTHER ANALYSES\n\n{catalog}\n\nQUESTION\n\n{question.strip()}"
    )
    try:
        response = effort.call_json(
            provider,
            system=ALSO_PROMPT,
            prompt=prompt,
            schema=schema,
            temperature=0.0,
            max_tokens=ROUTING_TOKENS,
        )
        choice = json.loads(response.text).get("also", "none")
    except TruncatedResponse:
        raise
    except SchemaReplyError as exc:
        # The model answered, just not as JSON. Read that answer first; ask
        # again only if it is not a word from the list.
        choice = _first_word(exc.text)
        if choice not in others and choice != "none":
            choice = _also_in_prose(prompt, provider)
    except Exception:  # noqa: BLE001 - a missed second report is a follow-up, not an error
        # A timeout or a refused request has no reply to recover, and a
        # second call would only double the wait for the first report.
        choice = "none"
    # `strict` should make this impossible; a schema nobody enforced is the
    # one case that cannot be detected from inside.
    return choice if choice in others else "none"


def _first_word(text: str) -> str:
    words = text.strip().strip(".`\"'").split()
    return words[0].lower().strip(".`\"',") if words else "none"


def _also_in_prose(prompt: str, provider) -> str:
    """The second-analysis question as one word, when the schema reply was unreadable.

    Routing has always had this fallback and this had not, and the gap was
    measured rather than guessed. `glm-5.3-flash` through OpenRouter sometimes
    ignores the schema's wrapper and replies with the bare word — `frameworks`
    rather than `{"also": "frameworks"}`. The provider rightly refuses a reply
    that is not the JSON it promised, and without a fallback that correct
    answer became `none`: chaining went from 11/11 to 8/11 on one run while
    routing, which recovers exactly this way, held at 54/54.

    That bare word is read straight from the refused reply (`SchemaReplyError`
    carries it), so this call is made only when the reply was neither JSON nor
    a word from the list. The first version asked again on any failure, which
    re-asked for an answer already in hand and turned a timeout into two
    (found by policyforge-ba).

    The word is still checked against the closed list by the caller, so this
    cannot invent an analysis — only recover one the model already chose.
    """
    try:
        response = effort.call(
            provider,
            system=ALSO_PROMPT,
            prompt=f"{prompt}\n\nReply with exactly one word: the analysis name, or none.",
            temperature=0.0,
            max_tokens=ROUTING_TOKENS,
        )
    except TruncatedResponse:
        raise
    except Exception:  # noqa: BLE001 - a missed second report is a follow-up, not an error
        return "none"
    return _first_word(response.text)


def route_plan(question: str, provider=None) -> list[Routed]:
    """Every analysis the question asks for — one, or at most two.

    The first is exactly what `route_with_arguments` returns, by the same
    calls. A second is looked for only when the first is an analysis: a
    question sent to the documents is answered from passages, and there is no
    report to set another beside.

    Chaining needs a schema. Without one, the second call would be a word
    parsed out of prose, which is the least reliable thing this module does,
    and a spurious extra report costs the reader more than a missing one.
    """
    first = route_with_arguments(question, provider)
    if first.skill == NO_SKILL or provider is None:
        return [first]
    if not getattr(provider, "supports_schema", lambda: False)():
        return [first]

    second = _route_also(question, first.skill, provider)
    if second == "none":
        return [first]
    arguments = _fill_arguments(question, second, provider) if SKILLS[second].arguments else {}
    return [first, Routed(second, arguments)][:MAX_ANALYSES]


def _route_by_name(question: str, provider) -> str:
    """The original router: one word, no arguments.

    Still here because most local models cannot be held to a schema, and a
    router that worked only on hosted ones would be a worse router than the
    one that was already here.
    """
    if provider is None:
        return route_offline(question)

    catalog = "\n".join(f"{name}: {skill.answers}" for name, skill in SKILLS.items())

    # A schema turns "did the model reply with exactly one word" from
    # something the prompt asks for into something the API guarantees.
    # Rule 2 of ROUTER_SYSTEM_PROMPT — never explain, never add
    # punctuation — exists only because that guarantee was unavailable;
    # where it is available the rule stops being load-bearing.
    #
    # Falls through to the prose path on any failure. Most local models
    # cannot enforce a schema, and a router that worked only on hosted
    # ones would be a worse router than the one already here.
    if provider is not None and getattr(provider, "supports_schema", lambda: False)():
        routed = _route_with_schema(question, catalog, provider)
        if routed is not None:
            return routed

    try:
        response = effort.call(
            provider,
            # The call that least wants deliberation: one word from a closed
            # list, where thinking its way past `documents` is the failure.
            effort=effort.ROUTING,
            system=ROUTER_SYSTEM_PROMPT,
            prompt=f"ANALYSES\n\n{catalog}\n\nQUESTION\n\n{question.strip()}\n\nOne word.",
            temperature=0.0,
            # Room for a reasoning preamble plus one word. The shim retries a
            # truncation, but paying for that on every call would be silly.
            # See zardoz/budgets.py: how much preamble is enough depends on
            # the model, so this is tunable per run — and `effort` above is
            # the lever that should eventually make the budget unnecessary.
            max_tokens=ROUTING_TOKENS,
        )
    except TruncatedResponse:
        # Retried once already. Routing offline here would grade — and
        # answer — a cut-off reply as a decision about the question.
        raise
    except Exception:  # noqa: BLE001 - a routing failure is not a session failure
        return route_offline(question)

    choice = response.text.strip().strip(".`").split()[0].lower() if response.text.strip() else ""
    return choice if choice in SKILLS else NO_SKILL


def run_skill(name: str, state, args: list[str] | None = None) -> str:
    """Run one skill and return its output, unmodified.

    The return value is printed verbatim. Nothing between here and the
    terminal is allowed to summarise it, which is what makes the numbers in
    a Zardoz answer worth the same as the numbers from the CLI.
    """
    skill = SKILLS.get(name)
    if skill is None:
        return f"No such analysis: {name}"
    try:
        return skill.run(state, args or [])
    except Exception as exc:  # noqa: BLE001 - one broken analysis, not a dead shell
        return f"{skill.name} could not run ({type(exc).__name__}: {exc})."


def skill_fields() -> list[tuple[str, str]]:
    """(name, summary) for the help table."""
    return [(name, skill.summary) for name, skill in SKILLS.items()]
