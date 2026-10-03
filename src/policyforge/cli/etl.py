"""Fetching framework catalogs and turning them into this project's schema."""

from __future__ import annotations

import shlex
from pathlib import Path

import click

from policyforge.cli import cli
from policyforge.cli._common import (
    get_provider,
    load_config,
    load_config_or_empty,
)
from policyforge.textfile import write_text_lf


@cli.command("etl-vault")
@click.option(
    "--controls-dir",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to an Obsidian vault's Frameworks/NIST-800-53/Controls/ directory.",
)
@click.option(
    "--out",
    default=Path("data/frameworks/nist-800-53-r5/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed, public-domain-only control data.",
)
def etl_vault(controls_dir: Path, out: Path):
    """Parse NIST 800-53 control notes from an existing vault into this
    project's data schema. Public-domain content only - see
    ingest/nist_vault_loader.py for what crosswalk columns are stripped
    by default and why.
    """
    import dataclasses
    import json

    from policyforge.ingest.nist_vault_loader import load_vault_controls

    report = load_vault_controls(controls_dir)

    # **Every refusal happens before the write, and the ordering is the
    # whole point.** The first version of this checked after writing, so a
    # mistyped `--controls-dir` replaced a good catalog with `[]` and
    # *then* exited 1. An exit code after the damage is a report, not a
    # refusal: the only moment the check could have helped had already
    # passed. Found by policyforge-80 on #225 — and it is the same family
    # as a guard whose argument is fetched at the moment of use, with the
    # ordering inverted rather than the source.
    if not report.attempted:
        raise click.ClickException(
            f"no *.md control notes found in {controls_dir}. An empty directory is "
            f"not an empty vault — check the path points at Controls/. Nothing was "
            f"written; {out} is unchanged."
        )
    if report.unreadable:
        for path, why in report.unreadable:
            click.echo(f"  unreadable: {path}: {why}", err=True)
        raise click.ClickException(
            f"{len(report.unreadable)} of {report.attempted} control note(s) could not "
            f"be parsed. Nothing was written; {out} is unchanged. A short catalog "
            f"written over a good one loses the same controls as an empty one and "
            f"looks healthier, so the partial result is discarded rather than "
            f"committed — fix the notes named above and run again."
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in report.controls], indent=2))
    # Says what was attempted, not only what survived. The old line read
    # `Parsed {len(controls)} controls` and exited 0 whatever happened, so
    # five good notes and two empty files reported "Parsed 7 controls".
    click.echo(f"Parsed {len(report.controls)} of {report.attempted} control note(s) -> {out}")


@cli.command("etl-oscal")
@click.option(
    "--out",
    default=Path("data/frameworks/nist-800-53-r5/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed control data.",
)
@click.option(
    "--no-baselines",
    is_flag=True,
    help="Skip fetching the Low/Moderate/High profiles (controls load without "
    "baseline tagging, which `ssp --baseline` needs).",
)
def etl_oscal(out: Path, no_baselines: bool):
    """Fetch NIST's official OSCAL edition of SP 800-53 Rev 5 (plus the
    Low/Moderate/High baseline profiles) and parse it into this project's
    data schema.

    Public domain - a US government work, same basis as the eCFR and CPRT
    sources. Unlike `etl-vault`, this needs no pre-existing vault, so it's
    the way to populate 800-53 data from scratch. See ingest/oscal_loader.py.
    """
    import dataclasses
    import json

    from policyforge.ingest.oscal_loader import (
        CATALOG_URL,
        OSCAL_REF,
        fetch_oscal_baselines,
        fetch_oscal_catalog,
        parse_oscal_catalog,
    )
    from policyforge.ingest.provenance import record_source_provenance

    catalog = fetch_oscal_catalog()
    baselines = {} if no_baselines else fetch_oscal_baselines()
    controls, withdrawn = parse_oscal_catalog(catalog, baselines)

    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))
    enhancements = sum(len(c.enhancements) for c in controls)
    click.echo(
        f"Parsed {len(controls)} controls and {enhancements} enhancements "
        f"({catalog['catalog']['metadata']['version']}) -> {out}"
    )
    click.echo(f"Excluded {withdrawn} withdrawn controls/enhancements.")

    # Provenance travels with the data, not just in the loader's source. A
    # reviewer holding controls.json can now name the upstream revision and
    # check the hash, which is the difference between "an upstream revision"
    # and "an upstream compromise" — the two look identical in a diff.
    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=OSCAL_REF,
        source_url=CATALOG_URL,
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(f"Recorded provenance: {OSCAL_REF} sha256:{stamp[:16]}… -> {out.parent}")
    if baselines:
        for name, ids in baselines.items():
            click.echo(f"  {name} baseline: {len(ids)} controls")


@cli.command("etl-800-171")
@click.option(
    "--out",
    default=Path("data/frameworks/nist-800-171-r3/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed control data.",
)
def etl_800_171(out: Path):
    """Fetch NIST's official OSCAL edition of SP 800-171 Rev 3 and parse it
    into this project's data schema.

    Public domain - a US government work, same basis as 800-53 and the eCFR
    sources.

    REV 3, which is the revision NIST publishes machine-readable. CMMC
    Level 2 currently assesses against R2, a different revision with
    differently shaped identifiers - `03.01.01` here against `3.1.1` there.
    A citation to this catalog is a citation to rev 3. See the catalog's
    README before using it to prepare for a CMMC assessment.

    No `--no-baselines` flag, because there are no baselines: rev 3
    publishes no Low/Moderate/High profiles, so every control's `baseline`
    is empty. That empty is the source's, not a fetch that came up short.
    """
    import dataclasses
    import json

    from policyforge.ingest.oscal_loader import (
        NIST_800_171_REV3,
        fetch_800_171_catalog,
        parse_oscal_catalog,
    )
    from policyforge.ingest.provenance import record_source_provenance

    catalog = fetch_800_171_catalog()
    controls, withdrawn = parse_oscal_catalog(catalog, dialect=NIST_800_171_REV3)
    links = _require_800_53_links_resolve(controls)

    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))
    version = catalog["catalog"]["metadata"]["version"]
    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=version,
        source_url=NIST_800_171_REV3.source_url,
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(f"Recorded provenance: {version} sha256:{stamp[:16]}… -> {out.parent}")
    click.echo(f"Parsed {len(controls)} requirements (rev 3, {version}) -> {out}")
    click.echo(f"Excluded {withdrawn} withdrawn requirements.")
    click.echo(
        f"Carried NIST's {links['total']} links to 800-53 on {links['requirements']} requirements: "
        f"{links['controls']} to controls, {links['enhancements']} to enhancements, all resolved."
    )


def _require_800_53_links_resolve(controls) -> dict[str, int]:
    """Refuse unless every 800-53 id the requirements cite exists in 800-53 (#259).

    **A read count proves nothing here; a resolve count does.** NIST pads its
    ids (`AC-02(03)`), and 43 of rev 3's 157 targets are enhancements, which
    the 800-53 catalog nests under their control rather than listing as
    rows. Skip the padding and 22 resolve; key on `control_id` alone and 114
    do. Both are partial crosswalks that look like working ones (80 and 9b
    on #259). So each id must be a `control_id` or an `enhancement_id` of the
    bundled 800-53 catalog, and one that is neither stops the run.
    """
    import json

    from policyforge.ingest.oscal_loader import CROSSWALK_KEY_800_53
    from policyforge.scaffold import bundled_root

    rows = json.loads(
        bundled_root()
        .joinpath("frameworks", "nist-800-53-r5", "controls.json")
        .read_text(encoding="utf-8")
    )
    control_ids = {r["control_id"] for r in rows}
    enhancement_ids = {e["enhancement_id"] for r in rows for e in r.get("enhancements") or []}
    cited = [
        (c.control_id, i.strip())
        for c in controls
        for i in c.source_crosswalk.get(CROSSWALK_KEY_800_53, "").split(",")
        if i.strip()
    ]
    missing = [f"{req} -> {i}" for req, i in cited if i not in control_ids | enhancement_ids]
    if missing:
        raise click.ClickException(
            f"{len(missing)} of {len(cited)} links to 800-53 name no control or enhancement "
            f"in the bundled 800-53 catalog, so nothing was written: {', '.join(missing[:10])}"
        )
    return {
        "total": len(cited),
        "requirements": len({req for req, _ in cited}),
        "controls": sum(1 for _, i in cited if i in control_ids),
        "enhancements": sum(1 for _, i in cited if i in enhancement_ids),
    }


def _crosswalk_mappings(catalog: list[dict]) -> int:
    """How many crosswalk mappings a parsed catalog carries.

    Counted over controls **and** their enhancements, because most of them
    are on enhancements: the bundled HIPAA catalog has 25 at the top level
    and 40 below it, and a count that looked only at the top level would
    call a loss of 40 "no change". policyforge-1d made exactly that mistake
    when first counting, which is why it is counted this way here.

    Deliberately agnostic about the *key*. A mapping is any non-empty
    `source_crosswalk`, whatever framework it names — so this keeps working
    across the `nist` to `nist-800-53` rename in `fix/nist-family-catalog-key`
    and anything else added later. A guard that knew the key would need
    editing every time one was introduced, and would silently stop counting
    until someone did.
    """
    total = 0
    for control in catalog:
        if control.get("source_crosswalk"):
            total += 1
        for enhancement in control.get("enhancements") or []:
            if enhancement.get("source_crosswalk"):
                total += 1
    return total


