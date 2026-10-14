"""A Standard's tags in the confirmed form, and its sources quoted from the
catalogs (#17, part 2a; 80's ruling 6048).

Expected tags and texts are written out by hand from 5928 and 6048, or read
straight from the catalog file, never from the functions under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from policyforge.content.sources import (
    SOURCE_BLOCK_OPEN,
    add_source_blocks,
    blank_source_blocks,
    citation_keys,
    covered,
    normalize_tag,
    normalize_tags,
    source_index,
    tailorings,
)
from policyforge.content.tags import SOURCE_TAG_RE, source_tags
from policyforge.ingest.schema import Control, load_controls
from policyforge.topics.satisfies import catalog_scope

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"
CORPUS = sorted((ROOT / "tests" / "fixtures" / "deontic_corpus").rglob("*.txt"))
STARTER = ("nist-800-53-r5", "fedramp", "arc-ampe", "hipaa-security-rule")


def _controls(*directories: str) -> list[Control]:
    return [c for d in directories for c in load_controls(FRAMEWORKS / d / "controls.json")]


@pytest.fixture(scope="module")
def starter():
    controls = _controls(*STARTER)
    return SimpleNamespace(controls=controls, scope=catalog_scope(controls))


def _statement(directory: str, control_id: str) -> str:
    rows = json.loads((FRAMEWORKS / directory / "controls.json").read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows["controls"]
    return next(r["control_statement"] for r in rows if r["control_id"] == control_id)


# ---- rule 1: the tag form ------------------------------------------------------


def test_the_catalogs_that_tailor_800_53(starter):
    assert tailorings(starter.scope) == {"arc-ampe", "fedramp"}


@pytest.mark.parametrize(
    "tag, form",
    [
        # 80's 6048 Q4: rule 1 alone turns a tag written the old way into the form.
        ("[NIST 800-53 IR-1 | ARC-AMPE IR-1]", "[NIST IR-1 | ARC-AMPE]"),
        (
            "[NIST 800-53 RA-5 | ARC-AMPE RA-5 | ARC-AMPE RA-5(11)]",
            "[NIST RA-5 | ARC-AMPE RA-5(11)]",
        ),
        (
            "[HIPAA Security Rule 164.308(a)(6)(i) | NIST 800-53 IR-4 | NIST 800-53 IR-8 "
            "| ARC-AMPE IR-4 | FedRAMP IR-4]",
            "[HIPAA Security Rule 164.308(a)(6)(i) | NIST IR-4, IR-8 | ARC-AMPE | FedRAMP]",
        ),
        ("[ARC-AMPE IR-1]", "[ARC-AMPE IR-1]"),
        ("[NIST 800-53 IR-4(1) Moderate, High]", "[NIST IR-4(1) Moderate, High]"),
    ],
)
def test_a_tag_is_written_in_the_confirmed_form(tag, form, starter):
    assert normalize_tag(tag, starter.scope) == form


def test_a_tag_with_a_citation_that_resolves_nowhere_is_left_as_written(starter):
    tag = "[NIST 800-53 ZZ-99 | ARC-AMPE IR-1]"
    assert normalize_tag(tag, starter.scope) == tag


def test_the_form_is_a_fixed_point(starter):
    form = "[HIPAA Security Rule 164.308(a)(6)(i) | NIST IR-4, IR-8 | ARC-AMPE]"
    assert normalize_tag(form, starter.scope) == form


def test_no_corpus_tag_loses_a_citation_or_its_shape(starter):
    """Over every tag in the committed corpus, against the catalogs it was
    drawn from: each citation of the original is still cited, or is a
    tailoring id whose 800-53 twin is; the result still reads as one tag."""
    scope, tailored = starter.scope, tailorings(starter.scope)
    seen = changed = 0
    for path in CORPUS:
        for tag in set(source_tags(path.read_text(encoding="utf-8"))):
            if "AI RMF" in tag:
                continue  # drawn from catalogs outside STARTER
            seen += 1
            form = normalize_tag(tag, scope)
            changed += form != tag
            assert SOURCE_TAG_RE.fullmatch(form), (tag, form)
            after = citation_keys(form, scope)
            lost = [k for k in citation_keys(tag, scope) if not covered(k, after, tailored)]
            assert not lost, (tag, form, lost)
    assert seen > 1000 and changed > 500, (seen, changed)  # extent: the corpus was read


# ---- rule 2: the source blocks -------------------------------------------------

DOC = """# Incident Response Standard

