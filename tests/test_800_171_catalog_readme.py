"""The 800-171 catalog's README, held to the catalog it describes.

Written with the catalog rather than after someone blocked a PR for it.
The HIPAA version of this test was written two hours after a change
falsified five statements in a file that ships in the wheel; the Part 2
version was written two hours before that and not carried across. A guard
scoped to the catalog you happen to be thinking about is the recurring
shape, so this one is written at the same time as the thing it guards.

Numbers derive from `controls.json`, so these fail when the README goes
stale and not when NIST publishes a new revision.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

CATALOG = Path(__file__).parent.parent / "data" / "frameworks" / "nist-800-171-r3"


@pytest.fixture(scope="module")
def controls() -> list[dict]:
    return json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def readme() -> str:
    """The README with whitespace collapsed.

    `mdformat` reflows prose, so a sentence this file matches on can be
    split across lines by a formatting pass that changed nothing else. The
    first version of these tests broke exactly that way. Matching on
    collapsed text asserts what the README *says* rather than how it
    happens to be wrapped.
    """
    return " ".join((CATALOG / "README.md").read_text(encoding="utf-8").split())


def test_the_requirement_and_family_counts_match_the_catalog(controls, readme):
    """`97 requirements across 17 families`."""
    families = {c["family_abbr"] for c in controls}
    stated = re.search(r"(\d+) requirements across (\d+) families", readme)

    assert stated, "the README no longer states its counts"
    assert int(stated.group(1)) == len(controls)
    assert int(stated.group(2)) == len(families)


def test_the_withdrawn_arithmetic_closes(readme):
    """`130 in the publication, 97 live, 33 withdrawn` — stated as a sum
    so a reader can check it rather than take three numbers on trust."""
    stated = re.search(r"(\d+) in the publication, (\d+) live, (\d+) withdrawn", readme)

    assert stated, "the README no longer states the withdrawn arithmetic"
    total, live, withdrawn = (int(g) for g in stated.groups())
    assert live + withdrawn == total


def test_the_live_count_in_the_arithmetic_is_the_catalog(controls, readme):
    """Holds the two claims against each other, so a README that is
    internally consistent and wrong about the artefact still fails."""
    stated = re.search(r"\d+ in the publication, (\d+) live, \d+ withdrawn", readme)

    assert stated
    assert int(stated.group(1)) == len(controls)


def test_the_readme_states_the_measured_label_finding(readme):
    """The strip is only safe because it was measured lossless. If the
    README stops carrying the figure, the next person has the conclusion
    without the evidence."""
    stated = re.search(r"(\d+) part labels across every live requirement, (\d+) matching", readme)

    assert stated, "the README no longer carries the label measurement"
    assert stated.group(1) == stated.group(2), "the measurement only supports the strip if all fit"


def test_the_readme_says_loading_both_is_what_makes_a_bare_nist_ambiguous(readme):
    """**The consequence of loading both catalogs — not of upgrading.**

    Measured, not assumed:

        one NIST catalog loaded   'NIST' -> 'nist-800-53'
        both loaded               'NIST' -> ''            unresolved
        'NIST 800-53'             resolves either way

    The first draft of this said *bundling* a second catalog broke it, and
    that was wrong in the direction that costs a reader work they do not
    owe. **Bundling is not loading**: `satisfies`, `coverage`, `ssp` and
    the rest take `--controls` as a required option, and `map` defaults to
    800-53 alone. A catalog in `data/frameworks/` is not consulted until
    someone names it, so nothing breaks on upgrade.
    """
    assert "[NIST AC-2]" in readme
    assert "[NIST 800-53 AC-2]" in readme, "it must say what to write instead"
    assert "Bundling is not loading" in readme, (
        "the README must not let a reader think the upgrade itself broke their documents"
    )


def test_the_readme_leads_with_the_revision_a_cmmc_reader_needs(readme):
    """A buyer adopting 800-171 is usually doing it because of CMMC, and
    should meet the revision difference here rather than in an
    assessment. So it is above "What was parsed", not in a footnote."""
    raw = (CATALOG / "README.md").read_text(encoding="utf-8")
    cmmc = raw.index("CMMC")
    parsed = raw.index("## What was parsed")

    assert cmmc < parsed, "the CMMC revision warning must come before the contents"
    assert "3.1.1" in raw and "03.01.01" in raw, "both identifier shapes must appear"


def test_the_empty_baseline_is_explained_rather_than_left_blank(controls, readme):
    """A measured zero that would otherwise read as a parse failure."""
    assert all(not (c.get("baseline") or "").strip() for c in controls)
    assert "no baselines" in readme.lower()


def test_the_readme_names_the_framework_key_the_catalog_uses(controls, readme):
    assert {c["framework"] for c in controls} == {"NIST 800-171"}
    assert "nist-800-171" in readme


def test_no_command_loads_two_nist_catalogs_without_being_told_to():
    """**The claim the README makes about behaviour, held to the CLI.**

    "Bundling is not loading" is a statement about what commands do, and a
    statement about observable behaviour with nothing checking it is how
    the first draft of this README came to be wrong. If a command ever
    gains a default that loads every bundled catalog, the README's
    reassurance becomes false and this fails.

    `map` is the one command with a catalog default, and it is 800-53 by
    itself.
    """
    from policyforge.cli import cli

    defaults_loading_catalogs = {}
    for name, command in cli.commands.items():
        for param in command.params:
            if param.name not in ("controls_paths", "controls_path"):
                continue
            if param.required:
                continue
            default = param.default
            paths = default if isinstance(default, (list, tuple)) else [default]
            nist = [str(p) for p in paths if p and "nist-800-" in str(p)]
            if len(nist) > 1:
                defaults_loading_catalogs[name] = nist

    assert defaults_loading_catalogs == {}, (
        f"these commands load more than one NIST catalog by default: "
        f"{defaults_loading_catalogs}. The catalog README tells users nothing "
        f"breaks on upgrade because bundling is not loading; that is now false."
    )


def test_citation_resolution_requires_the_user_to_name_the_catalogs():
    """The other half: the commands that resolve citations make it the
    user's choice, so two NIST catalogs only meet each other deliberately."""
    from policyforge.cli import cli

    for name in ("satisfies", "coverage", "ssp", "addresses"):
        param = next(p for p in cli.commands[name].params if p.name == "controls_paths")
        assert param.required, f"{name} no longer requires --controls"


