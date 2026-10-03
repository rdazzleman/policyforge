"""A reply the model cut off at its budget is never written as a document.

Fifteen of eighty calls in the first 20-topic cost run ended with
stop_reason "length", and every one was written to disk as finished, with
exit 0. A Standard missing its last four requirements reads as a Standard.

The rule is enforced in one place, `llm/effort.py`, which every model call
goes through: a cut-off reply is retried once at a larger budget and refused
if still cut off. These tests hold the writers to it end to end - synthesize,
generate at each tier, edit-topic --apply, the Zardoz answer path - and hold
the harness to grading it as a failure. The CLI-level tests import nothing
that only exists on the fixed branch, so run against main they fail for the
right reason: a file written, exit 0.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.llm.base import LLMResponse

SRC = Path(__file__).resolve().parents[1] / "src" / "policyforge"


class CutProvider:
    """Answers with text that stopped at the budget, every time, and remembers
    the budget each call asked for."""

    CUT_TEXT = "# Standard\n\n- Requirement one [NIST AC-2]\n- Requirem"

    def __init__(self, text=CUT_TEXT, reason="length"):
        self.text = text
        self.reason = reason
        self.budgets: list[int | None] = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
        self.budgets.append(max_tokens)
        return LLMResponse(text=self.text, model="cut", stop_reason=self.reason)

    def check(self):
        return True


class FinishesOnRetry(CutProvider):
    """Cut off once, complete on the second, larger request."""

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
        self.budgets.append(max_tokens)
        if len(self.budgets) == 1:
            return LLMResponse(text="- half", model="m", stop_reason="max_tokens")
        return LLMResponse(text="- whole [NIST AC-2]", model="m", stop_reason="end_turn")


# ---- the flag ---------------------------------------------------------------


@pytest.mark.parametrize(
    "reason, cut",
    [
        ("length", True),
        ("max_tokens", True),
        ("MAX_TOKENS", True),
        (" max_tokens ", True),
        ("max_output_tokens", True),
        ("end_turn", False),
        ("stop", False),
        ("tool_use", False),
        (None, False),
        ("", False),
    ],
)
def test_every_vendors_spelling_of_cut_off_reads_as_truncated(reason, cut):
    assert LLMResponse(text="x", model="m", stop_reason=reason).truncated is cut


# ---- the helper -------------------------------------------------------------


def test_a_cut_off_reply_is_retried_once_at_twice_the_budget():
    from policyforge.llm import effort

    provider = FinishesOnRetry()

    response = effort.call(provider, system="S", prompt="P", max_tokens=3000)

    assert response.text == "- whole [NIST AC-2]"
    assert provider.budgets == [3000, 6000]


def test_a_reply_cut_off_twice_is_refused_with_the_budget_reached():
    from policyforge.llm import effort
    from policyforge.llm.base import TruncatedResponse

    provider = CutProvider()

    with pytest.raises(TruncatedResponse) as caught:
        effort.call(provider, system="S", prompt="P", max_tokens=4096)

    assert provider.budgets == [4096, 8192]
    assert caught.value.first_budget == 4096
    assert caught.value.budget == 8192
    assert caught.value.stop_reason == "length"
    assert "8192" in str(caught.value)
    assert "Nothing was written" in str(caught.value)


def test_the_retry_never_exceeds_the_ceiling_and_the_default_budget_is_known():
    from policyforge.llm import effort
    from policyforge.llm.base import TruncatedResponse

    at_ceiling = CutProvider()
    with pytest.raises(TruncatedResponse):
        effort.call(at_ceiling, system="S", prompt="P", max_tokens=effort.RETRY_CEILING)
    # Nothing larger to try, so one call, refused.
    assert at_ceiling.budgets == [effort.RETRY_CEILING]

    # A site that names no budget gets the provider's default on the first
    # call and twice the helper's known default on the retry.
    unnamed = CutProvider()
    with pytest.raises(TruncatedResponse):
        effort.call(unnamed, system="S", prompt="P")
    assert unnamed.budgets == [4096, effort.DEFAULT_MAX_TOKENS * 2]


def test_json_and_grounded_calls_are_held_to_the_same_rule():
    from policyforge.llm import effort
    from policyforge.llm.base import TruncatedResponse

    class Cut:
        def __init__(self):
            self.budgets = []

        def supports_schema(self):
            return True

        def supports_grounding(self):
            return True

        def generate_json(self, *, system, prompt, schema, max_tokens=4096, **kw):
            self.budgets.append(max_tokens)
            return LLMResponse(text='{"analysis": "cov', model="m", stop_reason="length")

        def generate_grounded(self, *, system, prompt, documents, max_tokens=4096, **kw):
            self.budgets.append(max_tokens)
            return LLMResponse(text="cited [1] and", model="m", stop_reason="max_tokens")

    provider = Cut()
    with pytest.raises(TruncatedResponse):
        effort.call_json(provider, system="S", prompt="P", schema={}, max_tokens=800)
    with pytest.raises(TruncatedResponse):
        effort.call_grounded(provider, documents=[], system="S", prompt="P", max_tokens=800)
    assert provider.budgets == [800, 1600, 800, 1600]


def test_both_billed_calls_reach_the_ledger_and_the_subject_is_named(tmp_path):
    from policyforge.llm import effort, ledger
    from policyforge.llm.base import TruncatedResponse
    from policyforge.llm.ledger import RecordingProvider

    wrapped = RecordingProvider(
        CutProvider(), provider_name="fake", provider_class="local", path=tmp_path / "c.jsonl"
    )

    with (
        ledger.about("synthesis/incident-response", site="synthesize"),
        pytest.raises(TruncatedResponse) as caught,
    ):
        effort.call(wrapped, system="S", prompt="P", max_tokens=4096)

    records = ledger.load(tmp_path / "c.jsonl")
    assert [r.stop_reason for r in records] == ["length", "length"]
    assert caught.value.subject == "synthesis/incident-response"
    assert str(caught.value).startswith("synthesis/incident-response (synthesize)")


# ---- the writers ------------------------------------------------------------


def _controls(tmp_path):
    from tests.test_cli import _control

    controls = tmp_path / "controls.json"
    controls.write_text(
        json.dumps([_control(control_id="AC-2", title="Account Management")]), encoding="utf-8"
    )
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")
    return controls, crosswalk


def test_synthesize_refuses_a_cut_off_reply_and_writes_nothing(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    controls, crosswalk = _controls(tmp_path)
    out_dir = tmp_path / "synthesis"
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: CutProvider())

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Incident Response",
            "--nist-controls",
            "AC-2",
            "--controls",
            str(controls),
            "--crosswalk",
            str(crosswalk),
            "--out-dir",
            str(out_dir),
        ],
    )

    assert result.exit_code != 0, result.output
    assert "cut off" in result.output
    assert "synthesis/incident-response" in result.output
    # 16384 asked, retried at 32768, refused: the message names the budget
    # reached and where it started.
    assert "32768" in result.output
    assert "retry from 16384" in result.output
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


@pytest.mark.parametrize("tier", ["standard", "policy", "procedure"])
def test_generate_refuses_a_cut_off_reply_at_every_tier(tmp_path, monkeypatch, tier):
    import policyforge.cli as cli_mod

    synthesis = tmp_path / "incident-response.md"
    synthesis.write_text("- Incidents must be reported. [NIST IR-6]\n", encoding="utf-8")
    # The already-published Standard the lower tiers reference, kept apart
    # from the output paths so "no file written" is checkable for every tier.
    standard = tmp_path / "published" / "incident-response-standard.md"
    standard.parent.mkdir()
    standard.write_text("# Incident Response Standard\n\nReport incidents. [NIST IR-6]\n", "utf-8")
    out_path = tmp_path / "drafts" / f"{tier}s" / "incident-response.md"
    history_dir = tmp_path / "history"
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"org": {"name": "Acme"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: CutProvider())

    args = [
        "generate",
        "--tier",
        tier,
        "--synthesis",
        str(synthesis),
        "--out",
        str(out_path),
        "--history-dir",
        str(history_dir),
    ]
    if tier != "standard":
        args += ["--standard", str(standard)]
    result = CliRunner().invoke(cli_mod.cli, args)

    assert result.exit_code != 0, result.output
    assert "cut off" in result.output
    assert f"{tier}/incident-response" in result.output
    assert not out_path.exists()
    assert not history_dir.exists() or not any(history_dir.rglob("v*.md"))


def test_edit_topic_apply_refuses_a_cut_off_rewrite_and_touches_no_file(tmp_path, monkeypatch):
    from tests import test_edit_tree as tree_tests

    class CutOnRewrite(tree_tests.ScriptedProvider):
        """The plans arrive whole; the first rewrite is cut off."""

        def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
            text = self.responses.pop(0)
            self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
            cut = "Return the complete revised document" in prompt
            return LLMResponse(
                text=text[: len(text) // 2] if cut else text,
                model="fake",
                stop_reason="length" if cut else "end_turn",
            )

    monkeypatch.setattr(tree_tests, "ScriptedProvider", CutOnRewrite)
    docs = tree_tests._tree(tmp_path)
    before = {p: p.read_bytes() for p in docs.rglob("*.md")}
    # One extra rewrite body for the retry, so the queue never runs dry.
    responses = (*tree_tests.RESPONSES, tree_tests.RESPONSES[-1])

    result, provider = tree_tests._run(
        monkeypatch, tmp_path, docs, "--apply", "--yes", responses=responses
    )

    assert result.exit_code != 0, result.output
    assert "cut off" in result.output
    assert {p: p.read_bytes() for p in docs.rglob("*.md")} == before
    assert list(docs.rglob("*.plan.json")) == []
    rewrites = [c["max_tokens"] for c in provider.calls if "complete revised" in c["prompt"]]
    assert rewrites == [8192, 16384]


def test_the_answer_path_refuses_rather_than_answering_from_a_cut_off_reply():
    from policyforge.llm.base import TruncatedResponse
    from policyforge.zardoz.answer import answer_question
    from tests.test_native_citations import PASSAGES

    with pytest.raises(TruncatedResponse):
        answer_question("how often are accounts reviewed?", PASSAGES, CutProvider())


def test_routing_does_not_fall_through_to_the_documents_on_a_cut_off_reply():
    from policyforge.llm.base import TruncatedResponse
    from policyforge.zardoz.skills import route

    with pytest.raises(TruncatedResponse):
        route("which controls does nobody own?", CutProvider(text="cover"))


def test_batch_narratives_are_refused_by_control_id(monkeypatch):
    from policyforge.llm.base import TruncatedResponse
    from policyforge.ssp import narrative

    class Batch:
        def supports_batch(self):
            return True

        def generate_batch(self, requests, **kwargs):
            return {
                r.custom_id: LLMResponse(
                    text="Acme implements",
                    model="m",
                    stop_reason="max_tokens" if r.custom_id == "AC-2" else "end_turn",
                )
                for r in requests
            }

    class Control:
        def __init__(self, cid):
            self.control_id = cid
            self.title = cid
            self.control_statement = "Do the thing."
            self.discussion = ""
            self.enhancements = []

    monkeypatch.setattr(narrative, "_narrative_prompt", lambda c, o, s: ("stable", "varying"))
    with pytest.raises(TruncatedResponse) as caught:
        narrative._draft_as_batch([Control("AC-1"), Control("AC-2")], None, None, Batch())
    assert "AC-2" in str(caught.value)
    assert "AC-1" not in caught.value.subject


# ---- the harness ------------------------------------------------------------


def test_the_harness_grades_a_cut_off_reply_as_a_failure_not_noise(monkeypatch):
    from evals import runner

    def probe(case, provider, corpora):
        from policyforge.llm import effort

        effort.call(provider, system="S", prompt=case["question"], max_tokens=64)
        return runner.Outcome(True, "")

    monkeypatch.setitem(runner.SUITES, "probe", probe)

    result = runner.run_case("probe", {"name": "c", "question": "q?"}, CutProvider())

    (outcome,) = result.outcomes
    assert outcome.passed is False
    assert outcome.errored is False
    assert outcome.detail.startswith("truncated at 128 tokens")
    assert result.errors == []


# ---- the guard --------------------------------------------------------------


def test_no_module_outside_llm_calls_a_provider_directly():
    """Every model call goes through `llm/effort.py`, where the rule lives.

    A call site that reaches `provider.generate` itself gets no retry and no
    refusal, and the four Zardoz routing calls that did exactly that are
    why this test exists. `generate_batch` is the one allowed exception:
    a batch cannot be retried per item, and `ssp/narrative.py` checks its
    answers by control id instead.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.relative_to(SRC).parts[0] == "llm":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("generate", "generate_json", "generate_grounded")
            ):
                offenders.append(f"{path.relative_to(SRC).as_posix()}:{node.lineno}")
    assert offenders == [], f"direct provider calls, not held to the truncation rule: {offenders}"


