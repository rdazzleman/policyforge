"""Where framework content lives, and who is allowed to hold a copy of it.

Some of the catalogs this project maps between are public-domain government
works and some are licensed. That is not a detail of packaging — it decides
which repository a file may sit in, and getting it wrong is a licence
breach rather than a bug.

The arrangement this supports has two repositories with different rights:

* **This one** is open, Apache-licensed, and may hold only content anyone
  may redistribute: NIST 800-53, FedRAMP, ARC-AMPE, the HIPAA Security Rule.
  A HITRUST CSF export must never be committed here, and `local_content/` is
  gitignored precisely so an accidental copy cannot be.
* **The organization's own repository** — private, holding its `docs/` tree,
  its `topics.yaml`, its config — very often *may* hold that same HITRUST
  export, because its own MyCSF licence permits internal use. Telling that
  organization to keep its licensed catalog outside its own private repo,
  when the licence allows it, is a restriction this project has no standing
  to impose and which makes CI harder for no benefit.

So the rule cannot be "licensed content never goes in a repo". It has to be
"licensed content never goes in a repo that has not said it may hold it",
and that is a decision only the repository owner can make. They make it once,
in config:

    frameworks:
      allow_licensed_in_repo: true    # our MyCSF licence permits this

This project's own config does not set it, so `policyforge check` fails here
the moment licensed content is committed — while the same command passes in
a repository whose owner has declared the right. The mechanism is a declared
permission rather than a path convention, because a path convention is a
thing you can forget and a failing check is not.

**What is not decided here.** Whether a *generated document* citing
`[HITRUST 01.c]` may be redistributed is a question about identifiers,
paraphrase and fair use that depends on the licence and the jurisdiction,
and this tool has no business answering it. What it can do is tell you which
of your documents drew on licensed material — see `derived_from` — so the
question is asked about the right files by someone qualified to answer it.
"""

from __future__ import annotations

# Used for exactly one thing: asking git whether a path is tracked.
import subprocess  # nosec B404
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

#: Filename declaring what a framework directory holds and under what terms.
MANIFEST_NAME = "framework.yaml"

#: Searched in order when config names none. `data/frameworks` is what ships
#: with this project; `frameworks/` is where a consuming repository would
#: naturally put its own, next to its `docs/`.
DEFAULT_SEARCH_PATHS = ("data/frameworks", "frameworks", "local_content")

PUBLIC_DOMAIN = "public-domain"
LICENSED = "licensed"