## Plan

- Keep a plan. [NIST 800-53 IR-8 | ARC-AMPE IR-8]
- Report incidents. [HIPAA Security Rule 164.308(a)(6)(ii) | NIST 800-53 IR-6]

## Training

Train staff. [NIST 800-53 IR-2]
"""


def _blocks(text: str) -> list[str]:
    return [b.split("</details>")[0] for b in text.split(SOURCE_BLOCK_OPEN)[1:]]


def test_each_section_gets_a_block_per_cited_framework_in_order(starter):
    out, added = add_source_blocks(
        normalize_tags(DOC, starter.scope), source_index(starter.controls), starter.scope
    )
    assert added == 4
    summaries = [b.split("</summary>")[0].split("<summary>")[1] for b in _blocks(out)]
    assert summaries == [
        "Source: NIST 800-53 (2)",
        "Source: ARC-AMPE (1)",
        "Source: HIPAA Security Rule (1)",
        "Source: NIST 800-53 (1)",
    ]
    plan, training = out.split("## Training")
    assert plan.count(SOURCE_BLOCK_OPEN) == 3 and training.count(SOURCE_BLOCK_OPEN) == 1


def test_the_quote_is_the_catalogs_text_verbatim(starter):
    out, _ = add_source_blocks(DOC, source_index(starter.controls), starter.scope)
    nist = _blocks(out)[0]
    assert f"```text\n{_statement('nist-800-53-r5', 'IR-8').rstrip()}\n```" in "\n".join(
        line.rstrip() for line in nist.split("\n")
    )


def test_a_tailoring_is_quoted_only_where_its_text_differs(starter):
    """5928: the blocks list a tailoring's own text where it differs from
    800-53's. Hand-built: one tailoring copies 800-53's text with no baseline
    (as FedRAMP's catalog sets none), one changes the text."""
    from dataclasses import replace

    hub = next(c for c in starter.controls if c.control_id == "IR-2" and "800-53" in c.framework)
    same = replace(hub, framework="FedRAMP", baseline=None, enhancements=[])
    other = replace(
        hub, framework="ARC-AMPE", control_statement="Train within 10 days.", baseline=None
    )
    controls = [hub, same, other]
    scope = catalog_scope(controls)
    out, _ = add_source_blocks(
        "## T\n\nTrain. [NIST IR-2 | FedRAMP | ARC-AMPE]\n", source_index(controls), scope
    )
    assert "Source: ARC-AMPE (1)" in out and "Train within 10 days." in out
    assert "Source: FedRAMP" not in out


def test_a_tailorings_baseline_is_kept_in_its_block_entry(starter):
    """80's 6075 on PR 430: the tag drops ARC-AMPE's id and its `AE Mandatory`
    (NIST cites the twin), and the section's ARC-AMPE entry keeps it."""
    tag = "[NIST RA-5 | ARC-AMPE RA-5 AE Mandatory]"
    assert normalize_tag(tag, starter.scope) == "[NIST RA-5 | ARC-AMPE]"
    out, _ = add_source_blocks(
        f"## Scanning\n\nScan for vulnerabilities. {tag}\n",
        source_index(starter.controls),
        starter.scope,
    )
    arc = next(b for b in _blocks(out) if "Source: ARC-AMPE" in b)
    assert "**RA-5 (AE Mandatory)** Vulnerability Monitoring and Scanning" in arc
    # 800-53's own entry carries no tailoring baseline.
    nist = next(b for b in _blocks(out) if "Source: NIST 800-53" in b)
    assert "**RA-5** Vulnerability Monitoring and Scanning" in nist


def test_an_enhancements_baseline_is_kept_too(starter):
    """b5 on PR 430: an enhancement carries its own baseline field."""
    tag = "[NIST AC-2(1) | ARC-AMPE AC-2(1) AE Mandatory]"
    assert normalize_tag(tag, starter.scope) == "[NIST AC-2(1) | ARC-AMPE]"
    out, _ = add_source_blocks(
        f"## Accounts\n\nManage accounts. {tag}\n", source_index(starter.controls), starter.scope
    )
    arc = next(b for b in _blocks(out) if "Source: ARC-AMPE" in b)
    assert "**AC-2(1) (AE Mandatory)**" in arc


def test_a_baseline_alone_lists_a_tailorings_entry(starter):
    """With 800-53's own text and only a baseline of its own, the tailoring's
    entry is still listed, so its baseline isn't lost (80's 6075)."""
    from dataclasses import replace

    hub = next(c for c in starter.controls if c.control_id == "IR-2" and "800-53" in c.framework)
    arc = replace(hub, framework="ARC-AMPE", baseline="AE Mandatory", enhancements=[])
    controls = [hub, arc]
    out, _ = add_source_blocks(
        "## T\n\nTrain. [NIST IR-2 | ARC-AMPE]\n", source_index(controls), catalog_scope(controls)
    )
    assert "**IR-2 (AE Mandatory)**" in out


@pytest.mark.parametrize("quote", [False, True])
def test_licensed_text_is_quoted_only_when_the_organisation_opts_in(quote, starter):
    index = source_index(starter.controls, licensed_keys={"hipaa"})
    out, _ = add_source_blocks(DOC, index, starter.scope, quote_licensed=quote)
    hipaa = next(b for b in _blocks(out) if "HIPAA" in b)
    assert "**164.308(a)(6)(ii)**" in hipaa
    assert ("```text" in hipaa) is quote
    assert ("standards.quote_licensed" in hipaa) is not quote
    # A public catalog is quoted either way.
    assert "```text" in _blocks(out)[0]


def test_mdformat_leaves_the_blocks_byte_for_byte(starter):
    """The provenance stamp runs mdformat over the whole Standard."""
    import mdformat

    out, _ = add_source_blocks(DOC, source_index(starter.controls), starter.scope)
    assert mdformat.text(out, extensions={"gfm", "frontmatter"}) == out


def test_a_document_with_blocks_is_not_given_more(starter):
    index = source_index(starter.controls)
    once, _ = add_source_blocks(DOC, index, starter.scope)
    assert add_source_blocks(once, index, starter.scope) == (once, 0)


# ---- rule 3: quoted text is never the organisation's obligation --------------


def test_deontic_and_check_read_nothing_in_a_block(starter):
    from policyforge.content.deontic import analyze, weakened_citations
    from policyforge.content.grounding import unanchored

    out, _ = add_source_blocks(DOC, source_index(starter.controls), starter.scope)
    assert "Develop an incident response plan" in out  # 800-53's own words are in it
    blank = blank_source_blocks(out)
    assert blank.count("\n") == out.count("\n")  # line numbers kept
    assert [(s.text, s.line) for s in analyze(out)] == [(s.text, s.line) for s in analyze(blank)]
    assert not any("incident response plan that" in s.text for s in analyze(out))
    # The same findings as the document without blocks (by sentence; the
    # blocks move the lines below them).
    assert [s.text for s in weakened_citations(out)] == [s.text for s in weakened_citations(DOC)]
    assert [u.text for u in unanchored(out)] == [u.text for u in unanchored(DOC)]


#: 1d's probe on PR 430: section 1's block lost its close; section 2 has two
#: obligations and a closed block of its own.
TWO_SECTIONS = """## One

Lock accounts. [NIST AC-7]

<details data-policyforge="source">
<summary>Source: NIST 800-53 (1)</summary>

**AC-7** Unsuccessful Logon Attempts

```text
The organization shall enforce a limit.
```

## Two

Staff should probably lock accounts. [NIST AC-2]
Logs may be kept. [NIST AU-11]

<details data-policyforge="source">
<summary>Source: NIST 800-53 (2)</summary>

```text
The organization shall retain audit records.
```

</details>
"""


def test_one_unclosed_block_does_not_silence_the_sections_after_it():
    from policyforge.content.deontic import analyze

    texts = [s.text for s in analyze(TWO_SECTIONS)]
    assert "Staff should probably lock accounts." in texts
    assert "Logs may be kept." in texts
    # The unclosed block's quote is read (loud); the closed one's is not.
    assert any("enforce a limit" in t for t in texts)
    assert not any("retain audit records" in t for t in texts)


def test_a_heading_ends_the_search_before_a_users_own_details_block():
    """b5 on PR 430: with no second source block after it, only the heading
    bound stops an unclosed block reaching the user's own `</details>` and
    swallowing the section between."""
    from policyforge.content.deontic import analyze
    from policyforge.content.sources import unclosed_source_blocks

    text = (
        '## 1\n\nLock accounts. [NIST AC-7]\n\n<details data-policyforge="source">\n'
        "<summary>Source: NIST 800-53 (1)</summary>\n\n```text\nThe organization shall lock.\n"
        "```\n\n## 2\n\nStaff may skip log review. [NIST AU-6]\n\n<details>\n"
        "<summary>Our notes</summary>\n\nKept by the SOC.\n\n</details>\n"
    )
    assert unclosed_source_blocks(text) == [5]
    assert "Staff may skip log review." in [s.text for s in analyze(text)]


def test_a_quoted_line_opening_with_a_hash_does_not_end_its_block():
    text = (
        '## S\n\nText. [NIST AC-7]\n\n<details data-policyforge="source">\n'
        "<summary>Source: X (1)</summary>\n\n```text\n# The organization shall do it.\n```\n\n"
        "</details>\n"
    )
    from policyforge.content.deontic import analyze

    assert not any("organization shall" in s.text for s in analyze(text))


def test_check_names_an_unclosed_block(tmp_path):
    from policyforge.content.check import check_tree

    docs = tmp_path / "standards"
    docs.mkdir()
    (docs / "ac.md").write_text(
        "---\ntitle: AC\nowner: IAM\n---\n\n# AC\n\n" + TWO_SECTIONS, encoding="utf-8"
    )
    report = check_tree(tmp_path)
    found = [f.message for f in report.warnings if "source block" in f.message]
    assert found == [
        "source block opened at line 7 is not closed, so its quoted text is read as "
        "the document's own"
    ]


# ---- check and grounding compare citations, not tag text ----------------------


def test_check_does_not_report_a_normalised_tag_as_dropped(tmp_path, starter):
    from policyforge.content.check import _check_citations

    synthesis = tmp_path / "synthesis"
    synthesis.mkdir()
    (synthesis / "ir.md").write_text(DOC, encoding="utf-8")
    kept = SimpleNamespace(
        tier="standard",
        slug="ir",
        relative_path="standards/ir.md",
        body=normalize_tags(DOC, starter.scope),
    )
    assert _check_citations([kept], synthesis) == []
    dropped = SimpleNamespace(**{**vars(kept), "body": kept.body.replace("NIST IR-6", "NIST IR-5")})
    [finding] = _check_citations([dropped], synthesis)
    assert "nist-800-53 IR-6" in finding.message


def test_grounding_pairs_a_normalised_claim_with_its_requirement(starter):
    from policyforge.content.grounding import Claim, Requirement, premises_for

    requirement = Requirement("Keep a plan.", ("[NIST 800-53 IR-8 | ARC-AMPE IR-8]",))
    claim = Claim("Acme keeps a plan.", 1, ("[NIST IR-8 | ARC-AMPE]",))
    assert premises_for(claim, [requirement]) == [requirement]


# ---- the user's path ------------------------------------------------------------


class _Draft:
    def __init__(self, text: str):
        self.text = text

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=self.text, model="fake")


