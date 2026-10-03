"""The AI RMF catalog, its parser, and the claims its README makes.

**The question each test here answers is 80's: what would this have to be
wrong about for the suite to stay green?** The catalog is 19 rows of
well-formed JSON whether or not it is the Framework, so a test that only
loads it and counts would stay green through a parser that dropped a
third of the Core.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from policyforge.content.tags import source_tags
from policyforge.ingest.ai_rmf import (
    FRAMEWORK,
    FRAMEWORK_VERSION,
    FUNCTIONS,
    AiRmfParseError,
    expected_shape,
    parse_ai_rmf,
)
from policyforge.mapping.crosswalk import normalize_framework
from policyforge.topics.satisfies import split_citation

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "frameworks" / "nist-ai-rmf"
FIXTURE = ROOT / "tests" / "fixtures" / "airc_ai_rmf_core.html"
#: The shape pin as the catalog states it (#189) -- read, not restated.
PIN = expected_shape(CATALOG / "framework.yaml")


@pytest.fixture(scope="module")
def controls() -> list[dict]:
    return json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def parsed():
    return parse_ai_rmf(FIXTURE.read_text(encoding="utf-8", errors="replace"), expected_shape=PIN)


# --- what the catalog is ------------------------------------------------


def test_the_whole_core_is_present(controls):
    """19 and 72 are the Framework's own numbers, not this parser's.

    Pinned as exact equality rather than a floor: a floor would pass a
    parser that started emitting duplicates, and `>= 19` is satisfied by
    every catalog that will ever be larger by accident.
    """
    assert len(controls) == 19
    assert sum(len(c["enhancements"]) for c in controls) == 72


def test_every_category_is_contiguous_within_its_function(controls):
    """The check that catches a partial parse.

    A catalog missing `Govern 3` is well-formed, has a plausible count,
    and is wrong. Nothing else here would notice.
    """
    for function in FUNCTIONS:
        numbers = sorted(
            int(c["control_id"].split()[1]) for c in controls if c["family"] == function
        )
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"{function} categories are not contiguous: {numbers}"
        )


def test_the_catalog_is_ordered_function_major(controls):
    """Counting rows says nothing about their arrangement.

    The first version of the loader sorted on the number alone and emitted
    `Govern 1, Map 1, Measure 1, Manage 1, Govern 2, ...` — every row
    present, every count correct, an order the Framework does not have.
    All three count assertions above passed against it.
    """
    families = [c["family"] for c in controls]
    assert families == sorted(families, key=FUNCTIONS.index), (
        "the four functions are interleaved rather than grouped"
    )


def test_nothing_emitted_is_empty(controls):
    """Stated over entries rather than over the file.

    A catalog whose every title is `""` is valid JSON of the right length.
    """
    for control in controls:
        assert control["title"].strip(), f"{control['control_id']} has no title"
        for enhancement in control["enhancements"]:
            assert enhancement["description"].strip(), (
                f"{enhancement['enhancement_id']} has no text"
            )


# --- citability: the defect two shipped catalogs had --------------------


def test_every_identifier_is_citable(controls):
    """**The test `45 CFR 171` and `42 CFR Part 2` failed for months.**

    Both declared a digit-initial name, so `SOURCE_TAG_RE` never matched,
    every citation to them was prose, and `satisfies --strict` exited 0
    reporting nothing cited. Every gate stayed green throughout.

    Asserted over *every* identifier rather than a worked example: a
    sample chosen because it is well known is selected for the property
    that makes it easy to get right.
    """
    for control in controls:
        for identifier in [control["control_id"]] + [
            e["enhancement_id"] for e in control["enhancements"]
        ]:
            tag = f"[{FRAMEWORK} {identifier}]"
            assert source_tags(tag) == [tag], f"{tag} is not a legal source tag"


def test_every_citation_resolves_back_to_this_catalog(controls):
    """A legal tag is not enough — it must also split the right way.

    Resolution runs against the *full* population of declared names, which
    is where the risk lives: `NIST AI RMF` competes with `NIST 800-53` and
    `NIST 800-171`, and a shorter match would strand the identifier.
    Dropping `NIST AI RMF` from the population yields
    `("NIST", "AI RMF Govern 1.1")` — the bare-`NIST` orphan the
    NIST-family split exists to prevent — so this assertion can fail.
    """
    names = sorted(_declared_names() | {FRAMEWORK})
    identifiers = [c["control_id"] for c in controls] + [
        e["enhancement_id"] for c in controls for e in c["enhancements"]
    ]
    ids = {normalize_framework(FRAMEWORK): identifiers}
    for identifier in identifiers:
        framework, requirement, _ = split_citation(f"{FRAMEWORK} {identifier}", names, ids)
        assert (framework, requirement) == (FRAMEWORK, identifier)


def test_the_catalog_keys_to_itself_and_not_to_another_nist_catalog(controls):
    assert normalize_framework(FRAMEWORK) == "nist-ai-rmf"
    assert normalize_framework(FRAMEWORK) != normalize_framework("NIST 800-53")
    assert {c["framework"] for c in controls} == {FRAMEWORK}
    assert {c["framework_version"] for c in controls} == {FRAMEWORK_VERSION}


def _declared_names() -> set[str]:
    names = set()
    for directory in sorted((ROOT / "data" / "frameworks").iterdir()):
        controls_json = directory / "controls.json"
        if controls_json.exists():
            names.add(json.loads(controls_json.read_text(encoding="utf-8"))[0]["framework"])
    return names


# --- the parser's guards, each made to fire -----------------------------
#
# A guard nobody has seen fail is a guard nobody has tested. Each of these
# mutates the fixture in the one way the corresponding check exists to
# catch, and requires the parser to raise rather than to return something
# smaller.


def test_a_page_that_is_not_the_core_raises(parsed):
    with pytest.raises(AiRmfParseError, match=r"parser failure"):
        parse_ai_rmf("<html><body>AIRC is down for maintenance.</body></html>", expected_shape=PIN)


def test_a_restyle_that_drops_every_row_raises():
    """The silent-empty failure, which is why the parser keys on the
    identifier rather than on the class."""
    html = FIXTURE.read_text(encoding="utf-8", errors="replace")
    with pytest.raises(AiRmfParseError):
        parse_ai_rmf(html.replace("</th>", "</td>"), expected_shape=PIN)


def test_a_missing_category_orphans_its_subcategories():
    """Delete `Govern 6` and the result must be an error, not an 18-row
    catalog.

    **Renamed, and its `match=` tightened, because the name was wrong.**
    policyforge-ba deleted each guard in turn and recorded which test
    noticed: this one was written for *contiguity* and is actually
    satisfied by the *orphan* guard — removing `Govern 6` strands
    `Govern 6.1` and `6.2`, orphans are checked first, and contiguity is
    never reached. The `contiguous|parent` alternation is what hid it. A
    test whose name says one thing and whose assertion accepts either is
    a test nobody can audit by reading.
    """
    html = FIXTURE.read_text(encoding="utf-8", errors="replace")
    mutated = re.sub(r'<span class="[^"]*">\s*Govern 6\s*</span>', "<span>x</span>", html)
    assert mutated != html, "the mutation did not apply; the fixture changed shape"
    with pytest.raises(AiRmfParseError, match=r"parent"):
        parse_ai_rmf(mutated, expected_shape=PIN)


def test_a_gap_in_the_numbering_raises():
    """Contiguity, reached on purpose rather than by accident.

    To get here the mutation has to remove a category **and** its
    subcategories, or the orphan guard fires first. Removing `Govern 5`
    and its children leaves `Govern 1,2,3,4,6` — every remaining row
    well-formed, every parent present, and a framework that is missing
    one category.

    That is the failure mode with no other symptom: a partial parse looks
    exactly like a smaller framework.
    """
    html = FIXTURE.read_text(encoding="utf-8", errors="replace")
    mutated = re.sub(r'<span class="[^"]*">\s*Govern 5(?:\.\d+)?\s*</span>', "<span>x</span>", html)
    assert mutated != html, "the mutation did not apply; the fixture changed shape"
    with pytest.raises(AiRmfParseError, match=r"contiguous"):
        parse_ai_rmf(mutated, expected_shape=PIN)


def test_an_unknown_function_name_raises():
    """**This guard was unreachable until 2026-09-20.**

    `_ROW` enumerated `(?:Govern|Map|Measure|Manage)` inside the
    identifier capture, so the regex could not produce a name the guard
    would reject. A fifth NIST function did not raise — the row **failed
    to match at all** and vanished, and the reader got a confusing
    complaint about category numbering. Found by ba, by deleting the
    guard and observing that nothing noticed.

    Both cases now land where they should.
    """
    html = FIXTURE.read_text(encoding="utf-8", errors="replace")

    for mutated in (
        html.replace("Measure 1", "Meassure 1"),
        html.replace("Govern 1<", "Sustain 1<").replace("Govern 1 ", "Sustain 1 "),
    ):
        assert mutated != html
        with pytest.raises(AiRmfParseError, match=r"unrecognised AI RMF function"):
            parse_ai_rmf(mutated, expected_shape=PIN)


def test_a_row_with_no_text_raises():
    """The remaining guard ba found untested. It was reachable all along
    and one line from being covered.

    **Mutated through the parser's own match span rather than a
    hand-written regex.** The first attempt wrote a second pattern meant
    to mean the same thing as `_ROW`, and it did not: it swallowed the
    row's closing tag, so `Map 2` vanished entirely and the *orphan*
    guard fired on its subcategories. The test would have passed with a
    looser `match=` and been testing the wrong guard — which is the exact
    defect ba had just found in the test above it.

    Using `_ROW`'s own span makes the mutation mean what the parser
    means, by construction.
    """
    from policyforge.ingest.ai_rmf import _ROW

    html = FIXTURE.read_text(encoding="utf-8", errors="replace")
    match = next(m for m in _ROW.finditer(html) if m.group("id") == "Map 2")
    mutated = html[: match.start("text")] + html[match.end("text") :]
    assert mutated != html, "the mutation did not apply; the fixture changed shape"

    with pytest.raises(AiRmfParseError, match=r"no text"):
        parse_ai_rmf(mutated, expected_shape=PIN)


def test_parsing_more_than_nist_publishes_raises():
    """**An over-parse is as much a defect as an under-parse, and the
    structural checks do not catch it.**

    Contiguity accepts an *extension*: an invented `Govern 7` gives 20
    categories with every other assertion satisfied — every row
    well-formed, every parent present, numbering contiguous from 1. An
    extra subcategory is worse, since it disturbs nothing at all.

    Raised by policyforge-9b against the widened row pattern, and it was
    already true of the narrow one — widening made it visible rather than
    introducing it.

    The pin is an exact pair, not a floor, because a page yielding a
    different shape is either a restyle or a new revision, and **both need
    a person**: the first is a parser bug, the second makes this catalog's
    pin, README and provenance stamp stale together.
    """
    html = FIXTURE.read_text(encoding="utf-8", errors="replace")

    for label, extra in [
        ("an extra category", '<th><span class="x">Govern 7</span>: invented</th>'),
        ("an extra subcategory", '<th><span class="x">Govern 1.8</span>: invented</th>'),
    ]:
        mutated = html.replace("</table>", extra + "</table>", 1)
        assert mutated != html, f"{label}: the mutation did not apply"
        with pytest.raises(AiRmfParseError, match=r"revision 1\.0 is pinned at 19 and 72"):
            parse_ai_rmf(mutated, expected_shape=PIN)


def test_the_shape_guard_allows_the_real_page():
    """What the guard must ALLOW, stated beside what it refuses.

    A pinned shape is one typo away from refusing everything, and a guard
    that refuses its own source is indistinguishable from a broken parser.
    """
    controls = parse_ai_rmf(
        FIXTURE.read_text(encoding="utf-8", errors="replace"), expected_shape=PIN
    )
    assert (len(controls), sum(len(c.enhancements) for c in controls)) == PIN


# --- the pin itself, now data (#189) ----------------------------------------


def test_the_pin_equals_the_shipped_catalog(controls):
    """The yaml pin is DERIVED: it must equal the shape of the controls.json
    beside it. A pin edited without regenerating, or a catalog regenerated
    against a hand-widened pin, disagrees here."""
    shipped = (len(controls), sum(len(c.get("enhancements") or []) for c in controls))
    assert PIN == shipped == (19, 72)


def test_the_parse_refuses_a_pin_that_disagrees_with_the_page():
    """A stale pin fails LOUDLY: the real page against a pin one off, in
    each direction and each field, is refused rather than absorbed."""
    html = FIXTURE.read_text(encoding="utf-8", errors="replace")
    for wrong in ((18, 72), (20, 72), (19, 71), (19, 73)):
        with pytest.raises(AiRmfParseError, match=r"parsed to 19 categories and 72"):
            parse_ai_rmf(html, expected_shape=wrong)


@pytest.mark.parametrize(
    "body, reason",
    [
        ("id: nist-ai-rmf\n", "no shape key"),
        ("shape: 19\n", "shape not a mapping"),
        ("shape:\n  categories: 19\n", "subcategories missing"),
        ("shape:\n  categories: '19'\n  subcategories: 72\n", "a string"),
        ("shape:\n  categories: true\n  subcategories: 72\n", "a bool"),
        ("shape:\n  categories: 0\n  subcategories: 72\n", "zero"),
        ("", "empty file"),
    ],
)
def test_expected_shape_refuses_a_missing_or_malformed_pin(tmp_path, body, reason):
    path = tmp_path / "framework.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(AiRmfParseError, match=r"shape"):
        expected_shape(path)


def test_expected_shape_refuses_a_missing_file(tmp_path):
    with pytest.raises(AiRmfParseError, match=r"no `shape:` pin"):
        expected_shape(tmp_path / "framework.yaml")


def test_expected_shape_reads_what_a_valid_pin_says(tmp_path):
    """The passing case, with values unlike the real ones, so a reader that
    returned a constant could not pass."""
    path = tmp_path / "framework.yaml"
    path.write_text("shape:\n  categories: 3\n  subcategories: 11\n", encoding="utf-8")
    assert expected_shape(path) == (3, 11)


def test_the_provenance_rerun_leaves_the_pin_untouched(tmp_path):
    """The ETL records provenance into this same file. The pin is not one of
    the keys it owns, so a re-run must leave it exactly as a person wrote it:
    a guard the guarded run could rewrite would be no guard."""
    from policyforge.ingest.provenance import PROVENANCE_KEYS, record_source_provenance

    assert "shape" not in PROVENANCE_KEYS
    catalog = tmp_path / "nist-ai-rmf"
    catalog.mkdir()
    (catalog / "framework.yaml").write_bytes((CATALOG / "framework.yaml").read_bytes())
    (catalog / "controls.json").write_bytes((CATALOG / "controls.json").read_bytes())
    record_source_provenance(
        catalog / "controls.json",
        source_ref="AI RMF 1.0",
        source_url="https://example.invalid/core",
        content=b"changed upstream bytes",
    )
    assert expected_shape(catalog / "framework.yaml") == PIN


def test_the_etl_reads_the_pin_from_the_catalog_it_regenerates(tmp_path):
    """On the user's path: `etl-ai-rmf --out <catalog>/controls.json` reads
    `<catalog>/framework.yaml`. A pin edited there to disagree with the page
    refuses the run and writes nothing."""
    from click.testing import CliRunner

    from policyforge import cli as cli_mod

    catalog = tmp_path / "nist-ai-rmf"
    catalog.mkdir()
    pin = (CATALOG / "framework.yaml").read_text(encoding="utf-8")
    assert pin.count("subcategories: 72") == 1
    (catalog / "framework.yaml").write_text(
        pin.replace("subcategories: 72", "subcategories: 73"), encoding="utf-8"
    )
    out = catalog / "controls.json"
    result = CliRunner().invoke(
        cli_mod.cli, ["etl-ai-rmf", "--html", str(FIXTURE), "--out", str(out)]
    )
    assert result.exit_code == 1, result.output
    assert "Error: the Core parsed to 19 categories and 72" in result.output
    assert "pinned at 19 and 73" in result.output
    assert "Traceback" not in result.output
    assert not out.exists(), "a refused parse wrote a catalog"


def test_a_scratch_out_falls_back_to_the_bundled_pin(tmp_path):
    """`--out` in a scratch directory has no framework.yaml beside it; the
    bundled catalog's pin applies, the parse succeeds, and nothing is
    stamped into the scratch directory."""
    from click.testing import CliRunner

    from policyforge import cli as cli_mod

    out = tmp_path / "scratch" / "controls.json"
    result = CliRunner().invoke(
        cli_mod.cli, ["etl-ai-rmf", "--html", str(FIXTURE), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    shipped = (CATALOG / "controls.json").read_bytes().replace(b"\r\n", b"\n")
    assert out.read_bytes() == shipped
    assert not (out.parent / "framework.yaml").exists()