@dataclass
class Framework:
    """One catalog on disk, and the terms it came with."""

    id: str
    path: Path
    name: str = ""
    version: str = ""
    #: `public-domain` or `licensed`. Anything a manifest does not declare is
    #: treated as licensed: assuming content is freely redistributable
    #: because nobody said otherwise is the failure mode with consequences.
    licence: str = LICENSED
    source: str = ""
    notes: str = ""
    #: The framework key the catalog DECLARES (#295), e.g. `nist-800-53`, from
    #: `framework_id:` in its manifest: the identity citations, crosswalks,
    #: coverage and `/satisfies` all key it by. Not the manifest's `id:`,
    #: which names the directory (`nist-800-53-r5`). It wins over keying from the prose name;
    #: empty when the manifest has none. The manifest field is not called
    #: `key:` because gitleaks' generic-api-key rule reads `key: <long token>`
    #: as a secret (it flagged `cfr-170-315-onc-certification`).
    key: str = ""
    #: True when a manifest was found. Without one there is nothing to go on
    #: but the directory's location, so the framework is reported as
    #: undeclared rather than assumed safe.
    declared: bool = False
    #: The unit the regulation names as a requirement, for document reach
    #: (80's ruling on #423): `section`, `criterion` or `structure`. Empty for
    #: a catalog whose ids have a grammar in `topics.anchoring` or no nesting.
    family: str = ""
    #: {id: the id whose family it belongs to}, where the catalog's structure
    #: and the regulation disagree, each with its reason in the manifest.
    family_overrides: dict = field(default_factory=dict)
    #: Ids nested in the catalog for structure only: each is its own family.
    structure_only: tuple = ()
    #: The relationship every pair of this catalog's PUBLISHED crosswalk
    #: has, when its source declares it (#408): CSF 2.0's is
    #: `source-untyped`. Empty for a catalog that declares none, whose
    #: published pairs read as they always have.
    crosswalk_relationship: str = ""
    #: Who published that crosswalk, for a report's wording (#449): CSF 2.0's
    #: is `NIST OLIR 186`. Empty when undeclared.
    crosswalk_source: str = ""
    #: `family_links:` as the manifest writes it (#449): a source's link from
    #: one of this catalog's ids to a WHOLE family of another catalog, never
    #: to a control in it. Read by `declared_family_links` (#448).
    family_links: dict = field(default_factory=dict)
    #: `leaf_parts:` (#441): `listed` when a part cited below one of the
    #: catalog's leaves stands only if the leaf's text lists it; empty when
    #: the catalog keeps the residual (any part below a leaf stands).
    leaf_parts: str = ""
    #: `leaf_parts_measured:` what the setting rests on: the measurement,
    #: with where and when it was taken. A switch with none is refused.
    leaf_parts_measured: str = ""

    @property
    def redistributable(self) -> bool:
        return self.licence == PUBLIC_DOMAIN

    @property
    def controls_path(self) -> Path:
        return self.path / "controls.json"

    @property
    def has_controls(self) -> bool:
        return self.controls_path.exists()


def _read_manifest(directory: Path) -> dict:
    import yaml

    manifest = directory / MANIFEST_NAME
    if not manifest.exists():
        return {}
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def load_framework(directory: Path) -> Framework:
    """Read one framework directory, declared or not."""
    data = _read_manifest(directory)
    licence = str(data.get("licence") or data.get("license") or "").strip().lower()
    return Framework(
        id=str(data.get("id") or directory.name),
        path=directory,
        name=str(data.get("name") or directory.name),
        version=str(data.get("version") or ""),
        licence=PUBLIC_DOMAIN if licence == PUBLIC_DOMAIN else LICENSED,
        source=str(data.get("source") or ""),
        notes=str(data.get("notes") or ""),
        key=str(data.get("framework_id") or "").strip(),
        declared=bool(data),
        family=str(data.get("family") or "").strip(),
        family_overrides={str(k): str(v) for k, v in (data.get("family_overrides") or {}).items()},
        structure_only=tuple(str(i) for i in (data.get("structure_only") or ())),
        crosswalk_relationship=str(data.get("crosswalk_relationship") or "").strip(),
        crosswalk_source=str(data.get("crosswalk_source") or "").strip(),
        family_links=dict(data.get("family_links") or {}),
        leaf_parts=str(data.get("leaf_parts") or "").strip(),
        leaf_parts_measured=" ".join(str(data.get("leaf_parts_measured") or "").split()),
    )


def frameworks_config(config: dict | None = None) -> dict:
    """Normalize the `frameworks:` block, which has had two shapes.

    The first shape was a list of `{id, source, path}` entries. It was
    documented for a long time and read by nothing, so config files in the
    wild contain it — and a version that crashed on them would break every
    existing install at once. The paths it names are carried over as search
    roots, which is the only part of it that was ever actionable.

    `allow_licensed_in_repo` did not exist in that shape, so it stays off.
    Defaulting a permission to on because an old config could not express it
    is exactly backwards.
    """
    raw = (config or {}).get("frameworks")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        extra: list[str] = []
        for entry in raw:
            if isinstance(entry, dict) and entry.get("path"):
                parent = str(Path(str(entry["path"])).parent)
                if parent and parent not in extra:
                    extra.append(parent)
        return {"search_paths": [*DEFAULT_SEARCH_PATHS, *extra]} if extra else {}
    return {}


