"""`generate --document acceptable-use` (418; 80's spec 5926, amended by 6334).

The section lists, the control set and the crosswalk are hand-written here
from 5926 and 5923, never imported from the kind's own definition, and the
control text is read straight from the catalog file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge.content.tags import SOURCE_TAG_RE
from policyforge.llm.base import LLMResponse

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"

#: 5926: 5923's sections 1-12 and 14-15, in this order.
AUP_SECTIONS = [
    "Scope",
    "Acknowledgment",
    "Personal Use",
    "Prohibited Activities",
    "Monitoring Notice",
    "Credentials",
    "Data Handling",
    "Devices",
    "Remote Work and Travel",
    "Email, Internet, and Social Media",
    "Incident Reporting",
    "AI Use",
    "Sanctions",
    "Link to Training",
]
ADDENDUM_SECTIONS = ["Scope", "Privileged Access"]

#: 5926's fixed control set.
FIXED = {
    "PL-4",
    "PL-4(1)",
    "PS-6",
    "PS-8",
    "AC-6",
    "AC-8",
    "AC-17",
    "AC-19",
    "AC-20",
    "MP-7",
    "CM-10",
    "CM-11",
    "IR-6",
    "AT-2",
    "PT-2",
}

REGISTRY = """
topics:
  - name: Security Program Governance
    owner: Security Program / GRC
    nist_controls: [PM-1, PL-4, PS-6]
  - name: Identity Lifecycle
    owner: IAM
    nist_controls: [AC-2, AC-6]
"""

#: What a crosswalk maps: hand-written ids, one of them a licensed catalog's.
CROSSWALK = {
    "PL-4": {"hipaa": ["164.310(b)"], "hitrust": ["01.a"]},
    "IR-6": {"hipaa": ["164.308(a)(6)(ii)"]},
}


def _draft(sections, *, extra="", leave_out=()):
    body = [
        f"## {i + 1}. {title}\n\n- Follow the rule for {title.lower()}.\n"
        for i, title in enumerate(sections)
        if title not in leave_out
    ]
    return "\n".join(body) + extra


class _Fake:
    """Answers each document's call with a draft of its sections."""

    def __init__(self, *, aup=None, addendum=None, fail=False):
        self.aup = aup if aup is not None else _draft(AUP_SECTIONS)
        self.addendum = addendum if addendum is not None else _draft(ADDENDUM_SECTIONS)
        self.fail = fail
        self.prompts: list[str] = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("the provider is down")
        text = self.addendum if "Document: Privileged User Rules" in prompt else self.aup
        return LLMResponse(text=text, model="fake")

    def check(self):
        return True


def _project(tmp_path, monkeypatch, *, fake=None, registry=REGISTRY, crosswalk=CROSSWALK):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    if registry is not None:
        (tmp_path / "config" / "topics.yaml").write_text(registry, encoding="utf-8")
    walk = tmp_path / "crosswalk.json"
    if crosswalk is not None:
        walk.write_text(json.dumps(crosswalk), encoding="utf-8")
    monkeypatch.setattr("policyforge.cli.documents.ACCEPTABLE_USE_CATALOG", CATALOG)
    monkeypatch.setattr("policyforge.cli.documents.ACCEPTABLE_USE_CROSSWALK", walk)
    fake = fake or _Fake()
    cfg = {"org": {"name": "Acme", "industry": "Health"}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli_mod, "get_provider", lambda _cfg: fake)
    return fake


def _run(*args):
    return CliRunner().invoke(cli_mod.cli, ["generate", "--document", "acceptable-use", *args])


def _read(tmp_path, name):
    return (tmp_path / "docs" / "policies" / name).read_text(encoding="utf-8")


def _front(text):
    return yaml.safe_load(text.split("---")[1])


def _h2(text):
    return re.findall(r"^## (.+)$", text, re.MULTILINE)


def _appendix(text):
    lines = text.split("## Traceability", 1)[1].splitlines()
    return {
        m.group(1): m.group(2)
        for line in lines
        if (m := re.match(r"^- \*\*(.+?)\*\*: (.+)$", line))
    }


# ---- what is written ------------------------------------------------------------


