"""The topic registry: which topics exist, and who owns each one.

A *topic* is a coherent operational process — access review, backup and
restore, vendor risk — that one team can comfortably own end to end. It is
the unit this project generates documentation for, instead of generating one
document per control (300-plus artifacts nobody owns).

Why this is a declared file rather than a CLI argument: without it, a topic
exists only for the duration of one `policyforge synthesize` invocation, and
nothing records who owns it. Once topics are declared, the two questions that
actually sink a compliance program become computable — see `coverage.py`:

* which in-scope controls does **no** topic claim (nobody is doing it), and
* which are claimed by **two** (worse: it looks covered, while both owners
  assume the other has it).

The registry lives in `config/topics.yaml`, which is gitignored for the same
reason `config/config.yaml` is — it names your internal teams. Copy
`config/topics.example.yaml` to start from a reasonable default set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from policyforge.topics.anchoring import canonical_anchor

DEFAULT_TOPICS_PATH = Path("config/topics.yaml")


@dataclass
class Topic:
    """One ownable process, and the controls it answers for.

    `nist_controls` are *anchors*, not an exhaustive list: anchoring a
    control also claims its enhancements (see `coverage.py`), so a topic
    doesn't have to enumerate AC-2(1) through AC-2(13) to own AC-2.
    """

    name: str
    owner: str
    nist_controls: list[str] = field(default_factory=list)
    cadence: str = ""
    description: str = ""
    evidence: list[str] = field(default_factory=list)
    #: Where this topic's documents live, so `edit-topic` can change a
    #: topic's Policy, Standard and Procedure together:
    #:     confluence:
    #:       space: ENG
    #:       pages: {standard: "...", policy: "...", procedure: "..."}
    #: Optional — a topic with no published pages simply can't be edited by
    #: title, and says so rather than guessing at one.
    confluence: dict = field(default_factory=dict)
    #: The general form of the same declaration, one block per kind of
    #: store, so a topic can be published to more than one:
    #:     targets:
    #:       confluence: {space: ENG, pages: {...}}
    #: `confluence:` above is an alias for `targets.confluence`, and wins
    #: when both are given.
    targets: dict = field(default_factory=dict)
    #: The Policy this topic's Standard belongs to, by name (22, part 2; 80's
    #: 5919). One value, so a Standard has exactly one parent Policy by
    #: construction. Empty keeps today's behaviour: the topic is its own
    #: Policy, and `check` says so.
    policy: str = ""

    def target(self, kind: str) -> dict:
        """Where this topic's documents live in one kind of store, or {}."""
        if kind == "confluence" and self.confluence:
            return self.confluence
        block = self.targets.get(kind) if isinstance(self.targets, dict) else None
        return block if isinstance(block, dict) else {}

    def pages(self, kind: str) -> list[tuple[str, str]]:
        """(tier, page title) for each document published to `kind`, Policy first.

        Ordered Policy -> Standard -> Procedure so a reviewer reads the set
        the way the document hierarchy is meant to be read, rather than in
        whatever order the YAML happened to list them.
        """
        pages = self.target(kind).get("pages") or {}
        order = ("policy", "standard", "procedure")
        ordered = [(tier, pages[tier]) for tier in order if pages.get(tier)]
        ordered += [
            (tier, title) for tier, title in sorted(pages.items()) if tier not in order and title
        ]
        return ordered

    def confluence_pages(self) -> list[tuple[str, str]]:
        """(tier, page title) for each Confluence page, Policy first."""
        return self.pages("confluence")