def _read_catalog(path: Path):
    """`schema.read_catalog`, for the loss checks below: a licensed catalog is an
    envelope since #459 (b), and a check that saw a dict where it expected a
    list would return early and protect nothing."""
    from policyforge.ingest.schema import read_catalog

    return read_catalog(path)


def _preserve_crosswalk(out: Path, replacement: list[dict]) -> None:
    """Carry an existing catalog's crosswalk mappings onto a fresh parse.

    **`etl-hipaa` only refrains from destroying mappings. It never decides
    them** — `etl-hipaa-crosswalk` remains the single thing that reads
    NIST's published crosswalk and says what maps to what. This copies
    forward what is already on disk, keyed by citation, and invents
    nothing.

    Without it `_refuse_crosswalk_loss` refuses every re-parse, forever.
    The regulation carries no crosswalk, so a fresh parse always has zero
    mappings; once `etl-hipaa-crosswalk` has run the catalog has sixty-
    five; `had > keeps` is then permanently true and there is no `--force`
    on this command. **The refusal's own message told the reader to run
    `etl-hipaa` — the command that had just refused.** A guard whose
    remedy is circular is worse than one that simply says no, because the
    reader spends their time doing what it said before concluding the tool
    is wrong.

    The guard keeps its teeth. An entry that disappears from the parse
    takes its mapping with it, because there is nowhere to copy it to, and
    the count check still fires. That is the case worth protecting: a
    parser change that drops a mapped requirement is exactly what should
    stop a write.

    **Local to this command on purpose.** Preserving by citation means a
    mapping survives even if the crosswalk source would no longer produce
    it, which is right here — where the source is a separate command run
    afterwards — and would be wrong as a general policy for a loader that
    reads its own mappings from the document it parses.
    """

    if not out.exists():
        return
    try:
        existing, _ = _read_catalog(out)
    except (OSError, ValueError):
        return
    if not isinstance(existing, list):
        return

    # Flat by citation: a control and an enhancement never share one, and
    # this avoids assuming the parse still nests them the same way.
    known: dict[str, dict] = {}
    for control in existing:
        if control.get("control_id"):
            known[control["control_id"]] = control.get("source_crosswalk") or {}
        for enhancement in control.get("enhancements") or []:
            if enhancement.get("enhancement_id"):
                known[enhancement["enhancement_id"]] = enhancement.get("source_crosswalk") or {}

    for control in replacement:
        # Only where the fresh parse has nothing. A parser that learns to
        # read mappings itself should win over a copy of yesterday's file.
        if not control.get("source_crosswalk") and known.get(control.get("control_id")):
            control["source_crosswalk"] = dict(known[control["control_id"]])
        for enhancement in control.get("enhancements") or []:
            citation = enhancement.get("enhancement_id")
            if not enhancement.get("source_crosswalk") and known.get(citation):
                enhancement["source_crosswalk"] = dict(known[citation])


def _optional_field_counts(catalog: list[dict]) -> dict[str, int]:
    """How many entries carry each field a catalog may legitimately leave empty.

    **Guidance lives in two places, and counting one of them would pass the
    regression in the other.** ARC-AMPE puts a control's supplemental
    guidance in `discussion` and an enhancement's in
    `additional_requirements`: 179 and 127 in the shipped catalog. A guard
    that counted only the first would let a parse that emptied every
    enhancement's guidance through untouched.
    """
    enhancements = [e for c in catalog for e in (c.get("enhancements") or [])]
    return {
        "guidance": sum(1 for c in catalog if (c.get("discussion") or "").strip())
        + sum(1 for e in enhancements if (e.get("additional_requirements") or "").strip()),
        "related": sum(1 for c in catalog if c.get("related_controls")),
        "family": sum(1 for c in catalog if (c.get("family") or "").strip()),
    }


def _refuse_optional_field_loss(out: Path, replacement: list[dict]) -> None:
    """Stop before overwriting a catalog whose optional fields this write empties.

    The same shape as `_refuse_crosswalk_loss` below, for the same reason
    (#265). An optional column whose caption stops matching does not fail the
    parse — every row simply reads it as empty — so the catalog still loads,
    still has 215 controls, and has quietly lost its guidance or its related
    controls. Nothing in the result looks wrong.

    **Compared as counts against the catalog being overwritten**, so the bound
    is derived rather than pinned: the shipped revision's 306 guidance entries
    are what a re-parse of the same workbook reproduces, and what a re-pin
    that drifted a caption would fall below. **Refuses rather than warns**,
    because the parse's own warning is one line in a command's output and the
    loss it describes is invisible in the file.

    **A count that rises or holds is allowed**, and so is a first write with
    nothing to compare against — a guard that could not be satisfied by a
    faithful re-parse would be switched off.
    """

    if not out.exists():
        return
    try:
        existing, _ = _read_catalog(out)
    except (OSError, ValueError):
        return
    if not isinstance(existing, list):
        return

    had, keeps = _optional_field_counts(existing), _optional_field_counts(replacement)
    lost = {field: (had[field], keeps[field]) for field in had if keeps[field] < had[field]}
    if not lost:
        return

    shown = ", ".join(f"{field} {before} -> {after}" for field, (before, after) in lost.items())
    raise click.ClickException(
        f"{out} carries more optional content than this write would leave ({shown}), "
        "so nothing has been written.\n"
        "An optional column whose caption no longer matches does not fail the "
        "parse — every row reads it as empty. Check the header of the source "
        "sheet against the captions in `COLUMN_CAPTIONS`; a changed `&`/`and`, "
        "or a pluralization such as 'Related Control(s)', is enough.\n"
        "If CMS really did remove the content, move the existing catalog aside "
        "and re-run, so the loss is a decision rather than a side effect."
    )


def _refuse_crosswalk_loss(out: Path, replacement: list[dict]) -> None:
    """Stop before overwriting a catalog that carries mappings this write drops.

    `etl-hipaa` parses the regulation, which has no crosswalk in it; the
    mappings arrive from `etl-hipaa-crosswalk`, run afterwards. So running
    the first command alone — the obvious thing to do to refresh a
    provenance stamp — silently reduced 65 mappings to 0. The catalog still
    loaded and still parsed to 34 correct controls, and nothing said a word.

    It refuses rather than warning. A warning printed by one command in a
    two-command pipeline is a line nobody reads, and the failure it is
    warning about is invisible in the result.

    Compared as a count rather than as presence: a write that left 60 of 65
    would pass a presence check and has lost five mappings.
    """

    if not out.exists():
        return
    try:
        existing, _ = _read_catalog(out)
    except (OSError, ValueError):
        # An unreadable or non-JSON file is not a catalog to protect, and
        # refusing on it would block the first write into a broken path.
        return
    if not isinstance(existing, list):
        return

    had = _crosswalk_mappings(existing)
    keeps = _crosswalk_mappings(replacement)
    if had <= keeps:
        return

    raise click.ClickException(
        f"{out} carries {had} crosswalk mapping(s) and this write would leave "
        f"{keeps}, so nothing has been written.\n"
        "`etl-hipaa` copies existing mappings forward by citation, so a "
        "re-parse normally keeps all of them. Losing some means the parse no "
        "longer produces entries that were mapped — a dropped or renamed "
        "citation has taken its mapping with it, because there was nowhere "
        "to copy it to.\n"
        "Find which: compare the citations in the catalog against the ones "
        "the parser now emits. If the change is intended, re-run "
        "`etl-hipaa-crosswalk` afterwards to rebuild the mappings from "
        "NIST's published crosswalk rather than from this file."
    )


@cli.command("etl-hipaa")
@click.option(
    "--date",
    default=None,
    help="Specific eCFR effective date (YYYY-MM-DD) to fetch, for reproducibility. "
    "Default: eCFR's current published date for Title 45.",
)
@click.option(
    "--out",
    default=Path("data/frameworks/hipaa-security-rule/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed control data.",
)
def etl_hipaa(date: str | None, out: Path):
    """Fetch the HIPAA Security Rule (45 CFR 164 Subpart C) from eCFR's public
    API and parse it into this project's data schema. Public domain - a US
    federal regulation, same basis as NIST/FedRAMP/ARC-AMPE - so unlike
    HITRUST/GovRAMP this is safe to bundle directly. See
    ingest/hipaa_loader.py for parsing details.
    """
    import dataclasses
    import json

    from policyforge.ingest.hipaa_loader import (
        current_ecfr_date,
        ecfr_source_url,
        fetch_ecfr_subpart_c_xml,
        parse_hipaa_security_rule,
    )
    from policyforge.ingest.provenance import record_source_provenance

    # Resolved here rather than inside the fetch so the date recorded is
    # provably the date fetched. eCFR has no tags — the effective date is
    # the only thing that names a revision — so it is this framework's
    # equivalent of the OSCAL release tag.
    date = date or current_ecfr_date()

    xml_text = fetch_ecfr_subpart_c_xml(date=date)
    controls = parse_hipaa_security_rule(xml_text)
    parsed = [dataclasses.asdict(c) for c in controls]

    # Carry the mappings across, then check for loss. Without this the
    # check below refuses every re-parse forever — see `_preserve_crosswalk`.
    _preserve_crosswalk(out, parsed)

    # Before anything is written, including the provenance stamp, so a
    # refusal leaves no half-updated catalog directory behind.
    _refuse_crosswalk_loss(out, parsed)

    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps(parsed, indent=2))
    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=date,
        source_url=ecfr_source_url(date),
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(f"Recorded provenance: {date} sha256:{stamp[:16]}… -> {out.parent}")
    click.echo(f"Parsed {len(controls)} HIPAA Security Rule requirements -> {out}")


