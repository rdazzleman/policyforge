"""One Policy, drafted from the Standards that name it (22, part 2b; 80's 5919).

A Policy covers one or more Standards, and each Standard (a topic) names its
parent with `policy:`. The Policy is written from **the Standards**, not from
the syntheses: it states intent and scope, then gives one short section per
governed Standard, naming it by title, and ends with a "Standards under this
Policy" section listing each Standard's title and owner and where its file is.

**What the model writes and what code writes.** The model drafts the prose:
Purpose, Scope and one short section per Standard. Code writes everything a
reader will rely on to find the Standards: a Standard the model left out gets
a plain section naming it, and the closing section is assembled from the
files. A section the draft wrote that is only a list of Standards, or is
headed as a Standard the Policy doesn't govern, is dropped and named, so
there is one list and it is code's. **What code cannot do** is edit a
sentence: a Standard outside the group that the draft's prose names is
reported, not removed (`AssembledPolicy.foreign`), and a section the model
heads with a Standard's title but fills with another's content reads as
that Standard's.

**No control tags.** A Policy is read by the whole organisation (80's ruling
on the Procedure, 5904, applied here): every tag-shaped span is removed from
the draft, those that resolve in the run's catalogs and those that don't
(each named in the run), and what a Policy rests on is the Standards it
names.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from policyforge.llm.base import LLMProvider
from policyforge.llm.prompts import Prompt, register

#: A Standard's text, as far as the model is shown. A Policy states intent and
#: points to the Standard for detail, so the Standard's own requirements need
#: not all be read; a longer one is cut here, and the run says so.
MAX_STANDARD_CHARS = 12000

#: Tokens for the Policy: one short section per Standard, so it grows with the
#: group (see `policy_tokens`).
BASE_TOKENS = 2048
PER_STANDARD_TOKENS = 512

CLOSING_HEADING = "Standards under this Policy"

_POLICY_GROUP_SYSTEM_PROMPT = register(
    Prompt(
        name="generate.policy_group",
        version=1,
        text="""You are a compliance policy drafting engine. \
Draft one information security POLICY for one organization, from the \
Standards that sit under it. A Policy is read by the entire organization — \
most readers have no compliance background and will never see a framework \
control ID. It states why the organization has these rules and who they \
apply to, and says in plain language what it commits to for each Standard. \
The Standards hold the technical requirements.

Rules:
- Output portable, well-formed CommonMark markdown only: a single '#'
  document title, then '##' sections in this exact order: Purpose, Scope,
  and then one section for each Standard you are given, in the order given.
  No Obsidian-style [[wikilinks]], no vault-relative-only paths.
- The heading of each Standard's section is exactly that Standard's title,
  as given to you. Do not rename it, merge two Standards into one section, or
  skip one.
- Purpose: 1-2 sentences, plain language, on why this policy exists.
- Scope: 1-2 sentences naming who and what it applies to.
- Each Standard's section: 1-3 sentences saying what the organization
  commits to in that area, in plain language, drawn from that Standard. Do
  not restate its technical detail; the reader goes to the Standard for it.
  Never cite a framework or a control ID, and never write a citation in
  square brackets.
- Do not write a section listing the Standards, and do not write sections for
  roles, exceptions or enforcement: those are not part of this document.
- Never invent a specific fact (a named team, a named contact, a specific
  consequence) that isn't given to you below or in the Standards. Where a
  detail is missing, use a square-bracket placeholder in the same style as
  vendor placeholders, e.g. `[Security Team]` or `[Policy Owner Title]`,
  rather than making one up.