def search_paths(config: dict | None = None) -> list[Path]:
    configured = frameworks_config(config).get("search_paths")
    if isinstance(configured, list) and configured:
        return [Path(str(entry)) for entry in configured]
    return [Path(entry) for entry in DEFAULT_SEARCH_PATHS]


def discover(
    config: dict | None = None,
    *,
    roots: list[Path] | None = None,
    assume_written: Path | None = None,
) -> list[Framework]:
    """Every framework directory found under the search paths.

    A directory counts if it holds a `controls.json` or a manifest — the two
    things that make it a framework rather than somewhere a framework will
    eventually go.

    `assume_written` is a file about to be written, treated as present: the
    world as it will be once a catalog lands there (#459). The boundary asks
    this before a licensed ETL writes, because whether a directory is a
    framework, and which of two same-named ones wins, depends on files that
    do not exist yet.
    """
    pending = assume_written.resolve() if assume_written is not None else None
    found: dict[str, Framework] = {}
    for root in roots if roots is not None else search_paths(config):
        directories = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
        if (
            pending is not None
            and pending.parent.parent == root.resolve()
            and all(d.resolve() != pending.parent for d in directories)
        ):
            directories = sorted([*directories, pending.parent])
        for directory in directories:
            becomes_catalog = (
                pending is not None
                and pending.name == "controls.json"
                and directory.resolve() == pending.parent
            )
            if (
                not becomes_catalog
                and not (directory / "controls.json").exists()
                and not (directory / MANIFEST_NAME).exists()
            ):
                continue
            framework = load_framework(directory)
            # First search path wins, so a repository can shadow a bundled
            # catalog with its own newer export without deleting anything.
            found.setdefault(framework.id, framework)
    return list(found.values())


class FrameworkKeyWarning(UserWarning):
    """A declared framework key that prose keying would not have produced,
    or two catalogs declaring one name with different keys (#295)."""


def config_or_defaults(what: str, category: type[Warning] = UserWarning) -> dict:
    """This project's config, or `{}` -- the default search paths -- if it
    cannot be read. The ONE copy of that fallback (1d on #346).

    **A lookup never fails on the config file** (1d on #344). Keying a
    framework name runs under commands that never read config themselves,
    like `map`, so a config that does not parse must not become their
    traceback. (`check` does read config, since #329, and still raises on a
    malformed one; this fallback does not change that.) It is named in one
    warning of `category`, saying `what` was read from the default search
    paths and the bundled catalogs instead. A missing config is ordinary and
    silent.
    """
    import warnings

    import yaml

    from policyforge.config import load_config, resolve_config_path

    try:
        return load_config()
    except FileNotFoundError:
        return {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError, ValueError) as exc:
        warnings.warn(
            category(
                f"{resolve_config_path()} could not be read ({type(exc).__name__}), so {what} "
                "from the default search paths and the bundled catalogs only."
            ),
            stacklevel=4,
        )
        return {}


def _catalog_roots(config: dict | None) -> list[Path]:
    """The search paths, then the bundled catalogs: every place a catalog
    this project can cite may live, wherever the command runs. A
    repository's own copy shadows the bundled one exactly as `discover`
    lets it."""
    roots = list(search_paths(config))
    try:
        from policyforge.scaffold import bundled_root

        bundled = Path(str(bundled_root().joinpath("frameworks")))
        if bundled.is_dir():
            roots.append(bundled)
    except (RuntimeError, ModuleNotFoundError, OSError):
        pass
    return roots