@dataclass
class Policy:
    """One Policy, covering one or more Standards (22, part 2; 80's 5919).

    A topic names its parent with `policy:`; this block, `policies:` at the
    top of the registry, holds the Policy's own metadata. **The page a Policy
    is published to is declared here**, not on each topic: several topics
    would otherwise claim one page, which `check` rightly refuses.
    """

    name: str
    #: The accountable executive, often not a topic's owner.
    owner: str = ""
    cadence: str = ""
    description: str = ""
    #: As on a topic: `confluence: {space: ENG, pages: {policy: "..."}}` and
    #: the general `targets:` form.
    confluence: dict = field(default_factory=dict)
    targets: dict = field(default_factory=dict)

    def target(self, kind: str) -> dict:
        """Where this Policy lives in one kind of store, or {}."""
        if kind == "confluence" and self.confluence:
            return self.confluence
        block = self.targets.get(kind) if isinstance(self.targets, dict) else None
        return block if isinstance(block, dict) else {}

    def page(self, kind: str) -> tuple[str, str] | None:
        """(container, page title) this Policy publishes to in `kind`, or None.

        The container is the store's own: a Confluence space key, or a wiki
        repository. What two Policies must not share.
        """
        block = self.target(kind)
        title = str((block.get("pages") or {}).get("policy") or block.get("title") or "").strip()
        if not title:
            return None
        container = str(block.get("space") or block.get("repository") or "").strip()
        return container, title


@dataclass
class Registry:
    """The topics, and the Policies they name (22, part 2)."""

    topics: list[Topic] = field(default_factory=list)
    policies: list[Policy] = field(default_factory=list)

    def policy(self, name: str) -> Policy | None:
        key = name.strip().casefold()
        return next((p for p in self.policies if p.name.strip().casefold() == key), None)

    def undeclared_policies(self) -> dict[str, list[str]]:
        """{policy name a topic uses but `policies:` doesn't declare: topics}."""
        declared = {p.name.strip().casefold() for p in self.policies}
        found: dict[str, list[str]] = {}
        for topic in self.topics:
            if topic.policy and topic.policy.strip().casefold() not in declared:
                found.setdefault(topic.policy, []).append(topic.name)
        return found

    def standards_of(self, policy: str) -> list[Topic]:
        """The topics whose Standard belongs to `policy`, in registry order."""
        key = policy.strip().casefold()
        return [t for t in self.topics if t.policy.strip().casefold() == key]


class TopicRegistryError(ValueError):
    """Raised when a registry is malformed.

    Deliberately fatal rather than best-effort: a typo'd control ID or a
    topic missing an owner silently corrupts the coverage report, which is
    the one artifact whose whole job is to be trusted about what's covered.
    """


def parse_topics(data: dict) -> list[Topic]:
    """Parse and validate a loaded `topics.yaml` structure."""
    if not isinstance(data, dict) or "topics" not in data:
        raise TopicRegistryError(
            "Topic registry must be a mapping with a top-level `topics:` list. "
            "See config/topics.example.yaml."
        )
    raw_topics = data["topics"]
    if not isinstance(raw_topics, list) or not raw_topics:
        raise TopicRegistryError("`topics:` must be a non-empty list.")

    known_fields = {f.name for f in Topic.__dataclass_fields__.values()}
    topics: list[Topic] = []
    seen: set[str] = set()

    for index, raw in enumerate(raw_topics, start=1):
        if not isinstance(raw, dict):
            raise TopicRegistryError(f"Topic #{index} is not a mapping.")

        unknown = sorted(set(raw) - known_fields)
        if unknown:
            raise TopicRegistryError(
                f"Topic #{index} ({raw.get('name', '?')!r}) has unknown key(s): "
                f"{', '.join(unknown)}. Supported: {', '.join(sorted(known_fields))}."
            )
        for required in ("name", "owner"):
            if not raw.get(required):
                raise TopicRegistryError(
                    f"Topic #{index} is missing `{required}`. Every topic needs exactly "
                    "one accountable owner — that is the point of the registry."
                )

        name = str(raw["name"]).strip()
        if name.lower() in seen:
            raise TopicRegistryError(f"Duplicate topic name: {name!r}.")
        seen.add(name.lower())

        for anchor in raw.get("nist_controls") or []:
            # An unquoted `164.510` is the number 164.51 to YAML, and `164.500`
            # is 164.5: a regulatory section is spelled like a float (#420).
            # Refused by name, not read as an id nobody has.
            if isinstance(anchor, (int, float)) and not isinstance(anchor, bool):
                raise TopicRegistryError(
                    f"Topic {name!r} anchors {anchor} as a number. YAML reads an unquoted "
                    f'`164.510` as 164.51, so write the id in quotes ("164.510").'
                )
        topics.append(
            Topic(
                name=name,
                owner=str(raw["owner"]).strip(),
                # In each catalog's own spelling, so every reader compares one
                # (#188): `ac-3` here is the catalog's `AC-3`.
                nist_controls=[
                    canonical_anchor(str(c).strip()) for c in raw.get("nist_controls") or []
                ],
                cadence=str(raw.get("cadence") or "").strip(),
                description=str(raw.get("description") or "").strip(),
                evidence=[str(e).strip() for e in raw.get("evidence") or []],
                confluence=dict(raw.get("confluence") or {}),
                targets=dict(raw.get("targets") or {}),
                policy=str(raw.get("policy") or "").strip(),
            )
        )

    return topics