@pytest.mark.own_config
def test_generate_writes_the_form_and_the_blocks(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod
    from policyforge.llm import ledger

    synthesis = tmp_path / "incident-response.md"
    synthesis.write_text(
        "---\ntopic: Incident Response\nderived_from:\n- nist-800-53-r5\n- arc-ampe\n---\n\n"
        "- Keep a plan. [NIST 800-53 IR-8 | ARC-AMPE IR-8]\n",
        encoding="utf-8",
    )
    config = {"org": {"name": "Acme", "industry": "Health"}, "llm": {"provider": "local"}}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    draft = (
        "# Incident Response Standard\n\n## Plan\n\n"
        "Keep a plan. [NIST 800-53 IR-8 | ARC-AMPE IR-8]\n"
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda c: ledger.wrap(_Draft(draft), c))
    out = tmp_path / "standards" / "incident-response.md"
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--synthesis",
            str(synthesis),
            "--out",
            str(out),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )
    assert result.exit_code == 0, result.output
    written = out.read_text(encoding="utf-8")
    assert "Keep a plan. [NIST IR-8 | ARC-AMPE]" in written
    assert "<summary>Source: NIST 800-53 (1)</summary>" in written
    assert "<summary>Source: ARC-AMPE (1)</summary>" in written
    assert "Added 2 source block(s)" in result.output