def _catalog_names(
    config: dict | None, roots: list[Path] | None
) -> Iterator[tuple[Path, Framework, list[str]]]:
    """Each catalog on disk, with every name it goes by: the ONE walk (#340, #295).

    The manifest's `name` (when a manifest declares one) and each
    `framework` string its `controls.json` rows carry (`NIST 800-53` there,
    `NIST SP 800-53 Rev 5` in the manifest), for every catalog under
    `roots` -- by default the search paths, then the bundled root. A
    directory reached twice is read once, the first time. Rows that cannot
    be read cost the row names, not the manifest's.

    `known_framework_names` and `declared_keys` both read catalogs through
    this, so the names a tag part is matched against and the names a key is
    declared for cannot drift apart (9b on #346).
    """

    seen: set[Path] = set()
    for root in roots if roots is not None else _catalog_roots(config):
        if not root.is_dir():
            continue
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            resolved = directory.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            framework = load_framework(directory)
            names = {framework.name} if framework.declared else set()
            if framework.has_controls:
                try:
                    from policyforge.ingest.schema import read_catalog

                    rows, _ = read_catalog(framework.controls_path)
                    names.update(str(r.get("framework") or "") for r in rows if isinstance(r, dict))
                except (OSError, ValueError):
                    pass
            yield directory, framework, sorted(n for n in names if n.strip())


def known_framework_names(
    config: dict | None = None, *, roots: list[Path] | None = None
) -> frozenset[str]:
    """Every framework name a catalog on disk goes by (#340).

    See `_catalog_names` for which names and which catalogs: a catalog a
    user brings, under `frameworks/` or `local_content/`, is known as surely
    as a shipped one. Derived from the catalogs, never typed.

    The ONE list of names both readers of a tag's parts use (80's ruling on
    #340): the Playbook gate, to tell a part naming another framework from a
    shorthand part that inherits, and `satisfies`, which adds the catalogs it
    has loaded.
    """
    return frozenset(n for _, _, names in _catalog_names(config, roots) for n in names)


def known_requirement_index(
    config: dict | None = None, *, roots: list[Path] | None = None
) -> dict[str, set[str]]:
    """{normalized framework: every requirement id its catalogs hold}, for
    every catalog on disk (#189).

    The same walk as `known_framework_names` (`_catalog_names`), so the ids a
    tag is resolved against come from the catalogs its names came from, and
    the same index shape as `topics.satisfies._catalog_index`, the citation
    checker's: control ids, enhancement ids and requirement ids, filed under
    each row's normalized `framework`. A catalog whose rows can't be read
    contributes no ids, so a tag naming it resolves to nothing and is
    reported, the loud direction.
    """
    from policyforge.mapping.crosswalk import normalize_framework

    index: dict[str, set[str]] = {}
    for control in controls_on_disk(config, roots=roots):
        ids = index.setdefault(normalize_framework(control.framework), set())
        ids.add(control.control_id)
        ids.update(e.enhancement_id for e in control.enhancements)
        ids.update(r.requirement_id for r in control.requirements)
    return index


def catalogs_by_id(config: dict | None = None) -> dict[str, Framework]:
    """{framework id: the catalog} for every catalog a citation can name: the
    search paths, then the bundled catalogs (`_catalog_roots`), the first
    directory with an id winning as in `discover`, except that a catalog
    with controls.json wins over an earlier directory with NO controls.json
    (#417): `init`'s README-only stub, or a manifest-only directory. That
    last case differs from `discover`, which keeps a manifest-only directory
    first. Between two directories without controls, the first still wins.
    Where `generate` finds the catalogs its synthesis records in
    `derived_from` (#215)."""
    found: dict[str, Framework] = {}
    for _, framework, _ in _catalog_names(config, None):
        existing = found.get(framework.id)
        if existing is None or (not existing.has_controls and framework.has_controls):
            found[framework.id] = framework
    return found


def controls_on_disk(config: dict | None = None, *, roots: list[Path] | None = None) -> list:
    """Every control of every catalog on disk, by the same walk as
    `known_framework_names` (`_catalog_names`). What a bracket is resolved
    against when no run's catalog set is known (#215). A catalog whose rows
    can't be read contributes none, so a tag naming it is reported, the loud
    direction."""
    from policyforge.ingest.schema import load_controls

    controls: list = []
    for _, framework, _ in _catalog_names(config, roots):
        if not framework.has_controls:
            continue
        try:
            controls.extend(load_controls(framework.controls_path))
        except (OSError, ValueError):
            continue
    return controls


