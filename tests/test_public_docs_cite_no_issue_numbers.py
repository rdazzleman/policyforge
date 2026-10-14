"""Public documents cite no internal issue numbers.

The user's decision: every Markdown file in the tree ships in the public
snapshot, where `#NNN` and `GH#NNN` lead nowhere (the public repository holds
releases only), and on Forgejo a plain `#NNN` names a different issue anyway.
So no tracked `.md` file carries one, `CHANGELOG.md` and the `changelog.d/`
fragments included, which keeps future release notes clean. Code comments and
docstrings are out of scope.

80's rulings for this check:

- The population is `git ls-files '*.md'`, not a list. The changelog fragments
  are `.md` files (the fragment check refuses any other), so they are in it.
- Inline code spans and fenced blocks count, because a reader sees both.
- A bare `#<digits>` always fails, single digits included: Forgejo's #1 to #9
  are real issues, and a reader can't tell "#1 priority" from one either.
- So does a word of two or more letters glued to it: `GH#459`, `PR#12`,
  `issue#12`, `ops#1` (9b on #199). Not a list of prefixes, so a new spelling
  is caught too.
- These pass: `#` headings, colour codes with a letter in them (`#fff`,
  `#1e90ff`, `#00ff00`, `#12abcd`), same-page anchors with a letter or hyphen
  in them (`[x](#2-quick-start)`), HTML entities (`&#160;`), and a single
  letter before the `#` (`C#`, `F#`, `C#7`).

**What it can't tell apart, and so FAILS:**
- an all-digit colour code (`#333`, `#000000`);
- an all-digit anchor (`[see it](#12)`, `href="#12"`), which is a heading
  slug only if a heading is just a number (1d on #199);
- a URL whose fragment is all digits after letters (`page.html#12`).

None of these is in the tree (measured when this was written). Write such a
colour as a name or with `rgb()`, give the heading a word, or keep it out of
Markdown.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: A same-page anchor, `](#…)` or `href="#…"`, with something other than
#: digits in it: removed before scanning, because `#2-quick-start` is a
#: heading's slug, not an issue. An all-digit anchor (`](#12)`) is NOT removed,
#: so it is scanned and fails (1d on #199).
_ANCHOR = re.compile(
    r"\]\(#(?=[^)\s]*[^\d)\s])[^)\s]*\)|href=[\"']#(?=[^\"']*[^\d\"'])[^\"']*[\"']"
)

#: The end of a citation: not followed by a word character. `(?!\w)`, not
#: `(?![A-Za-z_])`: a digit is `\w` too, so `\d+` can't give a digit back to
#: let `#00ff00` read as `#0` or `#12abcd` as `#1` (b5 on #199).
_END = r"(?!\w)"

#: Two or more letters glued to `#<digits>`: `GH#459`, `PR#12`, `issue#12`,
#: `ops#1`. Not after a word character, `&`, `#` or `/`, as below.
_PREFIXED = re.compile(r"(?<![\w&#/])[A-Za-z]{2,}#\d+" + _END)

#: A bare `#` and digits. Not after a word character (`C#`, `F#`, and the
#: prefixed forms above, which `_PREFIXED` reports whole), `&` (an HTML entity,
#: `&#160;`), `#` (`##` headings run on) or `/` (a URL fragment). A hyphen does
#: NOT excuse it: `#12-14` is a range of issues, and an anchor's hyphen is
#: handled by `_ANCHOR`.
_BARE = re.compile(r"(?<![\w&#/])#\d+" + _END)


def _tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return [f for f in out.split("\0") if f]


def _citations(text: str) -> list[str]:
    """Every internal issue reference in `text`, in order."""
    text = _ANCHOR.sub("", text)
    found = [(m.start(), m.group()) for m in _PREFIXED.finditer(text)]
    found += [(m.start(), m.group()) for m in _BARE.finditer(text)]
    return [ref for _, ref in sorted(found)]


def _offending(pages: dict[str, str]) -> list[str]:
    return sorted(f"{path}: {refs}" for path, text in pages.items() if (refs := _citations(text)))


# ---- the real tree ----------------------------------------------------------


def test_the_population_is_every_tracked_markdown_file_changelog_included():
    """Extent, not just consistency: a narrowed pathspec can't pass on nothing."""
    files = _tracked_markdown()
    assert len(files) >= 20, f"only {len(files)} tracked .md files: the pathspec narrowed"
    assert "CHANGELOG.md" in files
    assert "README.md" in files
    fragments = [f for f in files if f.startswith("changelog.d/")]
    assert fragments, "no changelog.d/ file is in the population"


def test_no_public_document_cites_an_internal_issue_number():
    pages = {p: (ROOT / p).read_text(encoding="utf-8") for p in _tracked_markdown()}
    assert _offending(pages) == [], (
        "Public .md files (and changelog fragments) cite no internal issue numbers: "
        "say what the issue established instead, in words a public reader can check."
    )


# ---- the checks fail when they should ----------------------------------------


def test_a_changelog_fragment_citing_an_issue_fails():
    """80's must-fail arm."""
    pages = {"changelog.d/ba-x.md": "**Estimates follow one rule.** (#110)\n"}
    assert _offending(pages) == ["changelog.d/ba-x.md: ['#110']"]


def test_every_shape_of_a_citation_fails():
    """Written from the shapes the team's prose actually uses, not from the regex."""
    cases = {
        "#12": ["#12"],
        "(#12)": ["#12"],
        "see #12, then": ["#12"],
        "#12.": ["#12"],
        "issues #3 and #4": ["#3", "#4"],
        "#1 priority": ["#1"],
        "#12-14": ["#12"],
        "GH#459": ["GH#459"],
        "the gh#459 thread": ["gh#459"],
        "`#12` in a span": ["#12"],
        "```\nrun it (#12)\n```": ["#12"],
        "line one\n#7 starts a line": ["#7"],
        # 9b on #199: a word glued to the number.
        "PR#12": ["PR#12"],
        "see issue#12": ["issue#12"],
        "ops#1": ["ops#1"],
        "PRs#3 and pulls#4": ["PRs#3", "pulls#4"],
        # 1d on #199: an all-digit anchor is a citation, not a slug.
        "[see it](#12)": ["#12"],
        '<a href="#12">it</a>': ["#12"],
        # 1d's domain probes on #199.
        "[1]: #12": ["#12"],
        "PR&nbsp;#12": ["#12"],
        "**#12**": ["#12"],
        "#12" + chr(0x2013) + "14": ["#12"],  # an en-dash range
        "(GH#459, #7)": ["GH#459", "#7"],
    }
    for text, refs in cases.items():
        assert _citations(text) == refs, (text, _citations(text))


def test_what_is_not_a_citation_passes():
    """Each written by hand: headings, colours with a letter, anchors with a
    letter or hyphen (numeric-looking ones too), HTML entities, C# and F#."""
    passing = [
        "# Heading",
        "## 1.7.0",
        "### 2. Quick start",
        "color: #fff;",
        "color: #1e90ff;",
        # b5 on #199: a colour starting with digits must not backtrack to #0, #1.
        "color: #00ff00;",
        "color: #12abcd;",
        "C#7 and F#8",
        "See [the install](#install).",
        "See [quick start](#2-quick-start).",
        "See [C-05](#c-05--access-control).",
        '<a href="#3-usage">usage</a>',
        "a&#160;b",
        "an em dash &#x2014; here",
        "written in C# and F#",
        "https://example.com/page#section",
    ]
    for text in passing:
        assert _citations(text) == [], (text, _citations(text))