# ---- an empty reply that stopped normally ----------------------------------
#
# Found by the live acceptance run for the fix above: glm-5.3-flash answered
# the Incident Response synthesis with 4,869 output tokens, stop_reason
# "stop" and an empty content field (the tokens went into its reasoning
# channel), and the synthesis was written with frontmatter and no body, exit
# 0. The next command failed on the empty synthesis. Not a truncation, and
# not applied to every call - a routing reply may be empty and mean it - but
# a document never may.


class EmptyProvider(CutProvider):
    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
        self.budgets.append(max_tokens)
        return LLMResponse(text="  ", model="thinker", stop_reason="stop", output_tokens=4869)


def test_an_empty_document_reply_is_refused_by_name():
    from policyforge.llm import effort, ledger
    from policyforge.llm.base import EmptyReply

    response = LLMResponse(text="", model="thinker", stop_reason="stop", output_tokens=4869)
    with (
        ledger.about("synthesis/incident-response", site="synthesize"),
        pytest.raises(EmptyReply) as caught,
    ):
        effort.document_text(response, what="synthesis")

    message = str(caught.value)
    assert message.startswith("synthesis/incident-response (synthesize)")
    assert "no text for the synthesis" in message
    assert "4869 output tokens" in message
    assert "Nothing was written" in message
    assert effort.document_text(LLMResponse(text=" body ", model="m"), what="x") == "body"