@cli.command("etl-info-blocking")
@click.option(
    "--date",
    default=None,
    help="Specific eCFR effective date (YYYY-MM-DD) to fetch, for reproducibility. "
    "Default: eCFR's current published date for Title 45.",
)
@click.option(
    "--out",
    default=Path("data/frameworks/cfr-171-information-blocking/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed data.",
)
def etl_info_blocking(date: str | None, out: Path):
    """Fetch 45 CFR Part 171 (information blocking) from eCFR's public API
    and parse it into this project's data schema. Public domain - a US
    federal regulation, same basis as NIST/FedRAMP/ARC-AMPE/HIPAA - so safe
    to bundle directly.

    This part is not a control catalog. It defines information blocking and
    then sets out the exceptions to it, so each "control" here is a
    condition under which a practice does NOT count as blocking. Citing
    171.203(a) says a practice qualifies for the security exception, not
    that a safeguard exists. See ingest/info_blocking.py and the catalog's
    README before mapping it to anything.
    """
    import dataclasses
    import json

    from policyforge.ingest.info_blocking import (
        current_ecfr_date,
        ecfr_source_url,
        fetch_part_xml,
        parse_information_blocking,
    )
    from policyforge.ingest.provenance import record_source_provenance

    # Resolved here rather than inside the fetch, so the date recorded is
    # provably the date fetched — same reasoning as etl-hipaa above.
    date = date or current_ecfr_date()

    xml_text = fetch_part_xml(date=date)
    controls = parse_information_blocking(xml_text)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))
    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=date,
        source_url=ecfr_source_url(date),
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(f"Recorded provenance: {date} sha256:{stamp[:16]}… -> {out.parent}")
    conditions = sum(len(c.enhancements) for c in controls)
    click.echo(
        f"Parsed {len(controls)} Part 171 sections carrying {conditions} conditions -> {out}"
    )


@cli.command("etl-part2")
@click.option(
    "--date",
    default=None,
    help="Specific eCFR effective date (YYYY-MM-DD) to fetch, for reproducibility. "
    "Default: eCFR's current published date for Title 42.",
)
@click.option(
    "--out",
    default=Path("data/frameworks/cfr-42-part-2-sud-records/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed data.",
)
def etl_part2(date: str | None, out: Path):
    """Fetch 42 CFR Part 2 (confidentiality of substance use disorder patient
    records) from eCFR's public API and parse its security sections into this
    project's data schema. Public domain - a US federal regulation, same basis
    as NIST/FedRAMP/ARC-AMPE/HIPAA - so safe to bundle directly.

    TWO of this part's thirty-eight sections are controls, and that is the
    whole catalog. Most of Part 2 is conduct: when a disclosure is permitted,
    what a consent must contain, what a court must find before it orders a
    record produced. Citing 2.66 says a court may authorise a disclosure; it
    does not say a safeguard exists. The thinness is the correct answer rather
    than a parse failure - see the catalog's README for the test that decided
    it and for what it rejected.
    """
    import dataclasses
    import json

    from policyforge.ingest.part2_loader import (
        current_ecfr_date,
        ecfr_source_url,
        fetch_part_xml,
        parse_part2,
    )
    from policyforge.ingest.provenance import record_source_provenance

    # Resolved here rather than inside the fetch, so the date recorded is
    # provably the date fetched - same reasoning as etl-hipaa above.
    date = date or current_ecfr_date()

    xml_text = fetch_part_xml(date=date)
    controls = parse_part2(xml_text)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))
    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=date,
        source_url=ecfr_source_url(date),
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(f"Recorded provenance: {date} sha256:{stamp[:16]}… -> {out.parent}")
    requirements = sum(len(c.enhancements) for c in controls)
    click.echo(
        f"Parsed {len(controls)} Part 2 security sections carrying "
        f"{requirements} requirements -> {out}"
    )


@cli.command("etl-onc")
@click.option(
    "--date",
    default=None,
    help="eCFR effective date (YYYY-MM-DD) to fetch, for reproducibility. "
    "Default: eCFR's current published date for Title 45.",
)
@click.option(
    "--out",
    default=Path("data/frameworks/cfr-170-315-onc-certification/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed data.",
)
def etl_onc(date: str | None, out: Path):
    """Fetch the ONC certification criteria (45 CFR 170.315) from eCFR's
    public API and parse them into this project's data schema. A US federal
    regulation, so safe to bundle.

    ONE CRITERION IS ONE CONTROL, cited as 170.315(g)(10). Its category is
    the family, and its sub-paragraphs are its statement.

    THE PARSE IS REFUSED unless the live criteria EQUAL an independently
    agreed set (#179): this catalog shipped once with fabricated criteria
    (#152, reverted as #163). Reserved and expired criteria are left out and
    reported by id on every run.
    """
    import dataclasses
    import datetime as dt
    import json

    from policyforge.ingest.onc_loader import (
        FRAMEWORK_VERSION,
        OncParseError,
        current_ecfr_date,
        ecfr_source_url,
        fetch_part_xml,
        parse_onc_criteria,
    )
    from policyforge.ingest.provenance import record_source_provenance

    # Resolved here rather than inside the fetch, so the date recorded is
    # provably the date fetched. No saved-XML option, deliberately: the XML
    # parser's safety rests on eCFR being the only source (see onc_loader).
    date = date or current_ecfr_date()
    xml_text = fetch_part_xml(date=date)

    try:
        controls, excluded = parse_onc_criteria(xml_text, as_of=dt.date.fromisoformat(date))
    except OncParseError as exc:
        raise click.ClickException(str(exc)) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))
    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=date,
        source_url=ecfr_source_url(date),
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(f"Recorded provenance: {date} sha256:{stamp[:16]}… -> {out.parent}")
    click.echo(f"Parsed {len(controls)} certification criteria ({FRAMEWORK_VERSION}) -> {out}")
    for kind in ("reserved", "expired"):
        ids = excluded[kind]
        if ids:
            click.echo(f"Excluded {len(ids)} {kind}: " + ", ".join(ids))


@cli.command("etl-ai-rmf")
@click.option(
    "--out",
    default=Path("data/frameworks/nist-ai-rmf/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed data.",
)
@click.option(
    "--html",
    default=None,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Parse a saved copy of the Core page instead of fetching. For "
    "reproducing a past run, or working without a network.",
)
def etl_ai_rmf(out: Path, html: Path | None):
    """Fetch the NIST AI Risk Management Framework 1.0 Core and parse it into
    this project's data schema. A US government work, so safe to bundle.

    Nineteen categories carrying seventy-two subcategories, sourced from
    AIRC. CPRT does not publish the Core; that was checked rather than
    assumed.

    THIS CATALOG STATES OUTCOMES, NOT OBLIGATIONS, and that changes what a
    citation to it proves. Every other catalog here says what an
    organization must do; the AI RMF Core says what should end up true --
    NIST puts the actions in the separately versioned, explicitly voluntary
    Playbook. So a document citing a subcategory can be fully traceable and
    still commit nobody to anything, and `satisfies` will resolve the
    citation and then stop, because there is no crosswalk. Mapping an
    outcome to a control would assert that the control achieves the
    outcome, which is the claim NIST declined to make. The catalog's README
    carries the long form.
    """
    import dataclasses
    import json

    from policyforge.ingest.ai_rmf import (
        SOURCE_URL,
        AiRmfParseError,
        expected_shape,
        fetch_core_html,
        parse_ai_rmf,
    )
    from policyforge.ingest.provenance import record_source_provenance

    if html is not None:
        page = html.read_text(encoding="utf-8", errors="replace")
        click.echo(f"Parsing saved page {html} instead of fetching.")
    else:
        page = fetch_core_html()

    # The pin comes from the catalog being regenerated; a scratch --out falls
    # back to the bundled catalog's (#189). Neither -> a loud refusal, since a
    # parse with no expected shape would accept any.
    pinned = out.parent / "framework.yaml"
    if not pinned.exists():
        bundled = [d / "nist-ai-rmf" / "framework.yaml" for d in _bundled_catalog_dirs()]
        pinned = next((p for p in bundled if p.exists()), pinned)
    # A refusal is the guard working, so it reads as a sentence, not a traceback.
    try:
        controls = parse_ai_rmf(page, expected_shape=expected_shape(pinned))
    except AiRmfParseError as exc:
        raise click.ClickException(str(exc)) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))

    # The digest is taken over the PARSED output rather than the fetched
    # bytes, matching every other loader here: AIRC's page carries
    # navigation and build ids that change without the Framework changing,
    # so hashing the page would report drift on every deploy and teach
    # everyone to ignore it.
    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=f"AI RMF {controls[0].framework_version}",
        source_url=SOURCE_URL,
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(f"Recorded provenance: sha256:{stamp[:16]}… -> {out.parent}")
    subcategories = sum(len(c.enhancements) for c in controls)
    click.echo(
        f"Parsed {len(controls)} AI RMF categories carrying {subcategories} subcategories -> {out}"
    )