_POLICY_FIELDS = {"name", "owner", "cadence", "description", "confluence", "targets"}


def parse_registry(data: dict) -> Registry:
    """Parse a loaded `topics.yaml`: its topics, and its optional `policies:`.

    A registry with no `policies:` block is valid, and so is a topic with no
    `policy:` (22, part 2): existing registries keep working. A name a topic
    uses that the block doesn't declare is not refused here; it is reported
    (`Registry.undeclared_policies`, by `coverage` and `check`), so a half
    migrated registry can still be read and counted.
    """
    topics = parse_topics(data)
    raw_policies = data.get("policies")
    if raw_policies is None:
        return Registry(topics=topics)
    if not isinstance(raw_policies, list):
        raise TopicRegistryError("`policies:` must be a list, each entry a mapping with a `name`.")
    policies: list[Policy] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_policies, start=1):
        if not isinstance(raw, dict):
            raise TopicRegistryError(f"Policy #{index} is not a mapping.")
        unknown = sorted(set(raw) - _POLICY_FIELDS)
        if unknown:
            raise TopicRegistryError(
                f"Policy #{index} ({raw.get('name', '?')!r}) has unknown key(s): "
                f"{', '.join(unknown)}. Supported: {', '.join(sorted(_POLICY_FIELDS))}."
            )
        for required in ("name", "owner"):
            if not str(raw.get(required) or "").strip():
                raise TopicRegistryError(
                    f"Policy #{index} is missing `{required}`. A Policy names the executive "
                    "accountable for it, who is often not a topic's owner."
                )
        name = str(raw["name"]).strip()
        if name.casefold() in seen:
            raise TopicRegistryError(f"Duplicate Policy name: {name!r}.")
        seen.add(name.casefold())
        policies.append(
            Policy(
                name=name,
                owner=str(raw["owner"]).strip(),
                cadence=str(raw.get("cadence") or "").strip(),
                description=str(raw.get("description") or "").strip(),
                confluence=dict(raw.get("confluence") or {}),
                targets=dict(raw.get("targets") or {}),
            )
        )
    return Registry(topics=topics, policies=policies)


def load_topics(path: Path = DEFAULT_TOPICS_PATH) -> list[Topic]:
    """Load the topic registry from disk: its topics only."""
    return load_registry(path).topics


def load_registry(path: Path = DEFAULT_TOPICS_PATH) -> Registry:
    """Load the topic registry from disk: its topics and its Policies."""
    import yaml

    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config/topics.example.yaml to {path} and set the "
            "owners to your own teams."
        )
    # A syntax error is a malformed registry like any other, and is raised as
    # one. It used to escape as yaml's own ParserError, which no caller
    # catches — every one of them handles TopicRegistryError — so a single
    # unclosed bracket crashed `policyforge zardoz` at launch, in the code
    # whose comment says a broken registry is not fatal.
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:

        def at(mark) -> str:
            return f"line {mark.line + 1}, column {mark.column + 1}"

        # Both positions where YAML has both. For an unclosed bracket the
        # problem is only noticed at the end of the file; the line to go and
        # fix is where the bracket was opened, which YAML calls the context.
        problem_mark = getattr(exc, "problem_mark", None)
        context_mark = getattr(exc, "context_mark", None)
        where = f" at {at(problem_mark)}" if problem_mark else ""
        if context_mark and (not problem_mark or context_mark.line != problem_mark.line):
            where += f" (in what starts at {at(context_mark)})"
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        raise TopicRegistryError(f"{path} is not valid YAML{where}: {problem}.") from exc
    return parse_registry(data)