def test_synthesize_refuses_an_empty_reply_and_writes_nothing(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    controls, crosswalk = _controls(tmp_path)
    out_dir = tmp_path / "synthesis"
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: EmptyProvider())

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Incident Response",
            "--nist-controls",
            "AC-2",
            "--controls",
            str(controls),
            "--crosswalk",
            str(crosswalk),
            "--out-dir",
            str(out_dir),
        ],
    )

    assert result.exit_code != 0, result.output
    assert "no text for the synthesis" in result.output
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


@pytest.mark.parametrize("tier", ["standard", "policy", "procedure"])
def test_generate_refuses_an_empty_reply_at_every_tier(tmp_path, monkeypatch, tier):
    import policyforge.cli as cli_mod

    synthesis = tmp_path / "incident-response.md"
    synthesis.write_text("- Incidents must be reported. [NIST IR-6]\n", encoding="utf-8")
    standard = tmp_path / "published" / "incident-response-standard.md"
    standard.parent.mkdir()
    standard.write_text("# Incident Response Standard\n\nReport incidents. [NIST IR-6]\n", "utf-8")
    out_path = tmp_path / "drafts" / f"{tier}s" / "incident-response.md"
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"org": {"name": "Acme"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: EmptyProvider())

    args = ["generate", "--tier", tier, "--synthesis", str(synthesis), "--out", str(out_path)]
    args += ["--history-dir", str(tmp_path / "history")]
    if tier != "standard":
        args += ["--standard", str(standard)]
    result = CliRunner().invoke(cli_mod.cli, args)

    assert result.exit_code != 0, result.output
    assert "no text for the" in result.output
    assert not out_path.exists()
