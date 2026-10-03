"""The Standard's Playbook sentences are repaired, or the Standard is refused (#301).

Measured on production models, a first draft failed two ways: sonnet wrote
"must be decommissioned" where NIST wrote "will need to be ...
decommissioned" (the gate refused it), and glm left out Map 5.1 entirely.
80's ruling: regenerate what fails, bounded, and refuse loudly if it still
fails. These run against the shipped Playbook catalog; only the model is
faked, and each fake records how many times it was asked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.generate.playbook_repair import (
    MISSING_SECTION,
    REPAIR_ATTEMPTS,
    PlaybookRepairFailed,
    playbook_problems,
    repair_standard,
)
from policyforge.ingest.schema import load_controls
from policyforge.synthesis.merge import playbook_actions

ROOT = Path(__file__).resolve().parent.parent
CONTROLS = [
    c
    for p in sorted((ROOT / "data" / "frameworks").glob("*/controls.json"))
    for c in load_controls(p)
]
ACTORS = ("Acme Health",)


def _entries(*subs: str) -> list[dict]:
    return playbook_actions(list(subs), CONTROLS)


def _good(entry: dict, text: str = "maintaining awareness of legal requirements") -> str:
    """A sentence in the prompt's form, citing every action of `entry`."""
    ids = [a["id"] for a in entry["actions"]]
    tag = "[" + " | ".join(f"NIST AI RMF Playbook {i}" for i in ids) + "]"
    return f"NIST suggests, among its {len(ids)} actions for {entry['subcategory']}, {text}. {tag}"


class _Model:
    """Answers with each of `replies` in turn, the last one repeated."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def generate(self, **kwargs):
        from policyforge.llm.base import LLMResponse

        self.prompts.append(kwargs.get("prompt", ""))
        index = min(len(self.prompts) - 1, len(self.replies) - 1)
        return LLMResponse(text=self.replies[index], model="fake")


def test_the_premise_the_entries_are_real():
    (entry,) = _entries("Govern 1.1")
    assert entry["subcategory"] == "Govern 1.1"
    assert len(entry["actions"]) >= 2


def test_a_clean_draft_is_returned_unchanged_and_costs_no_call():
    (entry,) = _entries("Govern 1.1")
    draft = f"# Standard\n\n## Legal\n\n{_good(entry)}\n"
    model = _Model("unused")

    assert repair_standard(draft, [entry], model, ACTORS) == draft
    assert model.prompts == []


def test_a_refused_sentence_is_replaced_and_every_other_line_kept():
    """Sonnet's measured case, rebuilt on Govern 1.1: an obligation where NIST
    wrote a suggestion. Only that line changes."""
    (entry,) = _entries("Govern 1.1")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    bad = f"Acme Health must maintain awareness of legal requirements. [{ids}]"
    draft = f"# Standard\n\n## Legal\n\nThe team keeps a register.\n\n{bad}\n\nClosing text.\n"
    assert playbook_problems(draft, [entry], ACTORS), "the premise: the gate refuses it"
    fixed = _good(entry)
    model = _Model(fixed)

    out = repair_standard(draft, [entry], model, ACTORS)

    assert out == draft.replace(bad, fixed)
    assert playbook_problems(out, [entry], ACTORS) == []
    assert len(model.prompts) == 1
    assert bad in model.prompts[0], "the model is told what was refused"


def test_a_missing_subcategory_is_appended():
    """Glm's measured case: a subcategory with no sentence at all."""
    first, second = _entries("Govern 1.1", "Govern 1.2")
    draft = f"# Standard\n\n## Legal\n\n{_good(first)}\n"
    model = _Model(_good(second, "defining key terms"))

    out = repair_standard(draft, [first, second], model, ACTORS)

    assert out.startswith(draft.rstrip("\n"))
    assert MISSING_SECTION in out
    assert playbook_problems(out, [first, second], ACTORS) == []