def _shipped_buckets() -> dict[str, str]:
    """{first word: the shipped catalog name it comes from}: `nist`, `hipaa`...

    These are the buckets prose keying's first-word fallback fills: every
    "NIST ..." name nobody pinned keys to `nist`, which is the collision
    #295 exists to end. Derived from the bundled catalogs, never typed, and
    read whatever roots the caller passed, because which catalogs ship does
    not depend on where a command looks for others (80's ruling on #295).
    Every name a shipped catalog goes by counts, the rows' as well as the
    manifest's; the first name in sorted order is the one reported.
    """
    try:
        from policyforge.scaffold import bundled_root

        bundled = Path(str(bundled_root().joinpath("frameworks")))
    except (RuntimeError, ModuleNotFoundError, OSError):
        return {}
    buckets: dict[str, str] = {}
    for name in sorted(n for _, _, names in _catalog_names(None, [bundled]) for n in names):
        buckets.setdefault(name.lower().split()[0], name)
    return buckets


def _declarations(
    config: dict | None, roots: list[Path] | None
) -> tuple[dict[str, tuple[str, Path]], list[tuple[str, str, Path, str]]]:
    """({normal name: (declared key, directory)}, [names whose prose key differs]).

    Two catalogs declaring one name with different keys raise a
    `FrameworkKeyWarning` naming both, wherever keys are read: that is an
    ambiguity in what is on disk, and the first root's declaration is kept.
    """
    import warnings

    from policyforge.mapping.crosswalk import prose_framework_key

    found: dict[str, tuple[str, Path]] = {}
    differing: list[tuple[str, str, Path, str]] = []
    for directory, framework, names in _catalog_names(config, roots):
        if not framework.key:
            continue
        for name in names:
            normal = " ".join(name.lower().split())
            if normal in found:
                if found[normal][0] != framework.key:
                    warnings.warn(
                        FrameworkKeyWarning(
                            f"{name!r} is declared as {found[normal][0]!r} by "
                            f"{found[normal][1]} and as {framework.key!r} by {directory}; "
                            f"keeping {found[normal][0]!r}"
                        ),
                        stacklevel=3,
                    )
                continue
            found[normal] = (framework.key, directory)
            prose = prose_framework_key(name)
            if prose != framework.key:
                differing.append((name, framework.key, directory, prose))
    return found, differing


def declared_family_rules(
    config: dict | None = None, *, roots: list[Path] | None = None
) -> dict[str, Framework]:
    """{framework key: its manifest} for every catalog declaring a `family:`
    (80's ruling on #423). The same walk as `declared_keys`, so a catalog a
    user brings declares its unit exactly as a shipped one does."""
    from policyforge.mapping.crosswalk import normalize_framework

    rules: dict[str, Framework] = {}
    for _, framework, names in _catalog_names(config, roots):
        if not framework.family:
            continue
        for key in {framework.key, *(normalize_framework(n) for n in names)} - {""}:
            rules.setdefault(key, framework)
    return rules


#: The values `leaf_parts:` takes (#441).
LEAF_PARTS = ("listed",)


