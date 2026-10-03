"""Public docs name the plugin the licensed commands need (#101).

1.7 moved the licensed-framework parsers out of the public tool (#32).
`etl-hitrust`, `etl-govramp` and `etl-soc2-tsc` are still commands, but
without the separately distributed plugin each one only says it's missing.
80's post-zero review found public pages presenting them as working parts of
the tool, with the plugin never mentioned, so a reader following them hits a
refusal nobody warned them about.

The command set comes from the plugin seam (`plugins.LICENSED_FRAMEWORK_NAMES`),
not from a list here, so a fourth licensed command is covered when it's added.
The pages come from the tracked tree (`git ls-files '*.md'`), not from the
pages this fix happened to edit. The changelog is history: it records what
shipped while the parsers were part of the tool, so it's excluded by name.

A second check covers the same review's other finding (5b's): public docs
named perishable sessions (`policyforge-f8`, and in MEASUREMENTS.md `-d8` and
`-f2`). Stable handles, derived from `review_lines.STABLE_HANDLES`, are allowed;
any other `policyforge-<hex><hex>` on a public page fails.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from policyforge.plugins import LICENSED_FRAMEWORK_NAMES

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import review_lines  # noqa: E402

#: The phrase a page must carry once it names a licensed command.
PLUGIN = "licensed-framework plugin"

#: History, not instructions: what shipped when the parsers were in the tool.
HISTORY = {"CHANGELOG.md"}
HISTORY_DIRS = ("changelog.d/",)


SESSION_NAME = re.compile(r"\bpolicyforge-[0-9a-f]{2}\b")


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


def _public_pages() -> list[str]:
    return [f for f in _tracked_markdown() if f not in HISTORY and not f.startswith(HISTORY_DIRS)]


def _commands_named(text: str) -> set[str]:
    return {
        command
        for command in LICENSED_FRAMEWORK_NAMES
        if re.search(rf"(?<![\w-]){re.escape(command)}(?![\w-])", text)
    }


def _pages_missing_the_plugin(pages: dict[str, str]) -> list[str]:
    """Pages that name a licensed command and never name the plugin. The check
    is page-level: it can't see WHERE the plugin is named, so a page that names
    it only after the first mention still passes (ba on #105)."""
    return sorted(
        f"{path}: names {sorted(named)}"
        for path, text in pages.items()
        if (named := _commands_named(text)) and PLUGIN not in text
    )


def _stray_session_names(pages: dict[str, str]) -> list[str]:
    return sorted(
        f"{path}: {name}"
        for path, text in pages.items()
        for name in set(SESSION_NAME.findall(text))
        if name not in review_lines.STABLE_HANDLES
    )


def _read(paths: list[str]) -> dict[str, str]:
    return {p: (ROOT / p).read_text(encoding="utf-8") for p in paths}


# ---- the real tree ----------------------------------------------------------


def test_the_command_set_is_the_seams():
    """The expected set comes from the seam. If it ever went empty, every page
    would pass vacuously, so its size is asserted too."""
    assert len(LICENSED_FRAMEWORK_NAMES) >= 3


def test_every_public_page_that_names_a_licensed_command_names_the_plugin():
    pages = _read(_public_pages())
    naming = [p for p, t in pages.items() if _commands_named(t)]
    assert naming, "no public page names a licensed command: the matcher stopped matching"
    assert _pages_missing_the_plugin(pages) == []


def test_public_docs_name_no_perishable_session():
    """Over the same population as the plugin rule: every tracked page but the
    changelog (1d on #105: a narrower set let CONTRIBUTING.md and
    docs/commitments.md take a session name unseen)."""
    assert _stray_session_names(_read(_public_pages())) == []


# ---- the checks fail when they should ----------------------------------------


def test_a_page_naming_a_command_without_the_plugin_fails():
    pages = {"x/README.md": "Run `policyforge etl-govramp --export <path>`."}
    assert _pages_missing_the_plugin(pages) == ["x/README.md: names ['etl-govramp']"]


def test_a_page_naming_the_plugin_passes():
    text = "Run `etl-soc2-tsc`. It is part of the licensed-framework plugin."
    assert _pages_missing_the_plugin({"x/README.md": text}) == []


def test_a_longer_name_is_not_a_licensed_command():
    """`etl-hitrust-ai` doesn't exist yet and isn't the plugin's command, so a
    page naming only it isn't held to the rule."""
    assert _commands_named("There is no `etl-hitrust-ai` yet.") == set()


def test_a_session_name_fails_and_a_stable_handle_passes():
    pages = {"data/x/README.md": "as policyforge-f8 read them; found by policyforge-80"}
    assert _stray_session_names(pages) == ["data/x/README.md: policyforge-f8"]