def test_a_heading_tag_is_removed_without_a_model_call():
    (entry,) = _entries("Govern 1.1")
    draft = (
        f"# Standard\n\n## Legal [NIST 800-53 AC-1 | NIST AI RMF Playbook Govern 1.1 Action 1]\n\n"
        f"{_good(entry)}\n"
    )
    model = _Model("unused")

    out = repair_standard(draft, [entry], model, ACTORS)

    assert "## Legal [NIST 800-53 AC-1]\n" in out, "the other framework's part survives"
    assert model.prompts == []
    assert playbook_problems(out, [entry], ACTORS) == []


def test_a_wrong_action_count_is_repaired():
    (entry,) = _entries("Govern 1.1")
    wrong = _good(entry).replace(f"among its {len(entry['actions'])} ", "among its 99 ")
    draft = f"# Standard\n\n{wrong}\n"
    assert [p.kind for p in playbook_problems(draft, [entry], ACTORS)] == ["count"]
    model = _Model(_good(entry))

    out = repair_standard(draft, [entry], model, ACTORS)

    assert playbook_problems(out, [entry], ACTORS) == []


AC2 = "The owner must review access quarterly [NIST 800-53 AC-2]."


@pytest.mark.parametrize("order", ["playbook-first", "ac2-first"])
def test_a_line_carrying_another_requirement_is_refused_not_rewritten(order):
    """1d on #359: a line-level repair deleted a binding requirement that
    shared the line, and returned success. The line is now left as written,
    and the Standard refused, whichever sentence comes first."""
    (entry,) = _entries("Govern 1.1")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    bad = f"Acme Health must maintain awareness. [{ids}]"
    line = f"{bad} {AC2}" if order == "playbook-first" else f"{AC2} {bad}"
    model = _Model(_good(entry))

    with pytest.raises(PlaybookRepairFailed):
        repair_standard(f"# Standard\n\n{line}\n", [entry], model, ACTORS)
    assert model.prompts == [], "no call is made for a line it cannot safely replace"


def test_conservation_refuses_a_repair_that_would_lose_an_obligation(monkeypatch):
    """80's ruling on #359: every cited obligation the gate did not flag is in
    the output, checked on the RESULT. With the isolation guard switched off,
    as a future repair path might skip it, 1d's line would be rewritten and
    the AC-2 requirement lost; the conservation check refuses it and names it."""
    import policyforge.generate.playbook_repair as repair

    monkeypatch.setattr(repair, "_is_whole_line", lambda document, index: True)
    (entry,) = _entries("Govern 1.1")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    draft = (
        f"# Standard\n\n{AC2}\n\n{AC2.replace('access', 'logs')} Acme Health must act. [{ids}]\n"
    )

    with pytest.raises(PlaybookRepairFailed) as refused:
        repair_standard(draft, [entry], _Model(_good(entry)), ACTORS)

    assert "would remove a cited obligation" in str(refused.value)
    assert "review logs quarterly" in str(refused.value)


def test_a_list_items_marker_is_kept():
    """A refused Playbook sentence written as a list item comes back as a
    list item, with its indentation and marker (1d on #359)."""
    (entry,) = _entries("Govern 1.1")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    draft = f"# Standard\n\n- Intro.\n  - Acme Health must maintain awareness. [{ids}]\n"
    fixed = _good(entry)

    out = repair_standard(draft, [entry], _Model(fixed), ACTORS)

    assert f"\n  - {fixed}\n" in out
    assert "- Intro." in out


def test_it_retries_a_bounded_number_of_times_then_refuses_loudly():
    """A model that keeps writing an obligation is asked exactly
    REPAIR_ATTEMPTS times, and the Standard is refused, not returned."""
    (entry,) = _entries("Govern 1.1")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    bad = f"Acme Health must maintain awareness. [{ids}]"
    model = _Model(bad)

    with pytest.raises(PlaybookRepairFailed) as refused:
        repair_standard(f"# Standard\n\n{bad}\n", [entry], model, ACTORS)

    assert len(model.prompts) == REPAIR_ATTEMPTS
    assert "Refusing to write this Standard" in str(refused.value)
    assert "Govern 1.1" in str(refused.value)


