"""`/coverage` scopes to the set the topic registry anchors to.

Handing every loaded catalog to `analyze_coverage`'s `nist_controls`
made a HIPAA or CFR requirement an orphan by construction — no topic
anchors to its identifiers — so the denominator grew with every install
while the numerator never moved. `_addresses` in the same module already
did this correctly; these hold `_coverage` to its neighbour.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ---- the coverage skill's zero rows -----------------------------------------


def _framework_argument(line: str) -> str:
    """The value of `--framework` in a printed remedy, and only that.

    **Parsed rather than split to end-of-line.** The first version read
    `line.split("--framework", 1)[1]`, which was exact while the flag was
    last on the line. When `--controls` was appended the same expression
    kept returning a string and started returning the wrong one — the
    name plus the rest of the command — so `_refusal_reason` looked up
    something that matches nothing and returned `None`, and the assertion
    below passed for a reason unrelated to what it tests.

    A test does not have to fail to stop working.
    """
    import shlex

    command = line.split("`")[1] if "`" in line else line
    tokens = shlex.split(command)
    return tokens[tokens.index("--framework") + 1]


def _coverage_state():
    from pathlib import Path
    from types import SimpleNamespace

    from policyforge.topics.registry import load_topics

    root = Path(__file__).resolve().parent.parent
    return SimpleNamespace(
        topics=load_topics(root / "config" / "topics.example.yaml"),
        controls_paths=[],
        config={},
        content_dir=None,
    )


def test_a_zero_row_never_suggests_a_command_that_would_be_refused():
    """**The third instance of a pattern this project owns: the product
    instructing an action it refuses.**

    `overlay.py` told a reader to cite a catalog no tag could name. The
    same module's comment records renaming re-opening that class once
    already. Here the else-branch tells a reader to run `crosswalk seed`
    — which `seed_overlay` refuses with `NotAnchorableError` for exactly
    the catalog the if-branch is supposed to catch.

    `_refusal_reason` returning `None` is not a neutral default. It is a
    positive claim — *nobody has mapped this yet* — so a function whose
    None-branch asserts something is one rename away from asserting it
    falsely, and this is its second consumer. Found by b5.
    """
    from policyforge.crosswalk.overlay import _refusal_reason
    from policyforge.zardoz.skills import _coverage

    output = _coverage(_coverage_state(), [])
    seed_lines = [ln for ln in output.splitlines() if "crosswalk seed --framework" in ln]

    # Guard the population, or the loop below asserts nothing on an empty
    # list and this reports green. 80 caught that; it is entry 1 of the
    # checks-that-cannot-fail catalogue, in a test written about that
    # class. The test one down already guards its own rows this way.
    assert seed_lines, (
        "no zero row suggests `crosswalk seed`, so this test checked nothing. "
        "If every framework is now mapped that is good news and this guard "
        "needs rewriting rather than deleting."
    )

    for line in seed_lines:
        named = _framework_argument(line)
        assert _refusal_reason(named) is None, (
            f"the report tells a reader to seed {named!r}, which seed_overlay "
            f"refuses with NotAnchorableError. That is the product instructing "
            f"an action it declines to perform."
        )


def test_the_refused_catalog_gets_the_design_note_not_the_seed_suggestion():
    """The if-branch, so the pair is covered rather than just the else."""
    from policyforge.zardoz.skills import _coverage

    output = _coverage(_coverage_state(), [])
    row = [ln for ln in output.splitlines() if "CFR-171-INFORMATION-BLOCKING:" in ln]

    assert row, "the refused catalog has no zero-row note at all"
    assert "not mapped by design" in row[0]
    assert "crosswalk seed" not in row[0]


def test_a_reason_sentence_survives_the_house_citation_spelling():
    """Neither split works; the shared boundary rule does.

    b5 found `split(".")` cutting `45 C.F.R.` to `45 C` and proposed
    `split(". ")`. That is still wrong — `C.F.R. 171` carries a
    stop-space of its own — so the fix is the regex `entail/base.py`
    already has rather than a second attempt at splitting.
    """
    from policyforge.zardoz.skills import _first_sentence

    sentence = "A practice under 45 C.F.R. 171.203(a) qualifies. Second sentence."

    assert sentence.split(".")[0] == "A practice under 45 C"
    assert sentence.split(". ")[0] == "A practice under 45 C.F.R"
    assert _first_sentence(sentence) == "A practice under 45 C.F.R. 171.203(a) qualifies."


def test_the_shell_shares_the_one_sentence_boundary_rule():
    """A second copy drifts the day someone teaches one of them an
    abbreviation. Same argument as every reader sharing SOURCE_TAG_RE."""
    import inspect

    from policyforge.zardoz.skills import _first_sentence

    assert "_BOUNDARY_RE" in inspect.getsource(_first_sentence)


# ---- the remedy has to be performable, not merely well-formed ---------------


def test_the_suggested_command_names_the_catalogs_it_needs():
    """**80's finding: the printed remedy exited 1 when run.**

    `/coverage` answers from `discover()`, which finds every catalog on
    disk. The `crosswalk` CLI's own default context holds far fewer. So
    `crosswalk seed --framework 'NIST 800-171'` — copied exactly as the
    report printed it — failed with *"No catalog here declares the
    framework"*, while the same line run inside the shell worked.

    80 got exit 1, 9b got a clean run, from the same text. **Neither run
    was wrong; the disagreement between them was the finding.** A command
    that works only in the context that printed it is a remedy in the same
    sense that a check which cannot fail is a check.
    """
    import shlex
    from pathlib import Path

    from policyforge.zardoz.skills import _coverage

    root = Path(__file__).resolve().parent.parent
    output = _coverage(_coverage_state(), [])
    seed_lines = [ln for ln in output.splitlines() if "crosswalk seed --framework" in ln]
    assert seed_lines, "no zero row suggests `crosswalk seed`, so this test checked nothing."

    for line in seed_lines:
        tokens = shlex.split(line.split("`")[1])
        controls = [tokens[i + 1] for i, t in enumerate(tokens) if t == "--controls"]
        assert controls, (
            f"the remedy names no catalogs, so it is not runnable outside the "
            f"shell that printed it: {line.strip()}"
        )
        for path in controls:
            assert (root / path).is_file(), (
                f"the remedy points at {path!r}, which does not exist. A "
                f"suggestion naming a missing file is worse than one naming none."
            )


def test_the_suggested_paths_survive_a_posix_shell():
    r"""Forward slashes, because a backslash is an escape character.

    **This is the same defect as the test above, one level down, and it
    was in the fix for it.** `discover()` returns `data\frameworks\...`
    on Windows; pasted into bash the backslashes are eaten and the path
    arrives as `dataframeworks...`, exit 2. The first fix made the command
    context-independent and left it platform-dependent.

    Caught by running the printed text through a shell rather than reading
    it — **a remedy inherits the credibility of the finding that prompted
    it**, and gets looked at less hard for exactly that reason.
    """
    import shlex

    from policyforge.zardoz.skills import _coverage

    output = _coverage(_coverage_state(), [])
    for line in output.splitlines():
        if "crosswalk seed --framework" not in line:
            continue
        tokens = shlex.split(line.split("`")[1])
        for path in [tokens[i + 1] for i, t in enumerate(tokens) if t == "--controls"]:
            assert "\\" not in path, (
                f"the remedy contains a backslash path ({path!r}), which a POSIX "
                f"shell eats. Emit `Path(...).as_posix()`."
            )


def test_an_outcome_framework_is_refused_rather_than_suggested():
    """The AI RMF arrived documented as un-crosswalkable in four places —
    README, module docstring, command help, changelog — and `/coverage`
    still told the reader to run `crosswalk seed` against it.

    **Every piece of prose was right and the one line a user actually
    reads was wrong.** The `reason is None` branch does not omit a note,
    it prints the *other* one, so the row stayed well-formed and
    confidently said the opposite of the documentation. Found by running
    `/coverage` with the new catalog installed, not by reading the code.
    **The premise moved once and the test moved with it, deliberately.**
    The first version asserted the AI RMF appears as a crosswalk zero row
    carrying "not mapped by design". It stopped being true the moment the
    catalog became *anchorable*: an anchorable catalog is counted in the
    scope, not in the "reachable via the crosswalk" section, so it left
    that section entirely. The two mechanisms are mutually exclusive.

    The invariant underneath is unchanged and is what is asserted now:
    **whatever section it lands in, nothing ever tells the reader to seed
    a crosswalk for it.** Asserted over the whole report rather than one
    row, because the row is exactly the thing that moved.
    """
    from policyforge.mapping.crosswalk import anchors_a_topic
    from policyforge.zardoz.skills import _coverage

    output = _coverage(_coverage_state(), [])

    seed_lines = [ln for ln in output.splitlines() if "crosswalk seed" in ln]
    for line in seed_lines:
        assert "AI RMF" not in line, (
            "the report tells a reader to seed the AI RMF, which asserts that "
            "an 800-53 control ACHIEVES an AI RMF outcome — the one claim NIST "
            "declined to make when it split the Playbook out."
        )

    # And it is in the report at all, so the assertion above is not
    # satisfied by the catalog having quietly vanished from it.
    assert anchors_a_topic("NIST AI RMF"), (
        "the AI RMF is no longer anchorable, so this test is checking a "
        "configuration that no longer exists — rewrite it rather than delete it."
    )
    assert "NIST AI RMF" in output, (
        "the AI RMF does not appear in the coverage report at all. A catalog "
        "that is anchorable but invisible is worse than one that is neither."
    )


# ---- the shell and the CLI must answer one question one way ----------------


def test_the_shell_passes_the_relationships_the_cli_passes():
    """**Two views of one registry disagreed about the organisation's own
    reviewed decision.**

    `cli/programme.py` passes `relationships=accepted_relationships(...)`;
    the shell passed nothing. Every lookup then returned `None`, `None` is
    not in `PARTIAL_RELATIONSHIPS`, and a mapping recorded as `superset`
    or `intersects` counted as **full** coverage in the shell and
    **partial** in the CLI.

    Measured before the fix, with every HIPAA mapping recorded as
    `superset`: the shell said 65 of 74 covered, the CLI said 0.

    Asserted on the call rather than on a number, because the numbers move
    with the catalogs and the property does not: **whatever the CLI reads
    for this, the shell reads too.**
    """
    import inspect

    from policyforge.cli import programme
    from policyforge.zardoz import skills

    cli_source = inspect.getsource(programme)
    shell_source = inspect.getsource(skills._coverage)

    # Since #408 both read `relationships_for`, which layers a catalog's
    # declared relationship under the overlays' accepted rows; the property
    # is unchanged: one function, called the same way by both.
    call = "relationships=relationships_for("
    assert call in cli_source, "the CLI no longer passes relationships; this test's premise is gone"
    assert call in shell_source, (
        "the shell does not pass relationships, so a reviewed `superset` "
        "mapping counts as full coverage here and partial in the CLI"
    )


def test_a_partial_relationship_is_not_counted_as_full():
    """The behaviour underneath, so the check above is not the only guard.

    Built directly rather than through either caller: a crosswalk with one
    mapping, recorded as `superset`, must not report that requirement
    covered.
    """
    from policyforge.topics.coverage import PARTIAL_RELATIONSHIPS, analyze_coverage
    from policyforge.topics.registry import Topic

    controls = _nist_control("AC-2")
    other = _hipaa_control("164.308(a)(3)(i)")
    crosswalk = {"AC-2": {"hipaa": ["164.308(a)(3)(i)"]}}
    topics = [Topic(name="T", owner="O", nist_controls=["AC-2"])]

    assert "superset" in PARTIAL_RELATIONSHIPS

    full = analyze_coverage(
        topics, controls, other_controls=other, crosswalk=crosswalk, relationships={}
    )
    partial = analyze_coverage(
        topics,
        controls,
        other_controls=other,
        crosswalk=crosswalk,
        relationships={("hipaa", "164.308(a)(3)(i)", "AC-2"): "superset"},
    )

    covered = {f.framework: len(f.covered) for f in full.framework_coverage}
    with_rel = {f.framework: len(f.covered) for f in partial.framework_coverage}
    assert covered.get("hipaa") == 1, "the unqualified mapping should read as covered"
    assert with_rel.get("hipaa") == 0, (
        "a mapping the organisation reviewed as `superset` still reads as full coverage"
    )


def _nist_control(control_id):
    from policyforge.ingest.schema import Control

    return [
        Control(
            control_id=control_id, title="t", framework="NIST 800-53", framework_version="Rev 5"
        )
    ]


def _hipaa_control(control_id):
    from policyforge.ingest.schema import Control

    return [
        Control(
            control_id=control_id,
            title="t",
            framework="HIPAA Security Rule",
            framework_version="45 CFR 164",
        )
    ]


# ---- #260: the third kind of zero -------------------------------------------
#
# The report split every zero into two kinds -- refused by design, or nobody
# has published a mapping -- and told 800-171 users "no published crosswalk
# yet, `crosswalk seed` starts one". NIST publishes that mapping in the file
# the catalog is built from: all 97 requirements, 157 links. The zero is a gap
# in PolicyForge, not in the source.
#
# Each test asserts text ONLY the new branch writes. `_zero_row_reasons` has
# two other branches that also name the framework, and one also suggests a
# command, so a test matching the framework name alone would pass whichever
# branch spoke.

_UPSTREAM_TEXT = "a gap in PolicyForge, not in the source"


def _row(output: str, heading: str) -> str:
    rows = [ln for ln in output.splitlines() if ln.strip().startswith(f"{heading}:")]
    assert len(rows) == 1, f"expected exactly one {heading} row, found {len(rows)}"
    return rows[0]


def test_800_171_carries_nists_mapping_so_its_zero_is_counted_and_never_seeded():
    """**The row #260 was about, after #259.** #260 made it say the gap was
    PolicyForge's; #259 closed the gap, so 800-171 now carries NIST's 157
    links. With the example registry it is not a zero row at all. Forced to
    zero -- every requirement uncovered -- it must be a crosswalk-carrying
    row: counted clauses, never the upstream text (the gap is closed) and
    never a `crosswalk seed` hint (NIST's mapping is the one to use)."""
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from policyforge.ingest.schema import load_controls
    from policyforge.zardoz.skills import _coverage, _zero_row_reasons

    output = _coverage(_coverage_state(), [])
    assert not [ln for ln in output.splitlines() if ln.strip().startswith("NIST-800-171:")], (
        "800-171 reads zero with the example registry, which owns what it maps to"
    )

    path = Path(__file__).resolve().parent.parent / "data/frameworks/nist-800-171-r3/controls.json"
    controls = load_controls(path)
    ids = [c.control_id for c in controls]
    assert len(ids) == len(json.loads(path.read_text(encoding="utf-8"))) == 97
    report = SimpleNamespace(
        framework_coverage=[
            SimpleNamespace(framework="nist-800-171", covered=0, partial=[], uncovered=ids)
        ]
    )
    row = _row("\n".join(_zero_row_reasons(controls, report)), "NIST-800-171")

    assert "97 map to 800-53 controls none of your topics owns" in row, row
    assert _UPSTREAM_TEXT not in row, "800-171 still says the gap is PolicyForge's"
    assert "no published crosswalk" not in row, "800-171 claims no mapping is published"
    assert "crosswalk seed" not in row, "800-171 tells the user to seed by hand"


def test_the_header_says_zeros_differ_and_counts_nothing():
    """The header was a two-way partition with no place for a zero the source
    has already answered (#260), then said "one nobody has published", which
    no search established (#264), then went from three kinds to five in one
    evening (#270). **A count in output is a claim that has to stay true**,
    so it names none and the rows carry the taxonomy (80, on #270)."""
    from policyforge.zardoz.skills import _coverage

    output = _coverage(_coverage_state(), [])
    header = output[output.index("Why those are zero") :].split("\n  CFR-")[0]

    assert "has one of several causes" in header, header
    assert "Each line below names its cause." in header, header
    assert not re.search(r"\b(two|three|four|five|six)\b", header), (
        f"the header counts the kinds again, and the count will go stale: {header!r}"
    )
    assert "nobody has published" not in header, "the header still claims a search nobody made"


def test_an_upstream_crosswalk_is_not_a_refusal():
    """**Never merge the two tables.** `NOT_CROSSWALK_ANCHORABLE` makes
    `seed_overlay` refuse and prints "not mapped by design"; both are false
    for a framework whose publisher mapped it. Seeding by hand is ADVISED
    AGAINST in the report -- a hand-made mapping competes with the source's --
    but not refused by the tool."""
    from policyforge.crosswalk.overlay import (
        NOT_CROSSWALK_ANCHORABLE,
        PUBLISHED_UPSTREAM,
        _canonical,
        _refusal_reason,
    )

    refused = {_canonical(name) for name in NOT_CROSSWALK_ANCHORABLE}
    for name in PUBLISHED_UPSTREAM:
        assert _canonical(name) not in refused, f"{name!r} is in both tables"
        assert _refusal_reason(name) is None, f"seeding {name!r} would be refused"


def test_every_upstream_crosswalk_names_a_shipped_catalog():
    """**A rename must not silently restore the false message.** The refusal
    table was re-opened once by exactly that: a catalog renamed, a key that
    stopped matching, and the old wording back with no test failing. Every
    key here has to match a framework some shipped catalog declares."""
    import json
    from pathlib import Path

    from policyforge.crosswalk.overlay import PUBLISHED_UPSTREAM, _canonical

    root = Path(__file__).resolve().parent.parent / "data" / "frameworks"
    declared = {
        _canonical(row["framework"])
        for path in root.glob("*/controls.json")
        for row in json.loads(path.read_text(encoding="utf-8"))
    }
    # Empty since #259 (see the table's comment), so this loop checks nothing
    # today; it binds the next entry. The branch itself is exercised below.
    for name in PUBLISHED_UPSTREAM:
        assert _canonical(name) in declared, (
            f"PUBLISHED_UPSTREAM names {name!r}, which no shipped catalog declares. "
            "If the catalog was renamed, re-key this entry; otherwise its row falls "
            "back to 'no published crosswalk yet', which is the false claim #260 removed."
        )


def test_800_171_is_no_longer_an_upstream_gap():
    """#259: its mapping is read now, so the table must not still claim it."""
    from policyforge.crosswalk.overlay import _upstream_reason

    assert _upstream_reason("NIST 800-171") is None


def test_the_upstream_branch_still_prints_the_gap_for_a_listed_framework(monkeypatch):
    """The table is empty since #259, but the kind of zero it names is 80's
    ruling on #260 and will recur. So the branch is exercised with an entry
    injected for a made-up framework: its row says the gap is PolicyForge's
    and offers no command."""
    from types import SimpleNamespace

    import policyforge.crosswalk.overlay as overlay
    from policyforge.ingest.schema import Control
    from policyforge.zardoz.skills import _zero_row_reasons

    name = "Example Published Framework"
    monkeypatch.setitem(
        overlay.PUBLISHED_UPSTREAM,
        name,
        "Its publisher maps it, and this release does not read that mapping. The zero is "
        "a gap in PolicyForge, not in the source.",
    )
    controls = [Control(control_id="EPF-1", title="t", framework=name, framework_version="1")]
    report = SimpleNamespace(
        framework_coverage=[SimpleNamespace(framework=name, covered=0, partial=[])]
    )
    row = _row("\n".join(_zero_row_reasons(controls, report)), name.upper())

    assert _UPSTREAM_TEXT in row, row
    assert "crosswalk seed" not in row, row


# ---- #264: "found" is reserved for a framework someone searched for --------
#
# The seed branch printed "no published crosswalk yet" for every catalog in
# no table. For Part 2 a search was made (policyforge-f8, records on #264
# and #279: NIST OLIR and CPRT enumerated on 2026-09-26, their datasets'
# elements not) and found nothing; for a BYOC catalog nobody searched at
# all. The two now print different claims, and each test asserts text only
# its own branch writes.

_SEARCHED_TEXT = "no published crosswalk found"
_CARRIES_TEXT = "this catalog carries no crosswalk"


def test_part_2_says_a_search_found_nothing_and_still_offers_a_seed():
    """Part 2 is the one shipped catalog that reaches the seed advice. It
    may say "found" because a search was made, and the seed pointer stays:
    with no mapping found, building one is the right advice (80, #264)."""
    from policyforge.zardoz.skills import _coverage

    row = _row(_coverage(_coverage_state(), []), "CFR-42-PART-2-SUD-RECORDS")

    assert _SEARCHED_TEXT in row, f"Part 2 row does not name the search result: {row!r}"
    assert "crosswalk seed --framework" in row, "Part 2 lost its seed pointer"
    assert "no published crosswalk yet" not in row, "the pre-#264 wording is back"
    assert "nobody has published" not in row, row
    assert _CARRIES_TEXT not in row, "Part 2 routed to the generic branch"


def test_a_catalog_in_no_table_claims_nothing_about_the_world():
    """**The branch #264 was really about.** Every BYOC catalog, and any
    shipped one added later without a crosswalk, lands here. Nobody searched
    for any of them, so the row may state what the catalog carries and
    nothing else -- no "found", no "published"."""
    from types import SimpleNamespace

    from policyforge.crosswalk.overlay import (
        _refusal_reason,
        _searched_none_found,
        _upstream_reason,
    )
    from policyforge.ingest.schema import Control
    from policyforge.zardoz.skills import _zero_row_reasons

    name = "Example Customer Framework"
    # The premise, asserted first: if a table ever claims this name, the
    # test would be exercising another branch and should say so by name.
    assert _refusal_reason(name) is None
    assert _upstream_reason(name) is None
    assert _searched_none_found(name) is None

    controls = [Control(control_id="ECF-1", title="t", framework=name, framework_version="1")]
    report = SimpleNamespace(
        framework_coverage=[SimpleNamespace(framework=name, covered=0, partial=[])]
    )
    row = _row("\n".join(_zero_row_reasons(controls, report)), name.upper())

    assert _CARRIES_TEXT in row, f"generic row does not state what the catalog carries: {row!r}"
    assert "crosswalk seed --framework" in row, "generic row lost its seed pointer"
    assert "found" not in row, f"generic row claims a search nobody made: {row!r}"
    assert "published" not in row, f"generic row claims a fact about the world: {row!r}"


def test_the_crosswalk_tables_are_disjoint():
    """A framework in two tables would print whichever branch is checked
    first, and the other table's claim would be dead text nobody reads."""
    from policyforge.crosswalk.overlay import (
        NOT_CROSSWALK_ANCHORABLE,
        PUBLISHED_UPSTREAM,
        SEARCHED_NONE_FOUND,
        _canonical,
    )

    tables = {
        "NOT_CROSSWALK_ANCHORABLE": NOT_CROSSWALK_ANCHORABLE,
        "PUBLISHED_UPSTREAM": PUBLISHED_UPSTREAM,
        "SEARCHED_NONE_FOUND": SEARCHED_NONE_FOUND,
    }
    seen: dict[str, str] = {}
    for table, entries in tables.items():
        for name in entries:
            key = _canonical(name)
            assert key not in seen, f"{name!r} is in both {seen[key]} and {table}"
            seen[key] = table


def test_every_searched_framework_names_a_shipped_catalog():
    """**A rename must not silently move Part 2 to the generic branch.**
    That is the safe direction -- it claims less -- but it discards a search
    someone made, and the record beside it would describe nothing. Same
    guard as `PUBLISHED_UPSTREAM`'s."""
    import json
    from pathlib import Path

    from policyforge.crosswalk.overlay import SEARCHED_NONE_FOUND, _canonical

    root = Path(__file__).resolve().parent.parent / "data" / "frameworks"
    declared = {
        _canonical(row["framework"])
        for path in root.glob("*/controls.json")
        for row in json.loads(path.read_text(encoding="utf-8"))
    }
    assert SEARCHED_NONE_FOUND, "the table is empty, so the test below checks nothing"
    for name, record in SEARCHED_NONE_FOUND.items():
        assert _canonical(name) in declared, (
            f"SEARCHED_NONE_FOUND names {name!r}, which no shipped catalog declares. "
            "If the catalog was renamed, re-key this entry; otherwise its search "
            "record describes nothing and its row claims no search."
        )
        assert "not enumerated" in record, (
            f"{name!r}'s search record does not say what was NOT searched; a "
            "negative result without its limits is a shrug, not a finding."
        )


# ---- #270: a catalog that carries a crosswalk, read zero --------------------
#
# `covered` is the catalog joined to the user's topics and overlay, not a fact
# about the file. One sentence spoke for a mixed population three times on
# #270 -- every catalog with a crosswalk told to seed; a partial-only catalog
# told its owned controls were unowned; HIPAA's 9 unmapped requirements told
# their gap was in the topics -- so the row is one counted clause per kind of
# requirement (80's ruling), and the tests below CONSERVE: the clauses must
# sum to every requirement the catalog has, derived from the raw files.

_ID_RE = re.compile(r"\b[A-Z]{2}-\d+(?:\(\d+\))?")
_CLAUSE_RE = {
    "P": re.compile(r"(\d+) reach controls your topics own, but only in part"),
    "R": re.compile(r"(\d+) map to 800-53 controls none of your topics owns"),
    "N": re.compile(r"(\d+) carry no mapping in this catalog's crosswalk"),
}


def _shipped_rows():
    root = Path(__file__).resolve().parent.parent / "data" / "frameworks"
    for path in sorted(root.glob("*/controls.json")):
        yield from json.loads(path.read_text(encoding="utf-8"))


def _crosswalks(row) -> list[dict]:
    return [row.get("source_crosswalk") or {}] + [
        e.get("source_crosswalk") or {} for e in row.get("enhancements", [])
    ]


def _requirements(framework_key: str) -> dict[str, bool]:
    """`{requirement id: carries a mapping}` for one shipped framework, from
    the raw `controls.json` -- controls and enhancements, the ids coverage
    lists -- read by 800-53 id rather than by the loader's truthiness."""
    from policyforge.zardoz.skills import _framework_key

    found = {}
    for row in _shipped_rows():
        if _framework_key(row["framework"]) != framework_key:
            continue
        levels = [(row["control_id"], row.get("source_crosswalk") or {})] + [
            (e["enhancement_id"], e.get("source_crosswalk") or {})
            for e in row.get("enhancements", [])
        ]
        for requirement_id, crosswalk in levels:
            found[requirement_id] = bool(_ID_RE.findall(" ".join(crosswalk.values())))
    return found


def _clauses(row: str) -> dict[str, int]:
    """The counted clauses in a row; an absent clause is 0."""
    counts = {}
    for kind, pattern in _CLAUSE_RE.items():
        matches = pattern.findall(row)
        assert len(matches) <= 1, f"clause {kind} printed twice: {row!r}"
        counts[kind] = int(matches[0]) if matches else 0
    return counts


def _zero_rows(output: str) -> dict[str, str]:
    """`{row heading: row}` for every cause line under "Why those are zero"."""
    block = output[output.index("Why those are zero") :].splitlines()
    rows = {}
    for line in block[3:]:
        if not line.strip():
            break
        heading, sep, _ = line.strip().partition(": ")
        if sep and heading.upper() == heading:
            rows[heading] = line
    return rows


def _one_topic_state(anchor: str):
    import dataclasses
    from types import SimpleNamespace

    from policyforge.topics.registry import load_topics

    root = Path(__file__).resolve().parent.parent
    topic = dataclasses.replace(
        load_topics(root / "config" / "topics.example.yaml")[0], nist_controls=[anchor]
    )
    return SimpleNamespace(topics=[topic], controls_paths=[], config={}, content_dir=None)


def test_every_catalog_carrying_a_crosswalk_accounts_for_every_requirement():
    """**1d's first case, generalised, and the pure-R twin.** One topic,
    anchored on an 800-53 control no shipped crosswalk reaches, so nothing is
    covered or partial. For every catalog that carries a crosswalk -- derived
    from the raw files, none named -- the row must split its requirements
    into R (mapped, unowned) and N (no mapping), with R + N the catalog's
    whole count and N exactly the requirements the data leaves unmapped.
    FedRAMP (N = 0) prints R alone; HIPAA (N = 9) prints both."""
    from policyforge.zardoz.skills import _coverage, _framework_key

    reached: set[str] = set()
    carriers: set[str] = set()
    anchorable: set[str] = set()
    for row in _shipped_rows():
        crosswalks = _crosswalks(row)
        for crosswalk in crosswalks:
            for value in crosswalk.values():
                reached |= set(_ID_RE.findall(value))
        if any(crosswalks):
            carriers.add(_framework_key(row["framework"]))
        if _framework_key(row["framework"]) == _framework_key("NIST 800-53"):
            anchorable.add(row["control_id"])
    free = sorted(anchorable - reached)
    assert carriers, "no shipped catalog carries a crosswalk, so this checks nothing"
    assert free, "every 800-53 control is reached by some crosswalk; pick another anchor"

    rows = _zero_rows(_coverage(_one_topic_state(free[0]), []))
    seen = {heading: row for heading, row in rows.items() if _framework_key(heading) in carriers}
    assert seen, f"no crosswalk-carrying catalog reached a zero row: {sorted(rows)}"
    kinds_seen = set()
    for heading, row in seen.items():
        requirements = _requirements(_framework_key(heading))
        unmapped = sum(1 for has in requirements.values() if not has)
        counts = _clauses(row)
        assert counts["P"] + counts["R"] + counts["N"] == len(requirements), (
            f"{heading}: clauses {counts} do not sum to its {len(requirements)} requirements"
        )
        assert counts["P"] == 0, f"{heading}: partial with no overlay: {row!r}"
        assert counts["N"] == unmapped, f"{heading}: N={counts['N']}, data says {unmapped}"
        assert "crosswalk seed" not in row, f"{heading} is told to run seed: {row!r}"
        assert "carries no crosswalk" not in row, f"{heading} is told it carries none"
        assert "so do not seed them by hand" in row, f"{heading}: R lost its advice: {row!r}"
        kinds_seen.add("R+N" if counts["N"] else "R only")
    # Both shapes must be exercised, or the N clause could be dead code here.
    assert kinds_seen == {"R only", "R+N"}, f"only {kinds_seen} reached; the twins need both"
    for heading, row in rows.items():
        if heading not in seen:
            assert not any(_clauses(row).values()), f"{heading} has no crosswalk: {row!r}"


def test_a_crosswalk_carried_only_by_enhancements_still_counts():
    """NIST's HIPAA crosswalk maps implementation specifications separately
    from their Standards, so a catalog can carry its mapping only below the
    control. No shipped catalog is shaped that way, so a hand-built one: the
    enhancement is mapped (R), the control above it is not (N)."""
    from types import SimpleNamespace

    from policyforge.ingest.schema import Control, ControlEnhancement
    from policyforge.zardoz.skills import _zero_row_reasons

    name = "Example Enhancement Framework"
    control = Control(control_id="EEF-1", title="t", framework=name, framework_version="1")
    control.enhancements = [
        ControlEnhancement(
            enhancement_id="EEF-1(a)",
            title="t",
            baseline="",
            description="",
            source_crosswalk={"NIST 800-53": "AC-2"},
        )
    ]
    assert not control.source_crosswalk, "the premise: nothing at control level"
    coverage = SimpleNamespace(
        framework=name, covered=0, partial=[], uncovered=["EEF-1", "EEF-1(a)"]
    )
    report = SimpleNamespace(framework_coverage=[coverage])
    row = _row("\n".join(_zero_row_reasons([control], report)), name.upper())

    assert _clauses(row) == {"P": 0, "R": 1, "N": 1}, row
    assert "crosswalk seed" not in row, row


def _every_pair_recorded_superset(framework_key: str) -> dict[tuple[str, str, str], str]:
    """Relationships as an overlay would hold them if the organisation
    recorded every mapping of one framework as `superset` -- 1d's case on
    #270 -- built from the raw `controls.json`, keyed as
    `accepted_relationships` keys them. A real, non-empty dict: an empty
    stand-in is falsy, and `analyze_coverage` then reads no relationships at
    all, which is how this test's first draft passed through nothing."""
    from policyforge.zardoz.skills import _framework_key

    pairs = {}
    for row in _shipped_rows():
        if _framework_key(row["framework"]) != framework_key:
            continue
        levels = [(row["control_id"], row.get("source_crosswalk") or {})] + [
            (e["enhancement_id"], e.get("source_crosswalk") or {})
            for e in row.get("enhancements", [])
        ]
        for requirement_id, crosswalk in levels:
            for value in crosswalk.values():
                for nist_id in _ID_RE.findall(value):
                    pairs[(framework_key, requirement_id, nist_id)] = "superset"
    assert pairs, f"no {framework_key} mapping found to record; the test would check nothing"
    return pairs


def test_a_mixed_catalog_names_each_part_and_the_parts_sum(monkeypatch):
    """**1d's mixed case, through the real `_coverage`.** A topic owns one
    control HIPAA maps to, and every HIPAA mapping is recorded `superset`:
    some requirements are partial, most reach nothing owned, and 9 carry no
    mapping at all. The row must name all three with the counts the report
    and the data give, and they must sum to HIPAA's whole count."""
    import policyforge.crosswalk.overlay as overlay
    from policyforge.zardoz.skills import _coverage, _framework_key

    hipaa = _framework_key("HIPAA Security Rule")
    requirements = _requirements(hipaa)
    anchor = next(
        match
        for row in _shipped_rows()
        if _framework_key(row["framework"]) == hipaa
        for crosswalk in _crosswalks(row)
        for value in crosswalk.values()
        for match in _ID_RE.findall(value)
    )
    relationships = _every_pair_recorded_superset(hipaa)
    monkeypatch.setattr(overlay, "accepted_relationships", lambda _: relationships)

    output = _coverage(_one_topic_state(anchor), [])
    row = _zero_rows(output)["HIPAA"]
    counts = _clauses(row)
    partial_line = re.search(r"(\d+) more are reached only in part", output)
    assert partial_line, "the report lists no partial requirements for the row to point at"

    assert counts["P"] == int(partial_line.group(1)), (counts, partial_line.group(0))
    assert counts["N"] == sum(1 for has in requirements.values() if not has), counts
    assert counts["P"] and counts["R"] and counts["N"], f"not a mixed case: {counts}"
    assert sum(counts.values()) == len(requirements), (
        f"clauses {counts} do not sum to HIPAA's {len(requirements)} requirements"
    )
    assert "belongs to one of your topics" not in row, "the retired single sentence is back"
    assert "crosswalk seed" not in row, row
    # The P clause points at a section; that section must be in this output.
    assert "(listed as partial above)" in row
    above = output[: output.index("Why those are zero")]
    assert "HIPAA reachable via the crosswalk" in above, "the section the row points at is gone"


def test_a_purely_partial_catalog_prints_only_the_partial_clause():
    """The pure-partial twin: every requirement is partial, none uncovered.
    One clause, no R, no N, and nothing about seeding."""
    from types import SimpleNamespace

    from policyforge.ingest.schema import Control
    from policyforge.zardoz.skills import _zero_row_reasons

    name = "Example Partial Framework"
    controls = [
        Control(
            control_id=f"EPF-{n}",
            title="t",
            framework=name,
            framework_version="1",
            source_crosswalk={"NIST 800-53": "AC-2"},
        )
        for n in (1, 2, 3)
    ]
    coverage = SimpleNamespace(
        framework=name, covered=0, partial=["EPF-1", "EPF-2", "EPF-3"], uncovered=[]
    )
    row = _row(
        "\n".join(_zero_row_reasons(controls, SimpleNamespace(framework_coverage=[coverage]))),
        name.upper(),
    )

    assert _clauses(row) == {"P": 3, "R": 0, "N": 0}, row
    assert "seed" not in row, row


# ---- #282 + #278: coverage counts in the crosswalk's unit, one instrument ---


def _hitrust_01a():
    """A HITRUST control built by the importer itself: Level 1 maps to AC-2,
    Level 2 to AC-3 -- ba's reproduction on #282."""
    from policyforge.ingest import levelled

    base = {
        "category": "01.0 - Example Category",
        "objective": "01.01 Example Objective",
        "objective_statement": "To do the example thing.",
        "reference": "01.a Example Control",
        "specification": "The organization shall do the example thing.",
        "factor_type": "Organizational",
        "statement": "The example thing is documented.",
    }
    return levelled.build_controls(
        [
            levelled.Record(**base, level="Level 1", mapping="NIST SP 800-53 r5 AC-2"),
            levelled.Record(**base, level="Level 2", mapping="NIST SP 800-53 r5 AC-3"),
        ]
    )


def _run_coverage_cli(tmp_path, anchors: list[str], controls_json) -> str:
    import dataclasses

    from click.testing import CliRunner

    from policyforge import cli as cli_mod

    hitrust_file = tmp_path / "hitrust.json"
    hitrust_file.write_text(
        json.dumps([dataclasses.asdict(c) for c in controls_json]), encoding="utf-8"
    )
    topics = tmp_path / "topics.yaml"
    topics.write_text(
        "topics:\n  - name: access\n    owner: team-a\n"
        f"    nist_controls: [{', '.join(anchors)}]\n"
        "    cadence: annual\n    description: d\n",
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parent.parent
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "coverage",
            "--topics",
            str(topics),
            "--controls",
            str(root / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"),
            "--controls",
            str(hitrust_file),
        ],
    )
    assert result.exit_code in (0, 1), result.output
    return result.output


@pytest.mark.parametrize(
    ("anchors", "expected"), [(["AC-2", "AC-3"], "2 of 2"), (["AC-2"], "1 of 2")]
)
def test_hitrust_coverage_counts_level_requirements(tmp_path, anchors, expected):
    """**#282, through the real `policyforge coverage`.** HITRUST's crosswalk
    is recorded per level ("01.a Level 1"); coverage listed the control
    ("01.a"), so the two never met and HITRUST read 0 of 1 whatever the
    topics owned. Counted per level, all levels, and the report says so."""
    output = _run_coverage_cli(tmp_path, anchors, _hitrust_01a())
    block = output[output.index("HITRUST-CSF reachable via the crosswalk") :]

    assert f"  {expected} requirements map to an owned NIST control" in block, block
    assert "counted per level requirement (all levels in the catalog)" in block, block
    assert output.count("counted per level requirement") == 1, "the unit line printed elsewhere"


def test_no_catalog_counted_per_control_carries_the_level_unit_line():
    """**The quiet twin, b5's finding on #286.** `per_level=True` for every
    framework survived the whole suite and printed the HITRUST unit line
    under ARC-AMPE, FedRAMP, HIPAA and more on shipped data. No shipped
    catalog carries level requirements, so through the real `/coverage` the
    line must not appear at all -- while the rows it would sit under do."""
    from policyforge.zardoz.skills import _coverage

    output = _coverage(_coverage_state(), [])
    assert "reachable via the crosswalk" in output, "no framework rows, so this checks nothing"
    assert "counted per level requirement" not in output, (
        "a catalog counted per control says it is counted per level requirement"
    )


def test_a_mapping_carried_on_the_800_53_side_reaches_coverage():
    """**#278, 9b's path.** An 800-53 control naming another framework's id,
    under the name a person writes ("Example Framework"). `build_crosswalk`
    kept the raw name as the key, coverage looked it up normalised, and the
    mapping was in the crosswalk yet reached nothing."""
    import dataclasses
    from types import SimpleNamespace  # noqa: F401

    from policyforge.ingest.schema import Control
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.coverage import analyze_coverage
    from policyforge.topics.registry import load_topics

    root = Path(__file__).resolve().parent.parent
    topic = dataclasses.replace(
        load_topics(root / "config" / "topics.example.yaml")[0], nist_controls=["AC-2"]
    )
    nist = [
        Control(
            control_id="AC-2",
            title="t",
            framework="NIST 800-53",
            framework_version="r5",
            source_crosswalk={"Example Framework": "REQ-1"},
        )
    ]
    other = [
        Control(control_id="REQ-1", title="t", framework="Example Framework", framework_version="1")
    ]
    report = analyze_coverage(
        [topic], nist, other_controls=other, crosswalk=build_crosswalk(nist + other)
    )

    (framework,) = report.framework_coverage
    assert framework.covered == ["REQ-1"], framework


def test_the_zero_row_reads_every_place_a_mapping_can_live():
    """**#278.** A catalog that reads zero, with one requirement mapped on
    each of the three paths the crosswalk has -- its own `source_crosswalk`,
    the 800-53 side, and HITRUST's level scope -- and one mapped nowhere.
    Three are R, one is N, and P + R + N is the whole count. Reading only
    `source_crosswalk` (the #270 code) counts the other two as N."""
    from types import SimpleNamespace

    from policyforge.ingest.schema import Control
    from policyforge.zardoz.skills import _zero_row_reasons

    nist = [
        Control(
            control_id="AC-2",
            title="t",
            framework="NIST 800-53",
            framework_version="r5",
            source_crosswalk={"Example Framework": "EXF-2"},
        )
    ]
    other = [
        Control(
            control_id="EXF-1",
            title="t",
            framework="Example Framework",
            framework_version="1",
            source_crosswalk={"NIST 800-53": "AC-2"},
        ),
        Control(
            control_id="EXF-2", title="t", framework="Example Framework", framework_version="1"
        ),
        Control(
            control_id="EXF-3", title="t", framework="Example Framework", framework_version="1"
        ),
    ]
    hitrust_controls = _hitrust_01a()
    level_ids = [r.requirement_id for c in hitrust_controls for r in c.requirements]

    coverage = [
        SimpleNamespace(
            framework="example", covered=[], partial=[], uncovered=["EXF-1", "EXF-2", "EXF-3"]
        ),
        SimpleNamespace(framework="hitrust-csf", covered=[], partial=[], uncovered=level_ids),
    ]
    rows = "\n".join(
        _zero_row_reasons(
            nist + other + hitrust_controls, SimpleNamespace(framework_coverage=coverage)
        )
    )

    example = _clauses(_row(rows, "EXAMPLE"))
    assert example == {"P": 0, "R": 2, "N": 1}, example
    hitrust_row = _clauses(_row(rows, "HITRUST-CSF"))
    assert hitrust_row == {"P": 0, "R": 2, "N": 0}, hitrust_row
    assert sum(example.values()) == 3 and sum(hitrust_row.values()) == len(level_ids)
