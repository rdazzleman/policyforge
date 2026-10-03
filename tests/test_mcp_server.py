"""The MCP surface, whose defining property is that it cannot write.

Exposing the analyses to other agents is only safe because every tool reads
what is already on disk. That has to be a structural fact rather than a
promise, so the guard below mirrors the one that already keeps the publish
path unreachable from `zardoz/`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from policyforge.mcp import server as mcp_server
from policyforge.mcp.server import TOOLS, build_state, call_tool

# ---- the property that makes this safe ---------------------------------


def test_no_module_here_can_reach_the_publish_path():
    """Structural, not a convention.

    Checked over the parsed AST rather than as a substring, so the module
    stays free to *discuss* the publish path in its docstring — which it
    needs to, since the reason it does not call it is the whole point. The
    walk covers every node, so a lazy import inside a function body is
    caught as readily as one at the top of the file.
    """
    forbidden = {
        "update_page_body",
        "export_to_confluence",
        "confluence_exporter",
        "publish_tree",
        "apply_edit_plan",
        "apply_targets",
    }
    sources = list(Path(mcp_server.__file__).parent.glob("*.py"))
    assert sources, "expected to find the mcp package sources"

    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                referenced.update(part for a in node.names for part in a.name.split("."))
            elif isinstance(node, ast.ImportFrom):
                referenced.update((node.module or "").split("."))
                referenced.update(a.name for a in node.names)
            elif isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)

        leaked = referenced & forbidden
        assert not leaked, f"{path.name} reaches a write path: {', '.join(sorted(leaked))}"


def test_the_tool_list_is_closed_and_every_tool_is_dispatchable():
    """A tool nobody can run, or a route to a tool nobody declared, is a bug.

    The registry of skills grows whenever somebody adds an analysis. The
    tool list must not, which is why it is written by hand — adding a tool
    should be a deliberate act with a diff.
    """
    declared = {tool.name for tool in TOOLS}
    routable = set(mcp_server._DISPATCH) | {"ask_documents"}
    assert declared == routable


def test_no_tool_describes_itself_as_changing_anything():
    """The descriptions are what an agent chooses a tool from.

    Matching on the bare word "publish" was the first attempt and was wrong:
    NIST *publishes* the crosswalk, and a topic *publishes* documents, so
    two honest descriptions tripped it. The check has to be about phrasings
    that tell a caller this tool mutates something, not about a word that
    also describes who wrote a standard.
    """
    mutating = (
        "publishes the",
        "will publish",
        "applies the",
        "writes the",
        "updates the page",
        "edits the page",
        "changes the document",
    )
    for tool in TOOLS:
        described = tool.description.lower()
        for phrase in mutating:
            assert phrase not in described, f"{tool.name} describes itself as writing: {phrase!r}"


def test_every_tool_has_a_usable_schema():
    for tool in TOOLS:
        assert tool.schema["type"] == "object"
        assert tool.schema.get("additionalProperties") is False
        for name, spec in tool.schema["properties"].items():
            assert spec.get("description"), f"{tool.name}.{name} has no description"
        for required in tool.schema.get("required", []):
            assert required in tool.schema["properties"]


# ---- behaviour, against a state with nothing loaded --------------------


@pytest.fixture
def empty_state(tmp_path):
    """A server with no registry, no corpus and no model.

    The most common way this will first be run, and every tool has to answer
    rather than raise.
    """
    return build_state({}, corpus_dir=tmp_path / "corpus", topics_path=tmp_path / "topics.yaml")


def test_an_empty_install_answers_every_tool_without_raising(empty_state):
    for tool in TOOLS:
        result = call_tool(empty_state, tool.name, {})
        assert isinstance(result, str) and result.strip(), f"{tool.name} returned nothing"


def test_an_unknown_tool_is_reported_not_raised(empty_state):
    assert "No such tool" in call_tool(empty_state, "delete_everything", {})


def test_asking_nothing_asks_for_a_question(empty_state):
    assert "Ask a question" in call_tool(empty_state, "ask_documents", {"question": "   "})


def test_missing_arguments_do_not_crash_a_skill(empty_state):
    assert call_tool(empty_state, "team_bundle", {}).strip()
    assert call_tool(empty_state, "addresses", {"requirement": ""}).strip()


def test_arguments_of_the_wrong_type_are_coerced_not_fatal(empty_state):
    """A client can send anything; a tool call should not be the thing that
    takes the server down."""
    assert call_tool(empty_state, "coverage", {"baseline": 3}).strip()
    assert call_tool(empty_state, "ask_documents", {"question": None}).strip()


def test_none_arguments_are_tolerated(empty_state):
    assert call_tool(empty_state, "corpus", None).strip()


# ---- the answers match the shell's ------------------------------------


def test_a_tool_returns_exactly_what_the_shell_returns(empty_state):
    """No second code path. A tool that reformatted a report would let the
    CLI and an agent disagree about the same repository.

    The report is the shell's, byte for byte. When something could not be
    loaded at startup, the notes follow it — the terminal shows those under
    its banner, and an agent has no banner.
    """
    from policyforge.mcp.server import with_startup_notes
    from policyforge.zardoz.shell import dispatch

    for tool, command in (("corpus", "/corpus"), ("topics", "/topics")):
        report = dispatch(command, empty_state)
        result = call_tool(empty_state, tool, {})
        assert result.startswith(report)
        assert result == with_startup_notes(report, empty_state)


def test_with_nothing_missing_a_tool_returns_only_the_report():
    """The notes are there to explain an absence, not to decorate every answer."""
    from types import SimpleNamespace

    from policyforge.mcp.server import with_startup_notes

    assert with_startup_notes("the report", SimpleNamespace(startup_notes=[])) == "the report"


def _registry_state(tmp_path, body: str):
    topics = tmp_path / "topics.yaml"
    topics.write_text(body, encoding="utf-8")

    def no_model(config):
        raise RuntimeError("no key")

    return build_state(
        {}, corpus_dir=tmp_path / "corpus", topics_path=topics, provider_factory=no_model
    )


def test_a_broken_registry_is_not_reported_as_a_missing_one(tmp_path):
    """The misdiagnosis this change exists to stop.

    Before, the server swallowed the load error, so with a topics.yaml that
    exists and does not parse an agent calling `coverage` was told "No topic
    registry loaded" and nothing else. The terminal has always said why.
    """
    state = _registry_state(tmp_path, "topics:\n  - name: [unclosed\n")

    result = call_tool(state, "coverage", {})

    assert "could not be read" in result
    assert "not valid YAML" in result
    assert "When this PolicyForge server started" in result


def test_the_server_and_the_terminal_give_the_same_notes(tmp_path):
    """One function decides what is worth saying, so they cannot drift again."""
    from policyforge.zardoz.startup import open_session

    topics = tmp_path / "topics.yaml"
    topics.write_text("topics:\n  - name: [unclosed\n", encoding="utf-8")

    def no_model(config):
        raise RuntimeError("no key")

    terminal = open_session(
        topics_path=topics, corpus_dir=tmp_path / "corpus", config={}, provider_factory=no_model
    )
    server = build_state(
        {}, corpus_dir=tmp_path / "corpus", topics_path=topics, provider_factory=no_model
    )
    assert server.startup_notes == terminal.notes
    assert len(terminal.notes) == 3  # registry, corpus, model


def test_a_stale_snapshot_is_reported_to_the_agent(tmp_path):
    """An agent could answer from a two-month-old corpus with no sign of it."""
    import json

    from policyforge.zardoz.corpus import TRUSTED, CorpusDocument, write_corpus

    corpus_dir = tmp_path / "corpus"
    write_corpus(
        [
            CorpusDocument(
                doc_id="access",
                title="Access Control Standard",
                space="ENG",
                confidence=TRUSTED,
                owner="IAM",
                body="# Access Control Standard\n\nReviews are quarterly.\n",
            )
        ],
        corpus_dir=corpus_dir,
    )
    manifest = corpus_dir / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["synced_at"] = "2020-01-01T00:00:00+00:00"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    state = build_state(
        {},
        corpus_dir=corpus_dir,
        topics_path=tmp_path / "none.yaml",
        provider_factory=lambda config: object(),
    )

    assert "days old" in call_tool(state, "corpus", {})


def test_the_server_session_is_recorded_as_mcp(tmp_path):
    state = _registry_state(tmp_path, "topics:\n  - name: A\n    owner: B\n")
    assert state.surface == "mcp"


def test_the_bundle_tool_reaches_the_registry(tmp_path):
    import yaml

    topics_path = tmp_path / "topics.yaml"
    topics_path.write_text(
        yaml.safe_dump(
            {
                "topics": [
                    {"name": "Access Review", "owner": "IAM", "nist_controls": ["AC-2"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    state = build_state({}, corpus_dir=tmp_path / "corpus", topics_path=topics_path)
    assert "Access Review" in call_tool(state, "topics", {})


# ---- the optional dependency -------------------------------------------


def test_the_missing_dependency_message_names_the_extra():
    """`mcp` is an optional extra, installed in CI by name.

    Anyone installing PolicyForge normally will not have it, and the error
    they get should name the extra rather than being an ImportError.
    """
    assert "policyforge[mcp]" in mcp_server._INSTALL_HINT
    assert "optional" in mcp_server._INSTALL_HINT


def test_model_calls_from_a_tool_are_attributed_to_the_mcp_surface(empty_state):
    """A-09 put questions in the ledger so a challenged answer could be
    traced to what was asked. Once an agent can call ask_documents in a
    loop, "a person asked this" stops being implied — and an unexpected
    line on a bill has to be attributable to a tool rather than a colleague.
    """
    assert empty_state.surface == "mcp"


def test_a_terminal_session_is_still_attributed_to_zardoz():
    """The default must not have moved under the REPL."""
    from policyforge.zardoz.shell import ShellState

    assert ShellState().surface == "zardoz"


def test_only_one_tool_calls_a_model_and_says_so():
    """The cost asymmetry is invisible otherwise: six of seven tools are
    set arithmetic over local files, and the calling agent chooses from
    these descriptions."""
    costly = [t for t in TOOLS if "calls a language model" in t.description]
    assert [t.name for t in costly] == ["ask_documents"]


# ---- the server itself, spoken to over stdio ---------------------------


def test_the_server_answers_a_client_over_stdio(tmp_path):
    """Spawn `policyforge mcp` and speak JSON-RPC to it, as a client would.

    Everything above calls `call_tool` directly, and none of it touches the
    MCP SDK: `serve()` is the only code that does. So the SDK could move out
    from under it and the suite stay green — which is what happened with
    mcp 2.0, where `Server.list_tools` no longer exists and the server died
    on startup while every test here passed. This is the test that notices.
    """
    pytest.importorskip("mcp")
    import json
    import queue
    import subprocess
    import sys
    import threading

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "policyforge.cli",
            "mcp",
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "--topics",
            str(tmp_path / "topics.yaml"),
        ],
        # No config.yaml here, and the credentials are already stripped from
        # the environment this inherits: the server takes its no-model path.
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",  # the server logs in the platform encoding
    )
    lines: queue.Queue = queue.Queue()

    def pump() -> None:
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)  # stdout closed: the server is gone

    threading.Thread(target=pump, daemon=True).start()

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def reply(want_id: int) -> dict:
        while True:
            try:
                line = lines.get(timeout=60)
            except queue.Empty:
                pytest.fail(f"no reply to request {want_id} within 60s")
            if line is None:
                proc.wait(timeout=10)
                pytest.fail(f"server exited before replying to {want_id}:\n{proc.stderr.read()}")
            message = json.loads(line)
            if message.get("id") == want_id:
                return message

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        init = reply(1)
        assert init["result"]["serverInfo"]["name"] == "policyforge", init

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = [tool["name"] for tool in reply(2)["result"]["tools"]]
        assert listed == [tool.name for tool in TOOLS]

        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "topics", "arguments": {}},
            }
        )
        result = reply(3)["result"]
        assert not result.get("isError"), result
        assert result["content"][0]["text"].strip()
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def test_a_cut_off_answer_comes_back_as_text_without_the_partial_answer():
    """The docstring promises errors as text. A reply the model cut off at
    its budget twice is refused by the answering path; the tool must report
    that in words, not raise, and not hand the agent the half-answer."""
    from policyforge.llm.base import LLMResponse
    from policyforge.zardoz.shell import ShellState
    from tests.test_zardoz_answer import _corpus

    class CutProvider:
        def __init__(self):
            self.calls = 0

        def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
            self.calls += 1
            return LLMResponse(
                text="Recertified quarterly [1] and", model="m", stop_reason="length"
            )

        def check(self):
            return True

    provider = CutProvider()
    state = ShellState(corpus=_corpus(), provider=provider)

    result = call_tool(state, "ask_documents", {"question": "how often are accounts recertified?"})

    assert isinstance(result, str)
    assert "cut off at" in result
    assert "not shown" in result
    assert "Recertified quarterly [1] and" not in result
    assert "4.1 Account Review" in result
    # Routing asked twice (cut off, said aloud, sent to the documents), then
    # answering asked twice (cut off, refused). Never more: no third try.
    assert provider.calls == 4