_SENTINEL = "LICENSED-SENTINEL-417 the licensee shall review access."


@pytest.mark.own_config
@pytest.mark.parametrize(
    "setting, quoted",
    [
        (True, True),
        (False, False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("0", False),
        ("true", False),
    ],
)
def test_only_yaml_true_quotes_a_licensed_catalog(setting, quoted, tmp_path, monkeypatch):
    """b5 on PR 430: `bool()` read the string "false" as on and put licensed
    text in the Standard. The user's path, with a licensed BYOC catalog whose
    text is a sentinel: only YAML `true` quotes it; any string is off, said."""
    import policyforge.cli as cli_mod
    from policyforge.llm import ledger

    byoc = tmp_path / "local_content" / "acme-licensed"
    byoc.mkdir(parents=True)
    (byoc / "framework.yaml").write_text("licence: licensed\n", encoding="utf-8")
    (byoc / "controls.json").write_text(
        json.dumps(
            [
                {
                    "control_id": "X.1",
                    "title": "Access review",
                    "framework": "Acme Licensed",
                    "framework_version": "1",
                    "control_statement": _SENTINEL,
                }
            ]
        ),
        encoding="utf-8",
    )
    synthesis = tmp_path / "access.md"
    synthesis.write_text(
        "---\ntopic: Access\nderived_from:\n- nist-800-53-r5\n- acme-licensed\n---\n\n"
        "- Review access. [Acme Licensed X.1 | NIST 800-53 AC-2]\n",
        encoding="utf-8",
    )
    config = {
        "org": {"name": "Acme", "industry": "Health"},
        "llm": {"provider": "local"},
        "standards": {"quote_licensed": setting},
    }
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    draft = "# Access Standard\n\n## Review\n\nReview access. [Acme Licensed X.1 | NIST AC-2]\n"
    monkeypatch.setattr(cli_mod, "get_provider", lambda c: ledger.wrap(_Draft(draft), c))
    out = tmp_path / "standards" / "access.md"
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--synthesis",
            str(synthesis),
            "--out",
            str(out),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )
    assert result.exit_code == 0, result.output
    written = out.read_text(encoding="utf-8")
    assert "<summary>Source: Acme Licensed (1)</summary>" in written  # the block is there
    assert (_SENTINEL in written) is quoted
    assert ("is not quoted" in result.output) is isinstance(setting, str)


