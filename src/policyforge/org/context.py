"""Reading an organization's tools and teams, and filling them in exactly.

Two halves, and the second is the point.

**Reading** accepts both shapes. `vendors` has always been a flat list, and
config files people already maintain must keep working, so a list is still
valid and still means "these products exist, work out where they go". A
mapping is the richer form: `identity_provider: Okta` says what Okta is
*for*, which is the only fact a substitution needs.

**Filling in** happens after the model has written the document, not inside
the prompt. That ordering is the whole reason this module exists. Asking a
model to use a name consistently gets it right most of the time, and "most
of the time" across forty documents is a set where the same system is called
three things. A post-pass over the finished text is exact, repeatable, and
testable without an API key: the generator writes `[Identity Provider]`
wherever the role belongs, and every one of those becomes `Okta` or none of
them does.

Placeholders that stay unfilled are the useful output of the same pass. A
document saying `[Ticketing System]` is telling you a fact about your
configuration, not failing — and knowing which ones are outstanding is worth
more than a document that quietly invented a product name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from policyforge.content.tags import is_citation_tag, is_unresolved_citation

from .roles import TEAM_ROLES, VENDOR_ROLES, Role, resolve_role

#: Matches `[Identity Provider]` and the hedged `[Identity Provider - Okta]`
#: form a model writes when it has been given a name but is unsure the role
#: is right. Both resolve to the configured value when there is one.
#:
#: The dash separating a hedge must have whitespace on both sides. Without
#: that, `[On-Call Paging]` parses as the role "On" hedged with "Call
#: Paging" - a label with a hyphen in it is ordinary English, and several of
#: the role labels have one.
#:
#: Em dash, en dash, hyphen, built by code point rather than written out:
#: three dashes side by side in a source file are indistinguishable to a
#: reader and to a linter, and only one of them is the ASCII one.
_HEDGE_DASHES = "".join(chr(code) for code in (0x2014, 0x2013, 0x2D))


#: Every declared role label, read VERBATIM (80's ruling (c) on #243): escaped,
#: longest first, tried before the generic label class below, which is
#: unchanged. One declared label, "Governance, Risk and Compliance", has a
#: comma the generic class can't read, so it was never filled or reported.
#: Widening the class to take a comma would make every capitalised bracketed
#: list in prose (`[Access Control, Audit]`) a placeholder; a literal matches
#: only exactly a declared label, so nothing else newly matches.
#:
#: **Case: the generic class's own rule** (1d on PR 249). The fill pass looks
#: a label up lowercased (`OrgProfile.substitutions`), so `[SECURITY
#: ENGINEERING]` already fills through the generic class, which requires only
#: a capital first. A literal follows the same rule: its first character as
#: declared, the rest in any case. So `[GOVERNANCE, RISK AND COMPLIANCE]` is
#: read, and nothing newly matches that the generic class wouldn't read with
#: a comma-free label (`[security engineering]` stays unmatched).
#:
#: **Longest first, and the label set is an input** (b5 on PR 249): with
#: labels `Ops` and `Ops - Night`, `[Ops - Night]` is the longer label,
#: not `Ops` hedged with "Night". Today's 47 labels have no such pair, so the
#: order is pinned with stand-in labels, through this function.
def placeholder_pattern(labels) -> re.Pattern:
    """The placeholder pattern for a set of declared role labels: each label
    as a literal (first character as declared, the rest any case), longest
    first, then the generic label class, then an optional hedge.

    Group 1 is a declared label, group 2 the generic label; the one that
    matched is the placeholder's label (`_label_of`).

    A span followed at once by `(` or `[` is link syntax, never a placeholder
    (80's scope on issue 295): `[Text](url)`, `[Text][ref]` and an image's
    `![Alt](url)`. Read as a placeholder, a link's text was reported as
    unfilled, or filled into `Jira(<url>)`, a broken link. One
    lookahead after the closing bracket covers every label form, declared or
    generic. Not `:` (`[Owner]: approves ...` is a placeholder in prose) and
    not a space (`[Owner] (see below)` is one too)."""
    literals = "|".join(
        re.escape(label[0]) + "(?i:" + re.escape(label[1:]) + ")"
        for label in sorted(set(labels), key=lambda label: (-len(label), label))
    )
    return re.compile(
        # One character, or one balanced parenthesised group, per step (#268:
        # `(SIEM)`, `(IDS/IPS)`, no nesting). Not `(?:[...]+|\(...\))*`: a
        # repeated group whose branch repeats too backtracks exponentially on
        # a bracket that never closes (ba measured 4.5 s at 24 characters on
        # the carry). This form is linear.
        rf"\[(?:({literals})|([A-Z](?:[A-Za-z0-9/ .&-]|\([A-Za-z0-9/ .&-]+\))*?))"
        # Link syntax is never a placeholder (issue 295): one lookahead after
        # the closing bracket, for every label form above.
        rf"(?:\s+[{_HEDGE_DASHES}]\s+[^\]]+)?\](?![(\[])"
    )


_PLACEHOLDER_RE = placeholder_pattern(
    role.label for role in (*VENDOR_ROLES.values(), *TEAM_ROLES.values())
)


#: The closed class of English function words (prepositions, articles and
#: conjunctions) a label may carry in lower case (80's ruling on 267, comment
#: 6577). **This is the class, not a choice:** add a word only with a measured
#: label that needs it. Measured on the 128 committed documents, the eight
#: words 6476 first listed misread nine parameter labels (`[Systems in Plan
#: Scope]`, `[Review Frequency per Section 2.2]`).
_FUNCTION_WORDS = frozenset(
    {
        "in", "on", "at", "by", "with", "from", "per", "as", "via", "into", "within",
        "a", "an", "the", "under", "over", "than", "of", "and", "or", "for", "to", "&", "/",
    }
)  # fmt: skip


def is_bracketed_prose(label: str) -> bool:
    """Whether a bracketed span reads as a sentence, not a label (267).

    **A placeholder is a label** (80's ruling on 267): a lower-case word makes
    a span prose only when it is a content word. Function words, the closed
    class `_FUNCTION_WORDS`, never do, because a label can carry them, and
    neither does anything after a colon (`[Assignment: organization-defined
    frequency]`). Whether the span fills a whole list item does not decide it;
    the words do. `[Access to policy management system]` is prose;
    `[Head of Security]`, `[Systems in Plan Scope]` and a title-cased
    `[Access To Policy Management System]` are labels.

    A word is a lower-case content word when it has letters and none is
    upper-case, so a mixed-case word (`macOS`, `eDiscovery`, `(SIEM)`) is
    read as part of a label: the over-report that leaves is the visible kind.
    Nothing this returns True for is dropped: `apply_substitutions` lists it
    under `prose`, left exactly as written."""
    head = label.split(":", 1)[0]
    for word in head.split():
        if word.lower() in _FUNCTION_WORDS:
            continue
        letters = [c for c in word if c.isalpha()]
        if letters and not any(c.isupper() for c in letters):
            return True
    return False


def _label_of(match: re.Match) -> str:
    """The placeholder's label: the declared label it matched verbatim, or
    the generic label."""
    return (match.group(1) or match.group(2)).strip()


@dataclass
class Assignment:
    """One role, and what fills it."""

    role: Role
    value: str


@dataclass
class OrgProfile:
    """Who the organization is, and what it runs."""

    name: str = ""
    industry: str = ""
    #: Products named with no role attached. Still passed to the generator,
    #: which infers what it can — the old behaviour, kept because config
    #: files in the wild use it.
    unkeyed_vendors: list[str] = field(default_factory=list)
    vendors: dict[str, Assignment] = field(default_factory=dict)
    teams: dict[str, Assignment] = field(default_factory=dict)
    #: Keys that matched no known role. Reported rather than dropped: a
    #: typo'd `identity-provdier` should be visible, not silently ignored.
    unknown: list[str] = field(default_factory=list)

    @property
    def assignments(self) -> list[Assignment]:
        return list(self.vendors.values()) + list(self.teams.values())

    def substitutions(self) -> dict[str, str]:
        """Placeholder label -> the value that replaces it, lowercased keys."""
        return {a.role.label.lower(): a.value for a in self.assignments}


def _parse(raw, roles: dict[str, Role]) -> tuple[dict[str, Assignment], list[str], list[str]]:
    """Read a role mapping, tolerating the legacy list form."""
    if not raw:
        return {}, [], []
    if isinstance(raw, list):
        return {}, [str(item) for item in raw if str(item).strip()], []

    if not isinstance(raw, dict):
        return {}, [], []

    assignments: dict[str, Assignment] = {}
    unknown: list[str] = []
    for key, value in raw.items():
        text = str(value).strip() if value is not None else ""
        if not text:
            continue
        role = resolve_role(str(key), roles)
        if role is None:
            unknown.append(str(key))
            continue
        assignments[role.key] = Assignment(role=role, value=text)
    return assignments, [], sorted(unknown)


def load_org_profile(config: dict) -> OrgProfile:
    """Build a profile from the `org:` block of a loaded config."""
    org = (config or {}).get("org") or {}
    vendors, unkeyed, unknown_vendors = _parse(org.get("vendors"), VENDOR_ROLES)
    teams, unkeyed_teams, unknown_teams = _parse(org.get("teams"), TEAM_ROLES)

    return OrgProfile(
        name=str(org.get("name") or ""),
        industry=str(org.get("industry") or ""),
        # A team list with no roles is meaningless — a team name only helps
        # when you know what it owns — so those fall through to `unknown`
        # rather than being carried as free-floating strings.
        unkeyed_vendors=unkeyed,
        vendors=vendors,
        teams=teams,
        unknown=sorted(unknown_vendors + unknown_teams + unkeyed_teams),
    )


def org_actors(config: dict) -> tuple[str, ...]:
    """The names the organization acts under, from its own config (#323).

    Its name, and every team and vendor name it declares, whatever the key:
    a recognised role, an unrecognised key, or a plain list. The Playbook
    gate refuses one of these opening a clause after "NIST suggests ...",
    because that clause is the organization committing. Derived from config,
    never typed, so it cannot drift from the organization it describes.

    **Read from the raw `org:` block, not from `load_org_profile`** (9b on
    #329). The profile keeps only names under a role it recognises, and sends
    an unrecognised key such as `it_ops: IT Ops`, or an unkeyed team list, to
    `unknown`, which holds the KEYS and not the names. For substitution that
    is right. Here it dropped actors the organization had declared, so
    "..., and IT Ops will adopt it" passed.
    """
    # `check` is the pre-publish gate and never read `org:` before #323, so a
    # shape it did not expect must not turn it into a traceback (1d on #329):
    # a bare string is the organization's name, any other non-mapping gives
    # no actors, and only string values count as names.
    org = (config or {}).get("org")
    if isinstance(org, str):
        org = {"name": org}
    if not isinstance(org, dict):
        return ()
    names = [org.get("name")]
    for block in (org.get("teams"), org.get("vendors")):
        if isinstance(block, dict):
            names += list(block.values())
        elif isinstance(block, list):
            names += block
    return tuple(dict.fromkeys(n.strip() for n in names if isinstance(n, str) and n.strip()))


@dataclass
class SubstitutionResult:
    text: str
    #: (placeholder label, value) actually replaced, in document order.
    filled: list[tuple[str, str]] = field(default_factory=list)
    #: Labels left as placeholders, deduplicated. Not a failure: a document
    #: that says [Ticketing System] is reporting a gap in the configuration
    #: rather than inventing a product.
    outstanding: list[str] = field(default_factory=list)
    #: The labels in `outstanding` written as framework citations that don't
    #: resolve against the run's catalogs (`content.tags
    #: .is_unresolved_citation`, #222), in the same order. Filling them under
    #: `org.vendors` or `org.teams` would be wrong advice.
    unresolved_citations: list[str] = field(default_factory=list)
    #: Bracketed spans that read as sentences, not labels (267,
    #: `is_bracketed_prose`), left in the text as written, deduplicated, in
    #: document order. **Apart from `outstanding`**: they are not placeholders
    #: to fill, and `roles_left`, `unfilled` and `unresolved_citations` still
    #: partition `outstanding`. Every span the reader stops counting as a
    #: placeholder is here, so nothing goes silent.
    prose: list[str] = field(default_factory=list)

    @property
    def roles_left(self) -> list[str]:
        """The labels in `outstanding` that are roles: a label the role
        vocabulary declares (`org.roles`), matched as the fill pass matches
        it (`OrgProfile.substitutions`: the role's label, lowercased), so a
        role listed here is one setting `org.vendors` or `org.teams` would
        fill. `[CISO]` is not one: no declared role's label is `CISO`, so
        no config fills it, and it is in `unfilled` (80's ruling 3918 on
        #228)."""
        citations = set(self.unresolved_citations)
        return [
            label
            for label in self.outstanding
            if label not in citations and label.lower() in _ROLE_LABELS
        ]

    @property
    def unfilled(self) -> list[str]:
        """The rest of `outstanding`: neither an unresolved citation nor a
        declared role. Mostly organisation-defined parameter values the
        model named itself (`[Access Review Frequency]`), plus systems and
        roles the vocabulary doesn't declare (`[Certificate Authority]`,
        `[CISO]`). Nothing here says which a label is: that needs the
        parameter's identity carried through generation (#240), so the
        advice names both places (80's ruling 3918 on #228).

        `roles_left`, `unfilled` and `unresolved_citations` partition
        `outstanding`: their lengths always sum to it."""
        known = set(self.unresolved_citations) | set(self.roles_left)
        return [label for label in self.outstanding if label not in known]


#: The labels the fill pass can fill, lowercased as `OrgProfile.substitutions`
#: keys them: every declared vendor and team role (#228).
_ROLE_LABELS = frozenset(
    role.label.lower() for role in (*VENDOR_ROLES.values(), *TEAM_ROLES.values())
)


def apply_substitutions(
    text: str, profile: OrgProfile, scope=None, *, ignore=None, owner: str = ""
) -> SubstitutionResult:
    """Replace every placeholder whose role the organization has filled.

    Deterministic by construction: the same document and the same config
    produce the same output, and no model is consulted. Placeholders for
    roles nobody has assigned are left exactly as written.

    `scope` is the run's catalog set (`topics.satisfies.CatalogScope`), so a
    bracket is told from a role as `satisfies` would resolve it (#215); with
    none, every catalog on disk (`content.tags.is_citation_tag`).
    """
    table = profile.substitutions()
    # Bracketed spans that are the document's own references, not placeholders
    # (#266): a Procedure's Related Standard, written as the Standard's title
    # in brackets. Written back without the brackets, and never counted.
    ignored = {label.strip().lower() for label in ignore or () if label and label.strip()}
    clean_owner = owner.strip() if owner else ""
    filled: list[tuple[str, str]] = []
    outstanding: list[str] = []
    unresolved_citations: list[str] = []
    prose: list[str] = []

    def _replace(match: re.Match) -> str:
        if is_citation_tag(match.group(0), scope):
            # `[NIST 800-53 AC-2]` cites a framework; it is not an unfilled
            # role, so it is neither filled nor reported (#189).
            return match.group(0)
        label = _label_of(match)
        if label.lower() in ignored:
            # A link's text never reaches here: the pattern itself refuses a
            # span followed by `(` or `[` (issue 295), which replaced 284's
            # check for the ignored title alone.
            return label
        value = table.get(label.lower())
        if value is None and clean_owner and label == clean_owner:
            # The document's own owner, from its synthesis frontmatter (#422):
            # a name, not a role left to fill, so it is written without its
            # brackets. Only after the table: a team the organization declared
            # under `org.teams` keeps the value it gave (b5 on 422, measured
            # with `IT Operations` declared and also the topic's owner).
            filled.append((label, clean_owner))
            return clean_owner
        if value is None:
            citation = is_unresolved_citation(f"[{label}]", scope)
            # After the table and the owner, so a role the config fills is
            # filled; and never for a declared label (group 1) or a span
            # written as a citation, which keep their own lists (267).
            if match.group(1) is None and not citation and is_bracketed_prose(label):
                if label not in prose:
                    prose.append(label)
                return match.group(0)
            if label not in outstanding:
                outstanding.append(label)
                if citation:
                    unresolved_citations.append(label)
            return match.group(0)
        filled.append((label, value))
        return value

    return SubstitutionResult(
        text=_PLACEHOLDER_RE.sub(_replace, text),
        filled=filled,
        outstanding=outstanding,
        unresolved_citations=unresolved_citations,
        prose=prose,
    )


def render_for_prompt(profile: OrgProfile) -> str:
    """The organization block a generator prompt is given.

    Roles with a value are stated as facts. Roles without one are *not*
    listed: naming thirty unfilled roles would invite the model to mention
    systems the organization never said it had, and the placeholder it
    should write is already specified by the drafting rules.
    """
    lines = [f"Organization: {profile.name}", f"Industry: {profile.industry}"]

    if profile.vendors:
        lines.append("")
        lines.append("Tools, by the role each one fills:")
        lines += [
            f"  {a.role.label}: {a.value}"
            for a in sorted(profile.vendors.values(), key=lambda a: a.role.label)
        ]
        lines.append(
            "  Use these names for these roles. Where the document needs a system "
            "whose role is not listed above, write the role in square brackets "
            "(for example [Ticketing System]) rather than naming a product the "
            "organization has not said it uses."
        )
    if profile.unkeyed_vendors:
        lines.append("")
        lines.append(
            "Other tools in use, roles not stated: "
            + ", ".join(profile.unkeyed_vendors)
            + ". Use them only where you are confident what they are for."
        )
    if profile.teams:
        lines.append("")
        lines.append("Teams, by the function each one performs:")
        lines += [
            f"  {a.role.label}: {a.value}"
            for a in sorted(profile.teams.values(), key=lambda a: a.role.label)
        ]

    if not profile.vendors and not profile.unkeyed_vendors:
        lines.append("")
        lines.append(
            "No tools supplied. Write the role in square brackets wherever the "
            "document needs to name a system — [Identity Provider], [Ticketing "
            "System] — rather than naming a product."
        )
    return "\n".join(lines)
