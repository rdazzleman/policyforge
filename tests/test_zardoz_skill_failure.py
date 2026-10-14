"""A skill that raises no longer ends the shell session (#302).

`run_shell` wrapped only `read()`, so any exception a skill raised ended the
session with a traceback and lost its state -- including #294's
`UndecodableOutput`, a refusal made loud on purpose, which then took the
user's session down with it.

80's ruling: keep the session; print a refusal as its class and message; print
anything unexpected as a traceback, because it is a bug and must stay
visible; and **never print anything that reads as the command succeeding**.
Every test here drives the real loop, not `dispatch()`, because the loop is
what was missing the handler.
"""

from __future__ import annotations

import ast
from pathlib import Path

import click
import pytest

from policyforge.zardoz import shell
from policyforge.zardoz.art import PLAIN_VOICE, VOICE
from policyforge.zardoz.shell import Command, ShellState, refusal_types, run_shell

SRC = Path(shell.__file__).resolve().parents[1]
FAILED = VOICE["command_failed"]


def _scripted(lines):
    queue = list(lines)

    def read(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    return read


def _run(monkeypatch, handler, lines, state=None):
    monkeypatch.setitem(shell.COMMANDS, "boom", Command("raises", handler))
    written: list[str] = []
    state = state or ShellState()
    run_shell(state, read=_scripted(lines), write=written.append)
    return state, written


def _refuse(args, state):
    raise click.ClickException("the catalog is pinned to r5 and this is r4")


def _undecodable(args, state):
    from policyforge.child_output import strict_text

    return strict_text(b"ok \xff", site="/drift", argv=["git", "log", "-p"])


def _bug(args, state):
    success = "Drift checked: nothing changed."  # computed, then the bug  # noqa: F841
    return {}["missing"]


def test_a_refusal_prints_its_message_and_the_session_continues(monkeypatch):
    state, written = _run(monkeypatch, _refuse, ["/boom", "/help"])

    text = "\n".join(written)
    assert "ClickException: the catalog is pinned to r5 and this is r4" in text
    assert FAILED in text
    assert "Traceback" not in text, "an expected refusal is not a crash"
    assert any("Zardoz permits" in line for line in written), "the next command still ran"
    assert state.running is True or any("silent" in line for line in written)


def test_the_refusal_294_made_loud_no_longer_ends_the_session(monkeypatch):
    """The case the issue names: `UndecodableOutput` from `/drift`."""
    _, written = _run(monkeypatch, _undecodable, ["/boom", "/help"])

    text = "\n".join(written)
    assert "UndecodableOutput: /drift: `git log -p` wrote output that is not UTF-8" in text
    assert "byte 0xff at position 3" in text
    assert FAILED in text
    assert any("Zardoz permits" in line for line in written)


def test_a_bug_prints_its_traceback_and_the_session_continues(monkeypatch):
    """**Not a silent swallow.** An unexpected exception is a bug, so its
    whole traceback is shown -- and the session still survives."""
    _, written = _run(monkeypatch, _bug, ["/boom", "/help"])

    text = "\n".join(written)
    assert "Traceback (most recent call last)" in text
    assert "KeyError: 'missing'" in text
    assert FAILED in text
    assert any("Zardoz permits" in line for line in written)


@pytest.mark.parametrize("handler", [_refuse, _undecodable, _bug])
def test_a_failed_command_never_reads_as_a_success(monkeypatch, handler):
    _, written = _run(monkeypatch, handler, ["/boom"])

    text = "\n".join(written)
    assert "nothing changed" not in text
    assert FAILED in text, "every failure says it failed"


def test_an_ordinary_skill_prints_normally(monkeypatch):
    """The passing twin: a skill that returns prints its output, and nothing
    about failure appears."""
    _, written = _run(monkeypatch, lambda args, state: "Drift checked: nothing changed.", ["/boom"])

    assert "Drift checked: nothing changed." in written
    assert not any(FAILED in line for line in written)


def test_the_session_state_survives_the_failure(monkeypatch):
    state = ShellState()
    state.last_answer = "kept"
    state, _ = _run(monkeypatch, _bug, ["/boom"], state=state)

    assert state.last_answer == "kept"


def test_ctrl_c_inside_a_skill_is_not_swallowed(monkeypatch):
    """Ctrl-C and Ctrl-D are unchanged: a KeyboardInterrupt is not an
    Exception, so the handler never sees it."""

    def interrupted(args, state):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, interrupted, ["/boom"])


