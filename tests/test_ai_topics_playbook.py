"""AI topics draw on the Playbook for their Standard only, and own none of it (#301).

The Core states outcomes; NIST put the actions in the Playbook, keyed by the
same subcategory ids. So a Standard for an AI topic may draw on "the actions
NIST suggests" for the subcategories that topic anchors -- derived, not
listed, so nothing is selected by hand (80's ruling).

Three things are held here, each from the shipped catalogs:
- the Standard's input carries exactly its subcategories' actions;
- no Policy or Procedure input carries any Playbook action, because neither
  is ever given the block (a tier flag, not text removal);
- `/coverage` is unchanged: the Playbook is not something a topic owns.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from policyforge.generate.policy_writer import (
    OrgContext,
    TopicContext,
    generate_policy,
    generate_procedure,
    generate_standard,
)
from policyforge.ingest.schema import load_controls
from policyforge.mapping.crosswalk import anchors_a_topic
from policyforge.synthesis.merge import playbook_actions, read_synthesis, write_synthesis

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"
CONTROLS = [c for p in sorted(FRAMEWORKS.glob("*/controls.json")) for c in load_controls(p)]
TOPICS = yaml.safe_load((ROOT / "config" / "topics.example.yaml").read_text(encoding="utf-8"))[
    "topics"
]
AI = re.compile(r"^(Govern|Map|Measure|Manage)\s+\d")
AI_TOPICS = [t for t in TOPICS if any(AI.match(str(a)) for a in t.get("nist_controls") or [])]

#: Every Playbook action id the catalog ships.
ALL_ACTION_IDS = {
    e.enhancement_id
    for c in CONTROLS
    if c.framework == "NIST AI RMF Playbook"
    for e in c.enhancements
}


def _compliant(entries: list[dict]) -> str:
    """A draft the Playbook repair step accepts as it stands: one sentence
    per subcategory, citing all its actions. A Standard that would fail it
    is regenerated or refused before it is returned (test_playbook_repair),
    so a fake meant to test the INPUT has to answer with a passing draft."""
    lines = ["# Draft", ""]
    for entry in entries:
        ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
        lines += [
            f"NIST suggests, among its {len(entry['actions'])} actions for "
            f"{entry['subcategory']}, maintaining an inventory. [{ids}]",
            "",
        ]
    return "\n".join(lines)


class _Capture:
    """A provider that records the prompt and returns a fixed document."""

    def __init__(self, text: str = "# Draft\n\nNIST suggests an inventory.\n"):
        self.text = text
        self.prompts: list[str] = []

    def generate(self, **kwargs):
        from policyforge.llm.base import LLMResponse

        self.prompts.append(kwargs.get("prompt", ""))
        return LLMResponse(text=self.text, model="fake")


def _context(topic: dict) -> TopicContext:
    return TopicContext(
        name=topic["name"],
        owner="AI Team",
        playbook=playbook_actions(topic["nist_controls"], CONTROLS),
    )


def _mentioned(prompt: str) -> set[str]:
    return {action for action in ALL_ACTION_IDS if f"Playbook {action}]" in prompt}


def test_the_premise():
    """Five AI topics, and a Playbook catalog with actions to derive from."""
    assert len(AI_TOPICS) == 5
    assert len(ALL_ACTION_IDS) == 459


@pytest.mark.parametrize("topic", AI_TOPICS, ids=lambda t: t["name"])
def test_the_standard_input_carries_exactly_its_subcategories_actions(topic):
    context = _context(topic)
    expected = {a["id"] for entry in context.playbook for a in entry["actions"]}
    provider = _Capture(_compliant(context.playbook))

    generate_standard(
        "- An AI inventory is maintained. [NIST AI RMF GOVERN-1.6]",
        OrgContext(name="Acme Health", industry="Healthcare"),
        provider,
        topic=context,
    )

    (prompt,) = provider.prompts
    assert expected, "every AI topic anchors at least one subcategory with actions"
    assert _mentioned(prompt) == expected
    for entry in context.playbook:
        assert f"Subcategory {entry['subcategory']} ({len(entry['actions'])} actions):" in prompt


@pytest.mark.parametrize("tier", ["policy", "procedure"])
@pytest.mark.parametrize("topic", AI_TOPICS, ids=lambda t: t["name"])
def test_no_policy_or_procedure_input_carries_any_playbook_action(topic, tier):
    """**Structural** (80): the block is never given to these tiers, even
    though their topic context carries it."""
    context = _context(topic)
    assert context.playbook, "the premise: the context does carry the Playbook"
    provider = _Capture()
    generator = generate_policy if tier == "policy" else generate_procedure

    generator(
        "- An AI inventory is maintained. [NIST AI RMF GOVERN-1.6]",
        OrgContext(name="Acme Health", industry="Healthcare"),
        provider,
        standard_title="AI Standard",
        topic=context,
    )

    (prompt,) = provider.prompts
    assert _mentioned(prompt) == set()
    assert "NIST AI RMF Playbook" not in prompt


def test_a_topic_without_ai_anchors_gets_no_playbook_block():
    other = next(t for t in TOPICS if t not in AI_TOPICS)

    assert playbook_actions(other["nist_controls"], CONTROLS) == []


def test_a_subcategory_anchor_derives_only_that_subcategory():
    (entry,) = playbook_actions(["Govern 1.6"], CONTROLS)

    assert entry["subcategory"] == "Govern 1.6"
    assert all(a["id"].startswith("Govern 1.6 Action") for a in entry["actions"])


def test_the_playbook_rides_in_the_frontmatter_not_the_body():
    """The body is what every tier reads; the Playbook must not be in it."""
    context = _context(AI_TOPICS[0])
    text = write_synthesis("- An inventory is maintained.", topic="t", playbook=context.playbook)

    metadata, body = read_synthesis(text)
    assert metadata["playbook"] == context.playbook
    assert "Playbook" not in body


def test_the_playbook_is_not_something_a_topic_owns():
    """`/coverage` unchanged is 80's test. Measured byte-identical on the
    shipped catalogs before and after; pinned here at its cause."""
    assert not anchors_a_topic("NIST AI RMF Playbook")
    assert anchors_a_topic("NIST AI RMF")


# --------------------------------------------------------------------------
# Through the real commands: the wiring, which the functions above cannot see
# --------------------------------------------------------------------------


class _Recorder:
    """A provider for the CLI: records every prompt, answers with fixed text."""

    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    def generate(self, **kwargs):
        from policyforge.llm.base import LLMResponse

        self.prompts.append(kwargs.get("prompt", ""))
        return LLMResponse(text=self.text, model="fake")


def test_synthesize_writes_the_playbook_block_into_the_frontmatter(tmp_path, monkeypatch):
    """The command, not the helper: a mutation that stopped passing the block
    to `write_synthesis` left every function-level test green."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")
    fake = _Recorder("- AI legal requirements are understood. [NIST AI RMF GOVERN-1.1]\n")
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize", "--topic", "AI Governance", "--nist-controls", "Govern 1",
            "--controls", str(FRAMEWORKS / "nist-ai-rmf" / "controls.json"),
            "--controls", str(FRAMEWORKS / "nist-ai-rmf-playbook" / "controls.json"),
            "--crosswalk", str(crosswalk), "--out-dir", str(tmp_path / "synthesis"),
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    metadata, body = read_synthesis(
        (tmp_path / "synthesis" / "ai-governance.md").read_text(encoding="utf-8")
    )
    assert metadata["playbook"] == playbook_actions(["Govern 1"], CONTROLS)
    assert "Playbook" not in body
    assert "NIST AI RMF Playbook" not in fake.prompts[0], "the synthesis prompt never sees it"