def test_addresses_answers_are_unchanged_by_installing_this_catalog():
    """**The claim the README makes, measured rather than reasoned.**

    Three people reasoned about this paragraph and all three were wrong
    in a different way: the first draft said upgrading broke documents
    (it does not), the correction said the shell resolves no citations so
    nothing changes there (true, and incomplete), and the hold said the
    shell's citations go ambiguous (it never calls that resolver).

    What settles it is running the shell's own view with and without the
    catalog. `/addresses` answers for existing citations must not move.
    """
    from policyforge.ingest.schema import load_controls
    from policyforge.mapping.crosswalk import NIST_ANCHOR, build_crosswalk, normalize_framework
    from policyforge.topics.bundles import requirement_view
    from policyforge.topics.registry import load_topics

    root = Path(__file__).parent.parent
    topics = load_topics(root / "config" / "topics.example.yaml")

    def load(names):
        controls = []
        for name in names:
            controls.extend(load_controls(root / "data" / "frameworks" / name / "controls.json"))
        return controls

    without = load(["nist-800-53-r5", "hipaa-security-rule", "arc-ampe"])
    with_171 = without + load(["nist-800-171-r3"])

    def view(controls, requirement):
        anchored = [c for c in controls if normalize_framework(c.framework) == NIST_ANCHOR]
        other = [c for c in controls if normalize_framework(c.framework) != NIST_ANCHOR]
        return requirement_view(
            topics,
            anchored,
            requirement,
            other_controls=other,
            crosswalk=build_crosswalk(controls),
        ).render()

    for citation in ("AC-2", "164.308(a)(1)(i)"):
        assert view(without, citation) == view(with_171, citation), (
            f"installing 800-171 changed the answer for {citation}. The catalog "
            f"README tells users existing citations are unaffected; that is now false."
        )