""",
    )
)


@dataclass
class GovernedStandard:
    """One Standard a Policy governs, as found in the content tree."""

    topic: str
    title: str
    owner: str
    #: The file, and its path from the content tree's root (for a link).
    path: Path
    relative: str
    #: Its frontmatter metadata and body.
    metadata: dict = field(default_factory=dict)
    body: str = ""


@dataclass
class PolicyGroup:
    """What `generate --policy` needs: the Policy, and its Standards found."""

    policy: object
    standards: list[GovernedStandard]
    #: Names of the files that would have to be generated first, when a topic
    #: that names the Policy has no Standard yet.
    missing: list[str] = field(default_factory=list)
    #: Standards two files claim, by topic.
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    #: The titles of the other Standards in the tree, which this Policy does
    #: not govern: a draft that names one is reported.
    others: list[str] = field(default_factory=list)


def policy_slug(name: str) -> str:
    """The file stem a Policy is written under: the rule `synthesize` names a
    topic's file by, so a name reads the same on disk everywhere."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def find_standards(registry, policy, content_dir: Path) -> PolicyGroup:
    """The Standards whose topics name `policy`, found in `content_dir`.

    **One rule for finding a topic's Standard**, `edit.tree.select_topic_files`
    (22 part 2b, ba on PR 445): a file whose frontmatter says `topic:`, or one
    bound to the page the registry declares for the topic's Standard, which is
    what `pull` writes. Two rules would have `edit-topic` find a file that
    `generate --policy` calls missing. One topic with no Standard in the tree
    is `missing`, and one with two is `ambiguous`: both are named and refused
    by the caller, since drafting a Policy over a subset would read as
    complete.
    """
    from policyforge.content.tree import load_content_tree
    from policyforge.edit.tree import select_topic_files

    documents, _problems = load_content_tree(content_dir)
    standards = [d for d in documents if (d.tier or "").lower() == "standard"]
    by_path = {d.relative_path: d for d in standards}
    group = PolicyGroup(policy=policy, standards=[])
    governed: set[int] = set()
    for topic in registry.standards_of(policy.name):
        selection = select_topic_files(content_dir, topic, tiers={"standard"}, documents=documents)
        if "standard" in selection.ambiguous:
            group.ambiguous[topic.name] = selection.ambiguous["standard"]
            continue
        found = selection.files.get("standard")
        if found is None:
            group.missing.append(topic.name)
            continue
        document = by_path[found.relative]
        governed.add(id(document))
        group.standards.append(
            GovernedStandard(
                topic=topic.name,
                title=document.title,
                owner=str(document.metadata.get("owner") or topic.owner or ""),
                path=document.path,
                relative=document.relative_path,
                metadata=dict(document.metadata),
                body=document.body,
            )
        )
    group.others = [d.title for d in standards if id(d) not in governed and d.title]
    return group


def group_content_class(group: PolicyGroup, config: dict):
    """What the Policy is drawn from, for the boundary check: **the most
    guarded class among its Standards** (the licensing boundary, 22 part 2b).

    A Standard says its own class where it states one (`content_class` or
    `generated_by.content_class`, the more guarded winning, an unreadable
    value counting as licensed), and is classified by where it lives
    otherwise, as `generate` classifies a synthesis written before the class
    travelled. One licensed Standard makes the Policy licensed: a Policy that
    restates it is its restatement."""
    from policyforge.content.provenance import declared_content_class, stamped_class
    from policyforge.llm import boundary

    ranked: list[tuple[int, str, str]] = []
    for standard in group.standards:
        said = declared_content_class(standard.metadata)
        if said:
            klass = stamped_class(said) or boundary.ORG_INTERNAL
            how = "its frontmatter says so"
        else:
            klass = boundary.classify_path(standard.path, config).klass
            how = "classified by where it lives"
        ranked.append((boundary.CONTENT_CLASSES.index(klass), klass, f"{standard.title}: {how}"))
    _rank, klass, why = max(ranked, key=lambda item: item[0])
    return boundary.ContentClassification(
        klass, f"the most guarded of {len(ranked)} Standard(s) under the Policy ({why})"
    )


def policy_tokens(standards: int) -> int:
    return BASE_TOKENS + PER_STANDARD_TOKENS * max(standards, 1)