@pytest.mark.parametrize("with_playbook", [True, False])
def test_synthesize_says_what_it_retrieved_or_why_not(tmp_path, monkeypatch, with_playbook):
    """1d on #359: an empty Playbook block read the same whether the topic has
    no AI RMF anchors or the Playbook catalog was simply not loaded. The
    command now says how many it retrieved, or warns that it retrieved none."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")
    fake = _Recorder("- AI legal requirements are understood. [NIST AI RMF GOVERN-1.1]\n")
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)
    catalogs = ["nist-ai-rmf"] + (["nist-ai-rmf-playbook"] if with_playbook else [])
    args = ["synthesize", "--topic", "AI Governance", "--nist-controls", "Govern 1",
            "--crosswalk", str(crosswalk), "--out-dir", str(tmp_path / "synthesis")]  # fmt: skip
    for catalog in catalogs:
        args += ["--controls", str(FRAMEWORKS / catalog / "controls.json")]

    result = CliRunner().invoke(cli_mod.cli, args)

    assert result.exit_code == 0, result.output
    expected = playbook_actions(["Govern 1"], CONTROLS)
    if with_playbook:
        actions = sum(len(e["actions"]) for e in expected)
        assert f"{len(expected)} subcategories, {actions} suggested actions" in result.output
    else:
        assert "no NIST AI RMF Playbook catalog is loaded" in result.output


def test_synthesize_is_silent_for_a_topic_with_no_ai_rmf_anchors(tmp_path, monkeypatch):
    """No warning where none applies: an 800-53 topic, no Playbook loaded."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")
    fake = _Recorder("- Accounts are managed. [NIST 800-53 AC-2]\n")
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    result = CliRunner().invoke(
        cli_mod.cli,
        ["synthesize", "--topic", "Access", "--nist-controls", "AC-2",
         "--controls", str(FRAMEWORKS / "nist-800-53-r5" / "controls.json"),
         "--crosswalk", str(crosswalk), "--out-dir", str(tmp_path / "synthesis")],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "Playbook" not in result.output


@pytest.mark.parametrize("tier", ["standard", "procedure"])
def test_generate_gives_the_block_to_the_standard_only(tmp_path, monkeypatch, tier):
    """The command reads the block from the frontmatter into the topic
    context; only the Standard's prompt carries it."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    synthesis = tmp_path / "ai-governance.md"
    synthesis.write_text(
        write_synthesis(
            "- AI legal requirements are understood. [NIST AI RMF GOVERN-1.1]",
            topic="AI Governance",
            playbook=playbook_actions(["Govern 1"], CONTROLS),
        ),
        encoding="utf-8",
    )
    standard = tmp_path / "standard.md"
    standard.write_text("# AI Governance Standard\n", encoding="utf-8")
    fake = _Recorder(
        _compliant(playbook_actions(["Govern 1"], CONTROLS))
        if tier == "standard"
        else "# AI Governance\n\nNIST suggests an inventory.\n"
    )
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"org": {"name": "Acme", "industry": "Health"}}
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: fake)

    args = [
        "generate", "--tier", tier, "--synthesis", str(synthesis),
        "--out", str(tmp_path / "out.md"),
        "--history-dir", str(tmp_path / "history"),
    ]  # fmt: skip
    if tier != "standard":
        args += ["--standard", str(standard)]
    result = CliRunner().invoke(cli_mod.cli, args)

    assert result.exit_code == 0, result.output
    carried = _mentioned(fake.prompts[0])
    if tier == "standard":
        expected = {a["id"] for e in playbook_actions(["Govern 1"], CONTROLS) for a in e["actions"]}
        assert carried == expected
    else:
        assert carried == set()


# --------------------------------------------------------------------------
# The prompt's sentence form and the #309 gate cannot disagree
# --------------------------------------------------------------------------


def test_the_prompts_own_sentence_form_passes_the_gate():
    """**Measured failure this pins** (#301). The first wording, "Among the N
    actions NIST suggests for ...", starts with "Among"; the gate reads the
    subject from the sentence start; a real run flagged all 17 compliant
    sentences. The form is one constant, filled in here and gated."""
    from policyforge.content.deontic import playbook_obligations
    from policyforge.generate.policy_writer import _STANDARD_SYSTEM_PROMPT, PLAYBOOK_SENTENCE_FORM

    assert PLAYBOOK_SENTENCE_FORM in _STANDARD_SYSTEM_PROMPT
    sentence = (
        PLAYBOOK_SENTENCE_FORM.replace(" N ", " 7 ")
        .replace("<subcategory>", "Govern 1.4")
        .replace("...", "documenting AI actor contact information.")
    )
    tag = "[NIST AI RMF Playbook Govern 1.4 Action 1]"

    assert playbook_obligations(f"{sentence} {tag}\n") == []


def test_the_prompts_merged_tag_is_one_the_gate_reads_as_the_playbook():
    """**Measured failure this pins** (#301). The rule said only "several may
    share one tag, separated by |"; glm wrote `[NIST AI RMF Playbook Govern
    1.1 Action 1 | Govern 1.1 Action 2]` in 17 of 17 sentences of one
    Standard. The gate reads each part alone, so none of the 17 was a
    Playbook citation to it, and "Acme Health must ..." under that tag
    passed. The prompt now shows a merged tag; the gate must read that tag
    as the Playbook's, or it is teaching a form the gate cannot see."""
    from policyforge.content.deontic import analyze, playbook_obligations
    from policyforge.generate.policy_writer import (
        _STANDARD_SYSTEM_PROMPT,
        PLAYBOOK_MERGED_TAG_EXAMPLE,
    )

    assert PLAYBOOK_MERGED_TAG_EXAMPLE in _STANDARD_SYSTEM_PROMPT
    assert PLAYBOOK_MERGED_TAG_EXAMPLE.count("|") >= 1, "the premise: it is a MERGED tag"
    (statement,) = analyze(f"NIST suggests documenting contacts. {PLAYBOOK_MERGED_TAG_EXAMPLE}\n")
    assert statement.cites_only_the_playbook

    wrong = f"Acme Health must document contacts. {PLAYBOOK_MERGED_TAG_EXAMPLE}\n"
    assert len(playbook_obligations(wrong, ("Acme Health",))) == 1


def test_the_same_form_with_the_organization_as_subject_is_refused():
    """The other arm, so the form test above is known to distinguish forms:
    the prompt's own sentence, filled the same way, with the organization in
    NIST's place. (This arm used the first wording, "Among the 7 actions NIST
    suggests for ...", until #319 made that opener one of four allowed shapes;
    it now passes, correctly, so it no longer distinguishes anything.)"""
    from policyforge.content.deontic import playbook_obligations
    from policyforge.generate.policy_writer import PLAYBOOK_SENTENCE_FORM

    assert PLAYBOOK_SENTENCE_FORM.startswith("NIST suggests"), "the premise the swap relies on"
    sentence = (
        PLAYBOOK_SENTENCE_FORM.replace("NIST suggests", "Acme Health adopts", 1)
        .replace(" N ", " 7 ")
        .replace("<subcategory>", "Govern 1.4")
        .replace("...", "documenting AI actor contact information.")
    )
    tag = "[NIST AI RMF Playbook Govern 1.4 Action 1]"

    assert len(playbook_obligations(f"{sentence} {tag}\n", ("Acme Health",))) == 1
