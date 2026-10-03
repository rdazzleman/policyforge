"""Opening a Zardoz session: what loads, what is missing, and what to say about it.

Two things open sessions. `policyforge zardoz` opens one for a person at a
terminal; `policyforge mcp` opens one for another agent. They used to build
their sessions separately — the CLI inline, entangled with printing its
banner, and the MCP server as a shorter copy — and the copy had drifted in
the direction that matters most to whoever is on the other end:

* It dropped every note. A person was told "topic registry at X could not be
  read: <why>"; an agent got nothing, so with a broken `topics.yaml` it saw an
  empty registry and `coverage` answered "No topic registry loaded" — a
  confident misdiagnosis of a file that exists and is broken.
* It never said a snapshot was stale, so an agent could answer from a corpus
  two months old with no sign of it.

Here, loading and explaining are one step. Every absence is a supported way
to run — no registry, no synced corpus, no model — and each comes back as a
note in the words the terminal has always used. The CLI prints them under its
banner; the MCP server reports them to the calling agent. Neither decides
what is worth mentioning on its own any more.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .shell import ShellState


@dataclass
class Session:
    """A ready session and what was wrong when it opened."""

    state: ShellState
    #: In the order a person should read them, each already worded the way the
    #: terminal prints it. Empty when nothing was missing.
    notes: list[str] = field(default_factory=list)


def load_registry(topics_path: Path) -> tuple[list, str]:
    """The topic registry, or an empty one and the reason.

    Separate from `open_session` because `policyforge zardoz sync` and
    `discover` need the registry without opening a shell, and loading it twice
    would be two chances to disagree about why it failed.
    """
    from policyforge.topics.registry import TopicRegistryError, load_topics

    # A missing or broken registry is not fatal: the shell is still useful
    # without it, and starting up to say what's wrong beats a traceback.
    try:
        return load_topics(topics_path), ""
    except FileNotFoundError:
        return [], (
            f"  (no topic registry at {topics_path} — /topics will be empty. "
            "Copy config/topics.example.yaml to start one.)"
        )
    except TopicRegistryError as exc:
        return [], f"  (topic registry at {topics_path} could not be read: {exc})"


def open_session(
    *,
    topics_path: Path,
    corpus_dir: Path,
    config: dict,
    provider_factory: Callable[[dict], object],
    registry: tuple[list, str] | None = None,
    surface: str = "zardoz",
    plain: bool = False,
    history_dir: Path = Path("output/.history"),
) -> Session:
    """Load everything a session needs and say what could not be loaded.

    `provider_factory` is passed in rather than imported, so the CLI can hand
    over the seam every command uses — tests substitute a fake provider by
    patching `policyforge.cli.get_provider`, and a session that imported the
    real factory here would not see the patch.

    `registry` takes an already-loaded `(topics, note)` from `load_registry`,
    for a caller that needed the topics before deciding to open a session.
    """
    from .corpus import load_corpus

    topics, registry_note = registry if registry is not None else load_registry(topics_path)

    # An unsynced corpus is a normal state to open the shell in, not an
    # error: /topics and /corpus both still answer, and /corpus is where the
    # explanation of what to do about it lives.
    corpus, corpus_note = None, ""
    try:
        corpus = load_corpus(corpus_dir)
    except FileNotFoundError:
        corpus_note = "  (no documents synced yet — run `policyforge zardoz sync`)"
    except ValueError as exc:
        corpus_note = f"  ({exc})"

    # An LLM is optional. Without one the shell still finds and shows the
    # passages a question is about — retrieval is entirely offline — so a
    # missing API key costs you the prose, not the search.
    provider, provider_note = None, ""
    try:
        provider = provider_factory(config)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as exc:
        # RuntimeError is what a missing API key raises, which is the most
        # common way to open this shell — running without a model is a
        # supported mode, so it must not be a crash on launch. It was one
        # until the test suite stopped carrying a live key.
        provider_note = f"No model configured ({str(exc).splitlines()[0]})"

    # The judge is opt-in and costs a call per cited sentence, so a session
    # that could not build one says so rather than answering as though the
    # check had run and found nothing.
    entailer, entail_note = None, ""
    if (config.get("entail") or {}).get("answering"):
        try:
            from policyforge.entail import get_entailer

            entailer = get_entailer(config)
            if entailer is None:
                entail_note = "entail.answering is set but there is no entail block to build from"
        except (KeyError, ValueError, RuntimeError) as exc:
            entail_note = f"entailment is on but its judge could not be built ({exc})"

    configured_content = (config.get("zardoz") or {}).get("content_dir") or ""

    notes = [note for note in (registry_note, corpus_note) if note]
    if provider_note:
        notes.append(f"  ({provider_note} — questions will return passages, not prose)")
    if entail_note:
        notes.append(f"  ({entail_note} — answers will not be entailment-checked)")
    elif entailer is not None:
        notes.append("  (entailment checking on — one extra model call per cited sentence)")
    if corpus is not None and corpus.is_stale:
        notes.append(f"  (this snapshot is {corpus.age_days:.0f} days old — re-sync, then /reload)")

    state = ShellState(
        topics=topics,
        plain=plain,
        corpus=corpus,
        corpus_dir=corpus_dir,
        provider=provider,
        provider_note=provider_note,
        # What the analyses read. Paths rather than loaded data: a shell
        # opened to ask one question should not pay to parse a thousand
        # controls it may never look at.
        config=config,
        parameters_path=Path("config/parameters.yaml"),
        history_dir=history_dir,
        content_dir=Path(configured_content) if configured_content else None,
        surface=surface,
        startup_notes=list(notes),
    )
    return Session(state=state, notes=notes)