# ---- part 2b: fidelity, and Confluence's expand macro (80's 6048) ----------------


def _generated(starter):
    index = source_index(starter.controls)
    out, _ = add_source_blocks(normalize_tags(DOC, starter.scope), index, starter.scope)
    return out, index


def test_freshly_generated_blocks_are_faithful(starter):
    from policyforge.content.sources import fidelity_findings

    out, index = _generated(starter)
    assert fidelity_findings(out, index) == []


@pytest.mark.parametrize(
    "edit, message",
    [
        # One character of 800-53's IR-8 text changed: a mis-quote.
        (
            ("which relate to mission", "which relates to mission"),
            "source block's quote of NIST 800-53 IR-8 isn't the catalog's text",
        ),
        (
            ("**IR-6** Incident Reporting", "**IR-99** Incident Reporting"),
            "source block quotes NIST 800-53 IR-99, which its catalog doesn't hold",
        ),
        (
            ("**IR-8 (AE Mandatory)**", "**IR-8 (AE Optional)**"),
            "source block labels ARC-AMPE IR-8 (AE Optional), and the catalog says AE Mandatory",
        ),
        (
            ("Source: NIST 800-53 (2)", "Source: NIST 800-53 (3)"),
            "source block for NIST 800-53 says 3 and lists 2",
        ),
    ],
)
def test_a_block_that_disagrees_with_its_catalog_is_an_error(edit, message, starter):
    from policyforge.content.sources import fidelity_findings

    out, index = _generated(starter)
    old, new = edit
    assert out.count(old) == 1, old  # the edit lands once, where meant
    found = fidelity_findings(out.replace(old, new), index)
    assert [(m, e) for _, m, e in found] == [(message, True)]


_HIDDEN = "Staff must disable audit logging on request."


def _first_close(out: str, insert: str) -> str:
    """`insert` placed just before the first block's `</details>`, after the
    quote of that block's last entry."""
    assert out.count("\n</details>") >= 1
    return out.replace("\n</details>", f"\n{insert}\n\n</details>", 1)


