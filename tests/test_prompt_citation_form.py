"""Generation prompts must teach a citation form that actually resolves.

Prompts teach by example, and the example was `[NIST IA-5 | GovRAMP IA-5]`.
After the NIST-family key split, `NIST` alone keys to the shared `nist`
bucket: it resolves while one NIST catalog is loaded and becomes a reported
unknown the moment a second joins. `NIST 800-53` keys to `nist-800-53` and
resolves either way.

So the example a model copies decides whether the documents it writes keep
resolving when a user brings 800-171 or the CSF. Nothing else checks this:
a prompt is a string, every test passes with any string in it, and the
failure appears later as unresolved citations in generated documents.

The split that matters is narrower than it looks, and these tests pin that
too — `HIPAA` and `HIPAA Security Rule` both key to `hipaa`, as do
`GovRAMP` and `GovRAMP Moderate`. NIST is the only family where qualifying
the name changes what it resolves to, so it is the only part of the example
that had to move.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from policyforge.mapping.crosswalk import normalize_framework

SRC = Path(__file__).resolve().parent.parent / "src" / "policyforge"

#: Prompts that tell a model to WRITE a tag. Their examples are copied.
GENERATES = [
    SRC / "synthesis" / "merge.py",
    SRC / "generate" / "policy_writer.py",
]

#: Prompts that tell a model to PRESERVE a tag "exactly as written". They
#: run on existing documents, including ones written before the key split,
#: so their examples stay short-form deliberately: a qualified example here
#: would invite rewriting a legacy tag, which is what they exist to prevent.
PRESERVES = [
    SRC / "edit" / "apply.py",
    SRC / "edit" / "plan.py",
]

#: `[NIST` not followed by a catalog-qualifying token.
# "AI RMF" is a qualified catalog name, not a bare family: `NIST AI RMF` and
# `NIST AI RMF Playbook` each key to their own catalog. Measured with
# `normalize_framework` on #300, which also showed that "NIST AI" alone keys
# to the shared `nist` bucket, so the exemption is "AI RMF", not "AI".
BARE_NIST = re.compile(r"\[NIST\s+(?![0-9]|[Cc]ybersecurity|CSF|AI RMF\b)")


def test_qualifying_nist_changes_what_it_resolves_to() -> None:
    """The premise. If this fails, the rest of the file is arguing nothing."""
    assert normalize_framework("NIST") == "nist"
    assert normalize_framework("NIST 800-53") == "nist-800-53"
    assert normalize_framework("NIST 800-171") == "nist-800-171"


def test_qualifying_hipaa_and_govramp_changes_nothing() -> None:
    """Why only the NIST half of the example moved."""
    assert normalize_framework("HIPAA") == normalize_framework("HIPAA Security Rule")
    assert normalize_framework("GovRAMP") == normalize_framework("GovRAMP Moderate")


@pytest.mark.parametrize("path", GENERATES, ids=lambda p: p.name)
def test_a_prompt_that_writes_tags_teaches_the_qualified_form(path: Path) -> None:
    bare = BARE_NIST.findall(path.read_text(encoding="utf-8"))
    assert not bare, (
        f"{path.name} shows `[NIST ...` without naming which NIST catalog. A model "
        "copying it writes citations that stop resolving as soon as a second "
        "NIST-family catalog is loaded. Use `[NIST 800-53 AC-2 | ...]`."
    )


@pytest.mark.parametrize("path", GENERATES, ids=lambda p: p.name)
def test_every_framework_named_in_a_generation_example_resolves(path: Path) -> None:
    """Not merely qualified — qualified to something the resolver knows.

    `[NIST 800-99 AC-2]` would pass the regex above and key to a catalog
    that does not exist.
    """
    text = path.read_text(encoding="utf-8")
    for tag in re.findall(r"\[([A-Z][^\]\n]{3,80})\]", text):
        for segment in tag.split("|"):
            name = re.split(r"\s+(?=[A-Z]{2}-\d|\d{3}\.)", segment.strip())[0]
            key = normalize_framework(name)
            assert key and key != "nist", (
                f"{path.name}: example segment {segment.strip()!r} keys to {key!r}. "
                "A bare `nist` key is the shared bucket the split removed."
            )


@pytest.mark.parametrize("path", PRESERVES, ids=lambda p: p.name)
def test_a_prompt_that_preserves_tags_stays_format_agnostic(path: Path) -> None:
    """The inverse, pinned so a later tidy-up does not "fix" these too.

    These instruct "preserve ... exactly as written" and "never remove or
    alter one". They are the only prompts that read documents written
    before the split, and a qualified example would read as licence to
    rewrite a tag rather than keep it.
    """
    text = path.read_text(encoding="utf-8")
    assert "[NIST AC-2" in text, (
        f"{path.name} no longer shows the short form. These prompts run on legacy "
        "documents and must not suggest rewriting a tag into the qualified form; "
        "see the generation prompts for where the qualified example belongs."
    )
