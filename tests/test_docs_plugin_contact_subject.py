"""Every docs link to the plugin contact asks with the CLI's subject line.

The docs say how to ask for the licensed-framework plugin with a
`mailto:info@zardoz.io?subject=…` link, and the CLI's refusal names the same
subject (`plugins.PLUGIN_CONTACT`), so mail about the plugin arrives under one
subject the project can filter on. Nothing kept the two together: a page's
subject could drift and every test still passed (ba, reading the PR that added
the links). This test is that pin.

- The population is `git ls-files '*.md'`, not a list of pages, and the links
  the docs carry today must all be found (an extent check, so a narrowed
  pathspec can't pass on nothing).
- The expected subject is parsed out of `PLUGIN_CONTACT`, never retyped here,
  and the parse must find something, so a reworded constant can't make the
  expected subject empty.
- A link with no subject, an empty one, a different one or two of them fails,
  naming the file and line.

**Decoding is `urllib.parse.unquote`, not `unquote_plus`:** RFC 6068 doesn't
make `+` a space in a mailto URI, and mail clients differ, so a subject
written with `+` could reach the inbox as "PolicyForge+licensed…". Such a
link fails here; write spaces as `%20`.

A CommonMark email autolink (`<info@zardoz.io>`) is a link too, and has no
subject, so it fails (b5 on #210).

**What it doesn't check:** a BARE address in prose (`email info@zardoz.io`).
GitHub's extended autolinks render it as a mailto with no subject, but bare
prose is how the agent skill and a changelog fragment state the contact,
with the subject in words beside it. Whether those should become links is a
docs decision, not this test's. The docs test for the contact
(`test_docs_name_the_plugin.py`) only requires the address there.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote

from policyforge.plugins import PLUGIN_CONTACT

ROOT = Path(__file__).resolve().parent.parent

#: A link to the contact address: `mailto:info@zardoz.io…`, or the CommonMark
#: email autolink `<info@zardoz.io>`, which renders as a mailto with no
#: subject and so fails (b5 on #210). Matched case-insensitively, because an
#: address in another case still reaches the same inbox. The query stops at
#: `>`, so an autolink reads as "0 subject(s)".
_LINK = re.compile(r"(?:mailto:|<)info@zardoz\.io(?P<query>[^\s)\"'<>\]]*)", re.IGNORECASE)


def _subject_from_constant() -> str:
    match = re.search(r'with the subject "([^"]+)"', PLUGIN_CONTACT)
    assert match and match.group(1).strip(), (
        f"PLUGIN_CONTACT no longer names a subject in the shape this reads: {PLUGIN_CONTACT!r}"
    )
    return match.group(1)


SUBJECT = _subject_from_constant()


def _tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    files = [f for f in out.split("\0") if f]
    assert files, "git ls-files matched no *.md: the pathspec stopped matching"
    return files


def _subjects(query: str) -> list[str]:
    """Every `subject` value in a mailto query, decoded. Header names are
    case-insensitive (RFC 6068), so `Subject=` counts."""
    if not query.startswith("?"):
        return []
    found = []
    for field in query[1:].split("&"):
        name, _, value = field.partition("=")
        if name.lower() == "subject":
            found.append(unquote(value))
    return found


def _problems(pages: dict[str, str]) -> list[str]:
    """`path:line: why` for every contact link whose subject isn't exactly
    the CLI's, once."""
    problems = []
    for path, text in pages.items():
        for number, line in enumerate(text.splitlines(), 1):
            for match in _LINK.finditer(line):
                query = match.group("query")
                if query and not query.startswith("?"):
                    # Anything else straight after the address (a second
                    # recipient, `,other@x`) is named as itself, not
                    # misreported as a missing subject (1d on #210).
                    problems.append(
                        f"{path}:{number}: {query!r} follows the address, must be ?subject="
                    )
                    continue
                subjects = _subjects(query)
                if len(subjects) != 1:
                    problems.append(f"{path}:{number}: {len(subjects)} subject(s), must be 1")
                elif subjects[0] != SUBJECT:
                    problems.append(
                        f"{path}:{number}: subject {subjects[0]!r}, must be {SUBJECT!r}"
                    )
    return sorted(problems)