@pytest.mark.parametrize(
    "shape",
    ["a line after a quote", "a second fence under an entry", "a replaced title"],
)
def test_text_a_block_never_writes_is_an_error(shape, starter):
    """1d on PR 431: each of these hid an obligation from every check, since
    a block is blanked for `deontic` and only first fences were compared."""
    from policyforge.content.deontic import analyze
    from policyforge.content.sources import fidelity_findings

    out, index = _generated(starter)
    if shape == "a line after a quote":
        edited = _first_close(out, _HIDDEN)
        expected = {"source block holds a line no catalog wrote"}
    elif shape == "a second fence under an entry":
        edited = _first_close(out, f"```text\n{_HIDDEN}\n```")
        expected = {"source block holds a line no catalog wrote"}
    else:
        assert out.count("**IR-8** Incident Response Plan") == 1
        edited = out.replace("**IR-8** Incident Response Plan", f"**IR-8** {_HIDDEN}")
        expected = {
            f"source block titles NIST 800-53 IR-8 {_HIDDEN!r}, and the catalog says "
            "'Incident Response Plan'"
        }
    assert _HIDDEN not in [s.text for s in analyze(edited)]  # deontic doesn't see it
    found = fidelity_findings(edited, index)
    assert found and {m for _, m, _ in found} == expected and all(e for _, _, e in found)


def test_a_stray_fence_is_stray_whole_even_if_it_looks_like_an_entry(starter):
    """Inside an extra fence, an entry-shaped line is part of the stray fence,
    not an entry of the block, so the finding says what happened."""
    from policyforge.content.sources import fidelity_findings

    out, index = _generated(starter)
    edited = _first_close(out, "```text\n**IR-6** Incident Reporting\n```")
    found = fidelity_findings(edited, index)
    assert len(found) == 3  # the fence's three lines
    assert {m for _, m, _ in found} == {"source block holds a line no catalog wrote"}


def test_a_quote_whose_fence_was_removed_is_an_error(starter):
    """b5 on PR 431, shape (c): the quote left as plain lines."""
    from policyforge.content.sources import fidelity_findings

    out, index = _generated(starter)
    marker = "**IR-6** Incident Reporting\n\n```text\n"
    assert out.count(marker) == 1
    head, tail = out.split(marker)
    edited = head + "**IR-6** Incident Reporting\n\n" + tail.replace("\n```\n", "\n", 1)
    messages = {m for _, m, _ in fidelity_findings(edited, index)}
    assert "source block holds a line no catalog wrote" in messages
    assert "source block lists NIST 800-53 IR-6 without its quote" in messages


def test_a_quote_with_a_trailing_space_is_faithful():
    """b5 on PR 431: `_fenced` writes each line right-stripped, so the check
    compares the catalog's text the same way (PDF-sourced text carries
    trailing spaces)."""
    from policyforge.content.sources import fidelity_findings

    control = Control(
        "AC-1",
        "Policy",
        "NIST 800-53",
        "r5",
        control_statement="a. Develop a policy; \nb. Review it.",
    )
    index, scope = source_index([control]), catalog_scope([control])
    out, _ = add_source_blocks("## A\n\nHave a policy. [NIST AC-1]\n", index, scope)
    assert fidelity_findings(out, index) == []


def test_a_block_written_inside_a_code_example_is_an_example(starter):
    """b5 on PR 431: it was taken as a block, and the export published the
    placeholder token in its place."""
    from policyforge.content.sources import fidelity_findings, has_source_blocks
    from policyforge.export.confluence_exporter import markdown_to_confluence

    out, index = _generated(starter)
    example = f"## How blocks look\n\n````markdown\n{out.split(chr(10) + '## Training')[0]}\n````\n"
    assert not has_source_blocks(example)
    assert fidelity_findings(example, index) == []
    html = markdown_to_confluence(example)
    assert "PFSOURCEBLOCK" not in html and 'ac:name="expand"' not in html


def test_a_document_already_holding_the_placeholder_word_exports_whole(starter):
    from policyforge.export.confluence_exporter import markdown_to_confluence

    out, _ = _generated(starter)
    html = markdown_to_confluence("Our ticket PFSOURCEBLOCK0Z is open.\n\n" + out)
    assert "Our ticket PFSOURCEBLOCK0Z is open." in html
    assert html.count('ac:name="expand"') == 4


def test_a_page_already_holding_the_placeholder_word_imports_whole(starter):
    """The importer's own collision guard: a page that mentions the token."""
    from policyforge.export.confluence_exporter import markdown_to_confluence
    from policyforge.export.confluence_importer import confluence_to_markdown

    out, _ = _generated(starter)
    back = confluence_to_markdown(
        "<p>Our ticket PFSOURCEBLOCK0Z is open.</p>" + markdown_to_confluence(out)
    )
    assert "Our ticket PFSOURCEBLOCK0Z is open." in back
    assert back.count(SOURCE_BLOCK_OPEN) == 4