@cli.command("etl-ai-rmf-playbook")
@click.option(
    "--out",
    default=Path("data/frameworks/nist-ai-rmf-playbook/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed data.",
)
@click.option(
    "--json",
    "json_path",
    default=None,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Parse a saved copy of playbook.json instead of fetching. It must be the "
    "pinned export, byte for byte.",
)
@click.option(
    "--core",
    default=Path("data/frameworks/nist-ai-rmf/controls.json"),
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="The AI RMF Core catalog. Every Playbook entry must serve one of its subcategories.",
)
def etl_ai_rmf_playbook(out: Path, json_path: Path | None, core: Path):
    """Fetch the NIST AI RMF Playbook -- NIST's suggested actions per Core
    subcategory -- and parse it into this project's data schema. A US
    government work, so safe to bundle.

    THE PLAYBOOK IS VOLUNTARY: NIST calls its suggestions "voluntary" and
    "neither a checklist nor set of steps to be followed in its entirety".
    A document citing an action may say NIST suggests it, never that NIST
    requires it. The catalog's README carries the long form.

    Pinned to one export by its SHA-256, because NIST publishes no revision
    number: a different export is refused, and on the scheduled drift job
    that refusal is the job working. The per-subcategory action counts are
    pinned to an independent count, and a short or long parse is refused
    whole.
    """
    import dataclasses
    import json

    from policyforge.ingest.ai_rmf_playbook import (
        FRAMEWORK_VERSION,
        SOURCE_URL,
        AiRmfPlaybookError,
        fetch_playbook,
        parse_playbook,
    )
    from policyforge.ingest.provenance import record_source_provenance

    if json_path is not None:
        raw = json_path.read_bytes()
        click.echo(f"Parsing saved export {json_path} instead of fetching.")
    else:
        raw, last_modified = fetch_playbook()
        click.echo(f"Fetched {SOURCE_URL} (Last-Modified: {last_modified or 'not sent'}).")

    core_rows = json.loads(core.read_text(encoding="utf-8"))
    subcategories = {e["enhancement_id"] for row in core_rows for e in row["enhancements"]}
    try:
        controls = parse_playbook(raw, subcategories)
    except AiRmfPlaybookError as exc:
        # A refused export is the pin WORKING -- on the drift job, the
        # expected outcome when NIST re-publishes -- so it is a clean error
        # line naming the reason, not a traceback. Nothing is written.
        raise click.ClickException(str(exc)) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))

    # Digest over the PARSED output, as every other loader here; the export's
    # own hash is the pin in `ai_rmf_playbook.SOURCE_SHA256`.
    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=FRAMEWORK_VERSION,
        source_url=SOURCE_URL,
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(f"Recorded provenance: sha256:{stamp[:16]}… -> {out.parent}")
    actions = sum(len(c.enhancements) for c in controls)
    click.echo(
        f"Parsed {len(controls)} AI RMF subcategories carrying {actions} suggested actions -> {out}"
    )


def _bundled_catalog_dirs() -> list[Path]:
    """The bundled catalog directories that exist and must never take licensed data.

    Every `data/frameworks` found by searching upward from where the command
    runs, the way git finds a repository root, plus the one in the checkout
    this code was imported from.

    Upward rather than only in the working directory, and the difference was
    a bypass: the first version of this looked for `./data/frameworks`, so a
    command run from inside `data/` looked for `data/data/frameworks`, found
    nothing, and let `--out frameworks/...` write a licensed catalog into the
    repository's bundled directory. What must be protected is the directory
    that gets committed and pushed, wherever the command happens to run.

    Searching upward cannot over-refuse: a `data/frameworks` somewhere above
    only matters if the destination is inside it.
    """
    cwd = Path.cwd()
    candidates = [base / "data" / "frameworks" for base in (cwd, *cwd.parents)]
    here = Path(__file__).resolve()
    if len(here.parents) > 3:
        candidates.append(here.parents[3] / "data" / "frameworks")
    found: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir() and candidate not in found:
            found.append(candidate)
    return found


def _names_a_bundled_catalog_directory(out: Path) -> bool:
    """Whether `out`, as written or as resolved, runs through a `data/frameworks`.

    The identity test below only knows the bundled directories it can find
    from here, so another checkout's is protected by this one alone. It
    compares path components, case-folded, on both the path as given and its
    resolved form. Case-folded because on a case-insensitive filesystem
    `Data/Frameworks` is the same directory: run from outside another
    checkout, an absolute `.../Data/Frameworks/govramp/controls.json` got
    past the case-sensitive substring this replaced and wrote the catalog
    into that checkout's bundled directory (found by policyforge-ba).
    Resolved because `data/x/../frameworks` is the same directory too.

    On a case-sensitive filesystem this refuses a `Data/Frameworks` that is a
    different directory. That is the chosen side to err on: the cost is
    naming another directory, and the other side's cost is a licensed
    catalog in a public repository.
    """
    import os
    from itertools import pairwise

    for spelling in (out, Path(os.path.realpath(out))):
        parts = [part.casefold() for part in spelling.parts]
        if ("data", "frameworks") in pairwise(parts):
            return True
    return False


def _lands_in_bundled_catalogs(out: Path) -> bool:
    """Whether writing `out` would put a file inside a bundled catalog directory.

    Asked of the filesystem, not of the path's spelling. `out` usually does
    not exist yet, so each existing ancestor is compared to each bundled
    directory with `samefile`, which follows the filesystem's own rules: case
    folding where the filesystem folds case, `..`, symlinks and junctions. The
    path is joined to the working directory without normalising it, so a `..`
    is resolved physically, the way the write would resolve it.
    """
    import os

    bundled = _bundled_catalog_dirs()
    if not bundled:
        return False
    probe = out if out.is_absolute() else Path.cwd() / out
    for ancestor in (probe, *probe.parents):
        try:
            if not ancestor.exists():
                continue
            if any(os.path.samefile(ancestor, directory) for directory in bundled):
                return True
        except OSError:
            continue
    return False