def _links(pages: dict[str, str]) -> dict[str, int]:
    return {p: n for p, t in pages.items() if (n := len(_LINK.findall(t)))}


# ---- the real tree ----------------------------------------------------------


def test_every_docs_contact_link_asks_with_the_clis_subject():
    pages = {p: (ROOT / p).read_text(encoding="utf-8") for p in _tracked_markdown()}
    assert _problems(pages) == []


def test_the_links_the_docs_carry_are_all_found():
    """Extent: the pages that carried a contact link when this was written,
    listed by hand from the docs (the other side of the check), are all in
    the population, so a narrowed pathspec or pattern fails here.

    `docs/positioning.md` left the repo for zardoz.io/compare (289), taking
    two of the seven links with it, so the set and the floor shrank by
    decision, not by deletion: the same subject check now runs on every
    contact link of the site's /trust, /compare and /docs pages, in the
    site's `scripts/check_trust.py` (5b, 5559), which was shown failing on a
    planted wrong subject."""
    pages = {p: (ROOT / p).read_text(encoding="utf-8") for p in _tracked_markdown()}
    expected = {
        "README.md",
        "data/frameworks/govramp/README.md",
        "data/frameworks/hitrust-ai/README.md",
        "data/frameworks/hitrust-csf/README.md",
        "data/frameworks/soc2-tsc/README.md",
    }
    found = _links(pages)
    assert expected <= set(found), sorted(expected - set(found))
    assert sum(found.values()) >= 5, found


def test_the_expected_subject_comes_from_the_constant():
    """Not retyped: the subject is whatever `PLUGIN_CONTACT` quotes."""
    assert SUBJECT and SUBJECT in PLUGIN_CONTACT


# ---- the check fails when it should ------------------------------------------


def _page(query: str) -> dict[str, str]:
    return {"x/README.md": f"intro\n[info@zardoz.io](mailto:info@zardoz.io{query})\n"}


def test_a_page_whose_subject_drifted_fails_naming_the_file():
    """The arm from the issue: one page's subject changed."""
    assert _problems(_page("?subject=PolicyForge%20plugin")) == [
        f"x/README.md:2: subject 'PolicyForge plugin', must be {SUBJECT!r}"
    ]


def test_every_wrong_shape_fails():
    right = quote(SUBJECT)
    for query in (
        "",
        "?body=hello",
        "?subject=",
        f"?subject={right}&subject={right}",
        f"?subject={SUBJECT.replace(' ', '+')}",
    ):
        assert _problems(_page(query)), query


def test_an_email_autolink_fails_for_want_of_a_subject():
    """b5 on #210: `<info@zardoz.io>` renders as a mailto with no subject.
    Bare prose stays out of scope (the docstring says why)."""
    page = {"x/README.md": "Email <info@zardoz.io> for it.\nOr <MAILTO:info@zardoz.io>.\n"}
    assert _problems(page) == [
        "x/README.md:1: 0 subject(s), must be 1",
        "x/README.md:2: 0 subject(s), must be 1",
    ]
    assert _problems({"x/README.md": "Email info@zardoz.io for it.\n"}) == []


def test_a_second_recipient_is_named_as_itself():
    """1d on #210: it failed before, but as "0 subject(s)"."""
    query = f",other@example.com?subject={quote(SUBJECT)}"
    assert _problems(_page(query)) == [
        f"x/README.md:2: {query!r} follows the address, must be ?subject="
    ]


def test_the_right_subject_passes_in_any_header_order_and_case():
    right = quote(SUBJECT)
    for query in (f"?subject={right}", f"?body=hi&subject={right}", f"?Subject={right}"):
        assert _problems(_page(query)) == [], query


def test_another_case_of_the_address_is_still_checked():
    page = {"x/README.md": "[x](MAILTO:Info@Zardoz.io?subject=Other)\n"}
    assert _problems(page) == [f"x/README.md:1: subject 'Other', must be {SUBJECT!r}"]
