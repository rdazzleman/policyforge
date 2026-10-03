"""The Zardoz read-eval-print loop.

The loop itself is deliberately thin. Everything that decides *what to say*
lives in `dispatch()`, which is a pure function: line in, text out, with the
only side effect being flags it sets on the `ShellState` it is handed. That
split is what makes a conversational program testable — a test can drive a
hundred turns through `dispatch()` without a terminal, a subprocess, or a
fake stdin, and `run_shell()` stays small enough to read in one screen and
verify by eye.

The same split is what keeps retrieval and answering honest. Each is a
separate module returning a value this one only formats, so "did it find the
right section?" and "did it answer well from that section?" stay separately
testable rather than collapsing into one prompt nobody can debug.

Answering needs a model; retrieval does not. Running without one is
therefore supported rather than degraded: questions return the passages
themselves, which is the same evidence an answer would be built from, and an
API key is not the price of searching your own documents.

This module never imports the Confluence publish path. Zardoz is allowed to
*draft* an edit — it resolves the topic, works out what it would ask for and
hands back a `policyforge edit-topic` command to run — but the write itself
goes through that command's own gates: dry run by default, macro refusal,
citation checks, explicit confirmation. Keeping `update_page_body` out of
this module's import graph makes that a structural property rather than a
rule someone has to remember.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from policyforge.topics.registry import Topic

from .art import voice
from .conversation import Conversation, Turn, resolve_question
from .corpus import DEFAULT_CORPUS_DIR, Corpus, load_corpus
from .skills import skill_fields

PROMPT = "zardoz> "

#: How many documents of one kind `/corpus` lists before summarising the
#: rest. A twenty-topic registry publishes sixty pages, and a command whose
#: job is to orient you should not be the thing that scrolls the terminal.
_LIST_LIMIT = 15


@dataclass
class ShellState:
    """Everything one session knows.

    Held in one object rather than in closures so that a test can construct
    a session, drive turns through `dispatch()`, and then assert on what
    changed.
    """

    topics: list[Topic] = field(default_factory=list)
    plain: bool = False
    running: bool = True
    #: The synced document snapshot, or None when `zardoz sync` has not been
    #: run. Absent is a first-class state rather than an empty corpus: the
    #: two need different answers, since "nothing is synced" and "the
    #: documents do not mention that" are different facts about the world.
    corpus: Corpus | None = None
    #: Where the snapshot lives, so `/reload` can pick up a re-sync without
    #: the session having to end.
    corpus_dir: Path = DEFAULT_CORPUS_DIR
    #: Built on the first question rather than at launch, so opening the
    #: shell to run `/topics` doesn't pay for indexing a corpus nobody is
    #: about to search. Cleared whenever the corpus is replaced.
    _index: object | None = field(default=None, repr=False)
    #: The LLM that writes answers, or None when none is configured. Absent
    #: is a supported way to run: retrieval is offline, so the shell still
    #: finds and shows the passages, and the reader draws the conclusion.
    #: An API key should not be the price of searching your own documents.
    provider: object | None = field(default=None, repr=False)
    #: Why there is no provider, if there isn't one — shown once, when it
    #: first matters, rather than as a launch-time complaint.
    provider_note: str = ""
    #: The second model that judges whether a cited passage supports the
    #: claim made from it, or None when `entail.answering` is off — which is
    #: the default, because it costs a model call per cited sentence.
    entailer: object | None = field(default=None, repr=False)
    #: The last answer, so `/sources` can show what it was drawn from in
    #: full rather than in the excerpts that fit beside the prose.
    last_answer: object | None = field(default=None, repr=False)
    #: The last shadow comparison, for `/shadow`. None means no candidate
    #: retriever is configured, which is the default and is not a failure —
    #: see `shadow.py` for why these run at all.
    last_shadow: object | None = field(default=None, repr=False)
    #: The turns so far, so a follow-up can be resolved against them.
    #: "who owns that?" is only answerable in the light of what was asked
    #: before it, and that context lives here rather than in the model.
    conversation: Conversation = field(default_factory=Conversation)
    #: One id per session, so the ledger can gather a session's turns and a
    #: challenged answer can be traced to the question that produced it.
    session_id: str = field(default_factory=lambda: _new_session_id())
    #: Which surface drove the turn. "zardoz" is a person at a terminal;
    #: "mcp" is another agent calling a tool.
    #:
    #: A-09 put questions in the ledger so a challenged answer could be
    #: traced to the question that produced it. Once an MCP client can call
    #: `ask_documents`, "a person asked this" stops being implied — an agent
    #: can ask a hundred times in a loop, each one a billed model call that
    #: nobody typed. Recording the surface is what keeps "who asked" an
    #: answerable question, and it is the field that makes an unexpected
    #: line on a bill attributable to a tool rather than to a colleague.
    surface: str = "zardoz"
    #: What could not be loaded when this session opened, worded as the
    #: terminal prints it under its banner. The shell has already shown these
    #: by the time it reads a line; a surface with no banner — the MCP server —
    #: reports them itself, so an agent is told why an answer is empty rather
    #: than left to guess. See zardoz/startup.py.
    startup_notes: list[str] = field(default_factory=list)

    # Everything below is what the analyses need. Half the questions people
    # have about a compliance programme are not answerable from any
    # document — which controls nobody owns, what the catalog changed — and
    # those fall out of the registry, the catalogs and the ledger instead.
    config: dict = field(default_factory=dict)
    #: Control catalogs. Empty means "whatever the framework registry finds
    #: on disk", so opening the shell needs no --controls.
    controls_paths: tuple[Path, ...] = ()
    parameters_path: Path = Path("config/parameters.yaml")
    history_dir: Path = Path("output/.history")
    content_dir: Path | None = None

    @property
    def voice(self) -> dict[str, str]:
        return voice(plain=self.plain)

    @property
    def corpus_loaded(self) -> bool:
        return self.corpus is not None and len(self.corpus) > 0

    @property
    def index(self):
        """The retrieval index over the current corpus."""
        from .retrieve import build_index

        if self._index is None and self.corpus is not None:
            self._index = build_index(self.corpus)
        return self._index

    def set_corpus(self, corpus: Corpus | None) -> None:
        """Swap in a corpus and drop the index built over the old one.

        One method rather than two assignments at each call site: an index
        that outlived its corpus would answer from documents that are no
        longer there, which is the one failure a re-sync is supposed to fix.
        """
        self.corpus = corpus
        self._index = None


@dataclass
class Command:
    summary: str
    handler: Callable[[list[str], ShellState], str]
    aliases: tuple[str, ...] = ()


def _cmd_help(args: list[str], state: ShellState) -> str:
    """Rendered from the command table rather than written out, so it cannot
    drift out of date as commands are added."""
    width = max(len(name) for name in COMMANDS)
    lines = [state.voice["help_header"], ""]
    for name, command in COMMANDS.items():
        aliases = ", ".join("/" + alias for alias in command.aliases)
        alias_note = f"  (also {aliases})" if aliases else ""
        lines.append(f"  /{name.ljust(width)}  {command.summary}{alias_note}")
    lines += ["", "  Anything that does not start with / is treated as a question."]
    return "\n".join(lines)


def _cmd_quit(args: list[str], state: ShellState) -> str:
    state.running = False
    return state.voice["goodbye"]


def _cmd_topics(args: list[str], state: ShellState) -> str:
    """List the registry, flagging which topics have documents to sync.

    A topic with no `confluence:` block is invisible to everything Zardoz
    does, so it is worth showing that gap here rather than letting it look
    like a retrieval failure later.
    """
    if not state.topics:
        return (
            "No topic registry loaded. Copy config/topics.example.yaml to "
            "config/topics.yaml and set the owners to your own teams."
        )

    name_width = max(len(topic.name) for topic in state.topics)
    owner_width = max(len(topic.owner) for topic in state.topics)
    lines = [f"{len(state.topics)} topics:", ""]
    unpublished = 0
    for topic in state.topics:
        pages = topic.confluence_pages()
        if pages:
            where = ", ".join(tier for tier, _ in pages)
        else:
            where = "no pages declared"
            unpublished += 1
        lines.append(
            f"  {topic.name.ljust(name_width)}  {topic.owner.ljust(owner_width)}  [{where}]"
        )
    if unpublished:
        lines += [
            "",
            f"  {unpublished} topic(s) declare no Confluence pages, so there is "
            "nothing to read for them.",
        ]
    return "\n".join(lines)


def _cmd_corpus(args: list[str], state: ShellState) -> str:
    """Show what is synced, split by confidence.

    Worth its own command because the commonest cause of a disappointing
    answer is not the model but the corpus: a page that never synced cannot
    be quoted, and the difference is invisible from the answer alone.
    """
    if state.corpus is None:
        return (
            "No corpus synced. Run `policyforge zardoz sync` to read your markdown "
            "content tree, your published Confluence pages, or both."
        )

    corpus = state.corpus
    lines = [
        f"{len(corpus)} document(s), synced {corpus.synced_at or 'at an unknown time'}",
        f"  {len(corpus.trusted)} trusted    — owner known, from the registry or frontmatter",
        f"  {len(corpus.supporting)} supporting — real content nobody has claimed",
        f"  {len(corpus.from_markdown)} from markdown, {len(corpus.from_confluence)} from "
        "Confluence",
    ]
    if corpus.is_stale:
        age = corpus.age_days or 0
        lines += [
            "",
            f"  This snapshot is {age:.0f} days old. Anything published or edited since "
            "then is invisible here — re-sync, then /reload.",
        ]

    lines.append("")
    for doc in corpus.trusted[:_LIST_LIMIT]:
        where = doc.location or doc.space
        lines.append(f"  [{doc.tier or '?'}] {doc.title}  ({doc.topic} / {doc.owner})  {where}")
    if len(corpus.trusted) > _LIST_LIMIT:
        lines.append(f"  ... and {len(corpus.trusted) - _LIST_LIMIT} more")

    if corpus.supporting:
        lines.append("")
        for doc in corpus.supporting[:_LIST_LIMIT]:
            lines.append(f"  [supporting] {doc.title}  {doc.location or doc.space}")
        if len(corpus.supporting) > _LIST_LIMIT:
            lines.append(f"  ... and {len(corpus.supporting) - _LIST_LIMIT} more")

    brittle = [doc for doc in corpus.documents if not doc.is_editable]
    if brittle:
        lines += [
            "",
            f"  {len(brittle)} page(s) can be read but not safely edited (Confluence "
            "macros that would not survive a round trip).",
        ]
    return "\n".join(lines)


def _cmd_reload(args: list[str], state: ShellState) -> str:
    """Re-read the snapshot from disk without ending the session.

    The loop this exists for is the one a repo-backed setup makes normal:
    ask a question, notice the document is wrong, edit the markdown, ask
    again. Before this, "ask again" meant quitting the shell, and a tool you
    have to restart to see your own change is one people stop using.
    """
    try:
        state.set_corpus(load_corpus(state.corpus_dir))
    except FileNotFoundError:
        return (
            f"Still nothing synced at {state.corpus_dir}. Run `policyforge zardoz sync` "
            "in another terminal, then /reload."
        )
    except ValueError as exc:
        return str(exc)

    corpus = state.corpus
    local = len(corpus.from_markdown)
    return (
        f"Reloaded {len(corpus)} document(s) — {local} from markdown, "
        f"{len(corpus) - local} from Confluence, synced "
        f"{corpus.synced_at or 'at an unknown time'}."
    )


def _cmd_sources(args: list[str], state: ShellState) -> str:
    """Show, in full, what the last answer was drawn from.

    The answer lists its sources by title and section; this is the text
    itself. Checking a compliance answer means reading the requirement it
    came from, and making that a separate command keeps the answer short
    without putting the evidence out of reach.
    """
    answer = state.last_answer
    if answer is None or not getattr(answer, "passages", None):
        return "No sources yet — ask a question first."

    lines = []
    for number, passage in enumerate(answer.passages, start=1):
        marker = "  " if not answer.cited or number in answer.cited else " (uncited)"
        lines.append(f"[{number}]{marker} {passage.citation}")
        lines.append(_indent(" ".join(passage.chunk.text.split())))
        lines.append("")
    return "\n".join(lines).rstrip()


#: How many passages one question returns. Enough to cover a requirement
#: that is split across a Standard and its Procedure, few enough to read.
_PASSAGE_LIMIT = 4

#: A result at or below this is thin enough to be worth a second look
#: with guessed vocabulary. One passage is the shape that hides a
#: contradiction: the document that disagrees is simply not there, and
#: nothing in the answer can say so.
_THIN_RESULT = 1

#: ...and only while that result is at most this fraction of the corpus,
#: written as its reciprocal. A quarter: enough that there is plainly more
#: to look at, and not so lax that a small document set expands on every
#: question it answers perfectly well.
_THIN_SHARE = 4

#: Characters of a passage shown inline. The whole chunk is available to
#: whatever asks the index directly; this is what fits in a terminal without
#: burying the next result.
_EXCERPT_CHARS = 400


def _cmd_forget(args: list[str], state: ShellState) -> str:
    """Drop the conversation so the next question stands on its own.

    Follow-up resolution is a guess about intent, and a chain of them can
    drift: three turns after "access review" the subject has quietly become
    something adjacent, and every later question inherits it. Starting a new
    subject is the common case where that matters, and it is cheaper to
    offer a reset than to make the resolver clever enough not to need one.
    """
    count = len(state.conversation)
    state.conversation.clear()
    if not count:
        return "Nothing to forget — no questions asked yet."
    return f"Forgotten {count} turn(s). The next question will be read on its own."


def _cmd_shadow(args: list[str], state: ShellState) -> str:
    """Show where a candidate retriever disagreed with the live one.

    Nothing here has affected any answer. Dense retrieval, reranking and
    entailment are parked on evidence nobody has, because gathering it means
    running them — so they run beside the real retriever, their disagreement
    is recorded, and what the user sees is unchanged. This command is where
    that record is read.
    """
    from .shadow import ShadowReport

    report = getattr(state, "last_shadow", None)
    if report is None:
        if state.last_answer is None:
            return "No shadow comparison yet — ask a question first."
        return ShadowReport().render()
    return report.render()


COMMANDS: dict[str, Command] = {
    "help": Command("Show the available commands.", _cmd_help, aliases=("?",)),
    "shadow": Command("Show where a candidate retriever disagreed with the live one.", _cmd_shadow),
    "topics": Command("List the topic registry and who owns each topic.", _cmd_topics),
    "corpus": Command("Show which documents are synced and available.", _cmd_corpus),
    "sources": Command("Show the last answer's passages in full.", _cmd_sources),
    "forget": Command("Drop the conversation; ask the next question fresh.", _cmd_forget),
    "reload": Command("Re-read the snapshot from disk after a re-sync.", _cmd_reload),
    "quit": Command("Leave the shell.", _cmd_quit, aliases=("exit",)),
}


def _skill_command(name: str):
    """Wrap one analysis as a shell command.

    Generated from the registry rather than written out, so adding a skill
    adds its command, its help entry and its routing target at once and they
    cannot drift apart.
    """

    def handler(args: list[str], state: ShellState) -> str:
        from .skills import run_skill

        return run_skill(name, state, args)

    return handler


for _name, _summary in skill_fields():
    COMMANDS[_name] = Command(_summary, _skill_command(_name))


#: Flattened alias -> canonical name, built once from the table above.
_ALIASES = {alias: name for name, cmd in COMMANDS.items() for alias in cmd.aliases}


def dispatch(line: str, state: ShellState) -> str:
    """Turn one line of input into one block of output.

    Pure apart from the flags it sets on `state`, so the whole conversational
    surface can be tested without a terminal.
    """
    line = line.strip()
    if not line:
        return ""

    if not line.startswith("/"):
        return _answer(line, state)

    word, _, rest = line[1:].partition(" ")
    name = _ALIASES.get(word.lower(), word.lower())
    command = COMMANDS.get(name)
    if command is None:
        return state.voice["unknown_command"].format(command=f"/{word}")
    return command.handler(rest.split() if rest else [], state)


def _new_session_id() -> str:
    """A short id for one shell session, so its turns can be found together."""
    import secrets

    return secrets.token_hex(6)


def question_digest(question: str) -> str:
    """A short fingerprint of a question, for the ledger.

    The ledger records what was sent without recording the text, and a
    question is text an organization may not want written down. The digest
    is enough to match a challenged answer to the turn that produced it.
    """
    import hashlib

    return hashlib.sha256(question.strip().encode()).hexdigest()[:12]


def _answer(question: str, state: ShellState) -> str:
    """One turn, attributed in the ledger.

    Zardoz calls went into the ledger with no subject, so the day an answer
    was challenged the record could say which model answered but not which
    question it was answering. Every model call a turn makes — resolving,
    routing, expanding, answering — now shares one scope: the session at
    first, and the session plus a digest of the resolved question as soon
    as there is one.
    """
    from policyforge.llm import ledger

    with ledger.about(
        f"{state.surface}/{state.session_id}", site=f"{state.surface}.answer"
    ) as scope:
        return _answer_turn(question, state, scope)


def _answer_turn(question: str, state: ShellState, scope) -> str:
    """Answer a question from the synced corpus.

    Without a synced corpus there is nothing to answer *from*, and saying so
    is the only honest response — a chatbot that improvises an answer about
    what your access control standard requires is worse than one that admits
    it has not read it.

    With no LLM configured the passages themselves are shown instead. That
    is a supported way to run rather than a degraded one: retrieval is
    entirely offline, and an API key should not be the price of searching
    your own documents. The reader draws the conclusion, from the same
    evidence an answer would have been built out of.
    """
    from .answer import answer_question
    from .skills import NO_SKILL, route_plan, run_skill

    # Resolve before anything else. Retrieval is keyword scoring and has no
    # mechanism for "that", and neither has the router — rewriting the
    # question first is what turns a chain of follow-ups into a series of
    # separately answerable questions.
    try:
        resolved, rewritten = resolve_question(question, state.conversation, state.provider)
    except Exception:  # noqa: BLE001 - a failed rewrite must not lose the question
        resolved, rewritten = question, False

    # Now there is a question to name. Every call the turn makes from here on
    # is recorded against it; the rewrite above, made before there was a
    # resolved question, stays under the session alone.
    scope.subject = f"zardoz/{state.session_id}/q-{question_digest(resolved)}"

    preamble = [f"(reading that as: {resolved})", ""] if rewritten else []

    # Route before the corpus checks. "Which controls does nobody own?" is
    # answerable with nothing synced at all — it comes out of the registry
    # and the catalogs — and refusing it for want of a document corpus would
    # be answering a question nobody asked.
    from policyforge.llm.base import TruncatedResponse

    try:
        plan = route_plan(resolved, state.provider)
    except TruncatedResponse as exc:
        # Said aloud, then the documents: in the shell a person is waiting,
        # and a one-word routing reply that ran past its budget twice is a
        # model problem worth naming, not a reason to answer nothing. The
        # harness never takes this path — it calls route() directly and
        # grades the truncation as the failure it is.
        from .skills import Routed

        preamble += [
            f"(the model's routing reply was cut off at {exc.budget} tokens; "
            "reading the documents instead)",
            "",
        ]
        plan = [Routed(NO_SKILL)]
    if plan[0].skill != NO_SKILL:
        state.conversation.add(Turn(question=question, resolved=resolved, answer=""))
        blocks = []
        for routed in plan:
            args = routed.as_args()
            # Shown with its arguments, so the line reads as the command a
            # person could have typed to get the same thing. Routing can be
            # wrong in a way you can see only if what it decided is on screen,
            # and until now the scope it chose was invisible because there was
            # none: every routed analysis ran unnarrowed.
            ran = f"/{routed.skill}" + (" " + " ".join(args) if args else "")
            # The skill's output goes through untouched. A model that read the
            # numbers and told you about them could turn "14 orphaned" into
            # "mostly in the audit family", and there would be nothing to check
            # that against. Two analyses are two untouched reports, each under
            # the command that produced it — never merged into one account.
            blocks.append("\n".join([f"(ran {ran})", "", run_skill(routed.skill, state, args)]))
        return "\n".join([*preamble, "\n\n".join(blocks)])

    if state.corpus is None:
        return "\n".join(
            [
                *preamble,
                "No document corpus is synced yet, so I have nothing to answer from.",
                "Run `policyforge zardoz sync --content-dir <your markdown tree>` — "
                "that needs no Confluence credentials — then /reload.",
            ]
        )
    if not state.corpus_loaded:
        return "\n".join(
            [
                *preamble,
                "The corpus is empty — sync ran but found no documents. `/corpus` "
                "shows what it found, and `/topics` shows which topics declare "
                "pages at all.",
            ]
        )

    passages = state.index.search(resolved, limit=_PASSAGE_LIMIT)

    # Exact first, expansion on a miss — or on a thin result.
    #
    # "Only on a miss" was the original rule, on the reasoning that a search
    # already finding passages has nothing to gain from guessed vocabulary
    # and precision to lose. The precision half is handled elsewhere:
    # expansion terms score at EXPANSION_WEIGHT and are reported apart, so
    # a guess cannot outrank a word somebody typed.
    #
    # The other half was wrong, and it took measuring to see it. "Found
    # something" is not "found everything", and the gap between them is the
    # dangerous case rather than a cosmetic one: asked how often account
    # recertification happens, retrieval returned the Procedure saying
    # annually and never reached the Standard saying quarterly. Two
    # documents contradicted each other, one was invisible, and because a
    # passage *was* found the recovery path never ran. The answer was
    # confident, cited, and half the truth.
    #
    # So a single passage is treated as thin. It is the shape that hides a
    # disagreement, and the one where a second opinion on vocabulary costs
    # nothing but a call.
    # Judged against the corpus, never as a bare count. A fixed number of
    # passages means something different at three chunks than at three
    # hundred — the same mistake this project already made with an absolute
    # score floor, which was structurally unreachable in a small corpus.
    # One passage out of two is complete coverage and nothing is missing;
    # one out of two hundred is a sliver.
    thin = len(passages) <= _THIN_RESULT and len(passages) * _THIN_SHARE <= len(state.index)
    if thin and state.provider is not None:
        from .paraphrase import expand_query

        try:
            expansion = expand_query(resolved, state.provider)
        except TruncatedResponse as exc:
            expansion = ""
            preamble += [
                f"(the model's search-expansion reply was cut off at {exc.budget} tokens; "
                "searched the question's own words only)",
                "",
            ]
        if expansion:
            widened = state.index.search(resolved, limit=_PASSAGE_LIMIT, expansion=expansion)
            if len(widened) > len(passages):
                found_nothing = not passages
                passages = widened
                preamble += [
                    f"({'nothing matched those words' if found_nothing else 'that found little'}; "
                    f"searched also for: {expansion})",
                    "",
                ]

    def _record(answer_text: str) -> None:
        state.conversation.add(
            Turn(question=question, resolved=resolved, passages=passages, answer=answer_text)
        )

    if state.provider is None:
        _record("")
        if not passages:
            return "\n".join(
                [
                    *preamble,
                    "Nothing in the synced documents appears to bear on that.",
                    "That may be the answer — the documents genuinely may not say — or "
                    "the page you want may not be synced; `/corpus` shows what is.",
                ]
            )
        note = state.provider_note or "No LLM configured"
        return "\n".join(
            [
                *preamble,
                f"{note}, so here are the passages themselves:",
                "",
                _render_passages(passages),
            ]
        )

    try:
        answer = answer_question(resolved, passages, state.provider, entailer=state.entailer)
    except TruncatedResponse as exc:
        # The model was reached and answered; its answer ran past the
        # budget twice and is not shown, because half an answer with the
        # citations at the end cut off reads as a whole one. The passages
        # are the evidence and are shown as they would be with no model.
        lines = [
            f"The model's answer was cut off at {exc.budget} output tokens "
            f"(stop_reason {exc.stop_reason!r}) and is not shown. Ask a narrower question, "
            f"or raise the answering budget."
        ]
        if passages:
            lines += ["", "The passages that bear on it:", "", _render_passages(passages)]
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001 - any provider/SDK failure, session survives
        lines = [f"The model could not be reached ({type(exc).__name__}: {exc})."]
        if passages:
            lines += ["", "The passages that bear on it:", "", _render_passages(passages)]
        return "\n".join(lines)

    state.last_answer = answer
    _record("" if answer.refused else answer.text)
    return "\n".join([*preamble, _render_answer(answer)])


def _render_answer(answer) -> str:
    """Lay out an answer so the evidence is never further away than the claim."""
    lines: list[str] = []

    # Warnings go first. An integrity problem discovered after the fact is
    # only useful if the reader sees it before they believe the sentence it
    # is about.
    if answer.warnings:
        lines.append("!! This answer did not pass its own checks:")
        lines += [f"!!   - it {warning}" for warning in answer.warnings]
        lines.append("!! Read the passages below rather than trusting the prose.")
        lines.append("")

    lines.append(answer.text)

    if answer.refused and answer.passages:
        lines += ["", "Closest passages:", "", _render_passages(answer.passages)]
        return "\n".join(lines)
    if answer.refused:
        return "\n".join(lines)

    sources = answer.sources() or list(enumerate(answer.passages, start=1))
    lines += ["", "Sources:"]
    for number, passage in sources:
        lines.append(f"  [{number}] {passage.citation}")
        if not passage.is_trusted:
            lines.append("        (supporting — nobody has claimed this document)")
    lines.append("")
    lines.append("  /sources shows these in full.")
    return "\n".join(lines)


def _render_passages(passages) -> str:
    lines: list[str] = []
    for rank, passage in enumerate(passages, start=1):
        lines.append(f"{rank}. {passage.citation}")
        if not passage.is_trusted:
            lines.append("   (supporting — real content, but nobody has claimed it)")
        lines.append(_indent(_excerpt(passage.chunk.text)))
        why = []
        if passage.matched_controls:
            why.append("cites " + ", ".join(passage.matched_controls))
        if passage.matched_terms:
            why.append("matched " + ", ".join(passage.matched_terms[:6]))
        if passage.matched_expansions:
            why.append("via " + ", ".join(passage.matched_expansions[:4]) + " (guessed)")
        if why:
            lines.append(f"   [{'; '.join(why)}]")
        lines.append("")
    return "\n".join(lines).rstrip()


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _EXCERPT_CHARS:
        return collapsed
    return collapsed[:_EXCERPT_CHARS].rsplit(" ", 1)[0] + " ..."


def _indent(text: str) -> str:
    return "\n".join(f"   {line}" for line in textwrap.wrap(text, width=76)) or "   (empty)"


def run_shell(
    state: ShellState,
    *,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    """Drive the loop until the user leaves.

    The two ways out of a terminal program are handled differently on
    purpose. Ctrl-D (EOF, and also what a piped stdin does when it runs out)
    means "I am done" and exits. Ctrl-C means "abandon the line I am typing"
    and must *not* exit — killing a session because someone interrupted a
    half-typed question is the kind of small hostility that makes a REPL
    unpleasant to live in.
    """
    while state.running:
        try:
            line = read(PROMPT)
        except EOFError:
            write(state.voice["goodbye"])
            return
        except KeyboardInterrupt:
            write("")
            write(state.voice["interrupt"])
            continue

        # A skill that raises used to end the session with a traceback, and
        # the state with it (#302). Caught here, around `dispatch` only, and
        # never silently: a refusal prints its class and message, anything
        # else prints its whole traceback, and either way the next line says
        # the command FAILED -- nothing a skill produced before raising is
        # shown, so a failure cannot read as the command having worked.
        # `Exception`, not `BaseException`: Ctrl-C and `SystemExit` keep
        # their meaning.
        try:
            output = dispatch(line, state)
        except Exception as exc:  # noqa: BLE001 - every failure reported; see above
            write(describe_failure(exc, state))
            continue
        if output:
            write(output)


def refusal_types() -> tuple[type[BaseException], ...]:
    """Exceptions the product raises on purpose, with a message for the user.

    A refusal prints as its class and message; anything else prints a full
    traceback, because it is a bug and must stay visible. Imported here, on
    failure, rather than at module load: several of these live beside
    exporters and loaders the shell does not otherwise need.

    **The classification only decides presentation.** A refusal missing from
    this list still surfaces, with a traceback, so the list can be wrong in
    the safe direction only. `tests/test_zardoz_skill_failure.py` derives
    every exception class the package defines and requires each one to be
    classified, so a new one cannot be forgotten.

    `ConcurrentEditError` is absent on purpose: it belongs to the Confluence
    publish path, which this package may not import at all
    (`test_zardoz_cannot_reach_the_confluence_publish_path`), so no skill can
    raise it.
    """
    import click

    from policyforge.child_output import UndecodableOutput
    from policyforge.content.tree import ContentError
    from policyforge.crosswalk.overlay import OverlayError
    from policyforge.edit.apply import EchoedFenceError
    from policyforge.edit.tree import TreeEditError
    from policyforge.export.confluence_search import SearchLimitExceeded
    from policyforge.generate.playbook_repair import PlaybookRepairFailed
    from policyforge.ingest.ai_rmf import AiRmfParseError
    from policyforge.ingest.ai_rmf_playbook import AiRmfPlaybookError
    from policyforge.ingest.csf import CsfError
    from policyforge.ingest.hipaa_privacy import HipaaPrivacyError
    from policyforge.ingest.levelled import HitrustBuildError
    from policyforge.ingest.nist_rev4_rev5 import Rev4Rev5Error
    from policyforge.ingest.onc_loader import OncParseError
    from policyforge.llm._inline_thinking import ReasoningBudgetExhausted
    from policyforge.llm.base import (
        EmptyReply,
        ProviderRejected,
        SchemaReplyError,
        TruncatedResponse,
    )
    from policyforge.llm.batch import BatchError
    from policyforge.llm.boundary import BoundaryViolation

    # The licensed-framework plugin seam (#32): its five refusals, and a parser's own
    # refusal of a file. Each says what to do; none is a bug.
    from policyforge.plugins import ParseRefused, PluginError
    from policyforge.topics.coverage import ScopingError
    from policyforge.topics.registry import TopicRegistryError

    return (
        click.ClickException,
        UndecodableOutput,
        ContentError,
        OverlayError,
        EchoedFenceError,
        TreeEditError,
        SearchLimitExceeded,
        PlaybookRepairFailed,
        AiRmfParseError,
        AiRmfPlaybookError,
        CsfError,
        HipaaPrivacyError,
        Rev4Rev5Error,
        HitrustBuildError,
        OncParseError,
        ReasoningBudgetExhausted,
        EmptyReply,
        ProviderRejected,
        SchemaReplyError,
        TruncatedResponse,
        BatchError,
        BoundaryViolation,
        ScopingError,
        TopicRegistryError,
        PluginError,
        ParseRefused,
    )


def describe_failure(exc: Exception, state: ShellState) -> str:
    """What a failed command prints: the refusal or the traceback, then a
    line saying it failed and that the session goes on."""
    import traceback

    # If the refusal list itself cannot load, every failure is shown as a bug
    # -- a traceback plus the failed line -- rather than raising here, inside
    # the loop's handler, and ending the session: #302 reached through its
    # own fix (9b's finding on #303).
    try:
        kinds = refusal_types()
        unavailable = ""
    except Exception as lookup:  # noqa: BLE001 - reported below, not swallowed
        kinds = ()
        # Said, not swallowed (1d, on #303): the command's failure prints as a
        # bug either way, and this names why it could not be told apart.
        unavailable = f"\n(refusal list unavailable: {type(lookup).__name__}: {lookup})"
    if isinstance(exc, kinds):
        message = exc.format_message() if hasattr(exc, "format_message") else str(exc)
        head = f"{type(exc).__name__}: {message}"
    else:
        head = "".join(traceback.format_exception(exc)).rstrip()
    return f"{head}{unavailable}\n{state.voice['command_failed']}"
