"""One boundary decision for every model call (#327; the review's CR-01, 02, 03, 10).

`ledger.wrap` recorded which class of provider took a call and never decided
whether it might, so a command that forgot `boundary.check` got no check:
Zardoz and MCP answers, `edit-topic`, and the entailment judge each sent
licensed-derived text to a hosted model (ba re-measured all three at
`1ab05b16`, issue comment 4988). The wrapper now decides every call against
the class of what that call sends, and the commands say what they send:

- Zardoz: per call, the most guarded class of the passages retrieved for it
  and of any earlier exchange carried into it (80's ruling on 327, 4989);
- `edit-topic`: the classes its target files state;
- `check --entail`: the document's and its synthesis's.

Every provider here is a fake behind the REAL `get_provider` -> `ledger.wrap`
path (only `_build_provider` and the judge's `LiteLLMProvider` are
replaced), so the decision under test is the one production makes, and the
ledger rows are real rows.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.llm.base import LLMResponse

LICENSED = "Accounts shall be reviewed quarterly by the system owner."
PUBLIC = "Backups are taken nightly and kept for thirty-five days."


@pytest.fixture(autouse=True)
def _no_ambient_endpoint_variables(monkeypatch):
    """A covered call is refused while ANY variable holds a URL (b5's 1439),
    and a CI runner sets several (GITHUB_SERVER_URL, ACTIONS_*_URL,
    FORGEJO_*_URL): the first version of this cleared five names and went
    red on the runner only. Removed by test_licensed_agreement.py's rule,
    not by `endpoint_overrides()`, so a test expecting a call ALLOWED
    measures the code and not the machine it ran on."""
    import os

    for name, value in list(os.environ.items()):
        upper = name.upper()
        if "://" in value or any(w in upper for w in ("URL", "BASE", "ENDPOINT", "PROXY")):
            monkeypatch.delenv(name, raising=False)


class Fake:
    """A model behind the real wrapper. Answers each Zardoz prompt as its
    caller expects, and keeps every prompt it was sent."""

    model = "fake-model"

    def __init__(self, answer="Reviews are quarterly. [1]"):
        self.prompts: list[str] = []
        self.answer = answer

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kw):
        from policyforge.zardoz import conversation, paraphrase, skills

        self.prompts.append(f"{system}\n{prompt}")
        if system == skills.ROUTER_SYSTEM_PROMPT:
            text = skills.NO_SKILL
        elif system == paraphrase.EXPANSION_SYSTEM_PROMPT:
            text = ""
        elif system == conversation.RESOLVE_SYSTEM_PROMPT:
            text = "How long are backups kept?"
        else:
            text = self.answer
        return LLMResponse(text=text, model=self.model)

    def check(self):
        return True

    def saw(self, text: str) -> bool:
        return any(text in p for p in self.prompts)


def _rows(tmp: Path) -> list[tuple]:
    path = tmp / "ledger.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [(r.get("site"), r.get("content_class"), r.get("provider_class")) for r in rows]


def _hosted(tmp: Path, **extra) -> dict:
    llm = {
        "provider": "anthropic",
        "model": "claude-x",
        "api_key_env": "PF_UNUSED_KEY",
        "ledger": {"path": str(tmp / "ledger.jsonl")},
    }
    llm.update(extra)
    return {"llm": llm}


def _local(tmp: Path, **extra) -> dict:
    llm = {
        "provider": "openai-compat",
        "model": "qwen",
        "base_url": "http://localhost:11434/v1",
        "ledger": {"path": str(tmp / "ledger.jsonl")},
    }
    llm.update(extra)
    return {"llm": llm}


AGREEMENT = {"provider": "anthropic", "reference": "DPA-7"}


# ---- Zardoz and MCP (CR-01) ---------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _session(tmp: Path, monkeypatch, config: dict, *, fake: Fake):
    """An MCP session over a corpus holding a licensed-stamped Standard and
    an unstamped (the organization's own) backup Standard."""
    import policyforge.llm.base as base
    from policyforge.mcp.server import build_state
    from policyforge.zardoz.corpus import sync_corpus
    from tests.test_edit import _registry

    content = tmp / "docs"
    _write(
        content / "standards" / "access-review-standard.md",
        "---\ntitle: Access Review Standard\ntier: standard\nowner: IAM\n"
        "generated_by:\n  content_class: licensed\n---\n\n"
        f"## 1. Reviews\n\n{LICENSED}\n",
    )
    _write(
        content / "standards" / "backup-standard.md",
        "---\ntitle: Backup Standard\ntier: standard\nowner: Ops\n---\n\n"
        f"## 1. Retention\n\n{PUBLIC}\n",
    )
    sync_corpus([], content_dir=content, corpus_dir=tmp / "corpus")
    monkeypatch.setattr(base, "_build_provider", lambda config: fake)
    return build_state(
        config,
        corpus_dir=tmp / "corpus",
        topics_path=_registry(tmp),
        provider_factory=base.get_provider,
    )


def _ask(state, question: str) -> str:
    from policyforge.mcp.server import call_tool

    return call_tool(state, "ask_documents", {"question": question})


def test_the_corpus_carries_a_documents_stamped_class(tmp_path, monkeypatch):
    from policyforge.zardoz.corpus import load_corpus

    _session(tmp_path, monkeypatch, _hosted(tmp_path), fake=Fake())

    classes = {d.title: d.content_class for d in load_corpus(tmp_path / "corpus").documents}
    assert classes == {"Access Review Standard": "licensed", "Backup Standard": ""}


def test_a_licensed_passage_is_refused_for_a_hosted_model_naming_it(tmp_path, monkeypatch):
    fake = Fake()
    state = _session(tmp_path, monkeypatch, _hosted(tmp_path), fake=fake)

    out = _ask(state, "How often are accounts reviewed by the system owner?")

    assert "Not sent to the model" in out
    assert "the passage Access Review Standard § 1. Reviews" in out, "names the passage"
    assert "llm.licensed_agreement" in out, "says what would allow it"
    assert not fake.saw(LICENSED), "the licensed passage reached no model"
    assert ("mcp.answer", "licensed", "third-party") not in _rows(tmp_path)


def test_an_answer_from_public_passages_goes_to_the_hosted_model(tmp_path, monkeypatch):
    """80's ruling, (a) over (b): one licensed page synced doesn't refuse a
    question that never touches it."""
    fake = Fake(answer="Backups are kept for thirty-five days. [1]")
    state = _session(tmp_path, monkeypatch, _hosted(tmp_path), fake=fake)

    out = _ask(state, "How long are nightly backups kept?")

    assert "Not sent to the model" not in out
    assert fake.saw(PUBLIC) and not fake.saw(LICENSED)
    assert ("mcp.answer", None, "third-party") in _rows(tmp_path)


def test_a_local_model_answers_from_the_licensed_passage_and_the_row_says_so(tmp_path, monkeypatch):
    fake = Fake()
    state = _session(tmp_path, monkeypatch, _local(tmp_path), fake=fake)

    out = _ask(state, "How often are accounts reviewed by the system owner?")

    assert "Not sent to the model" not in out
    assert fake.saw(LICENSED)
    assert ("mcp.answer", "licensed", "local") in _rows(tmp_path)


def test_a_declared_agreement_lets_the_hosted_model_answer_and_is_recorded(tmp_path, monkeypatch):
    fake = Fake()
    state = _session(
        tmp_path, monkeypatch, _hosted(tmp_path, licensed_agreement=AGREEMENT), fake=fake
    )

    out = _ask(state, "How often are accounts reviewed by the system owner?")

    assert "Not sent to the model" not in out
    assert fake.saw(LICENSED)
    rows = [
        json.loads(line)
        for line in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    licensed = [r for r in rows if r.get("content_class") == "licensed"]
    assert licensed and all(r.get("licensed_agreement") for r in licensed)


def test_a_follow_up_never_sends_an_earlier_licensed_answer_to_a_hosted_model(
    tmp_path, monkeypatch
):
    """80's ruling: an earlier answer carried into a prompt carries its class.
    The rewrite of a follow-up is the call that sends the earlier exchange,
    so it is refused; the question is then answered as typed, from its own
    (public) passages, and says it wasn't read against the exchange."""
    from policyforge.zardoz.conversation import Turn
    from policyforge.zardoz.retrieve import RetrievalIndex

    fake = Fake(answer="Backups are kept for thirty-five days. [1]")
    state = _session(tmp_path, monkeypatch, _hosted(tmp_path), fake=fake)
    earlier = RetrievalIndex(state.corpus).search("accounts reviewed system owner", limit=3)
    assert any(p.document.content_class == "licensed" for p in earlier), "premise"
    state.conversation.add(
        Turn(question="How often are accounts reviewed?", passages=earlier, answer=LICENSED)
    )

    out = _ask(state, "and backups?")

    assert not fake.saw(LICENSED), "the earlier licensed answer reached no hosted model"
    assert "not read against the earlier exchange" in out
    assert "the earlier answer about 'How often are accounts reviewed?'" in out


def test_a_snapshot_from_before_classes_says_so(tmp_path, monkeypatch):
    manifest = tmp_path / "corpus" / "manifest.json"
    _session(tmp_path, monkeypatch, _local(tmp_path), fake=Fake())
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["schema"] = 1
    for record in data["documents"]:
        record.pop("content_class", None)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    import policyforge.llm.base as base
    from policyforge.mcp.server import build_state
    from tests.test_edit import _registry

    state = build_state(
        _local(tmp_path),
        corpus_dir=tmp_path / "corpus",
        topics_path=_registry(tmp_path),
        provider_factory=base.get_provider,
    )

    assert any("predates content classes" in note for note in state.startup_notes)


# ---- edit-topic (CR-02, CR-10) ------------------------------------------


def _edit(tmp: Path, monkeypatch, config: dict, *, stamp: str):
    import policyforge.cli as cli_mod
    import policyforge.llm.base as base
    from tests.test_edit import (
        DOCUMENT,
        PROCEDURE_DOC,
        ScriptedProvider,
        _plan_json,
        _procedure_plan_json,
        _registry,
    )
    from tests.test_edit_tree import _tree

    docs = _tree(tmp, commit=False)
    if stamp:
        standard = docs / "standards" / "access-control-standard.md"
        text = standard.read_text(encoding="utf-8")
        standard.write_text(text.replace("---\n", f"---\ncontent_class: {stamp}\n", 1), "utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
        subprocess.run(["git", "config", key, value], cwd=tmp, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-qm", "tree"], cwd=tmp, check=True)
    fake = ScriptedProvider(
        _plan_json(steps=[], out_of_scope=["Cadence is a Standard-tier detail."]),
        _plan_json(),
        _procedure_plan_json(),
        DOCUMENT.replace("quarterly", "monthly"),
        PROCEDURE_DOC.replace("quarterly", "monthly"),
    )
    monkeypatch.setattr(base, "_build_provider", lambda config: fake)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-topic",
            "--instruction",
            "Access reviews move from quarterly to monthly.",
            "--topic-name",
            "Access Review",
            "--topics",
            str(_registry(tmp)),
            "--content-dir",
            str(docs),
            "--out-dir",
            str(tmp / "edits"),
        ],
    )
    return result, fake


def _calls(fake) -> int:
    return len(getattr(fake, "calls", None) or getattr(fake, "prompts", None) or [])


def test_edit_topic_over_a_licensed_file_is_refused_for_a_hosted_model(tmp_path, monkeypatch):
    result, fake = _edit(tmp_path, monkeypatch, _hosted(tmp_path), stamp="licensed")

    assert result.exit_code != 0
    assert "standards/access-control-standard.md says content_class: licensed" in result.output
    assert _calls(fake) == 0
    assert _rows(tmp_path) == []


def test_edit_topic_over_a_licensed_file_runs_on_a_local_model_and_names_the_site(
    tmp_path, monkeypatch
):
    result, fake = _edit(tmp_path, monkeypatch, _local(tmp_path), stamp="licensed")

    assert result.exit_code == 0, result.output
    assert _calls(fake) == 5
    assert set(_rows(tmp_path)) == {("edit", "licensed", "local")}


def test_edit_topic_honours_a_tightened_ceiling_for_the_organizations_own_files(
    tmp_path, monkeypatch
):
    """CR-10: `organization-internal: local` was honoured by `generate` and
    ignored by the edit commands."""
    config = _hosted(tmp_path, boundary={"organization-internal": "local"})
    result, fake = _edit(tmp_path, monkeypatch, config, stamp="")

    assert result.exit_code != 0
    assert "organization-internal content -> third-party" in result.output
    assert _calls(fake) == 0


# ---- the entailment judge (CR-03) ----------------------------------------


def _entail(tmp: Path, monkeypatch, config: dict, *, synthesis_stamp: str):
    import policyforge.cli as cli_mod
    import policyforge.llm.litellm_provider as litellm_provider
    from tests.test_grounding import _tree

    judged: list[str] = []

    class Judge:
        def __init__(self, model=None, api_base=None, **kwargs):
            self.model = model

        def supports_schema(self):
            return True

        def generate_json(self, *, system, prompt, schema, **kwargs):
            judged.append(prompt)
            return LLMResponse(text='{"label": "entailed", "reason": "x"}', model=self.model)

    monkeypatch.setattr(litellm_provider, "LiteLLMProvider", Judge)
    front = f"---\ncontent_class: {synthesis_stamp}\n---\n\n" if synthesis_stamp else ""
    content, synthesis = _tree(
        tmp,
        f"## 1. Access\n\n{LICENSED} [NIST 800-53 AC-2]\n",
        f"{front}- {LICENSED} [NIST 800-53 AC-2]\n",
    )
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    result = CliRunner().invoke(
        cli_mod.cli,
        ["check", "--content-dir", str(content), "--synthesis-dir", str(synthesis), "--entail"],
    )
    return result, judged


def test_a_hosted_judge_is_refused_a_licensed_synthesis(tmp_path, monkeypatch):
    config = _local(tmp_path)
    config["entail"] = {"provider": "llm", "model": "openrouter/judge"}

    result, judged = _entail(tmp_path, monkeypatch, config, synthesis_stamp="licensed")

    assert result.exit_code != 0
    assert "the synthesis for access-control-standard.md says content_class: licensed" in (
        result.output
    )
    assert judged == []


def test_a_local_judge_judges_a_licensed_synthesis_under_its_scope(tmp_path, monkeypatch):
    config = _local(tmp_path)
    config["entail"] = {
        "provider": "llm",
        "model": "openai/judge",
        "api_base": "http://localhost:11434/v1",
    }

    result, judged = _entail(tmp_path, monkeypatch, config, synthesis_stamp="licensed")

    assert result.exit_code == 0, result.output
    assert judged
    assert ("entail", "licensed", "local") in _rows(tmp_path)


def test_the_judge_honours_the_projects_tightened_ceiling(tmp_path, monkeypatch):
    """The judge's wrapper was built from its own block alone, so the
    project's `llm.boundary` never reached it."""
    config = _local(tmp_path, boundary={"organization-internal": "local"})
    config["entail"] = {"provider": "llm", "model": "openrouter/judge"}

    result, judged = _entail(tmp_path, monkeypatch, config, synthesis_stamp="")

    assert result.exit_code != 0
    assert judged == []


# ---- the judge on Zardoz's answers (#388) --------------------------------


def _judged_session(tmp: Path, monkeypatch, config: dict, *, fake: Fake) -> tuple:
    """A session with `entail.answering` as the config says, and every judge
    call recorded. The judge was built at startup and never handed to the
    session, so no answer was judged while the note said it was (#388)."""
    import policyforge.llm.litellm_provider as litellm_provider

    judged: list[str] = []

    class Judge:
        def __init__(self, model=None, api_base=None, **kwargs):
            self.model = model

        def supports_schema(self):
            return True

        def generate_json(self, *, system, prompt, schema, **kwargs):
            judged.append(prompt)
            return LLMResponse(text='{"label": "entailed", "reason": "x"}', model=self.model)

    monkeypatch.setattr(litellm_provider, "LiteLLMProvider", Judge)
    return _session(tmp, monkeypatch, config, fake=fake), judged


def _judge_rows(tmp: Path) -> list[tuple]:
    """The ledger rows the judge's calls wrote: the answer's site, the
    passages' class, and the judge's own model."""
    path = tmp / "ledger.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [
        (r.get("site"), r.get("content_class"), r.get("provider_class"))
        for r in rows
        if "judge" in str(r.get("model"))
    ]


def _with_judge(config: dict, *, local: bool, answering: bool = True) -> dict:
    config["entail"] = {"provider": "llm", "model": "openrouter/judge", "answering": answering}
    if local:
        config["entail"].update(model="openai/judge", api_base="http://localhost:11434/v1")
    return config


def test_an_answer_is_judged_when_entail_answering_is_set(tmp_path, monkeypatch):
    fake = Fake(answer="Backups are kept for thirty-five days. [1]")
    config = _with_judge(_hosted(tmp_path), local=False)
    state, judged = _judged_session(tmp_path, monkeypatch, config, fake=fake)

    out = _ask(state, "How long are nightly backups kept?")

    assert "Not sent to the model" not in out
    assert judged, "the answer's cited sentence went to the judge"
    assert _judge_rows(tmp_path) == [("mcp.answer", None, "third-party")]


def test_no_judge_call_happens_when_entail_answering_is_unset(tmp_path, monkeypatch):
    fake = Fake(answer="Backups are kept for thirty-five days. [1]")
    config = _with_judge(_hosted(tmp_path), local=False, answering=False)
    state, judged = _judged_session(tmp_path, monkeypatch, config, fake=fake)

    _ask(state, "How long are nightly backups kept?")

    assert judged == []


def test_a_hosted_judge_is_refused_a_licensed_passage_a_local_model_answered(tmp_path, monkeypatch):
    """80's condition on #388: the judge's calls go through the same boundary.
    A local model may answer from the licensed Standard; a hosted judge may
    not read it, and the answer says the check did not run."""
    fake = Fake()
    config = _with_judge(_local(tmp_path), local=False)
    state, judged = _judged_session(tmp_path, monkeypatch, config, fake=fake)

    out = _ask(state, "How often are accounts reviewed by the system owner?")

    assert fake.saw(LICENSED), "the local model answered from it"
    assert judged == [], "the hosted judge never saw it"
    assert "entailment check did not run" in out


def test_a_local_judge_judges_a_licensed_answer(tmp_path, monkeypatch):
    fake = Fake()
    config = _with_judge(_local(tmp_path), local=True)
    state, judged = _judged_session(tmp_path, monkeypatch, config, fake=fake)

    out = _ask(state, "How often are accounts reviewed by the system owner?")

    assert "entailment check did not run" not in out
    assert judged and any(LICENSED in prompt for prompt in judged)
    assert _judge_rows(tmp_path) == [("mcp.answer", "licensed", "local")]