def test_two_documents_hold_every_section_in_order(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    result = _run()
    assert result.exit_code == 0, result.output

    aup = _read(tmp_path, "acceptable-use.md")
    assert aup.split("---", 2)[2].lstrip().startswith("# Acceptable Use Policy\n")
    assert _h2(aup) == [*AUP_SECTIONS, "Acknowledgment of Receipt", "Traceability"]
    addendum = _read(tmp_path, "privileged-user-rules.md")
    assert addendum.split("---", 2)[2].lstrip().startswith("# Privileged User Rules\n")
    assert _h2(addendum) == [*ADDENDUM_SECTIONS, "Traceability"]


@pytest.mark.parametrize("left_out", AUP_SECTIONS)
def test_a_section_the_draft_left_out_is_a_placeholder_and_named(tmp_path, monkeypatch, left_out):
    """Run both ways: every section is checked, by the hand-written list."""
    _project(tmp_path, monkeypatch, fake=_Fake(aup=_draft(AUP_SECTIONS, leave_out=(left_out,))))
    result = _run()
    assert result.exit_code == 0, result.output

    aup = _read(tmp_path, "acceptable-use.md")
    assert _h2(aup)[: len(AUP_SECTIONS)] == AUP_SECTIONS
    assert f"[{left_out}: rules to be written]" in aup
    assert f"gave no rules for {left_out}," in result.output


def test_the_acknowledgment_block_ends_the_main_document_only(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _run()
    aup = _read(tmp_path, "acceptable-use.md")
    block = aup.split("## Acknowledgment of Receipt", 1)[1].split("## Traceability")[0]
    for field in ("Name:", "Date:", "Signature:", "Version of this document:"):
        assert f"- {field}" in block
    assert "Acknowledgment of Receipt" not in _read(tmp_path, "privileged-user-rules.md")


def test_the_model_is_shown_each_sections_control_text(tmp_path, monkeypatch):
    fake = _project(tmp_path, monkeypatch)
    _run()
    rows = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows["controls"]
    pl4 = next(r for r in rows if r["control_id"] == "PL-4")
    social = next(e for e in pl4["enhancements"] if e["enhancement_id"] == "PL-4(1)")
    ir6 = next(r for r in rows if r["control_id"] == "IR-6")

    aup_prompt, addendum_prompt = fake.prompts
    for title in AUP_SECTIONS:
        assert f"### Section: {title}\n" in aup_prompt
    assert pl4["control_statement"].strip() in aup_prompt
    assert social["description"].strip() in aup_prompt
    assert ir6["control_statement"].strip() in aup_prompt
    assert "### Section: Privileged Access" in addendum_prompt


def test_the_body_carries_no_tag_and_the_run_says_how_many_it_removed(tmp_path, monkeypatch):
    draft = _draft(AUP_SECTIONS).replace(
        "rule for scope.", "rule for scope. [NIST 800-53 PL-4] [HIPAA 164.310(b)]"
    )
    _project(tmp_path, monkeypatch, fake=_Fake(aup=draft))
    result = _run()

    body = _read(tmp_path, "acceptable-use.md").split("## Traceability")[0]
    assert SOURCE_TAG_RE.findall(body) == []
    assert "removed 2 citation tag(s)" in result.output


def test_what_the_draft_wrote_outside_its_sections_is_dropped_and_named(tmp_path, monkeypatch):
    draft = "# Our AUP\n\nIntro text.\n\n" + _draft(
        AUP_SECTIONS, extra="\n## Related Standards\n\n- Access Control Standard\n"
    )
    _project(tmp_path, monkeypatch, fake=_Fake(aup=draft))
    result = _run()

    aup = _read(tmp_path, "acceptable-use.md")
    assert "Related Standards" not in aup and "Intro text." not in aup
    assert "'Related Standards'" in result.output
    assert "'Our AUP'" in result.output  # an H1 that is not the title, with its text


# ---- the appendix ---------------------------------------------------------------


def test_the_appendix_traces_every_section_to_the_fixed_set_and_the_crosswalk(
    tmp_path, monkeypatch
):
    _project(tmp_path, monkeypatch)
    _run()
    aup = _appendix(_read(tmp_path, "acceptable-use.md"))
    addendum = _appendix(_read(tmp_path, "privileged-user-rules.md"))

    assert list(aup) == AUP_SECTIONS and list(addendum) == ADDENDUM_SECTIONS
    named: set[str] = set()
    for entry in [*aup.values(), *addendum.values()]:
        nist = entry.split(";")[0]
        assert nist.startswith("NIST SP 800-53 ")
        ids = set(nist.removeprefix("NIST SP 800-53 ").split(", "))
        assert ids and ids <= FIXED, entry
        named |= ids
    assert named == FIXED
    assert aup["Scope"] == "NIST SP 800-53 PL-4; hipaa 164.310(b), hitrust 01.a"
    assert aup["Incident Reporting"] == "NIST SP 800-53 IR-6; hipaa 164.308(a)(6)(ii)"
    assert aup["Sanctions"] == "NIST SP 800-53 PS-8"


def test_a_licensed_catalogs_ids_appear_only_from_the_organisations_crosswalk(
    tmp_path, monkeypatch
):
    _project(tmp_path, monkeypatch, crosswalk={"PL-4": {"hipaa": ["164.310(b)"]}})
    _run()
    for name in ("acceptable-use.md", "privileged-user-rules.md"):
        assert "hitrust" not in _read(tmp_path, name).casefold()


def test_without_a_crosswalk_the_appendix_lists_800_53_and_says_so(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, crosswalk=None)
    result = _run()
    assert result.exit_code == 0, result.output
    aup = _read(tmp_path, "acceptable-use.md")
    assert _appendix(aup)["Scope"] == "NIST SP 800-53 PL-4"
    assert "No crosswalk was read" in aup
    assert "No crosswalk at" in result.output


# ---- frontmatter, owner, refusals --------------------------------------------------


def test_the_owner_is_the_pl4_topics_and_no_topic_is_written(tmp_path, monkeypatch):
    """A `topic:` would make edit-topic read both files as that topic's Policy."""
    _project(tmp_path, monkeypatch)
    result = _run()
    for name in ("acceptable-use.md", "privileged-user-rules.md"):
        front = _front(_read(tmp_path, name))
        assert front["tier"] == "policy"
        assert front["owner"] == "Security Program / GRC"
        assert "topic" not in front
    assert "Included the AI Use section: no 'ai' scope fact exists yet." in result.output


def test_both_documents_record_the_catalog_they_were_drafted_from(tmp_path, monkeypatch):
    """443 part 2: the catalog's hash, and `none` for a catalog in no library."""
    import hashlib

    _project(tmp_path, monkeypatch)
    assert _run().exit_code == 0
    digest = hashlib.sha256(CATALOG.read_bytes()).hexdigest()
    for name in ("acceptable-use.md", "privileged-user-rules.md"):
        stamp = _front(_read(tmp_path, name))["generated_by"]
        assert [(c["sha256"], c["library"]) for c in stamp["catalogs"]] == [(digest, "none")]
        assert "catalogs_not_recorded" not in stamp


def test_without_a_registry_the_example_registrys_owner_is_not_used(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, registry=None)
    (tmp_path / "config" / "topics.example.yaml").write_text(REGISTRY, encoding="utf-8")
    result = _run()
    assert result.exit_code == 0, result.output
    assert "owner" not in _front(_read(tmp_path, "acceptable-use.md"))
    assert "Security Program / GRC" not in _read(tmp_path, "acceptable-use.md")
    assert "[Responsible Team]" in result.output


def test_a_model_failure_fails_the_run_and_writes_nothing(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, fake=_Fake(fail=True))
    result = _run()
    assert result.exit_code != 0
    assert not (tmp_path / "docs").exists()


def test_the_kind_takes_no_synthesis_standard_or_policy(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    (tmp_path / "s.md").write_text("x", encoding="utf-8")
    for extra in (["--synthesis", "s.md"], ["--standard", "s.md"], ["--policy", "P"]):
        result = _run(*extra)
        assert result.exit_code == 2, (extra, result.output)
        assert "takes neither" in result.output


def test_a_decided_parameter_reaches_the_draft_as_its_value(tmp_path, monkeypatch):
    """`config/parameters.yaml` fills a control's [Assignment: ...] before the
    model sees it, as `synthesize` does."""
    fake = _project(tmp_path, monkeypatch)
    ledger = tmp_path / "config" / "parameters.yaml"
    ledger.write_text(
        "parameters:\n  PL-4/frequency:\n    value: every twelve months\n"
        "    rationale: test\n    source: test\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("policyforge.cli.documents.ACCEPTABLE_USE_PARAMETERS", ledger)
    result = _run()
    assert result.exit_code == 0, result.output
    assert "Applied 1 recorded parameter decision(s)" in result.output
    assert "Review and update the rules of behavior every twelve months" in fake.prompts[0]


# ---- heading forms a model writes (1d on PR 453) --------------------------------


def _headed(form):
    return "\n".join(
        f"{form(i + 1, title)}\n\n- Follow the rule for {title.lower()}.\n"
        for i, title in enumerate(AUP_SECTIONS)
    )


@pytest.mark.parametrize(
    "form",
    [
        lambda n, t: f"## Section {n}: {t}",
        lambda n, t: f"## {t} Rules",
        lambda n, t: f"## **{t}**",
        lambda n, t: f"# {t}",
        lambda n, t: f"### {n}. {t}",
    ],
    ids=["section-n", "title-plus-word", "bold", "h1-per-section", "h3-numbered"],
)
def test_every_section_is_read_whatever_heading_form_the_draft_uses(tmp_path, monkeypatch, form):
    _project(tmp_path, monkeypatch, fake=_Fake(aup=_headed(form)))
    result = _run()
    assert result.exit_code == 0, result.output

    aup = _read(tmp_path, "acceptable-use.md")
    assert "rules to be written" not in aup
    for title in AUP_SECTIONS:
        assert f"- Follow the rule for {title.lower()}." in aup
    assert "dropped" not in result.output


def test_the_documents_own_title_over_its_sections_is_not_reported(tmp_path, monkeypatch):
    draft = "# Acceptable Use Policy\n\n" + _draft(AUP_SECTIONS)
    _project(tmp_path, monkeypatch, fake=_Fake(aup=draft))
    result = _run()
    assert result.exit_code == 0, result.output
    assert "dropped" not in result.output


def test_a_draft_that_names_no_section_fails_and_writes_nothing(tmp_path, monkeypatch):
    draft = "## Introduction\n\nWelcome.\n\n## Our Values\n\nBe kind.\n"
    _project(tmp_path, monkeypatch, fake=_Fake(aup=draft))
    result = _run()
    assert result.exit_code == 1, result.output
    assert "named none of its sections" in result.output
    assert "'Introduction'" in result.output
    assert not (tmp_path / "docs").exists()


def test_text_before_any_heading_is_dropped_and_named(tmp_path, monkeypatch):
    _project(
        tmp_path, monkeypatch, fake=_Fake(aup="Here is the policy.\n\n" + _draft(AUP_SECTIONS))
    )
    result = _run()
    assert "Here is the policy." not in _read(tmp_path, "acceptable-use.md")
    assert "text before the first section" in result.output


# ---- headings that name a section in other words (b5 on PR 453) -----------------


@pytest.mark.parametrize(
    ("section", "heading"),
    [
        # Hand-written: what a model writes for each.
        ("Email, Internet, and Social Media", "Email, Internet and Social Media"),
        ("Email, Internet, and Social Media", "Email, Internet & Social Media"),
        ("Email, Internet, and Social Media", "Section: Email, Internet, and Social Media"),
        ("Email, Internet, and Social Media", "Email and Social Media"),
        ("Remote Work and Travel", "Remote Work & Travel"),
        ("Link to Training", "Training"),
        ("AI Use", "Use of AI"),
        ("Acknowledgment", "Acknowledgement"),
        ("Credentials", "Credential Rules"),
    ],
)
def test_a_heading_in_other_words_keeps_its_body(tmp_path, monkeypatch, section, heading):
    """One heading varied; the draft's count differs from the document's by
    one dropped section, so position can't be what reads it."""
    others = [t for t in AUP_SECTIONS if t != section and t != "Sanctions"]
    draft = _draft(others) + f"\n## {heading}\n\n- The varied section's rule.\n"
    _project(tmp_path, monkeypatch, fake=_Fake(aup=draft))
    result = _run()
    assert result.exit_code == 0, result.output

    aup = _read(tmp_path, "acceptable-use.md")
    body = aup.split(f"## {section}\n", 1)[1].split("\n## ")[0]
    assert "- The varied section's rule." in body
    assert "gave no rules for Sanctions," in result.output
    assert "read by position" not in result.output


def test_a_heading_in_its_position_is_read_as_that_section_and_named(tmp_path, monkeypatch):
    """`Prohibited Uses` shares no title's words: with exactly 14 headings in
    order, it is the fourth section."""
    draft = _draft(AUP_SECTIONS).replace("## 4. Prohibited Activities", "## 4. Prohibited Uses")
    _project(tmp_path, monkeypatch, fake=_Fake(aup=draft))
    result = _run()

    aup = _read(tmp_path, "acceptable-use.md")
    body = aup.split("## Prohibited Activities\n", 1)[1].split("\n## ")[0]
    assert "- Follow the rule for prohibited activities." in body
    assert "read by position, the headings naming no section: '4. Prohibited Uses' as " in (
        result.output
    )


def test_a_renamed_heading_in_a_gap_of_one_is_read_with_a_foreign_one_elsewhere(
    tmp_path, monkeypatch
):
    """The gap between Personal Use and Monitoring Notice holds one heading for
    one section; `Contacts` at the end is in no gap, and is dropped."""
    draft = _draft(AUP_SECTIONS, extra="\n## Contacts\n\n- Ask us.\n").replace(
        "## 4. Prohibited Activities", "## 4. Prohibited Uses"
    )
    _project(tmp_path, monkeypatch, fake=_Fake(aup=draft))
    result = _run()
    aup = _read(tmp_path, "acceptable-use.md")
    body = aup.split("## Prohibited Activities\n", 1)[1].split("\n## ")[0]
    assert "- Follow the rule for prohibited activities." in body
    assert "'4. Prohibited Uses' as Prohibited Activities" in result.output
    assert "- Ask us." not in aup and "'Contacts'" in result.output


def test_one_section_left_out_and_a_foreign_heading_added_is_not_read_by_position(
    tmp_path, monkeypatch
):
    """b5's case from the fuzz (6381): Incident Reporting left out, `Roles and
    Responsibilities` added after Scope, and the email section headed in words
    its title doesn't hold. Fourteen headings, so a count over the whole draft
    matched and filed the email rules under Incident Reporting."""
    titles = [t for t in AUP_SECTIONS if t != "Incident Reporting"]
    draft = _draft(titles).replace(
        "Email, Internet, and Social Media\n", "Email and Internet Use\n"
    )
    draft = draft.replace(
        "\n## 2. Acknowledgment",
        "\n## Roles and Responsibilities\n\n- Everyone has a role.\n\n## 2. Acknowledgment",
    )
    _project(tmp_path, monkeypatch, fake=_Fake(aup=draft))
    result = _run()

    aup = _read(tmp_path, "acceptable-use.md")
    incident = aup.split("## Incident Reporting\n", 1)[1].split("\n## ")[0]
    assert "email" not in incident.casefold()
    assert "[Incident Reporting: rules to be written]" in aup
    assert "read by position" not in result.output


def test_the_run_names_the_placeholders_left_unfilled(tmp_path, monkeypatch):
    draft = _draft(AUP_SECTIONS).replace(
        "rule for incident reporting.", "rule for incident reporting within [Reporting Deadline]."
    )
    _project(tmp_path, monkeypatch, fake=_Fake(aup=draft))
    result = _run()
    assert "acceptable-use.md: 1 placeholder(s) left unfilled: Reporting Deadline" in result.output


# ---- the `ai` scope fact (22, part 3) --------------------------------------------


def _declare_ai(monkeypatch, value):
    cfg = {"org": {"name": "Acme", "industry": "Health"}, "programme": {"ai": value}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: cfg)


def test_ai_none_leaves_the_ai_use_section_out_and_says_so(tmp_path, monkeypatch):
    fake = _project(tmp_path, monkeypatch)
    _declare_ai(monkeypatch, "none")
    without = [s for s in AUP_SECTIONS if s != "AI Use"]
    fake.aup = _draft(without)

    result = _run()

    assert result.exit_code == 0, result.output
    aup = _read(tmp_path, "acceptable-use.md")
    assert _h2(aup) == [*without, "Acknowledgment of Receipt", "Traceability"]
    assert "AI Use" not in _appendix(aup)
    assert "Left out the AI Use section: `programme.ai` is none." in result.output
    # The model is not asked for it either (the user arm below shows the
    # prompt does name the section when it is kept).
    assert all("AI Use" not in p for p in fake.prompts)


@pytest.mark.parametrize("value", ["user", "builder"])
def test_a_user_or_builder_of_ai_keeps_the_section(tmp_path, monkeypatch, value):
    fake = _project(tmp_path, monkeypatch)
    _declare_ai(monkeypatch, value)

    result = _run()

    assert result.exit_code == 0, result.output
    assert "AI Use" in _h2(_read(tmp_path, "acceptable-use.md"))
    assert any("AI Use" in p for p in fake.prompts)
    assert f"Included the AI Use section: `programme.ai` is {value}." in result.output


def test_a_bad_ai_value_refuses_naming_the_key(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    _declare_ai(monkeypatch, "both")

    result = _run()

    assert result.exit_code == 2, result.output
    assert "programme.ai" in result.output


# ---- a bracketed sentence is not a placeholder to fill (267) ----------------------


def test_the_aup_lists_bracketed_prose_under_its_own_heading(tmp_path, monkeypatch):
    """The acceptable-use site's own line (ba on PR 463): a sentence the draft
    put in brackets is left as written and listed apart from the placeholders
    to fill, with the document's name in front, as the unfilled line has it."""
    fake = _project(tmp_path, monkeypatch)
    fake.aup = _draft(AUP_SECTIONS).replace(
        "- Follow the rule for personal use.",
        "- Follow the rule for personal use. [Access to the ticketing platform]",
    )

    result = _run()

    assert result.exit_code == 0, result.output
    assert (
        "acceptable-use.md: Bracketed prose, left as written (1): Access to the ticketing platform"
    ) in result.output
    assert "[Access to the ticketing platform]" in _read(tmp_path, "acceptable-use.md")