def _add_framework_id(manifest: Path, key: str) -> str:
    """Add `framework_id: key` to the user's own `manifest`, or say why not.

    **The file is the user's, so it is never left worse** (1d on #347). One
    that does not parse, or does not hold a mapping, is not touched. After
    the line is appended, the file is parsed again and must hold exactly
    what it held plus `framework_id`; otherwise -- a `...` document-end
    marker puts the line in a second document, for one -- its original
    bytes are put back. Every outcome is said, never "Declared" for a file
    that did not take the declaration.
    """
    import yaml

    original = manifest.read_bytes()
    add = f"Add `framework_id: {key}` to it yourself."
    try:
        before = yaml.safe_load(original.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return f"{manifest} could not be read ({type(exc).__name__}), so it was left alone. {add}"
    if before is None:
        before = {}
    if not isinstance(before, dict):
        return f"{manifest} does not hold a mapping, so it was left alone. {add}"
    if "framework_id" in before:
        existing = str(before["framework_id"] or "").strip()
        if not existing:
            return f"{manifest} declares an empty framework_id, so it was left alone. {add}"
        if existing != key:
            return (
                f"{manifest} already declares framework_id {existing}; the import would "
                f"have declared {key}. Keeping yours."
            )
        return f"{manifest} already declares framework_id {key}."

    text = original.decode("utf-8")
    write_text_lf(manifest, text.rstrip("\r\n") + f"\nframework_id: {key}\n")
    try:
        after = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        after = None
    if after != {**before, "framework_id": key}:
        manifest.write_bytes(original)
        return (
            f"{manifest} did not take an appended framework_id (it would have changed what "
            f"the file says), so it was put back as it was. {add}"
        )
    return f"Declared framework_id {key} in {manifest}."


def _declare_catalog(out: Path, *, framework: str, source: str) -> str:
    """Declare the catalog just written at `out` by its framework key (#295).

    A catalog a user brings has no `framework.yaml`, so its name was keyed by
    prose -- an alias if one matched, else its first word. The importer knows
    what it imported, so it writes the declaration the bundled catalogs carry:
    `framework_id:` equal to the key the name already had, so nothing keyed
    before the import moves. The manifest says `licence: licensed`, because
    everything these importers read is.

    **A manifest already there is the user's.** Without a `framework_id` it
    gains one line (comments and keys kept); with one, it is left alone and
    a different value is named -- the declaration wins, and a person chose
    it. Returns what was done, for the command to print.
    """
    from policyforge.mapping.crosswalk import prose_framework_key, reset_declared_keys

    key = prose_framework_key(framework)
    manifest = out.parent / "framework.yaml"
    if not manifest.exists():
        write_text_lf(
            manifest,
            f"name: {framework}\nframework_id: {key}\nlicence: licensed\nsource: {source}\n",
        )
        done = f"Declared {manifest}: framework_id {key}."
    else:
        done = _add_framework_id(manifest, key)
    # Both caches, so a long-running process keys the new declaration and
    # knows the new catalog's name in a tag (1d on #347).
    from policyforge.content.tags import reset_known_framework_names

    reset_declared_keys()
    reset_known_framework_names()
    # Where a declaration is made is where its prose collision is shown (80 on #347).
    from policyforge.frameworks.registry import key_collisions

    facts = key_collisions(directory=out.parent)
    return "\n".join([done, *(f"Note: {fact}" for fact in facts)])


def _suggested_dir(product: str) -> str:
    return "-".join(product.lower().split()[:1]) or "licensed"


def _guard_licensed_write(out: Path, *, force: bool, product: str, licence: str, noun: str):
    """Refuse to write a licensed catalog anywhere it could be redistributed.

    `etl-hitrust` and `etl-govramp` each carried a copy of this, word for word
    apart from what the catalog is called. Two copies of a guard that exists
    to keep licensed content out of a public repository is two places for it
    to be weakened, and one of them would be the one nobody checks.

    Three rules, in order. The bundled `data/frameworks/` directory never takes
    a licensed catalog, whatever the flags — it is this project's public,
    redistributable half, and a file written there gets committed and pushed.

    **The destination must be one the model boundary will classify as
    licensed, whatever the flags** (#459, 80's ruling (a)). The boundary
    (`llm/boundary.classify_path`) knows a licensed catalog by where it sits:
    under `local_content/`, or in a framework directory with no manifest. This
    guard used to ask only whether git would stage the file, so `--out
    output/...` passed it and the catalog then classified as
    organization-internal, which a hosted model may read. `--force` does not
    bypass this: it grants a repository permission to carry licensed content,
    which is a different question from which models may read it.

    Anywhere licensed, a gitignored destination needs no permission, because a
    file git will never stage cannot be redistributed by accident; a tracked
    one needs the repository to have declared it may hold licensed content,
    or an explicit --force.
    """
    from policyforge.frameworks.registry import frameworks_config, is_ignored

    # The bundled directory is this project's public, redistributable half.
    # A licensed catalog written there would be committed and pushed.
    #
    # Two tests, because each alone was bypassable. The spelling test refuses
    # any path running through data/frameworks, in this checkout or another.
    # The identity test asks the filesystem whether the destination is inside
    # the bundled directory this command would actually read, which catches
    # `--out frameworks/...` run from inside data/. Both bypasses were
    # reproduced with --force before their fixes.
    if _names_a_bundled_catalog_directory(out) or _lands_in_bundled_catalogs(out):
        raise click.ClickException(
            f"{out} is inside data/frameworks/, which is this project's bundled "
            f"public content. A {product} must never be written there. Use "
            "local_content/ or your own repository's frameworks/ directory."
        )

    # No config file is a supported way to run (`load_config_or_empty`): the
    # boundary then applies its defaults, the strictest reading, rather than
    # failing with a raw FileNotFoundError. This ran after the parse until
    # #32 put it first, which is when a missing config began to reach it.
    config = load_config_or_empty()
    from policyforge.llm.boundary import LICENSED, classify_path

    # Asked about the world as it will be once the file is written: whether a
    # directory is a framework, and which of two same-named ones wins, depends
    # on the file this command is about to create.
    landing = classify_path(out, config, assume_written=True)
    if landing.klass != LICENSED:
        raise click.ClickException(
            f"{out} would be read as {landing.klass} ({landing.reason}), so your "
            f"boundary settings for licensed content would not apply to this {product}: "
            "it would go wherever that class of content may go. Write it under "
            f"local_content/, e.g. --out local_content/{_suggested_dir(product)}/"
            "controls.json, so they do."
        )

    # Gitignored destinations need no permission: a file git will never
    # stage cannot be redistributed by accident. Everywhere else, the
    # repository has to have said it may hold licensed content.
    ignored = is_ignored(out)
    permitted = bool(frameworks_config(config).get("allow_licensed_in_repo"))
    if not ignored and not permitted and not force:
        unknown = "" if ignored is False else " (and git could not confirm it is ignored)"
        click.echo(
            f"\n{out} is not gitignored{unknown}, and this repository has not "
            "declared `frameworks.allow_licensed_in_repo`, so writing a licensed "
            "catalog there is refused. Write it under local_content/, set that "
            f"flag in config.yaml if your {licence} licence permits your repository "
            f"to carry the {noun}, or pass --force."
        )
        raise SystemExit(1)


def _run_licensed(
    command: str,
    *,
    out: Path | None,
    force: bool,
    product: str,
    licence: str,
    noun: str,
    sample: Path,
    unwritten: str,
    **options,
):
    """Run a licensed parser from its plugin, then write what it returns (#32).

    **The public side owns every step that keeps licensed content safe**
    (1d on #32, comment 564): the licensed-write guard (GH#460) runs BEFORE
    the plugin is asked for anything, and the catalog is written here, in
    the #10 licence envelope, with `licence: licensed` in its manifest set
    last. A plugin only parses and returns; it never sees the output path,
    so no plugin can write a licensed catalog past the guard or without its
    marker.
    """
    import dataclasses

    from policyforge import plugins
    from policyforge.ingest.schema import LICENSED_MARKER, Control, catalog_json

    if out is not None:
        _guard_licensed_write(out, force=force, product=product, licence=licence, noun=noun)
    try:
        parser = plugins.require(command)
    except plugins.PluginError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        result = parser.parse(**options)
    except plugins.ParseRefused as exc:
        framework = plugins.GENERATE_PARSER_FRAMEWORK.get(command)
        hint = (
            f"\n\nIf this file's layout is one this project has not seen, run:\n  "
            f"policyforge generate-parser --framework {shlex.quote(framework)} --sample "
            f"{shlex.quote(str(sample))}"
            if exc.suggest_generate_parser and framework
            else ""
        )
        raise click.ClickException(f"{exc}\nNothing was written.{hint}") from exc
    if not isinstance(result, plugins.ParseResult) or not all(
        isinstance(c, Control) for c in result.controls
    ):
        raise click.ClickException(
            f"The plugin that provides `{command}` ({parser.origin}) returned something other "
            "than a catalog of controls. Nothing was written."
        )
    click.echo(result.report)
    if out is None:
        click.echo(unwritten)
        return

    manifest_path = out.parent / "framework.yaml"
    declared = None
    if result.framework_id or result.manifest:
        # Read, and refused if it isn't a mapping, BEFORE controls.json is
        # written: afterwards, a raw error would leave the pair half-updated
        # (1d on #12).
        declared = _read_manifest(manifest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # The catalog carries its licence (#459 (b)): licensed wherever it is saved.
    write_text_lf(
        out,
        catalog_json([dataclasses.asdict(c) for c in result.controls], licence=LICENSED_MARKER),
    )
    click.echo(f"\nWrote {result.written or f'{len(result.controls)} controls'} -> {out}")
    if declared is None:
        click.echo(_declare_catalog(out, framework=parser.framework, source=result.source))
        return
    import yaml

    declared.update(result.manifest)
    declared.update({"name": parser.framework, "framework_id": result.framework_id})
    declared["source"] = result.source
    declared["licence"] = "licensed"  # last, so no plugin key can change it
    write_text_lf(
        manifest_path, yaml.safe_dump(declared, sort_keys=False, allow_unicode=True, width=88)
    )
    click.echo(
        f"  Wrote {manifest_path} (licence: licensed). Content classed licensed reaches only "
        "local models under the default boundary."
    )


def _read_manifest(manifest_path: Path) -> dict:
    """The manifest already at `manifest_path`, or {} if none; refused, naming
    the file, if it isn't YAML or isn't a mapping."""
    import yaml

    if not manifest_path.exists():
        return {}
    try:
        loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise click.ClickException(
            f"{manifest_path} could not be read as YAML ({type(exc).__name__}), so the "
            "catalog's manifest can't be written into it.\nNothing was written."
        ) from exc
    if loaded is not None and not isinstance(loaded, dict):
        raise click.ClickException(
            f"{manifest_path} holds a {type(loaded).__name__}, not a mapping, so the "
            "catalog's manifest can't be written into it.\nNothing was written."
        )
    return loaded or {}


@cli.command("etl-hitrust")
@click.option(
    "--export",
    "export_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Your own MyCSF/HITRUST CSF export (.csv, .tsv, .xlsx, .html, .mhtml). "
    "Keep it in local_content/, which is gitignored.",
)
@click.option(
    "--version",
    "version",
    default="",
    help='CSF release this export is of, e.g. "v11.7". Default: read from the '
    "filename if it names one - a MyCSF export states its release nowhere inside.",
)
@click.option(
    "--out",
    default=None,
    type=click.Path(path_type=Path),
    help="Write the parsed catalog here as controls.json. Omit to parse and "
    "report without writing anything, which is the safe default for licensed "
    "content.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Write --out even though this repository has not declared "
    "`frameworks.allow_licensed_in_repo`.",
)
def etl_hitrust(export_path: Path, version: str, out: Path | None, force: bool):
    """Parse your own licensed HITRUST CSF export into this project's schema.

    HITRUST CSF is licensed content. This project never bundles it and never
    fetches it: you supply an export from your own MyCSF licence and it is
    parsed locally, in memory. Nothing is written unless you pass --out.

    Prefer a CSV export where you have the choice. The rendered HTML/MHTML of
    the same report is more clearly labelled but carries markedly fewer
    authoritative-source mappings, which is most of what makes a CSF export
    worth ingesting.

    The parser is part of the licensed-framework plugin, which is
    distributed separately; without it, this command says so. If the
    plugin can't read your export, or you don't have it, `policyforge
    generate-parser --framework hitrust` drafts a loader from a sample.
    """
    _run_licensed(
        "etl-hitrust",
        out=out,
        force=force,
        product="HITRUST export",
        licence="MyCSF",
        noun="export",
        sample=export_path,
        unwritten=(
            "\nNothing written. Pass --out <path> to save a controls.json - into "
            "a repository whose licence permits holding HITRUST content."
        ),
        export_path=export_path,
        version=version,
    )


@cli.command("etl-govramp")
@click.option(
    "--export",
    "export_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Your own GovRAMP controls matrix workbook (.xlsx/.xlsm), as published. "
    "Keep it in local_content/, which is gitignored.",
)
@click.option(
    "--impact-level",
    type=click.Choice(["Low", "Moderate", "High"], case_sensitive=False),
    default=None,
    help="FIPS 199 level this matrix is for. Default: read from the workbook's "
    "cover sheet, then from the filename. Worth setting on a template somebody "
    "has been working in, where the cover sheet is the first thing edited.",
)
@click.option(
    "--version",
    "version",
    default="",
    help='Revision and template version to stamp, e.g. "Rev 5 (V1.06)". '
    "Default: the revision from the cover sheet plus the version in the filename.",
)
@click.option(
    "--out",
    default=None,
    type=click.Path(path_type=Path),
    help="Write the parsed catalog here as controls.json. Omit to parse and "
    "report without writing anything, which is the safe default for licensed "
    "content.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Write --out even though this repository has not declared "
    "`frameworks.allow_licensed_in_repo`.",
)
def etl_govramp(export_path: Path, impact_level: str | None, version: str, out: Path | None, force):
    """Parse your own GovRAMP controls matrix into this project's schema.

    GovRAMP's Terms & Conditions claim ownership of the documents published
    on their site, and no redistribution grant was found - so, like HITRUST,
    this project never bundles the matrix and never fetches it. You supply
    the workbook and it is parsed locally, in memory. Nothing is written
    unless you pass --out.

    Pass the workbook as GovRAMP publishes it rather than an extract of it:
    the controls sheet is found among the template's other thirteen by its
    header captions, so the Low, Moderate and High workbooks all read
    without being told which they are.

    What this gives you that the 800-53 catalog does not is the profile's
    own two additions - the parameter values GovRAMP has already decided
    ("at least every 3 years"), which `policyforge parameters` would
    otherwise leave open for you to answer, and the requirements it layers
    on top of a control - across the Core/Ready/Authorized tiers. Those
    tiers are *not* impact levels: one Moderate matrix holds all three.

    The parser is part of the licensed-framework plugin, which is
    distributed separately; without it, this command says so. If the
    plugin can't read your workbook, or you don't have it, `policyforge
    generate-parser --framework govramp` drafts a loader from a sample.
    """
    _run_licensed(
        "etl-govramp",
        out=out,
        force=force,
        product="GovRAMP matrix",
        licence="GovRAMP",
        noun="matrix",
        sample=export_path,
        unwritten=(
            "\nNothing written. Pass --out <path> to save a controls.json - into "
            "a repository whose licence permits holding GovRAMP content."
        ),
        export_path=export_path,
        impact_level=(impact_level or "").title(),
        version=version,
    )


@cli.command("etl-hipaa-crosswalk")
@click.option(
    "--controls",
    "controls_path",
    default=Path("data/frameworks/hipaa-security-rule/controls.json"),
    type=click.Path(exists=True, path_type=Path),
    help="HIPAA controls.json produced by `etl-hipaa`, to enrich in place.",
)
@click.option(
    "--fixture",
    "fixture_path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Parse this saved CPRT payload instead of fetching "
    "(e.g. tests/fixtures/cprt_hipaa_to_800-53r5.json). Offline/reproducible.",
)
@click.option(
    "--out",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to write the enriched data (default: overwrite --controls).",
)
def etl_hipaa_crosswalk(controls_path: Path, fixture_path: Path | None, out: Path | None):
    """Attach NIST's official HIPAA-Security-Rule-to-SP-800-53-Rev-5 crosswalk
    to the HIPAA control data, so `map`/`synthesize` can pull HIPAA
    requirements into a NIST-anchored topic.

    Source is NIST's Cybersecurity and Privacy Reference Tool (CPRT), not SP
    800-66r2's PDF - Appendix D of that document states the mapping table was
    removed from the PDF and published in CPRT instead. See
    ingest/hipaa_crosswalk_loader.py.
    """
    import dataclasses
    import json

    from policyforge.ingest.hipaa_crosswalk_loader import (
        CPRT_FRAMEWORK_NAME,
        CPRT_FRAMEWORK_VERSION,
        apply_crosswalk,
        fetch_cprt_crosswalk,
        parse_cprt_crosswalk,
    )
    from policyforge.ingest.schema import load_controls

    if fixture_path is not None:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        click.echo(f"Source: {fixture_path} (saved CPRT export)")
    else:
        payload = fetch_cprt_crosswalk()
        click.echo(f"Source: CPRT {CPRT_FRAMEWORK_NAME} [{CPRT_FRAMEWORK_VERSION}]")

    mapping = parse_cprt_crosswalk(payload)
    controls = load_controls(controls_path)
    report = apply_crosswalk(controls, mapping)

    out_path = out or controls_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out_path, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))

    # This step rewrites the same controls.json `etl-hipaa` just stamped, so
    # the recorded hash has to follow it. Only the hash: source_ref and
    # source_url describe where the regulation text came from, and attaching
    # a crosswalk does not change that.
    from policyforge.ingest.provenance import restamp_content

    restamped = restamp_content(out_path.parent / "framework.yaml", content=out_path.read_bytes())

    click.echo(
        f"Mapped {report.mapped_controls} standards and "
        f"{report.mapped_enhancements} implementation specifications "
        f"from {len(mapping)} CPRT citations -> {out_path}"
    )
    if restamped is not None:
        click.echo(f"Updated recorded hash to sha256:{restamped[:16]}…")
    # Anything NIST's crosswalk doesn't cover is reported rather than left
    # invisible — this is compliance data, so a gap should be an observation,
    # not a surprise.
    if report.unmapped_requirements:
        click.echo(
            f"Not covered by NIST's crosswalk ({len(report.unmapped_requirements)}): "
            + ", ".join(report.unmapped_requirements)
        )
    if report.unmatched_citations:
        click.echo(
            f"WARNING: {len(report.unmatched_citations)} CPRT citation(s) matched no HIPAA "
            "requirement and were skipped: " + ", ".join(report.unmatched_citations)
        )
    if report.unparsed_nist_ids:
        click.echo(
            f"WARNING: {len(report.unparsed_nist_ids)} CPRT control ID(s) were unparseable "
            "and were skipped: " + ", ".join(report.unparsed_nist_ids)
        )