def declared_leaf_checks(
    config: dict | None = None, *, roots: list[Path] | None = None
) -> dict[str, Framework]:
    """{framework key: its manifest} for every catalog whose manifest says
    `leaf_parts: listed` (#441): a part cited below one of its leaves stands
    only when the leaf's text lists it (`topics.anchoring.lists_part`).

    Refused, naming the manifest: a value that isn't one of `LEAF_PARTS`
    (a misspelling would leave the residual on, the unsafe direction, while
    reading as switched on), and a switch with no `leaf_parts_measured:`
    (no switch without a number behind it, 441's done-when). The same walk
    as `declared_family_rules`."""
    from policyforge.mapping.crosswalk import normalize_framework

    found: dict[str, Framework] = {}
    for _, framework, names in _catalog_names(config, roots):
        if not framework.leaf_parts:
            continue
        where = framework.path / MANIFEST_NAME
        if framework.leaf_parts not in LEAF_PARTS:
            raise ValueError(
                f"{where}: leaf_parts: {framework.leaf_parts!r} is not one of "
                f"{', '.join(LEAF_PARTS)}; leave it out to keep the residual."
            )
        if not framework.leaf_parts_measured:
            raise ValueError(
                f"{where}: leaf_parts: {framework.leaf_parts} has no leaf_parts_measured:, "
                "the measurement it rests on."
            )
        for key in {framework.key, *(normalize_framework(n) for n in names)} - {""}:
            found.setdefault(key, framework)
    return found


def declared_crosswalk_relationships(
    config: dict | None = None, *, roots: list[Path] | None = None
) -> dict[str, str]:
    """{framework key: the relationship its published crosswalk declares} (#408).

    A value that is not a relationship is refused, naming the manifest: coverage
    reads anything outside `PARTIAL_RELATIONSHIPS` as full, so a misspelled
    `source-untyped` would count every pair as covered, the unsafe direction.
    """
    from policyforge.crosswalk.overlay import RELATIONSHIPS
    from policyforge.mapping.crosswalk import normalize_framework

    found: dict[str, str] = {}
    for _, framework, names in _catalog_names(config, roots):
        if not framework.crosswalk_relationship:
            continue
        if framework.crosswalk_relationship not in RELATIONSHIPS:
            raise ValueError(
                f"{framework.path / MANIFEST_NAME}: crosswalk_relationship "
                f"{framework.crosswalk_relationship!r} is not one of {', '.join(RELATIONSHIPS)}."
            )
        for key in {framework.key, *(normalize_framework(n) for n in names)} - {""}:
            found.setdefault(key, framework.crosswalk_relationship)
    return found


def declared_crosswalk_sources(
    config: dict | None = None, *, roots: list[Path] | None = None
) -> dict[str, str]:
    """{framework key: who published its crosswalk}, as its manifest says (#449)."""
    from policyforge.mapping.crosswalk import normalize_framework

    found: dict[str, str] = {}
    for _, framework, names in _catalog_names(config, roots):
        if not framework.crosswalk_source:
            continue
        for key in {framework.key, *(normalize_framework(n) for n in names)} - {""}:
            found.setdefault(key, framework.crosswalk_source)
    return found


def declared_family_links(
    config: dict | None = None, *, roots: list[Path] | None = None
) -> dict[str, dict[str, frozenset[str]]]:
    """{framework key: {its id: the 800-53 families its source links it to}}
    (#448, 80's ruling 2 on #408): CSF 2.0's `GV.OC-03 -> PT`,
    `PR.IR-03 -> CP, IR`. A family link names a WHOLE family, never a
    control, so no reader may count it as a control covered.

    Refused, naming the manifest, when it is not `relationship: family`
    into 800-53: the hub it is matched through is 800-53's, and a link read
    into another catalog would reach the wrong documents in silence.
    """
    from policyforge.mapping.crosswalk import NIST_ANCHOR, normalize_framework

    found: dict[str, dict[str, frozenset[str]]] = {}
    for _, framework, names in _catalog_names(config, roots):
        block = framework.family_links
        if not block:
            continue
        relationship = str(block.get("relationship") or "")
        target = normalize_framework(str(block.get("framework") or ""))
        if relationship != "family" or target != NIST_ANCHOR:
            raise ValueError(
                f"{framework.path / MANIFEST_NAME}: family_links must be "
                f"`relationship: family` into {NIST_ANCHOR}, not {relationship!r} into "
                f"{target!r}."
            )
        links = {
            str(source_id): frozenset(str(f).strip().upper() for f in (families or ()))
            for source_id, families in (block.get("links") or {}).items()
        }
        for key in {framework.key, *(normalize_framework(n) for n in names)} - {""}:
            found.setdefault(key, links)
    return found


