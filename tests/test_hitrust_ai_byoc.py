"""The HITRUST AI BYOC directory, and the claims its README makes.

**A README-only catalog is the whole deliverable**, so the README is the
thing to test. It makes three claims that can go stale without anyone
noticing: that the directory ships no manifest, that the two spellings key
away from the CSF, and that `etl-hitrust` must not be pointed at an AI
export.

The last is a claim about another command's behaviour, which is the class
that has been wrong twice on this project — a README saying what a command
does, with nothing checking it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.content.tags import source_tags
from policyforge.mapping.crosswalk import normalize_framework

CATALOG = Path(__file__).resolve().parent.parent / "data" / "frameworks" / "hitrust-ai"


@pytest.fixture(scope="module")
def readme() -> str:
    """Whitespace collapsed, so a reflow that changes nothing does not
    break an assertion about what the README says."""
    return " ".join((CATALOG / "README.md").read_text(encoding="utf-8").split())


# --------------------------------------------------------------------------
# It must stay README-only
# --------------------------------------------------------------------------


def test_the_directory_ships_no_manifest_and_no_controls():
    """A `framework.yaml` here would make the licence check report a
    licensed catalog committed to a public repository — which is the thing
    that check exists to catch, so the catalog that triggers it must not
    be one we shipped."""
    assert (CATALOG / "README.md").is_file()
    assert not (CATALOG / "framework.yaml").exists()
    assert not (CATALOG / "controls.json").exists()
    assert sorted(p.name for p in CATALOG.iterdir()) == ["README.md"]


def test_it_is_registered_as_bring_your_own_rather_than_bundled():
    from policyforge.scaffold import BUNDLED_CATALOGS, BYOC_CATALOGS

    assert "hitrust-ai" in BYOC_CATALOGS
    assert "hitrust-ai" not in BUNDLED_CATALOGS


# --------------------------------------------------------------------------
# The key separation the README promises
# --------------------------------------------------------------------------


@pytest.mark.parametrize("written", ["HITRUST AI Security Certification", "HITRUST AI"])
def test_both_spellings_the_readme_offers_key_away_from_the_csf(written: str):
    """The README tells a user to declare one of two names. Both must key
    to `hitrust-ai`, or the advice sends them into the collision."""
    assert normalize_framework(written) == "hitrust-ai"
    assert normalize_framework(written) != normalize_framework("HITRUST CSF")


@pytest.mark.parametrize("written", ["HITRUST AI Security Certification", "HITRUST AI"])
def test_both_spellings_are_citable(written: str):
    """A name beginning with a digit is not a source tag at all, which is
    what made two CFR catalogs impossible to cite. Neither of these has
    that shape, and the README says a citation will resolve."""
    tag = f"[{written} 01.a]"

    assert source_tags(tag) == [tag]


# --------------------------------------------------------------------------
# The claim about another command
# --------------------------------------------------------------------------


def test_etl_hitrust_really_does_stamp_one_framework_name_on_anything():
    """**The README's warning, held to the command it warns about.**

    `etl-hitrust` hardcodes its framework name, so an AI export parsed
    through it arrives labelled as CSF content — and a CSF export and an
    AI export come out under one name, pooling their requirement ids.

    If a `--framework` option is ever added, this fails and the warning
    has to be revisited rather than left telling users to avoid something
    that now works.
    """
    from policyforge.cli import cli
    from policyforge.ingest.levelled import FRAMEWORK

    assert FRAMEWORK == "HITRUST-CSF"
    options = {p.name for p in cli.commands["etl-hitrust"].params}
    assert "framework" not in options, "etl-hitrust can now be told what it is parsing"


def test_generate_parser_still_cannot_draft_one_for_hitrust_ai():
    """The README says the other route is closed too."""
    from policyforge.cli import cli

    param = next(p for p in cli.commands["generate-parser"].params if p.name == "framework")

    assert set(param.type.choices) == {"hitrust", "govramp"}


def test_the_readme_warns_against_the_command_that_would_mislabel_it(readme):
    assert "Do not run `etl-hitrust` on an AI export" in readme
    assert "HITRUST-CSF" in readme, "it must name what would be stamped"


def test_the_readme_says_it_is_an_add_on(readme):
    """An organization holding AI certification holds the CSF too, so the
    two catalogs meet in the normal case — which is why the key
    separation is a real problem rather than an anticipated one."""
    assert "add-on" in readme
    assert "cannot be held on its own" in readme