@cli.command("etl-fedramp")
@click.option(
    "--nist",
    "nist_path",
    default=Path("data/frameworks/nist-800-53-r5/controls.json"),
    type=click.Path(path_type=Path),
    help="The 800-53 catalog this tailoring is applied to. Produced by `policyforge etl-oscal`.",
)
@click.option(
    "--out",
    default=Path("data/frameworks/fedramp/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed control data.",
)
def etl_fedramp(nist_path: Path, out: Path):
    """Fetch FedRAMP's published control tailoring and apply it to 800-53.

    Public domain - a US federal program, same basis as the NIST and eCFR
    sources. Read from `fedramp-consolidated-rules.json` in `FedRAMP/rules`,
    which that repository calls its canonical rules dataset.

    This writes a profile, not a baseline. FedRAMP's machine-readable
    Low/Moderate/High baseline selection used to live in
    `GSA/fedramp-automation`, and that repository no longer exists - so
    nothing here says which controls a given system must implement, and
    `Control.baseline` is deliberately left unset rather than guessed. What
    it does carry is FedRAMP's own two additions to the controls it does
    speak to: the organization-defined values FedRAMP has already decided,
    and the guidance it layers on top. See ingest/fedramp.py.
    """
    import dataclasses
    import json

    from policyforge.ingest.fedramp import (
        FEDRAMP_RULES_REF,
        RULES_URL,
        fetch_fedramp_rules,
        parse_fedramp_rules,
    )
    from policyforge.ingest.provenance import record_source_provenance
    from policyforge.ingest.schema import load_controls

    if not nist_path.exists():
        raise click.ClickException(
            f"No 800-53 catalog at {nist_path}. FedRAMP publishes no control text of "
            "its own — it names controls NIST wrote and tailors them — so this "
            "command needs that catalog to join against. Run `policyforge etl-oscal` "
            "first, or point --nist at your own copy."
        )

    nist_controls = load_controls(nist_path)
    rules = fetch_fedramp_rules()
    controls, summary = parse_fedramp_rules(rules, nist_controls)

    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))
    enhancements = sum(len(c.enhancements) for c in controls)
    click.echo(
        f"Wrote {len(controls)} controls and {enhancements} enhancements "
        f"({rules.get('info', {}).get('version', '?')}) -> {out}"
    )
    for line in summary.format_report():
        click.echo(f"  {line}")

    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=FEDRAMP_RULES_REF,
        source_url=RULES_URL,
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(
            f"Recorded provenance: {FEDRAMP_RULES_REF[:12]}… sha256:{stamp[:16]}… -> {out.parent}"
        )