def declared_keys(config: dict | None = None, *, roots: list[Path] | None = None) -> dict[str, str]:
    """{name, whitespace-collapsed and lower-cased: declared key} (#295).

    **Every name a catalog goes by** (`_catalog_names`) is mapped to the
    `framework_id:` its `framework.yaml` declares; a catalog declaring none
    is skipped. The first root that declares a name wins, as in `discover`,
    and two catalogs declaring one name differently are warned about.

    A declared key that differs from the name's prose key is NOT warned
    about here, because this runs under every command that keys a name; see
    `key_collisions`, which the commands that make and inspect declarations
    print (80's ruling on #347).

    Read from the directories rather than registered when a catalog is
    loaded, so that keying a name does not depend on what was loaded first
    (80's condition on #295): a citation parsed before any catalog is read
    still gets the declared key.
    """
    found, _ = _declarations(config, roots)
    return {name: key for name, (key, _) in found.items()}


def key_collisions(
    config: dict | None = None,
    *,
    roots: list[Path] | None = None,
    directory: Path | None = None,
) -> list[str]:
    """Each declaration whose name, keyed by prose alone, lands on a KNOWN key.

    Then citations written before the declaration were filed under that
    other key, which is worth a person's attention. Every deliberate
    declaration differs from its name's first word -- "Acme Security
    Baseline" declared `acme-baseline` prose-keys to `acme` -- so a
    difference alone says nothing, and is not reported.

    A prose key is known if it is (1) a key another catalog declares, (2) an
    alias target, or (3) the first word of a shipped catalog's name -- the
    shared fallback bucket, `nist` above all, that #295 exists to empty (80's
    rulings on #347). Each is reported as a fact, naming which of the three
    made the key known, for the user to judge.

    **Shown only where a declaration is made or inspected**: the BYOC
    importers and `policyforge frameworks`. Not on every command that loads
    catalogs (80). `directory`, when given, limits the report to that
    catalog -- the one an importer just declared -- and reads its parent
    directory ahead of the search paths, since it may lie outside them.
    """
    import warnings

    from policyforge.mapping.crosswalk import FRAMEWORK_ALIASES

    if directory is not None and roots is None:
        # The catalog just written may sit outside the search paths.
        roots = [directory.parent, *_catalog_roots(config)]
    with warnings.catch_warnings():
        # Conflicts are warned about where keys are read; not twice here.
        warnings.simplefilter("ignore", FrameworkKeyWarning)
        found, differing = _declarations(config, roots)

    known: dict[str, str] = {}
    for key, source in sorted(found.values(), key=lambda item: (item[0], str(item[1]))):
        known.setdefault(key, f"the key {source} declares")
    for _, target in FRAMEWORK_ALIASES:
        known.setdefault(target, "a key the built-in alias table assigns")
    for word, name in _shipped_buckets().items():
        known.setdefault(word, f"the first word of shipped catalog {name!r}")

    wanted = directory.resolve() if directory is not None else None
    facts = []
    for name, key, where, prose in differing:
        if prose not in known or (wanted is not None and where.resolve() != wanted):
            continue
        facts.append(
            f"{where} declares {key!r} for {name!r}; its prose would key to {prose!r}, "
            f"{known[prose]}. Citations written before this declaration were filed "
            f"under {prose!r}."
        )
    return facts