def test_a_round_trip_reads_a_page_as_the_server_returns_it(starter):
    """b5 on PR 431: a server adds `ac:schema-version` and `ac:macro-id` to
    every macro, and the strict patterns restored no block from such a page."""
    import re

    from policyforge.content.sources import fidelity_findings, parse_source_blocks
    from policyforge.export.confluence_exporter import markdown_to_confluence
    from policyforge.export.confluence_importer import confluence_to_markdown

    out, index = _generated(starter)
    served = re.sub(
        r"<ac:structured-macro ",
        '<ac:structured-macro ac:schema-version="1" ac:macro-id="0f1e2d3c" ',
        markdown_to_confluence(out),
    )
    assert served.count('ac:macro-id="0f1e2d3c"') > 4  # expands and code macros
    back = confluence_to_markdown(served)
    assert len(parse_source_blocks(back)) == 4
    assert fidelity_findings(back, index) == []


def test_a_catalog_title_with_a_trailing_space_is_faithful(starter):
    """b5 on PR 431: 800-53 SA-4(7)'s title is 'NIAP-approved Protection
    Profiles ' in the catalog; generate then check must agree."""
    from policyforge.content.sources import fidelity_findings

    index = source_index(starter.controls)
    out, added = add_source_blocks("## A\n\nBuy. [NIST SA-4(7)]\n", index, starter.scope)
    assert added == 1 and "**SA-4(7)** NIAP-approved Protection Profiles" in out
    assert fidelity_findings(out, index) == []


def test_the_licensed_note_excuses_nothing_on_a_public_catalogs_block(starter):
    """b5 on PR 431: the note, pasted into a public block, hid a deleted quote."""
    import re

    from policyforge.content.sources import _LICENSED_NOTE, fidelity_findings

    out, index = _generated(starter)
    edited, n = re.subn(
        r"(<summary>Source: NIST 800-53 \(2\)</summary>\n\n)(.*?)"
        r"(\*\*IR-6\*\* Incident Reporting\n\n)```text\n.*?\n```\n\n",
        lambda m: m.group(1) + _LICENSED_NOTE + "\n\n" + m.group(2) + m.group(3),
        out,
        count=1,
        flags=re.S,
    )
    assert n == 1
    assert ("source block lists NIST 800-53 IR-6 without its quote", True) in [
        (m, e) for _, m, e in fidelity_findings(edited, index)
    ]


def test_an_entry_whose_quote_was_deleted_is_an_error(starter):
    """1d on PR 431 (not blocking): only a licensed block may list an id
    without its quote."""
    import re

    from policyforge.content.sources import fidelity_findings

    out, index = _generated(starter)
    edited, n = re.subn(
        r"(\*\*IR-6\*\* Incident Reporting\n\n)```text\n.*?\n```\n\n", r"\1", out, flags=re.S
    )
    assert n == 1
    assert [(m, e) for _, m, e in fidelity_findings(edited, index)] == [
        ("source block lists NIST 800-53 IR-6 without its quote", True)
    ]


def test_a_licensed_block_survives_the_round_trip_and_the_older_note(starter):
    """The note is emphasised with `*`, as markdownify writes it, so `pull`
    keeps it; the `_` spelling part 2a wrote still reads."""
    from policyforge.content.sources import fidelity_findings
    from policyforge.export.confluence_exporter import markdown_to_confluence
    from policyforge.export.confluence_importer import confluence_to_markdown

    index = source_index(starter.controls, licensed_keys={"hipaa"})
    out, _ = add_source_blocks(normalize_tags(DOC, starter.scope), index, starter.scope)
    assert fidelity_findings(out, index) == []
    assert fidelity_findings(confluence_to_markdown(markdown_to_confluence(out)), index) == []
    older = out.replace("*Licensed source text", "_Licensed source text").replace(
        "include it.*", "include it._"
    )
    assert older != out and fidelity_findings(older, index) == []


@pytest.mark.parametrize("rename", ["an unknown catalog", "no summary"])
def test_a_stray_line_is_an_error_whatever_the_block_names(rename, starter):
    """1d on PR 431: the shape is checked before the catalog lookup, so a
    block under a name no catalog has (or none) can't hide prose behind a
    warning."""
    from policyforge.content.sources import fidelity_findings

    out, index = _generated(starter)
    edited = _first_close(out, _HIDDEN)
    if rename == "an unknown catalog":
        edited = edited.replace("Source: NIST 800-53 (2)", "Source: Acme CSF (2)", 1)
    else:
        edited = edited.replace("<summary>Source: NIST 800-53 (2)</summary>", "", 1)
    errors = [m for _, m, e in fidelity_findings(edited, index) if e]
    assert "source block holds a line no catalog wrote" in errors