@cli.command("etl-arc-ampe")
@click.option(
    "--export",
    "export_path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Read a local ARC-AMPE Volume II workbook instead of fetching CMS's. "
    "Use this for the Direct Enrollment Entity baseline, which CMS distributes "
    "through zONE rather than publishing.",
)
@click.option(
    "--version",
    "version",
    default="",
    help='Document version to stamp, e.g. "v1.02". Default: the version this loader is pinned to.',
)
@click.option(
    "--nist",
    "nist_path",
    default=Path("data/frameworks/nist-800-53-r5/controls.json"),
    type=click.Path(path_type=Path),
    help="The 800-53 catalog to crosswalk against. ARC-AMPE numbers its "
    "controls with 800-53 identifiers, so each one that resolves here is "
    "anchored on its equivalent. If absent, prints a notice and carries on "
    "without a crosswalk - the catalog is usable alone, just invisible to "
    "`policyforge map`.",
)
@click.option(
    "--out",
    default=Path("data/frameworks/arc-ampe/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed control data.",
)
def etl_arc_ampe(export_path: Path | None, version: str, nist_path: Path, out: Path):
    """Fetch CMS's ARC-AMPE Volume II baseline and parse it into this schema.

    Public domain - published by CMS, a federal agency, with no copyright
    notice or redistribution restriction, same basis as the NIST and eCFR
    sources.

    Note which volume this reads. Volume I is the narrative PDF and holds no
    controls; Volume II is the System Security and Privacy Plan workbook,
    and its `AE Mandatory Baseline` sheet is the catalog - 402 controls
    required of an ACA Administering Entity, with CMS's parameter decisions
    already written into the control text. The sheet is found by its shape
    rather than its name, so the Direct Enrollment Entity workbook reads the
    same way via --export. See ingest/arc_ampe.py.
    """
    import dataclasses
    import json

    from policyforge.ingest.arc_ampe import (
        ARC_AMPE_URL,
        ARC_AMPE_VERSION,
        fetch_arc_ampe,
        load_workbook_from_bytes,
        parse_arc_ampe,
    )
    from policyforge.ingest.provenance import record_source_provenance
    from policyforge.ingest.schema import load_controls

    if export_path is not None:
        content = export_path.read_bytes()
        source_url = str(export_path)
    else:
        content = fetch_arc_ampe()
        source_url = ARC_AMPE_URL

    nist_ids: set[str] | None = None
    if nist_path.exists():
        nist_ids = set()
        for control in load_controls(nist_path):
            nist_ids.add(control.control_id)
            nist_ids.update(e.enhancement_id for e in control.enhancements)
    else:
        click.echo(
            f"No 800-53 catalog at {nist_path}, so no crosswalk is recorded. Run "
            "`policyforge etl-oscal` and re-run this to anchor ARC-AMPE on 800-53."
        )

    workbook = load_workbook_from_bytes(content)
    controls, summary = parse_arc_ampe(
        workbook, version=version or ARC_AMPE_VERSION, nist_ids=nist_ids
    )

    replacement = [dataclasses.asdict(c) for c in controls]
    # The report prints before the guard runs, so a refusal arrives with the
    # parse's own warning above it — the warning is what names the column.
    for line in summary.format_report():
        click.echo(f"  {line}")
    _refuse_optional_field_loss(out, replacement)

    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps(replacement, indent=2))
    click.echo(f"Wrote {len(controls)} controls -> {out}")

    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=version or ARC_AMPE_VERSION,
        source_url=source_url,
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(
            f"Recorded provenance: {version or ARC_AMPE_VERSION} "
            f"sha256:{stamp[:16]}… -> {out.parent}"
        )


#: Where a generated parser is written, checked and trial-run. Outside the
#: package on purpose: a file here is never imported by anything until a
#: person promotes it, and `output/` is gitignored.
_PARSER_CANDIDATE_DIR = Path("output/parsers")


#: Where a promoted parser goes, and where the candidate used to be written
#: directly — importable on the next run with nothing between the model's
#: output and the interpreter but `ast.parse`.
_PARSER_PACKAGE_DIR = Path("src/policyforge/ingest")


@cli.command("generate-parser")
@click.option(
    "--framework",
    required=True,
    type=click.Choice(["hitrust", "govramp"]),
    help="Which BYOC framework this sample export is for.",
)
@click.option(
    "--sample",
    "sample_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to a real sample export from your own license (e.g. a MyCSF CSV/Excel "
    "export). Its full content is sent to your configured LLM provider - confirm "
    "your license terms permit that before running this.",
)
@click.option(
    "--out",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to write the candidate parser (default: output/parsers/<framework>_loader.py). "
    "It is checked and trial-run there, and not imported by anything until promoted.",
)
@click.option(
    "--force", is_flag=True, help="Overwrite --out, or the promoted module, if it exists."
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the confirmation prompt before sending --sample's content to the LLM provider.",
)
@click.option(
    "--promote",
    is_flag=True,
    help="Copy the candidate into src/policyforge/ingest/ once it passes the static check "
    "and its trial run returns records. Without this it stays in output/ for review.",
)
def generate_parser_cmd(
    framework: str,
    sample_path: Path,
    out: Path | None,
    force: bool,
    yes: bool,
    promote: bool,
):
    """Generate a deterministic ETL parser for a BYOC framework export via your
    configured LLM, from a real sample export file.

    The sample is part of the prompt, so the code that comes back was written
    under the influence of a file this tool did not write - and it is about
    to run over a licensed one. So it is checked before it runs, run once
    under watch, and kept out of the package until you promote it. Nothing
    under ingest/*_loader.py calls the LLM at parse time - only this command
    does, and only when you run it.
    """
    from policyforge.ingest.parser_codegen import generate_byoc_parser
    from policyforge.ingest.parser_gate import check_generated_parser, trial_run
    from policyforge.llm.boundary import BoundaryViolation, enforce

    out_path = out or _PARSER_CANDIDATE_DIR / f"{framework}_loader.py"
    if out_path.exists() and not force:
        raise click.UsageError(f"{out_path} already exists. Pass --force to overwrite.")

    config = load_config()

    # Before the file is read, not after. This used to be a paragraph asking
    # the operator to confirm their own licence permitted sending a MyCSF
    # export to a hosted API, which put the one licence question this tool
    # can actually answer in front of the person least able to answer it at
    # 11pm. `enforce` answers it from the configuration instead.
    try:
        decision = enforce(sample_path, config)
    except BoundaryViolation as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Boundary: {decision.explain()}")
    click.echo(
        f"This sends the full contents of {sample_path} to your configured LLM "
        "provider. The boundary check above says the pairing is permitted; whether "
        "your licence permits this particular use of this particular export is "
        "still yours to confirm."
    )
    if not yes:
        click.confirm("Continue?", abort=True)

    sample_text = sample_path.read_text(encoding="utf-8", errors="replace")
    provider = get_provider(config)

    from policyforge.llm import ledger

    # Inside a scope naming what was sent, so the ledger row says licensed
    # content left the machine. It said nothing before #11, and a call under
    # a declared data agreement records that agreement only when the row
    # knows its content is licensed (80's spec 4).
    with ledger.about(
        f"parser/{framework}", site="generate-parser", content_class=decision.content.klass
    ):
        source = generate_byoc_parser(
            framework=framework,
            framework_slug=framework,
            sample_text=sample_text,
            provider=provider,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Before it runs, not after. `ast.parse` used to be the only check, and
    # it proves the output is Python rather than anything about what the
    # Python does.
    violations = check_generated_parser(source, framework_slug=framework)
    if violations:
        rejected = out_path.with_name(f"{out_path.stem}.rejected.py")
        write_text_lf(rejected, source)
        click.echo("Generated parser refused before running it:")
        for violation in violations:
            click.echo(f"  {violation}")
        click.echo(
            f"The code is at {rejected} so you can judge the refusal; it was not run "
            "and nothing imports it."
        )
        raise SystemExit(1)

    write_text_lf(out_path, source)
    click.echo(f"Wrote candidate parser -> {out_path}")

    trial = trial_run(out_path, sample_path, framework_slug=framework)
    if not trial.ok:
        click.echo(f"Trial run against {sample_path} failed: {trial.detail}")
        raise SystemExit(1)
    click.echo(f"Trial run against {sample_path}: {trial.records} record(s), nothing refused.")

    if trial.records == 0:
        click.echo(
            "A parser that returns nothing reads as a framework with no controls, and "
            "every report built on it would say there is nothing to do. Not promoting."
        )
        return

    target = _PARSER_PACKAGE_DIR / f"{framework}_loader.py"
    if not promote:
        click.echo(
            f"Read it before trusting it. Copy it to {target} once you have, or pass "
            "--promote to have this command do that step when both checks pass."
        )
        return
    if target.exists() and not force:
        raise click.UsageError(f"{target} already exists. Pass --force to replace it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(target, source)
    click.echo(
        f"Promoted -> {target}. Add it to your test suite and commit it like any other source file."
    )


@cli.command("etl-csf")
@click.option(
    "--out",
    default=Path("data/frameworks/nist-csf-2-0/controls.json"),
    type=click.Path(path_type=Path),
    help="Where to write the parsed control data.",
)
@click.option(
    "--oscal",
    "oscal_path",
    default=None,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Parse a saved copy of NIST's CSF 2.0 OSCAL catalog instead of fetching. It must "
    "be the pinned file, byte for byte.",
)
@click.option(
    "--olir",
    "olir_path",
    default=None,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Parse a saved copy of the OLIR 186 workbook instead of fetching. It must be the "
    "pinned file, byte for byte.",
)
def etl_csf(out: Path, oscal_path: Path | None, olir_path: Path | None):
    """Fetch NIST CSF 2.0 and NIST's mapping of it to SP 800-53, and parse
    them into this project's data schema. A US government work, so safe to
    bundle.

    CSF 2.0 STATES OUTCOMES, as the AI RMF Core does: a citation to it
    supports no obligation on its own. It is reached through its crosswalk
    to 800-53, not anchored.

    NIST's mapping (OLIR 186) is untyped and NIST marks it not
    comprehensive, so every link reads as partial coverage, and a
    subcategory it does not link is "not mapped by NIST", not "has no 800-53
    equivalent". Both files are pinned by their SHA-256, and any other file
    is refused; on the scheduled drift job that refusal is the job working.
    The catalog's README carries the long form.
    """
    import dataclasses
    import json

    from policyforge.ingest import csf
    from policyforge.ingest.oscal_loader import (
        CATALOG_URL_CSF_2_0,
        NIST_CSF_2_0,
        parse_oscal_catalog,
        require_csf_2_0_extent,
    )
    from policyforge.ingest.provenance import record_source_provenance
    from policyforge.scaffold import bundled_root

    def read(path: Path | None, url: str) -> bytes:
        if path is not None:
            click.echo(f"Parsing saved copy {path} instead of fetching {url}.")
            return path.read_bytes()
        return csf.fetch(url)

    rows = json.loads(
        bundled_root()
        .joinpath("frameworks", "nist-800-53-r5", "controls.json")
        .read_text(encoding="utf-8")
    )
    ids_800_53 = {r["control_id"] for r in rows} | {
        e["enhancement_id"] for r in rows for e in r.get("enhancements") or []
    }
    try:
        raw_catalog = read(oscal_path, CATALOG_URL_CSF_2_0)
        csf.require_sha256(raw_catalog, csf.CATALOG_SHA256, "The CSF 2.0 OSCAL catalog")
        raw_olir = read(olir_path, csf.OLIR_186_URL)
        csf.require_sha256(raw_olir, csf.OLIR_186_SHA256, "The OLIR 186 workbook")
        catalog = json.loads(raw_catalog)
        controls, withdrawn = parse_oscal_catalog(catalog, dialect=NIST_CSF_2_0)
        require_csf_2_0_extent(controls)
        links, families, refused = csf.parse_olir_186(raw_olir, ids_800_53)
        family_links = csf.attach(controls, links, families)
    except (csf.CsfError, ValueError) as exc:
        # A refused file is the pin WORKING, as for the AI RMF Playbook: a
        # clean error line naming the reason, and nothing written.
        raise click.ClickException(str(exc)) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))
    # `source_ref` is the catalog's OWN `metadata.version` (1.2.0 when pinned),
    # which NIST versions separately from the repository tag the URL names
    # (v1.5.0); both are recorded, the tag in `source_url` (80, on #449).
    version = catalog["catalog"]["metadata"]["version"]
    csf.record_family_links(out.parent / "framework.yaml", family_links)
    stamp = record_source_provenance(
        out.parent / "framework.yaml",
        source_ref=version,
        source_url=CATALOG_URL_CSF_2_0,
        content=out.read_bytes(),
    )
    if stamp is not None:
        click.echo(f"Recorded provenance: {version} sha256:{stamp[:16]}… -> {out.parent}")
    subcategories = sum(len(c.enhancements) for c in controls)
    click.echo(
        f"Parsed {len(controls)} categories and {subcategories} subcategories "
        f"(CSF 2.0, OSCAL {version}) -> {out}"
    )
    click.echo(f"Excluded {withdrawn} withdrawn CSF 1.1 elements.")
    click.echo(
        f"Carried NIST's {sum(map(len, links.values()))} links to 800-53 controls on "
        f"{len(links)} CSF ids, all resolved; "
        f"{sum(map(len, families.values()))} family links on {len(families)}, kept as family "
        "links and never expanded."
    )
    for focal, target in refused:
        click.echo(f"Refused {focal} -> {target}: no control of 800-53 rev 5 has that id.")