def test_a_candidate_citing_another_subcategorys_action_is_not_accepted():
    """Passing the gate is not enough: the replacement must cite this
    subcategory's real actions, or it would fix the flag and break the
    citation."""
    first, second = _entries("Govern 1.1", "Govern 1.2")
    borrowed = _good(first).replace("Govern 1.1 Action", "Govern 1.2 Action")
    draft = f"# Standard\n\n{_good(second, 'defining key terms')}\n"
    model = _Model(borrowed)

    with pytest.raises(PlaybookRepairFailed):
        repair_standard(draft, [first, second], model, ACTORS)
    assert len(model.prompts) == REPAIR_ATTEMPTS


def test_a_line_mixing_subcategories_is_refused_rather_than_rewritten():
    """Replacing that line would drop the other subcategory's sentence."""
    first, second = _entries("Govern 1.1", "Govern 1.2")
    ids1 = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in first["actions"])
    mixed = f"Acme Health must do both. [{ids1}] {_good(second, 'defining key terms')}"
    model = _Model(_good(first))

    with pytest.raises(PlaybookRepairFailed):
        repair_standard(f"# Standard\n\n{mixed}\n", [first, second], model, ACTORS)
    assert model.prompts == [], "no call is made for a line it cannot safely replace"


def test_generate_standard_repairs_before_returning():
    """Through the generator every caller uses: a first draft missing a
    subcategory comes back complete, and a topic without the Playbook is
    never touched."""
    from policyforge.generate.policy_writer import OrgContext, TopicContext, generate_standard

    first, second = _entries("Govern 1.1", "Govern 1.2")
    draft = f"# AI Standard\n\n## Legal\n\n{_good(first)}\n"
    model = _Model(draft, _good(second, "defining key terms"))

    out = generate_standard(
        "- Legal requirements are understood. [NIST AI RMF Govern 1.1]",
        OrgContext(name="Acme Health", industry="Healthcare"),
        model,
        topic=TopicContext(name="AI", owner="AI Team", playbook=[first, second]),
    )

    assert len(model.prompts) == 2, "one draft, one repair"
    assert playbook_problems(out, [first, second], ACTORS) == []

    plain = _Model("# Access Standard\n\nAccounts are managed. [NIST 800-53 AC-2]\n")
    generate_standard(
        "- Accounts are managed. [NIST 800-53 AC-2]",
        OrgContext(name="Acme Health", industry="Healthcare"),
        plain,
        topic=TopicContext(name="Access", owner="IAM"),
    )
    assert len(plain.prompts) == 1


def test_a_second_problem_for_the_same_subcategory_is_not_returned_unrepaired():
    """One subcategory, two failing lines: a refused sentence, and another
    sentence with the wrong action count. One repair is made per
    subcategory, so the second line survives it; the re-check of the whole
    repaired document is what refuses it, rather than returning a Standard
    that still fails."""
    (entry,) = _entries("Govern 1.1")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    bad = f"Acme Health must maintain awareness. [{ids}]"
    wrong = _good(entry, "tracking regulation").replace(
        f"among its {len(entry['actions'])} ", "among its 99 "
    )
    model = _Model(_good(entry))

    with pytest.raises(PlaybookRepairFailed):
        repair_standard(f"# Standard\n\n{bad}\n\n{wrong}\n", [entry], model, ACTORS)


def test_a_candidate_with_the_wrong_count_is_retried_not_accepted():
    """The candidate must state NIST's count: a wrong one is asked again."""
    (entry,) = _entries("Govern 1.1")
    wrong = _good(entry).replace(f"among its {len(entry['actions'])} ", "among its 2 ")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    model = _Model(wrong, _good(entry))

    out = repair_standard(f"# Standard\n\nAcme Health must act. [{ids}]\n", [entry], model, ACTORS)

    assert len(model.prompts) == 2
    assert playbook_problems(out, [entry], ACTORS) == []