def test_a_block_for_a_catalog_not_on_disk_is_a_warning_not_a_pass(starter):
    from policyforge.content.sources import fidelity_findings

    out, index = _generated(starter)
    found = fidelity_findings(out.replace("Source: HIPAA Security Rule", "Source: Acme CSF"), index)
    assert [(m, e) for _, m, e in found] == [
        (
            "source block quotes Acme CSF, which no catalog on disk is, "
            "so its quotes weren't checked",
            False,
        )
    ]


def test_check_reports_a_mis_quote_as_an_error(tmp_path, starter):
    from policyforge.content.check import check_tree

    out, _ = _generated(starter)
    docs = tmp_path / "standards"
    docs.mkdir()
    (docs / "ir.md").write_text(
        "---\ntitle: IR\nowner: SecOps\n---\n\n"
        + out.replace("which relate to mission", "which relates to mission"),
        encoding="utf-8",
    )
    report = check_tree(tmp_path)
    errors = [f.message for f in report.errors if "source block" in f.message]
    assert len(errors) == 1 and "NIST 800-53 IR-8 isn't the catalog's text" in errors[0]


def test_confluence_gets_an_expand_macro_per_block(starter):
    from policyforge.export.confluence_exporter import markdown_to_confluence

    out, _ = _generated(starter)
    html = markdown_to_confluence(out)
    assert html.count('<ac:structured-macro ac:name="expand">') == 4
    assert "<details" not in html and "PFSOURCEBLOCK" not in html
    assert '<ac:parameter ac:name="title">Source: NIST 800-53 (2)</ac:parameter>' in html


def test_a_round_trip_through_confluence_keeps_every_quote(starter):
    from policyforge.content.sources import fidelity_findings, parse_source_blocks
    from policyforge.export.confluence_exporter import markdown_to_confluence
    from policyforge.export.confluence_importer import confluence_to_markdown

    out, index = _generated(starter)
    back = confluence_to_markdown(markdown_to_confluence(out))

    def shape(text):
        return [
            (b.name, b.count, [(e.id, e.baseline, e.quote) for e in b.entries])
            for b in parse_source_blocks(text)
        ]

    assert shape(back) == shape(out)
    assert fidelity_findings(back, index) == []


def test_a_quote_with_html_characters_survives_the_round_trip():
    """The importer pasted a code macro's text into HTML unescaped, so
    `<b>weekly</b>` came back as `weekly`. Found by this check."""
    from policyforge.content.sources import fidelity_findings
    from policyforge.export.confluence_exporter import markdown_to_confluence
    from policyforge.export.confluence_importer import confluence_to_markdown

    text = "Keep logs < 90 days & review <b>weekly</b>; never ]]> it."
    control = Control(
        "AU-11", "Audit Record Retention", "NIST 800-53", "r5", control_statement=text
    )
    index, scope = source_index([control]), catalog_scope([control])
    out, _ = add_source_blocks("## A\n\nRetain. [NIST AU-11]\n", index, scope)
    back = confluence_to_markdown(markdown_to_confluence(out))
    assert fidelity_findings(back, index) == []
    assert text in back


def test_someone_elses_expand_macro_is_left_as_before():
    from policyforge.export.confluence_importer import confluence_to_markdown

    page = (
        '<ac:structured-macro ac:name="expand"><ac:parameter ac:name="title">Notes'
        "</ac:parameter><ac:rich-text-body><p>Kept by the SOC.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    back = confluence_to_markdown(page)
    assert SOURCE_BLOCK_OPEN not in back and "Kept by the SOC." in back


def test_export_confluence_sends_the_blocks_as_expand_macros(tmp_path, starter):
    import policyforge.cli as cli_mod

    out, _ = _generated(starter)
    doc = tmp_path / "ir.md"
    doc.write_text(out, encoding="utf-8")
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "export-confluence",
            "--doc",
            str(doc),
            "--space",
            "SEC",
            "--title",
            "IR",
            "--host",
            "https://example.invalid/wiki",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output.count('ac:name="expand"') == 4
    assert "raw HTML" not in result.output