@cli.command("etl-hipaa-privacy")
@click.option(
    "--date",
    default=None,
    help="The eCFR date to read, or `latest`. Defaults to the pinned date both catalogs "
    "were built from; the scheduled drift job passes `latest`.",
)
@click.option(
    "--frameworks",
    "frameworks_dir",
    default=Path("data/frameworks"),
    type=click.Path(path_type=Path, file_okay=False),
    help="Where the two catalog directories live.",
)
@click.option(
    "--saved",
    "saved_dir",
    default=None,
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Parse saved copies instead of fetching: renderer-D.html, ecfr-D.xml, "
    "renderer-E.html, ecfr-E.xml and renderer-E-<pre-rule date>.html.",
)
def etl_hipaa_privacy(date: str | None, frameworks_dir: Path, saved_dir: Path | None):
    """Fetch HIPAA's Breach Notification Rule (Subpart D) and Privacy Rule
    (Subpart E) from eCFR and parse them into two catalogs. A US federal
    regulation, so safe to bundle.

    eCFR STILL PRINTS TEXT A COURT VACATED. Purl v. HHS vacated the 2024
    reproductive-health amendments, and eCFR has not removed them. Both
    catalogs carry eCFR's text as printed, and the Privacy Rule's
    framework.yaml marks the vacated paragraphs: they are not used for
    generation, `check` warns on a citation to one, and where the rule only
    revised a paragraph, the pre-rule wording that binds again is quoted
    from eCFR's own earlier text. This is the project's reading of the
    judgment, not legal advice; the catalog README carries the long form.

    Refuses, and writes nothing, if eCFR's renderer and its XML disagree on
    any section's words, if either subpart's shape changes, or if the
    vacated text is not exactly what was pinned.
    """
    import dataclasses
    import hashlib
    import json

    import yaml

    from policyforge.ingest import hipaa_privacy as hp
    from policyforge.ingest.provenance import record_source_provenance

    date = date or hp.PIN_DATE
    if date == "latest":
        from policyforge.ingest.ecfr import current_date

        date = current_date(hp.TITLE)

    def read(name: str) -> str:
        return (saved_dir / name).read_bytes().decode("utf-8")

    fetched: dict[str, tuple[str, str]] = {}

    def subpart(letter: str) -> tuple[str, str]:
        if letter not in fetched:
            if saved_dir is None:
                fetched[letter] = hp.fetch(date, letter)
            else:
                fetched[letter] = (read(f"renderer-{letter}.html"), read(f"ecfr-{letter}.xml"))
        return fetched[letter]

    written = []
    try:
        pre_rule = (
            read(f"renderer-E-{hp.PRE_RULE_DATE}.html")
            if saved_dir is not None
            else hp.fetch(hp.PRE_RULE_DATE, "E")[0]
        )
        for (name, first, last), letter, directory, extent in (
            (hp.BREACH, "D", hp.BREACH_DIR, hp.BREACH_EXTENT),
            (hp.PRIVACY, "E", hp.PRIVACY_DIR, hp.PRIVACY_EXTENT),
        ):
            html, xml = subpart(letter)
            found = hp.sections(html, first, last)
            hp.require_text_agrees(found, hp.xml_section_texts(xml, first, last))
            controls = hp.to_controls(
                hp.without_definitions(found), name, f"45 CFR 164 Subpart {letter}"
            )
            hp.require_extent(controls, extent, name)
            status = None
            if letter == "E":
                status = hp.vacated_status(
                    found,
                    hp.sections(pre_rule, first, last),
                    pre_rule_sha256=hashlib.sha256(pre_rule.encode("utf-8")).hexdigest(),
                )
            written.append((frameworks_dir / directory, controls, status, letter))
    except hp.HipaaPrivacyError as exc:
        # Nothing is written for either catalog: a refused parse of one
        # subpart must not leave the other half-updated beside it.
        raise click.ClickException(str(exc)) from exc

    for directory, controls, status, letter in written:
        out = directory / "controls.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_text_lf(out, json.dumps([dataclasses.asdict(c) for c in controls], indent=2))
        manifest = directory / "framework.yaml"
        if status is not None and manifest.exists():
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            data["vacated"] = status
            write_text_lf(
                manifest, yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)
            )
        stamp = record_source_provenance(
            manifest,
            source_ref=date,
            source_url=hp.renderer_url(date, letter),
            content=out.read_bytes(),
        )
        paragraphs = sum(len(c.enhancements) for c in controls)
        click.echo(
            f"Parsed {len(controls)} sections and {paragraphs} paragraphs of Subpart {letter} "
            f"(eCFR {date}) -> {out}" + (f"; provenance sha256:{stamp[:16]}…" if stamp else "")
        )
        if status is not None:
            revised = sum(len(r["ids"]) for r in status["revised"].values())
            click.echo(
                f"  Marked {len(status['added'])} vacated and {revised} revised-and-vacated "
                f"unit(s) of the 2024 rule ({hp.JUDGMENT})."
            )


@cli.command("etl-soc2-tsc")
@click.option(
    "--criteria",
    "criteria_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="A plain-text export you made from YOUR OWN copy of the AICPA Trust Services "
    "Criteria. Keep it in local_content/.",
)
@click.option(
    "--mapping",
    "mapping_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Your own copy of the AICPA's TSC to NIST SP 800-53 mapping workbook (.xlsx).",
)
@click.option(
    "--version",
    "version",
    default="",
    help='The criteria edition, e.g. "2017 (points of focus revised 2022)".',
)
@click.option(
    "--nist-comparison",
    "comparison_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="A saved copy of NIST's SP 800-53 Rev 4 to Rev 5 comparison workbook, instead "
    "of fetching it from NIST. It must be the pinned file, byte for byte.",
)
@click.option(
    "--out",
    required=True,
    type=click.Path(path_type=Path),
    help="Where to write controls.json, under local_content/, "
    "e.g. local_content/soc2-tsc/controls.json.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Write --out even though this repository has not declared "
    "`frameworks.allow_licensed_in_repo`.",
)
def etl_soc2_tsc(
    criteria_path: Path,
    mapping_path: Path | None,
    version: str,
    comparison_path: Path | None,
    out: Path,
    force: bool,
):
    """Parse YOUR OWN copy of the AICPA Trust Services Criteria (SOC 2), and
    optionally your own copy of the AICPA's mapping to NIST SP 800-53, into a
    local, licensed catalog.

    NOTHING IS FETCHED FROM THE AICPA, and nothing licensed ships with this
    project. The AICPA's terms govern your copy; the catalog README quotes
    them, and whether they permit this use is your decision.

    Built and tested only against synthetic input, since no copy of the
    criteria is in this project: your run is the first real one. It refuses
    whole, writing nothing, if your file is shaped differently.

    Mapping links are read as partial (`source-untyped`), because the AICPA's
    mapping states no relationship and predates Rev 5. Each 800-53 id NIST
    marks as changed substantively since Rev 4 is flagged; one withdrawn in
    Rev 5, or not a Rev 5 id at all, is refused by name.
    """
    _run_licensed(
        "etl-soc2-tsc",
        out=out,
        force=force,
        product="TSC export",
        licence="AICPA",
        noun="criteria",
        sample=criteria_path,
        unwritten="",
        criteria_path=criteria_path,
        mapping_path=mapping_path,
        version=version,
        comparison_path=comparison_path,
    )