def test_the_readme_explains_that_the_coverage_move_is_gone(readme):
    """The README's job here changed with the behaviour.

    It used to reassure a reader that a falling percentage did not mean
    the programme got worse — 814 owned on both sides. `_coverage` now
    scopes to the 800-53 set the registry anchors to, so installing a
    catalog does not move the numbers at all and there is nothing to
    reassure anyone about.

    What it must do instead is say what a zero in the crosswalk section
    means, because `0 of 97` reads as ninety-seven unaddressed
    requirements and is nothing of the kind.

    **This test used to require the false explanation.** It asserted
    "nobody has published" was present, while NIST's rev 3 OSCAL -- the
    file this catalog is built from -- links all 97 requirements to 800-53
    (157 links; found by policyforge-f8, handle 5b, re-measured by 80,
    #260). So the test pinned the sentence that stopped anyone looking.
    It then required the true cause of the zero, "a gap in PolicyForge".

    **#259 closed that gap**: the catalog now carries NIST's own 157 links,
    so the README states what the catalog carries, and its counts are read
    from the shipped files rather than retyped (the shell's figure is held
    to the README's by the test below).
    """
    import json

    root = Path(__file__).resolve().parent.parent / "data" / "frameworks"
    rows = json.loads((root / "nist-800-171-r3" / "controls.json").read_text(encoding="utf-8"))
    nist = json.loads((root / "nist-800-53-r5" / "controls.json").read_text(encoding="utf-8"))
    control_ids = {c["control_id"] for c in nist}
    enhancement_ids = {e["enhancement_id"] for c in nist for e in c["enhancements"]}
    links = [
        i.strip()
        for r in rows
        for i in (r["source_crosswalk"].get("nist-800-53") or "").split(",")
        if i.strip()
    ]
    carried = sum(1 for r in rows if r["source_crosswalk"])
    to_controls = sum(1 for i in links if i in control_ids)
    to_enhancements = sum(1 for i in links if i in enhancement_ids)
    assert (carried, len(links), to_controls, to_enhancements) == (97, 157, 114, 43)

    flat = " ".join(readme.split())  # the README wraps lines anywhere
    assert f"{carried} of {len(rows)} requirements, {len(links)} links" in flat
    assert f"{to_controls} to controls and {to_enhancements} to enhancements" in flat
    assert "gap in PolicyForge, not in the source" not in readme, "#259 closed that gap"
    assert "does not ingest them yet" not in readme, "#259 ingests them"
    assert "nobody has published" not in readme, "NIST published it (#260)"
    assert "no authority publishes" not in readme, "NIST published it (#260)"
    assert "relative to everything on disk" not in readme, "that is no longer true"


def _shell_state(paths):
    """The shell's own state object, so a skill is exercised through the
    handler rather than through a reconstruction of it.

    **This is the point of these tests.** Four successive claims about
    what the shell does were made by running the right function with
    arguments the shell does not pass -- `resolve_framework` against the
    shell's catalogs when the shell never calls it, then
    `analyze_coverage` with `other_controls` and `crosswalk` when
    `_coverage` supplies neither. Both produced well-formed answers to a
    neighbouring question.
    """
    from types import SimpleNamespace

    from policyforge.topics.registry import load_topics
    from policyforge.zardoz.corpus import DEFAULT_CONTENT_DIR

    root = Path(__file__).parent.parent
    return SimpleNamespace(
        controls_paths=[str(p) for p in paths],
        topics=load_topics(root / "config" / "topics.example.yaml"),
        config={},
        content_dir=DEFAULT_CONTENT_DIR,
        parameters_path=root / "config" / "parameters.yaml",
    )


