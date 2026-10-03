"""zardoz/ tests — the conversational shell (milestone M0).

There is no LLM and no network in this milestone, so everything here is
fast and deterministic. Most of it drives `dispatch()` directly rather than
going through a terminal: that is the whole reason the shell separates
"decide what to say" from "read and print", and testing it that way keeps
the separation honest as later milestones add retrieval and answering.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from policyforge.topics.registry import Topic
from policyforge.zardoz.art import HEAD, banner
from policyforge.zardoz.shell import ShellState, dispatch, run_shell


def _topics():
    return [
        Topic(
            name="Access Review",
            owner="IAM Engineering",
            nist_controls=["AC-2"],
            confluence={
                "space": "SEC",
                "pages": {"policy": "AC Policy", "standard": "AC Standard"},
            },
        ),
        Topic(name="Vendor Risk", owner="Third-Party Risk", nist_controls=["SR-3"]),
    ]


# --------------------------------------------------------------------------
# The head
# --------------------------------------------------------------------------


def test_the_head_is_pure_ascii():
    """Block-drawing characters look better but turn into mojibake in a
    classic cmd.exe code page, and the launch splash is the worst possible
    place to discover an encoding problem."""
    assert HEAD.isascii()


def test_the_head_fits_an_eighty_column_terminal():
    assert max(len(line) for line in HEAD.splitlines()) <= 80


def test_banner_can_drop_the_art_but_keeps_the_greeting():
    with_art = banner()
    without = banner(art=False)

    assert "ZARDOZ SPEAKS TO YOU" in with_art
    assert "ZARDOZ SPEAKS TO YOU" in without
    assert HEAD.strip("\n") in with_art
    assert HEAD.strip("\n") not in without


def test_plain_mode_removes_the_persona_everywhere_at_once():
    """The persona is a table lookup rather than conditionals scattered
    through the shell, so that turning it off cannot half-apply."""
    plain = banner(art=False, plain=True)

    assert "ZARDOZ" not in plain
    assert "policyforge zardoz" in plain

    state = ShellState(plain=True)
    assert "Zardoz" not in dispatch("/help", state)
    assert "Zardoz" not in dispatch("/nope", state)
    assert "Zardoz" not in dispatch("/quit", state)


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def test_help_is_generated_from_the_command_table():
    """Written out by hand it would drift the first time a command was
    added, and a stale /help is worse than none."""
    from policyforge.zardoz.shell import COMMANDS

    output = dispatch("/help", ShellState())

    for name in COMMANDS:
        assert f"/{name}" in output


def test_aliases_reach_the_same_command():
    assert dispatch("/?", ShellState()) == dispatch("/help", ShellState())

    state = ShellState()
    dispatch("/exit", state)
    assert state.running is False


def test_an_unknown_command_says_so_rather_than_guessing():
    assert "/frobnicate" in dispatch("/frobnicate", ShellState())


def test_a_blank_line_produces_no_output():
    assert dispatch("   ", ShellState()) == ""


def test_commands_are_case_insensitive_and_tolerate_stray_spaces():
    state = ShellState()
    assert "help" in dispatch("  /HELP  ", state).lower()


def test_topics_lists_owners_and_flags_the_ones_with_no_pages():
    """A topic with no `confluence:` block is invisible to everything Zardoz
    does. Showing that here stops it looking like a retrieval failure."""
    output = dispatch("/topics", ShellState(topics=_topics()))

    assert "Access Review" in output
    assert "IAM Engineering" in output
    assert "policy, standard" in output
    assert "no pages declared" in output
    assert "1 topic(s) declare no Confluence pages" in output


def test_topics_without_a_registry_says_how_to_make_one():
    output = dispatch("/topics", ShellState(topics=[]))

    assert "config/topics.example.yaml" in output


# --------------------------------------------------------------------------
# Answering, before there is anything to answer from
# --------------------------------------------------------------------------


def test_a_question_with_no_corpus_refuses_instead_of_improvising():
    """The single most important property of the whole tool: a chatbot that
    invents what your access control standard requires is worse than one
    that admits it has not read it."""
    output = dispatch("what is our access review cadence?", ShellState())

    assert "nothing to answer from" in output
    assert "sync" in output


def test_a_question_is_not_mistaken_for_a_command():
    state = ShellState()
    dispatch("should I /quit my job", state)

    assert state.running is True


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def _scripted(lines):
    """A `read` that plays back lines, then raises EOF like a real stdin."""
    queue = list(lines)

    def read(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    return read


def test_the_loop_runs_until_quit():
    written: list[str] = []
    state = ShellState(topics=_topics())

    run_shell(state, read=_scripted(["/topics", "/quit", "/topics"]), write=written.append)

    assert state.running is False
    # The third line is never read, because /quit ended the loop.
    assert sum("Access Review" in line for line in written) == 1


def test_ctrl_d_exits_cleanly():
    """EOF is also what a piped stdin does when it runs out, so this is the
    path every non-interactive invocation takes."""
    written: list[str] = []

    run_shell(ShellState(), read=_scripted([]), write=written.append)

    assert any("silent" in line for line in written)


def test_ctrl_c_cancels_the_line_but_does_not_end_the_session():
    """Killing a session because someone interrupted a half-typed question
    is the kind of small hostility that makes a REPL unpleasant to live in."""
    written: list[str] = []
    calls = {"n": 0}

    def read(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt
        if calls["n"] == 2:
            return "/help"
        raise EOFError

    run_shell(ShellState(), read=read, write=written.append)

    assert any("patient" in line for line in written)
    assert any("Zardoz permits" in line for line in written)
    assert calls["n"] == 3  # it kept going after the interrupt


# --------------------------------------------------------------------------
# The structural guarantee
# --------------------------------------------------------------------------


def test_zardoz_cannot_reach_the_confluence_publish_path():
    """Zardoz is read-plus-propose: it may draft a `policyforge edit-topic`
    command, but the write goes through that command's own gates (dry run by
    default, macro refusal, citation checks, confirmation).

    Asserted against the package source rather than trusted to review, so
    that wiring a publish call in here fails a test rather than quietly
    shipping a chatbot with write access to live policy pages.

    Checked over the parsed AST rather than as a substring, so that the
    modules stay free to *discuss* the publish path in their docstrings —
    which they need to, since the reason they don't call it is the whole
    point. The walk covers every node, so a lazy import inside a function
    body is caught as readily as one at the top of the file.
    """
    import ast
    from pathlib import Path

    import policyforge.zardoz as pkg

    forbidden = {"update_page_body", "export_to_confluence", "confluence_exporter"}
    sources = list(Path(pkg.__file__).parent.glob("*.py"))
    assert sources, "expected to find the zardoz package sources"

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
        assert not leaked, f"{path.name} reaches the publish path: {', '.join(sorted(leaked))}"


# --------------------------------------------------------------------------
# The CLI entry point
# --------------------------------------------------------------------------


def _invoke(*args, stdin=""):
    from policyforge.cli import cli

    return CliRunner().invoke(cli, ["zardoz", *args], input=stdin)


def test_launching_prints_the_head_then_takes_input(tmp_path):
    result = _invoke("--topics", "config/topics.example.yaml", stdin="/quit\n")

    assert result.exit_code == 0, result.output
    assert "ZARDOZ SPEAKS TO YOU" in result.output
    assert "`--------'" in result.output  # the beard, i.e. the art rendered


def test_no_art_skips_the_head(tmp_path):
    result = _invoke("--no-art", "--topics", "config/topics.example.yaml", stdin="/quit\n")

    assert result.exit_code == 0, result.output
    assert "ZARDOZ SPEAKS TO YOU" in result.output
    assert "`--------'" not in result.output


def test_a_missing_registry_starts_anyway_and_says_what_is_wrong(tmp_path):
    """The shell is still useful without a registry, so this is a note on
    launch rather than a traceback."""
    result = _invoke("--topics", str(tmp_path / "absent.yaml"), stdin="/quit\n")

    assert result.exit_code == 0, result.output
    assert "no topic registry at" in result.output
    assert "topics.example.yaml" in result.output


def test_a_broken_registry_reports_the_error_without_crashing(tmp_path):
    bad = tmp_path / "topics.yaml"
    bad.write_text("topics:\n  - name: No Owner Here\n", encoding="utf-8")

    result = _invoke("--topics", str(bad), stdin="/quit\n")

    assert result.exit_code == 0, result.output
    assert "could not be read" in result.output
    assert "owner" in result.output


@pytest.mark.parametrize("flag", ["--plain", "--no-art"])
def test_the_flags_do_not_change_the_exit_path(flag, tmp_path):
    result = _invoke(flag, "--topics", "config/topics.example.yaml", stdin="/quit\n")

    assert result.exit_code == 0, result.output


# ---- A-09: the ledger knows which question each call answered -------------


class _AnsweringProvider:
    """Answers every call with one cited sentence; cannot be held to a schema."""

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text="Accounts are recertified quarterly. [1]", model="fake")

    def supports_schema(self):
        return False

    def check(self):
        return True


def _answering_state():
    from policyforge.llm import ledger
    from policyforge.zardoz.corpus import TRUSTED, Corpus
    from policyforge.zardoz.corpus import CorpusDocument as Doc

    corpus = Corpus(
        documents=[
            Doc(
                doc_id="acs",
                title="Access Control Standard",
                space="",
                confidence=TRUSTED,
                body=(
                    "# Access Control Standard\n\n## 4.1 Account Review\n\n"
                    "Accounts are recertified quarterly. [NIST AC-2]\n"
                ),
            )
        ]
    )
    return ShellState(plain=True, corpus=corpus, provider=ledger.wrap(_AnsweringProvider(), {}))


def test_every_call_a_turn_makes_is_recorded_against_its_question(tmp_path):
    """A challenged answer used to have a ledger record naming the model and
    nothing about what it was asked."""
    from policyforge.llm import ledger
    from policyforge.zardoz.shell import question_digest

    state = _answering_state()
    question = "how often are accounts recertified?"

    dispatch(question, state)

    records = ledger.load(tmp_path / "calls.jsonl")
    assert records, "the turn made model calls"
    assert all(r.site == "zardoz.answer" for r in records)
    assert all(r.subject.startswith(f"zardoz/{state.session_id}") for r in records)
    resolved = state.conversation.turns[-1].resolved
    assert records[-1].subject == f"zardoz/{state.session_id}/q-{question_digest(resolved)}"
    # A digest, never the question: the ledger records what was sent, not the text.
    assert question not in (tmp_path / "calls.jsonl").read_text(encoding="utf-8")


def test_each_session_has_its_own_id():
    assert ShellState().session_id != ShellState().session_id