def test_the_generate_command_gives_the_gate_the_configs_team_names(tmp_path, monkeypatch):
    """A team declared in config, committing in the clause after NIST's, is
    refused and repaired through the command, as `check` would refuse it."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod
    from policyforge.synthesis.merge import write_synthesis

    (entry,) = _entries("Govern 1.1")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    committing = (
        f"NIST suggests, among its {len(entry['actions'])} actions for Govern 1.1, "
        f"maintaining awareness, and AI Ops will maintain the register. [{ids}]"
    )
    synthesis = tmp_path / "ai.md"
    synthesis.write_text(
        write_synthesis(
            "- Legal requirements are understood. [NIST AI RMF Govern 1.1]",
            topic="AI",
            owner="AI Team",
            playbook=[entry],
        ),
        encoding="utf-8",
    )
    model = _Model(f"# AI Standard\n\n{committing}\n", _good(entry))
    config = {"org": {"name": "Acme", "industry": "Health", "teams": {"it_ops": "AI Ops"}}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: model)

    result = CliRunner().invoke(
        cli_mod.cli,
        ["generate", "--tier", "standard", "--synthesis", str(synthesis),
         "--out", str(tmp_path / "standard.md"), "--history-dir", str(tmp_path / "history")],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert len(model.prompts) == 2, "the team's clause was refused and repaired"
    assert "AI Ops will" not in (tmp_path / "standard.md").read_text(encoding="utf-8")


def test_the_generate_command_refuses_loudly_and_writes_nothing(tmp_path, monkeypatch):
    """Through the command: a model that cannot write the sentence gets a
    clear error naming the subcategory, exit non-zero, and no file."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod
    from policyforge.synthesis.merge import write_synthesis

    (entry,) = _entries("Govern 1.1")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    bad = f"Acme must maintain awareness. [{ids}]"
    synthesis = tmp_path / "ai.md"
    synthesis.write_text(
        write_synthesis(
            "- Legal requirements are understood. [NIST AI RMF Govern 1.1]",
            topic="AI",
            owner="AI Team",
            playbook=[entry],
        ),
        encoding="utf-8",
    )
    model = _Model(f"# AI Standard\n\n{bad}\n", bad)
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"org": {"name": "Acme", "industry": "Health"}}
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: model)
    out = tmp_path / "standard.md"

    result = CliRunner().invoke(
        cli_mod.cli,
        ["generate", "--tier", "standard", "--synthesis", str(synthesis), "--out", str(out),
         "--history-dir", str(tmp_path / "history")],
    )  # fmt: skip

    assert result.exit_code != 0
    assert "Refusing to write this Standard" in result.output
    assert "Govern 1.1" in result.output
    assert "Traceback" not in result.output
    assert not out.exists()
    assert len(model.prompts) == 1 + REPAIR_ATTEMPTS


def test_generate_standard_passes_the_organizations_names_to_the_gate():
    """A team named in config, opening the clause after NIST's, is the
    organization committing (#323); the repair sees it as the check does."""
    from policyforge.generate.policy_writer import OrgContext, TopicContext, generate_standard

    (entry,) = _entries("Govern 1.1")
    ids = " | ".join(f"NIST AI RMF Playbook {a['id']}" for a in entry["actions"])
    committing = (
        f"NIST suggests, among its {len(entry['actions'])} actions for Govern 1.1, "
        f"maintaining awareness, and AI Ops will maintain the register. [{ids}]"
    )
    model = _Model(f"# AI Standard\n\n{committing}\n", _good(entry))

    generate_standard(
        "- Legal requirements are understood. [NIST AI RMF Govern 1.1]",
        OrgContext(name="Acme Health", industry="Healthcare"),
        model,
        topic=TopicContext(name="AI", owner="AI Team", playbook=[entry]),
        org_actors=("AI Ops",),
    )

    assert len(model.prompts) == 2, "the team's clause was refused and repaired"