def standard_digest(standard: GovernedStandard) -> tuple[str, bool]:
    """(the text of a Standard the model is shown, whether it was cut).

    Without its source blocks (the catalogs' own wording, which a Policy never
    restates) and without any tag-shaped span, which a Policy carries none of.
    """
    from policyforge.content.sources import source_block_spans
    from policyforge.content.traceability import strip_tag_shaped

    lines = standard.body.split("\n")
    drop: set[int] = set()
    for start, end in source_block_spans(standard.body):
        drop.update(range(start, end + 1))
    text = "\n".join(line for index, line in enumerate(lines) if index not in drop)
    text, _tags = strip_tag_shaped(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_STANDARD_CHARS:
        return text[:MAX_STANDARD_CHARS].rstrip() + "\n[... cut here ...]", True
    return text, False


def render_standards(group: PolicyGroup) -> tuple[str, list[str]]:
    """The Standards as the prompt shows them, and the titles that were cut."""
    parts: list[str] = []
    cut: list[str] = []
    for standard in group.standards:
        text, was_cut = standard_digest(standard)
        if was_cut:
            cut.append(standard.title)
        parts.append(
            f"### Standard: {standard.title}\nOwner: {standard.owner or 'not recorded'}\n\n{text}"
        )
    return "\n\n".join(parts), cut


def generate_policy_group(
    org,
    provider: LLMProvider,
    *,
    group: PolicyGroup,
    standards_text: str,
) -> str:
    """The model's draft of one Policy over its Standards."""
    from policyforge.generate.policy_writer import _render_org

    policy = group.policy
    if not group.standards:
        raise ValueError("the Policy has no Standards to be drafted from.")
    lines = [
        _render_org(org),
        "",
        f"Policy: {policy.name}",
    ]
    if policy.owner:
        lines.append(
            f"Accountable executive: {policy.owner}. Name this role where the document "
            "must say who answers for the Policy; do not write [Policy Owner] for it."
        )
    if policy.cadence:
        lines.append(f"Review cadence: {policy.cadence}.")
    if policy.description:
        lines.append(f"What it covers: {policy.description}")
    lines += [
        "",
        "The Standards under this Policy, in the order to write their sections:",
        *[f"- {s.title}" for s in group.standards],
        "",
        "Their text follows. A Policy compresses it to plain commitments; it does not "
        "enumerate it.",
        "",
        standards_text,
        "",
        f"Draft the Policy titled {policy.name!r} per the rules above.",
    ]
    from policyforge.llm import effort

    response = effort.call(
        provider,
        effort=effort.DRAFTING,
        system=_POLICY_GROUP_SYSTEM_PROMPT,
        prompt="\n".join(lines),
        temperature=0.2,
        max_tokens=policy_tokens(len(group.standards)),
    )
    return effort.document_text(response, what="Policy")


def _headings(markdown: str) -> list[tuple[int, int, str]]:
    """(line index, level, text) of each ATX heading outside a code fence."""
    found: list[tuple[int, int, str]] = []
    fence = ""
    for index, line in enumerate(markdown.split("\n")):
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = ""
            continue
        if marker:
            fence = marker.group(1)
            continue
        atx = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if atx:
            found.append((index, len(atx.group(1)), atx.group(2).strip()))
    return found


#: A leading number on a heading: `2.`, `2)`, `2.1`, `Section 2:`, `Part II -`, `a)`.
_NUMBERING_RE = re.compile(
    r"^(?:(?:section|part|chapter|article)\s+)?"
    r"(?:\d+(?:\.\d+)*[.):]?|[ivxlc]+[.):]|[a-z][.)])\s+",
    re.IGNORECASE,
)


def _key(text: str) -> str:
    """A heading or title as it is compared: markdown emphasis and a leading
    number taken off, case and spacing folded. A model numbers its sections
    (`## 2. Access Review Standard`) and a title is still that title."""
    text = re.sub(r"[`*_]", "", text)
    text = " ".join(text.split())
    text = _NUMBERING_RE.sub("", text, count=1)
    return text.strip(" :.-").casefold()


def _names(heading: str, title_key: str) -> bool:
    """Whether a heading is this Standard's: its title once numbering is off,
    optionally followed by more words (`... Standard (summary)`), or the
    title without its document type (`## Access Review` for `Access Review
    Standard`): a model heading a Policy section names the subject."""
    key = _key(heading)
    if (
        key == title_key
        or key == title_key + "s"
        or (bool(title_key) and key.startswith(title_key) and not key[len(title_key)].isalnum())
    ):
        return True
    subject = title_key.removesuffix(" standard").strip()
    return bool(subject) and subject != title_key and key in (subject, subject + "s")


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_ITEM_SPLIT_RE = re.compile(r"\s+[" + chr(0x2014) + chr(0x2013) + r"-]\s+|:\s+")


def _item_names_a_standard(line: str, known: set[str]) -> bool:
    """Whether one list item is a reference to a Standard: its text, before an
    owner or a description (` - IAM`, `: covers vendors`), is a Standard's
    title or ends in `Standard(s)`. `- Access Review Standard owner: IAM` is a
    roles line that mentions one, not a reference to it."""
    match = _LIST_ITEM_RE.match(line)
    text = _LINK_RE.sub(r"\1", match.group(1) if match else line)
    key = _key(_ITEM_SPLIT_RE.split(text, maxsplit=1)[0])
    return key in known or bool(re.search(r"\bstandards?$", key))


_PROTECTED = {"purpose", "scope"}


def _section_end(heads, position: int, total: int) -> int:
    """The line index where the section opened by heads[position] ends."""
    level = heads[position][1]
    for later_index, later_level, _ in heads[position + 1 :]:
        if later_level <= level:
            return later_index
    return total


@dataclass
class AssembledPolicy:
    """The Policy text, and what assembling it changed or found."""

    text: str
    #: Standards the draft gave no section, so one was added.
    omitted: list[str] = field(default_factory=list)
    #: Citations that resolved in scope, and tag-shaped spans that did not, as
    #: written: both are taken out.
    tags_removed: list[str] = field(default_factory=list)
    tags_unresolved: list[str] = field(default_factory=list)
    #: Headings of sections the draft wrote that were a bare list of
    #: Standards: dropped, since code writes the one list.
    lists_dropped: list[str] = field(default_factory=list)
    #: Standards outside the group that the draft's prose names. Reported,
    #: not edited out: a sentence is the author's to fix.
    foreign: list[str] = field(default_factory=list)


def assemble_policy(draft: str, group: PolicyGroup, scope, *, link_from: Path) -> AssembledPolicy:
    """The Policy: the draft with code's structure on it.

    - every tag-shaped span is taken out: those that resolve in `scope`, and
      those that don't (`strip_tag_shaped`), both returned as written;
    - the title is the Policy's name when the draft has no `# ` title;
    - a section that is the closing list is dropped, whatever it is headed:
      the exact heading, or any section that is only a list naming Standards.
      Code writes the closing list. So is a section headed as a Standard the
      group doesn't govern, at any level (one outside the group, or one no
      file holds), unless it holds a governed Standard's own section;
    - a Standard has its section when a heading below the title is its title
      (numbering and emphasis ignored, at any level); one with none is given a
      plain section that names it;
    - the closing section lists each Standard by title with its owner and a
      link to its file, relative to `link_from`, the Policy's directory;
    - a Standard outside the group that the remaining prose names is
      returned, so the run can say so.
    """
    from urllib.parse import quote

    from policyforge.content.traceability import strip_tag_shaped, strip_tags

    text, removed = strip_tags(draft, scope)
    text, unresolved = strip_tag_shaped(text)
    text = re.sub(r"[ \t]+\n", "\n", text)

    governed = {_key(s.title) for s in group.standards}
    known = governed | {_key(title) for title in group.others}
    dropped: list[str] = []

    # Closing lists the model wrote, whatever they are headed. One at a time:
    # the headings are read again after each cut.
    while True:
        lines = text.split("\n")
        heads = _headings(text)
        cut = None
        for position, (index, level, title) in enumerate(heads):
            if level < 2 or _key(title) in _PROTECTED:
                continue
            if any(_names(title, g) for g in governed):
                continue
            # A section headed as a Standard: one outside the group, or one no
            # file holds (`## Physical Security Standard`). The Policy doesn't
            # govern it, so it has no section here.
            as_standard = any(_names(title, k) for k in known - governed if k) or bool(
                re.search(r"\bstandards?$", _key(title))
            )
            end = _section_end(heads, position, len(lines))
            # A parent (`## Standards`) holding a governed Standard's own
            # section is not cut: its child is the Standard's.
            if any(
                _names(child, g)
                for child_index, _, child in heads[position + 1 :]
                if child_index < end
                for g in governed
            ):
                continue
            body = [line for line in lines[index + 1 : end] if line.strip()]
            exact = _key(title) == _key(CLOSING_HEADING)
            only_list = (
                level == 2 and bool(body) and all(_LIST_ITEM_RE.match(line) for line in body)
            )
            names_standards = only_list and all(
                _item_names_a_standard(line, known) for line in body
            )
            if exact or names_standards or as_standard:
                cut = (index, end, title)
                break
        if cut is None:
            break
        index, end, title = cut
        dropped.append(title)
        text = "\n".join(lines[:index] + lines[end:])

    body = text.rstrip()
    heads = _headings(body)
    if not any(level == 1 for _, level, _ in heads):
        body = f"# {group.policy.name}\n\n{body.lstrip()}"
        heads = _headings(body)

    # Standards outside the group that the prose names, before code's own
    # sections and list are added.
    foreign = [
        title
        for title in group.others
        if _key(title) and _key(title) not in governed and _key(title) in _key(body)
    ]

    below_title = [title for _, level, title in heads if level >= 2]
    omitted: list[str] = []
    stubs: list[str] = []
    for standard in group.standards:
        key = _key(standard.title)
        if any(_names(title, key) for title in below_title):
            continue
        omitted.append(standard.title)
        stubs.append(
            f"## {standard.title}\n\nThis Policy governs the {standard.title}; its requirements "
            "are in that Standard."
        )
    if stubs:
        body = body + "\n\n" + "\n\n".join(stubs)

    items = []
    for standard in group.standards:
        link = quote(os.path.relpath(standard.path, link_from).replace(os.sep, "/"), safe="/")
        owner = f" \u2014 {standard.owner}" if standard.owner else ""
        items.append(f"- [{standard.title}]({link}){owner}")
    body = body + f"\n\n## {CLOSING_HEADING}\n\n" + "\n".join(items) + "\n"
    return AssembledPolicy(
        text=body,
        omitted=omitted,
        tags_removed=removed,
        tags_unresolved=unresolved,
        lists_dropped=dropped,
        foreign=foreign,
    )