def _catalog_paths(*, including_800_171: bool):
    root = Path(__file__).parent.parent / "data" / "frameworks"
    names = [
        "nist-800-53-r5",
        "hipaa-security-rule",
        "arc-ampe",
        "fedramp",
        "cfr-171-information-blocking",
        "cfr-42-part-2-sud-records",
    ]
    if including_800_171:
        names.append("nist-800-171-r3")
    return [root / name / "controls.json" for name in names]


def test_the_shell_coverage_report_names_800_171_as_crosswalk_reachable():
    """**This test asserted the defect, and it was right to at the time.**
    It pinned that `_coverage` renders no per-framework section — true,
    because it passed neither `other_controls` nor `crosswalk`, so every
    non-NIST requirement was an orphan by construction.

    e1 predicted the section would read `0 of 97`, measured by calling
    `analyze_coverage` the way `_addresses` already does. That was the
    fix, a day before anyone proposed it; the only wrong word was tense.

    **Since #259 the zero is gone**: the catalog carries NIST's 157 links,
    so requirements reach the 800-53 controls the example registry owns.
    The figure is read from the shell and held to the one the README
    prints, so neither can move without the other.
    """
    from policyforge.zardoz.skills import _coverage

    output = _coverage(_shell_state(_catalog_paths(including_800_171=True)), [])

    assert "reachable via the crosswalk" in output
    reached = re.search(r"(\d+) of 97 requirements map to an owned NIST control", output)
    assert reached, output[-800:]
    assert int(reached.group(1)) > 0, "the pre-#259 zero is back"
    readme = (
        (Path(__file__).resolve().parent.parent / "data" / "frameworks" / "nist-800-171-r3")
        .joinpath("README.md")
        .read_text(encoding="utf-8")
    )
    assert f"{reached.group(1)} of 97 requirements map to an owned NIST control" in readme


def test_installing_the_catalog_does_not_reduce_what_the_shell_says_is_owned():
    """The reassurance the README gives, held to the handler. The scope
    and the orphan count grow; **the owned count must not move**, because
    "you got worse" is what a falling percentage reads as."""
    import re

    from policyforge.zardoz.skills import _coverage

    def owned(paths):
        output = _coverage(_shell_state(paths), [])
        found = re.search(r"Owned\s+(\d+)", output)
        assert found, "coverage report has no Owned line: " + output[:300]
        return int(found.group(1))

    assert owned(_catalog_paths(including_800_171=False)) == owned(
        _catalog_paths(including_800_171=True)
    )


def test_a_scoped_coverage_report_is_untouched_because_rev_3_has_no_baselines():
    """`/coverage moderate` filters all 97 out, so the surprise only
    reaches someone who runs it bare."""
    from policyforge.zardoz.skills import _coverage

    without = _coverage(_shell_state(_catalog_paths(including_800_171=False)), ["moderate"])
    with_171 = _coverage(_shell_state(_catalog_paths(including_800_171=True)), ["moderate"])

    assert without == with_171


def test_the_shell_scope_no_longer_grows_when_a_catalog_is_installed(readme):
    """**The inverse of what this asserted, and the change is the point.**

    It pinned that installing a catalog enlarges the coverage scope, which
    kept the README's paragraph about the bare percentage being relative
    to what is on disk true while it was. It is not any more: the scope is
    the 800-53-anchored set whatever else is installed, so the numbers
    hold still across machines and across an install.
    """
    import re

    from policyforge.zardoz.skills import _coverage

    def in_scope(paths):
        output = _coverage(_shell_state(paths), [])
        found = re.search(r"In scope\s+(\d+)", output)
        assert found, "coverage report has no In scope line: " + output[:300]
        return int(found.group(1))

    without = in_scope(_catalog_paths(including_800_171=False))
    with_171 = in_scope(_catalog_paths(including_800_171=True))

    assert with_171 == without, (
        "installing a catalog changed the coverage scope again, so the bare "
        "percentage is relative to what is on disk once more. The catalog "
        "README says it is not. Update the README."
    )
    assert "relative to everything on disk" not in readme
