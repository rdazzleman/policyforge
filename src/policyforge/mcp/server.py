"""The read-only half of PolicyForge, as tools an agent can call.

Zardoz is a terminal REPL and nothing else, which means the analyses only
reach whoever is willing to open a shell. `dispatch()` was written as a pure
function precisely so another surface could drive it, and this is that
surface: the skills, retrieval and the edit *planner*, exposed over MCP so
Claude Code, Claude Desktop or any agent can ask "which controls does nobody
own" against a real repository — with the content boundary, the ledger and
the citation checks all still in the path, because the answer comes from the
same functions the CLI calls.

**Nothing here writes, and nothing here fetches.** Not by convention —
structurally. `tests/test_mcp_server.py` walks this package's AST and fails
if any module so much as names the publish path, mirroring the guard that
already protects `zardoz/`. A tool that wanted to publish could not be
written in this file.

**`plan_edit` is deliberately absent**, though the roadmap asks for it, and
the reason is worth stating rather than leaving as an omission. Planning an
edit means fetching the live wiki page first. That page is untrusted input —
finding S-01 — and the fence around it was red-teamed against real models
with a result that was not clean: one model obeyed a page claiming to speak
for the operator three times out of three. Today the person who triggers a
plan reads it before anything happens. Exposing it as a tool puts an
automated caller on the other end of that fence, where a page could steer a
planner whose output another model consumes without a person in between.
That may well be acceptable with the right gating, and it is a decision
about risk rather than a missing function, so it is not being made here.
Every tool below reads what is already synced to disk.

The transport is stdio, so this is a local subprocess an MCP client spawns,
not a network listener. It binds no port and accepts no remote connection.

Tool output is returned verbatim, for the same reason `skills.py` prints it
verbatim: a paraphrase of "14 orphaned controls" can become "mostly in the
audit family" with nothing to check it against. The calling model is free to
summarise what it is given — that is its business — but what it is given is
the report itself, not this server's account of it.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Ships as an optional extra: `pip install policyforge[mcp]`.
#:
#: Declared rather than assumed. It used to reach this project's own venv
#: only as a transitive dependency of semgrep — a dev tool. Building against
#: that would give a feature that works for anyone who installed the dev
#: extras and fails for everyone else, which is the same shape as the
#: litellm/.env problem S-09 fixed. semgrep now has its own environment, and
#: the CI lock installs this extra by name.
_INSTALL_HINT = (
    "The MCP server needs the `mcp` package, which ships as an optional extra:\n"
    "    pip install 'policyforge[mcp]'\n"
    "It is deliberately not a base dependency — most people use the CLI."
)


@dataclass(frozen=True)
class ToolSpec:
    """One exposed analysis: what it is called, and what it takes."""

    name: str
    description: str
    #: JSON Schema for the tool's arguments. Written out rather than
    #: generated from the skill registry because the registry's `answers`
    #: text is prose aimed at this project's own router, and an agent
    #: choosing between tools needs argument shapes, not routing hints.
    schema: dict


def _text_schema(name: str, description: str, *, required: bool) -> dict:
    return {
        "type": "object",
        "properties": {name: {"type": "string", "description": description}},
        "required": [name] if required else [],
        "additionalProperties": False,
    }


#: Every tool this server offers. A closed list, written by hand.
#:
#: Generating one tool per registered skill was the obvious move and is the
#: wrong one: the registry exists so a *router* can choose, and it grows
#: whenever somebody adds an analysis. A closed list means adding a tool is
#: a deliberate act with a diff, which is the property that makes "nothing
#: here writes" checkable rather than aspirational.
TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="ask_documents",
        description=(
            "Answer a question from the organization's own synced policy documents, "
            "with citations. Returns the grounded answer and the passages it rests "
            "on, or says the documents do not answer it. Never answers from general "
            "knowledge — if nothing is synced, it says so. "
            "This is the only tool here that calls a language model, so it is the "
            "only one that costs anything; the others are set arithmetic over local "
            "files. Prefer them when they answer the question."
        ),
        schema=_text_schema("question", "The question, in plain language.", required=True),
    ),
    ToolSpec(
        name="coverage",
        description=(
            "Which in-scope controls no topic owns, and which are claimed by two "
            "topics. Set arithmetic over the topic registry; no model involved. "
            "Optionally limited to a NIST baseline: low, moderate or high."
        ),
        schema=_text_schema(
            "baseline", "low, moderate or high. Omit for all controls.", required=False
        ),
    ),
    ToolSpec(
        name="team_bundle",
        description=(
            "Everything one team answers for: its topics, the requirements those "
            "topics own, the documents it must keep current, and its review "
            "cadence. Takes the owner exactly as the registry spells it."
        ),
        schema=_text_schema("owner", "The team name, as written in the registry.", required=True),
    ),
    ToolSpec(
        name="addresses",
        description=(
            "Who answers for one requirement and which document says so. Takes any "
            "requirement id — NIST, HIPAA, HITRUST — and resolves it through the "
            "published crosswalk. Says for each claim whether it was anchored "
            "directly, inherited from an anchored parent (a control, or an AI RMF "
            "category), or only reached through a "
            "crosswalk, which is the weakest of the three."
        ),
        schema=_text_schema("requirement", "A requirement id, e.g. AC-2.", required=True),
    ),
    ToolSpec(
        name="parameters",
        description=(
            "Organization-defined values that have been decided and those still "
            "outstanding — the frequencies and thresholds nobody has chosen yet."
        ),
        schema=_text_schema("baseline", "low, moderate or high. Omit for all.", required=False),
    ),
    ToolSpec(
        name="corpus",
        description=(
            "Which documents are synced and available to answer from, and how stale "
            "the snapshot is. Ask this first when an answer says nothing is synced."
        ),
        schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="topics",
        description=(
            "The topic registry: every topic, who owns it, and which documents it "
            "publishes. The map of the programme, and the right first call when a "
            "team or topic name is needed for another tool."
        ),
        schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
)


#: tool name -> (what runs it, which argument it takes)
#:
#: A shell command for the two that are commands, a skill for the rest. The
#: mapping is explicit because tool names are an interface an agent depends
#: on, while skill names are this project's internal vocabulary — pinning
#: them together would mean renaming a skill silently renamed a tool.
_DISPATCH = {
    "corpus": ("command", "/corpus", None),
    "topics": ("command", "/topics", None),
    "coverage": ("skill", "coverage", "baseline"),
    "team_bundle": ("skill", "bundle", "owner"),
    "addresses": ("skill", "addresses", "requirement"),
    "parameters": ("skill", "parameters", "baseline"),
}


def build_state(
    config: dict | None = None, *, corpus_dir=None, topics_path=None, provider_factory=None
):
    """A shell state for the server to run analyses against.

    The same session the terminal opens, built by the same function —
    `zardoz/startup.py` — so a tool's answer and what it says about a missing
    registry, corpus or model match the terminal's. This used to be a shorter
    copy of the CLI's construction, and the copy had drifted: it swallowed
    every note, so a broken topics.yaml looked to an agent like no registry at
    all, and a two-month-old snapshot gave no sign of its age.

    `provider_factory` defaults to the real one; tests pass their own.
    """
    from pathlib import Path

    from policyforge.zardoz.corpus import DEFAULT_CORPUS_DIR
    from policyforge.zardoz.startup import open_session

    if provider_factory is None:
        from policyforge.llm.base import get_provider as provider_factory

    session = open_session(
        topics_path=Path(topics_path) if topics_path else Path("config/topics.yaml"),
        corpus_dir=Path(corpus_dir) if corpus_dir else DEFAULT_CORPUS_DIR,
        config=config or {},
        provider_factory=provider_factory,
        # Every model call this server causes is recorded as `mcp/<session>`
        # rather than `zardoz/<session>`. Six of the seven tools call no model
        # at all; `ask_documents` does, and an agent can call it in a loop
        # without anyone typing. The ledger has to be able to say which of
        # those a cost came from.
        surface="mcp",
        plain=True,
    )
    return session.state


def with_startup_notes(report: str, state) -> str:
    """`report`, followed by anything that could not be loaded at startup.

    Appended to every tool's result rather than only to `topics` and `corpus`,
    because the misdiagnosis this prevents shows up in the analyses: with a
    broken registry, `coverage` says "No topic registry loaded", and an agent
    that called `coverage` first would never otherwise learn the registry is
    there and unreadable. The report itself is untouched — the notes follow
    it, marked as being about the server rather than about the question.
    """
    notes = [note.strip() for note in getattr(state, "startup_notes", []) or []]
    if not notes:
        return report
    listed = "\n".join(f"  {note}" for note in notes)
    return f"{report}\n\nWhen this PolicyForge server started:\n{listed}"


def call_tool(state, name: str, arguments: dict) -> str:
    """Run one tool and return its report verbatim.

    Errors come back as text rather than as exceptions. A tool call that
    fails should tell the calling agent what is missing — an unsynced
    corpus, an absent registry — in the same words the CLI would use, rather
    than surfacing a traceback it cannot act on.
    """
    from policyforge.zardoz.shell import dispatch
    from policyforge.zardoz.skills import run_skill

    arguments = arguments or {}

    if name == "ask_documents":
        question = str(arguments.get("question", "")).strip()
        if not question:
            return "Ask a question — this tool answers from the synced documents."
        # Through `dispatch`, not straight to `answer_question`: dispatch is
        # what applies the refusal rendering, the citation checks and the
        # conversation context, and a tool that skipped them would be a
        # second, laxer answering path.
        return with_startup_notes(dispatch(question, state), state)

    if name not in _DISPATCH:
        return f"No such tool: {name}"

    kind, target, argument = _DISPATCH[name]
    if kind == "command":
        return with_startup_notes(dispatch(target, state), state)

    value = str(arguments.get(argument, "")).strip() if argument else ""
    return with_startup_notes(run_skill(target, state, value.split() if value else []), state)


def serve(config: dict | None = None, *, corpus_dir=None, topics_path=None) -> None:
    """Run the server on stdio until the client disconnects.

    Imported lazily, and the missing-dependency message names the extra to
    install rather than letting an ImportError reach the user.
    """
    try:
        import mcp.types as types
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(_INSTALL_HINT) from exc

    import anyio

    state = build_state(config, corpus_dir=corpus_dir, topics_path=topics_path)
    # stdout is the protocol channel; stderr is where an MCP client keeps the
    # server's log, so that is where the operator who configured it looks.
    import sys

    for note in state.startup_notes:
        print(f"policyforge mcp:{note}", file=sys.stderr)
    server = Server("policyforge")

    @server.list_tools()
    async def list_tools() -> list:
        return [
            types.Tool(name=tool.name, description=tool.description, inputSchema=tool.schema)
            for tool in TOOLS
        ]

    @server.call_tool()
    async def handle(name: str, arguments: dict) -> list:
        # Run off the event loop: every analysis here is synchronous and
        # some read the whole corpus, and blocking the loop would stall the
        # client's other calls.
        text = await anyio.to_thread.run_sync(lambda: call_tool(state, name, arguments))
        return [types.TextContent(type="text", text=text)]

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(_run)