def test_a_refusal_list_that_cannot_load_does_not_end_the_session(monkeypatch):
    """**#302 reached through its own fix** (9b's finding). If an import
    inside `refusal_types()` ever fails, the failure is reported as a bug,
    with a traceback, instead of raising inside the loop's handler."""

    def broken():
        raise ImportError("an optional dependency went missing")

    monkeypatch.setattr(shell, "refusal_types", broken)
    _, written = _run(monkeypatch, _refuse, ["/boom", "/help"])

    text = "\n".join(written)
    assert "Traceback (most recent call last)" in text
    assert "ClickException" in text
    assert "(refusal list unavailable: ImportError: an optional dependency went missing)" in text
    assert FAILED in text
    assert any("Zardoz permits" in line for line in written), "the session went on"


def test_a_loadable_refusal_list_adds_no_unavailable_line(monkeypatch):
    """Quiet twin: the note appears only when the list failed to load."""
    _, written = _run(monkeypatch, _refuse, ["/boom"])

    assert not any("refusal list unavailable" in line for line in written)


def test_the_plain_voice_says_it_failed_too():
    assert "failed" in PLAIN_VOICE["command_failed"]
    assert set(PLAIN_VOICE) == set(VOICE)


# --------------------------------------------------------------------------
# Every exception the package defines is classified
# --------------------------------------------------------------------------

#: Defined in the package and deliberately NOT a user-facing refusal. A new
#: exception class must be added here or to `refusal_types()`.
NOT_REFUSALS = {
    # A sentinel for an `except` clause that must never match.
    "policyforge.llm.litellm_provider._NeverRaised",
    # The Confluence publish path's refusal. The shell may not import that
    # module at all (`test_zardoz_cannot_reach_the_confluence_publish_path`),
    # so no skill can raise it. Listing it as a refusal would need the very
    # import that guard forbids -- which is how this was found: the first
    # version imported it, and that guard went red on the control arm.
    "policyforge.export.confluence_exporter.ConcurrentEditError",
    # A warning category (#295): issued with `warnings.warn` when a catalog's
    # declared framework key disagrees with prose keying, never raised, so no
    # skill can fail with it.
    "policyforge.frameworks.registry.FrameworkKeyWarning",
    # The shipped pool and the topic rule's table have drifted apart (22 part
    # 3): a defect in the package no organisation's facts can fix, so it stays
    # a traceback on purpose.
    "policyforge.topics.build.PoolError",
    # `library add`'s refusal (443): a CLI command over the user's private
    # licensed library, which no skill calls, so no skill can fail with it.
    "policyforge.library.LibraryError",
}


def _defined_exception_classes() -> dict[str, type]:
    """Every top-level class in `src/policyforge` that IS an exception, keyed
    by module and name.

    **By module and name, not name.** Two classes are both called
    `ExportFormatError` (GovRAMP and HITRUST); a scan keyed by name folded
    them into one, and so would have hidden an unclassified class sharing a
    name. Each candidate is imported and tested with `issubclass`, so the
    answer comes from the class, not from its spelling.
    """
    import importlib

    found: dict[str, type] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [n.name for n in tree.body if isinstance(n, ast.ClassDef) and n.bases]
        if not names:
            continue
        parts = path.relative_to(SRC.parent).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        loaded = importlib.import_module(module)
        for name in names:
            cls = getattr(loaded, name, None)
            if isinstance(cls, type) and issubclass(cls, BaseException):
                found[f"{module}.{name}"] = cls
    return found


def test_every_exception_class_is_classified():
    """A refusal prints its message; anything else prints a traceback. A new
    exception class must be put on one side on purpose. Left off both, it
    would still surface with a traceback, the safe direction, but this makes
    the choice explicit."""
    defined = _defined_exception_classes()

    unclassified = sorted(
        key
        for key, cls in defined.items()
        if not issubclass(cls, refusal_types()) and key not in NOT_REFUSALS
    )
    assert unclassified == [], "classify each in refusal_types() or NOT_REFUSALS"
    # The population, measured on 2026-09-24: 20 classes in 16 modules. A
    # scan that silently read nothing would pass the check above.
    assert len(defined) >= 20, f"the scan found only {len(defined)}; is it reading the source?"