def is_tracked(path: Path) -> bool | None:
    """Whether git tracks anything under `path`.

    Tracked is the precise question, not "is it gitignored": a file can be
    absent from .gitignore and still untracked, and only a tracked file gets
    pushed. Returns None when git cannot answer — no repository, no git
    binary — so a caller can say "unknown" instead of implying safety.
    """
    try:
        # Fixed argv and no shell; the only variable part is a path the
        # caller already has. `git` resolves from PATH on purpose, since
        # pinning an absolute path would break every platform but one.
        # Bytes: only emptiness is read, so nothing is decoded and nothing
        # can fail to decode (#287).
        result = subprocess.run(  # nosec B603 B607
            ["git", "ls-files", "--", str(path)],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def is_ignored(path: Path) -> bool | None:
    """Whether git would ignore `path`, so writing there cannot commit it.

    The counterpart to `is_tracked`, and the question to ask *before* a
    write rather than after. "May this repository hold licensed content"
    has a cheaper answer than the config flag when the destination is
    gitignored: a file git will never stage cannot be redistributed by
    accident, whatever the repository has or has not declared.

    Returns None when git cannot answer — no repository, no git binary — so
    a caller can refuse rather than assume. `git check-ignore` exits 0 when
    the path *is* ignored, 1 when it is not, and something else on error,
    which is why the return code is read in three ways rather than two.
    """
    try:
        # Fixed argv and no shell; the only variable part is a path the
        # caller already has.
        # Bytes: `-q` prints nothing and only the return code is read (#287).
        result = subprocess.run(  # nosec B603 B607
            ["git", "check-ignore", "-q", "--no-index", str(path)],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


@dataclass
class LicenceFinding:
    framework: Framework
    message: str
    severity: str = "error"


@dataclass
class LicenceReport:
    frameworks: list[Framework] = field(default_factory=list)
    findings: list[LicenceFinding] = field(default_factory=list)
    allowed: bool = False

    @property
    def errors(self) -> list[LicenceFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def format_report(self) -> str:
        lines = [f"{len(self.frameworks)} framework(s):"]
        width = max((len(f.id) for f in self.frameworks), default=0)
        for framework in self.frameworks:
            terms = "public domain" if framework.redistributable else "LICENSED"
            declared = "" if framework.declared else "  (undeclared)"
            lines.append(f"  {framework.id.ljust(width)}  {terms}{declared}  {framework.path}")

        if self.findings:
            lines.append("")
            for finding in self.findings:
                mark = "ERROR" if finding.severity == "error" else "warn "
                lines.append(f"  {mark}  {finding.framework.id}: {finding.message}")
        return "\n".join(lines)


def check_licences(config: dict | None = None, *, roots: list[Path] | None = None) -> LicenceReport:
    """Verify no licensed catalog is committed without the right to hold it."""
    allowed = bool(frameworks_config(config).get("allow_licensed_in_repo"))
    frameworks = discover(config, roots=roots)
    report = LicenceReport(frameworks=frameworks, allowed=allowed)

    for framework in frameworks:
        if framework.redistributable:
            continue

        tracked = is_tracked(framework.path)
        if tracked and not allowed:
            report.findings.append(
                LicenceFinding(
                    framework,
                    f"licensed content at {framework.path} is committed to this "
                    "repository, which has not declared the right to hold it. Either "
                    "remove it and keep it untracked, or set "
                    "`frameworks.allow_licensed_in_repo: true` if your licence "
                    "permits your repository to carry it.",
                )
            )
        elif tracked is None:
            report.findings.append(
                LicenceFinding(
                    framework,
                    "could not ask git whether this is committed, so its licence "
                    "position is unverified here.",
                    severity="warning",
                )
            )
        if not framework.declared:
            report.findings.append(
                LicenceFinding(
                    framework,
                    f"has no {MANIFEST_NAME}, so it is treated as licensed. Add one "
                    "declaring its terms.",
                    severity="warning",
                )
            )

    return report


def derived_from(controls_paths: list[Path], config: dict | None = None) -> list[Framework]:
    """Which discovered frameworks a set of `--controls` inputs came from.

    The question worth asking about a generated document is not whether the
    tool is careful but whether *this file* drew on licensed material. This
    answers it for the inputs of a run, so the redistribution question gets
    asked about the right documents.
    """
    resolved = {path.resolve() for path in controls_paths}
    return [
        framework for framework in discover(config) if framework.controls_path.resolve() in resolved
    ]
