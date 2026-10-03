"""Zardoz's analyses: questions about the programme, not about its prose.

Half the questions people have are not answerable from any document, because
they are questions *about* the programme. Which controls nobody owns. What
the catalog changed. How many values are still undecided. Nothing in a
Standard states any of that.

The property that matters here is the division of labour: **the model routes
and the report speaks**. A model that read "14 orphaned" and told you about
it could say "mostly in the audit family" with nothing to check it against.
So the tests assert that a skill's output arrives byte-for-byte, that a
routing failure falls back to the documents rather than to an error, and
that the shell says which analysis it ran so a wrong route is visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from policyforge.topics.registry import Topic
from policyforge.zardoz.skills import (
    NO_SKILL,
    SKILLS,
    route,
    route_offline,
    run_skill,
    skill_fields,
)


@dataclass
class R:
    text: str
    model: str = "fake"


class Router:
    """Replies with a fixed routing decision, then with an answer."""

    def __init__(self, choice: str = NO_SKILL) -> None:
        self.choice = choice
        self.systems: list[str] = []

    def generate(self, *, system, prompt, **kwargs):
        self.systems.append(system)
        if "one word" in system.lower() or "decide whether" in system.lower():
            return R(self.choice)
        return R("An answer. [1]")


class Exploding:
    def generate(self, **kwargs):
        raise RuntimeError("no route for you")


def _state(**kwargs):
    from policyforge.zardoz.shell import ShellState

    kwargs.setdefault("config", {})
    return ShellState(**kwargs)


def _catalog(tmp_path: Path, controls) -> Path:
    """A framework directory the registry will discover."""
    import dataclasses
    import json

    directory = tmp_path / "frameworks" / "nist-800-53-r5"
    directory.mkdir(parents=True)
    (directory / "framework.yaml").write_text(
        "id: nist-800-53-r5\nlicence: public-domain\n", encoding="utf-8"
    )
    (directory / "controls.json").write_text(
        json.dumps([dataclasses.asdict(c) for c in controls]), encoding="utf-8"
    )
    return directory / "controls.json"


def _control(control_id="AC-2", statement="a. Do it.", baseline="Low, Moderate, High"):
    from policyforge.ingest.schema import Control

    return Control(
        control_id=control_id,
        title=f"{control_id} title",
        framework="NIST-800-53",
        framework_version="Rev 5",
        baseline=baseline,
        control_statement=statement,
    )


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------


def test_every_skill_is_reachable_as_a_command():
    from policyforge.zardoz.shell import COMMANDS

    for name in SKILLS:
        assert name in COMMANDS, f"/{name} is not a command"


def test_the_help_table_lists_them():
    from policyforge.zardoz.shell import ShellState, dispatch

    output = dispatch("/help", ShellState())

    for name in SKILLS:
        assert f"/{name}" in output


def test_skill_fields_matches_the_registry():
    assert [n for n, _ in skill_fields()] == list(SKILLS)


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def test_the_offline_router_catches_how_the_question_is_actually_asked():
    """ "nobody owns" misses "does nobody own?", which is the phrasing people
    use out loud."""
    assert route_offline("which controls does nobody own?") == "coverage"
    assert route_offline("what is orphaned?") == "coverage"
    assert route_offline("are any controls contested?") == "coverage"
    assert route_offline("how many parameters are undecided?") == "parameters"


def test_an_ordinary_document_question_is_not_hijacked():
    """A keyword router that guessed broadly would be worse than no router:
    it would send document questions to an analysis that cannot answer
    them."""
    for question in (
        "what is our access review cadence?",
        "who owns the backup standard?",
        "how often are accounts recertified?",
    ):
        assert route_offline(question) == NO_SKILL, question


def test_the_model_router_is_believed_when_it_names_a_skill():
    assert route("anything at all", Router("coverage")) == "coverage"


def test_a_router_naming_something_that_is_not_a_skill_falls_back():
    assert route("anything", Router("please_run_publish")) == NO_SKILL


def test_a_router_failure_sends_the_question_to_the_documents():
    """A refusal from the documents is recoverable; an exception is not."""
    assert route("which controls does nobody own?", Exploding()) == "coverage"


def test_routing_is_skipped_entirely_without_a_model():
    assert route("what is orphaned?", None) == "coverage"


# --------------------------------------------------------------------------
# Running one
# --------------------------------------------------------------------------


def test_a_skill_that_cannot_run_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert "No control catalogs on disk" in run_skill("coverage", _state())


def test_coverage_needs_a_registry_to_measure_against(tmp_path, monkeypatch):
    _catalog(tmp_path, [_control()])
    monkeypatch.chdir(tmp_path)

    assert "No topic registry" in run_skill("coverage", _state())


def test_coverage_reports_what_no_topic_claims(tmp_path, monkeypatch):
    _catalog(tmp_path, [_control("AC-2"), _control("AU-6")])
    monkeypatch.chdir(tmp_path)
    state = _state(topics=[Topic(name="Access Review", owner="IAM", nist_controls=["AC-2"])])

    output = run_skill("coverage", state)

    assert "Orphaned" in output
    assert "AU-6" in output


def test_a_baseline_argument_narrows_the_scope(tmp_path, monkeypatch):
    _catalog(tmp_path, [_control("AC-2", baseline="High")])
    monkeypatch.chdir(tmp_path)
    state = _state(topics=[Topic(name="T", owner="O", nist_controls=["AC-2"])])

    assert "No controls tagged for the low baseline" in run_skill("coverage", state, ["low"])
    assert "high baseline" in run_skill("coverage", state, ["high"])


def test_parameters_reports_what_is_still_undecided(tmp_path, monkeypatch):
    _catalog(
        tmp_path,
        [_control("AC-2", "a. Review [Assignment: organization-defined frequency].")],
    )
    monkeypatch.chdir(tmp_path)

    output = run_skill("parameters", _state())

    assert "undecided" in output


def test_an_unknown_skill_says_so():
    assert "No such analysis" in run_skill("publish", _state())


def test_a_broken_skill_does_not_end_the_session(monkeypatch):
    from policyforge.zardoz import skills

    monkeypatch.setitem(
        skills.SKILLS,
        "coverage",
        skills.Skill(
            name="coverage",
            summary="x",
            answers="x",
            run=lambda state, args: (_ for _ in ()).throw(RuntimeError("boom")),
        ),
    )

    assert "could not run" in run_skill("coverage", _state())


# --------------------------------------------------------------------------
# In the shell
# --------------------------------------------------------------------------


def test_a_routed_question_says_which_analysis_ran(tmp_path, monkeypatch):
    """Routing can be wrong; naming the skill is what makes it visible."""
    from policyforge.zardoz.shell import dispatch

    _catalog(tmp_path, [_control("AC-2"), _control("AU-6")])
    monkeypatch.chdir(tmp_path)
    state = _state(topics=[Topic(name="Access Review", owner="IAM", nist_controls=["AC-2"])])

    output = dispatch("which controls does nobody own?", state)

    assert "(ran /coverage)" in output


def test_the_report_arrives_unmodified(tmp_path, monkeypatch):
    """The model never reads a result and tells you about it. A paraphrase
    of "14 orphaned" can become "mostly in the audit family", and there
    would be nothing to check that against."""
    from policyforge.zardoz.shell import dispatch

    _catalog(tmp_path, [_control("AC-2"), _control("AU-6")])
    monkeypatch.chdir(tmp_path)
    state = _state(
        topics=[Topic(name="Access Review", owner="IAM", nist_controls=["AC-2"])],
        provider=Router("coverage"),
    )

    output = dispatch("anything", state)
    verbatim = run_skill("coverage", state)

    assert verbatim in output, "the skill's own text is what reaches the terminal"


def test_an_analysis_answers_with_no_corpus_synced(tmp_path, monkeypatch):
    """ "Which controls does nobody own?" comes out of the registry and the
    catalogs. Refusing it for want of a document corpus would be answering a
    question nobody asked."""
    from policyforge.zardoz.shell import dispatch

    _catalog(tmp_path, [_control("AC-2")])
    monkeypatch.chdir(tmp_path)
    state = _state(topics=[Topic(name="T", owner="O", nist_controls=["ZZ-9"])])
    assert state.corpus is None

    output = dispatch("which controls does nobody own?", state)

    assert "No document corpus" not in output
    assert "Orphaned" in output


def test_a_document_question_still_goes_to_the_documents():
    from policyforge.zardoz.shell import dispatch

    output = dispatch("what is our access review cadence?", _state())

    assert "No document corpus is synced yet" in output
    assert "(ran /" not in output


def test_a_routed_question_is_recorded_as_a_turn(tmp_path, monkeypatch):
    """So a follow-up after an analysis still resolves against it."""
    from policyforge.zardoz.shell import dispatch

    _catalog(tmp_path, [_control("AC-2")])
    monkeypatch.chdir(tmp_path)
    state = _state(topics=[Topic(name="T", owner="O", nist_controls=["AC-2"])])

    dispatch("what is orphaned?", state)

    assert len(state.conversation) == 1


def test_a_slash_command_runs_the_same_analysis(tmp_path, monkeypatch):
    from policyforge.zardoz.shell import dispatch

    _catalog(tmp_path, [_control("AC-2"), _control("AU-6")])
    monkeypatch.chdir(tmp_path)
    state = _state(topics=[Topic(name="T", owner="O", nist_controls=["AC-2"])])

    assert dispatch("/coverage", state) == run_skill("coverage", state)


def test_no_skill_can_reach_the_publish_path():
    """Not a convention: the package-wide AST guard makes it unwritable."""
    import ast

    source = Path("src/policyforge/zardoz/skills.py").read_text(encoding="utf-8")
    referenced = {
        node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Attribute, ast.Name))
    }

    assert not referenced & {"update_page_body", "export_to_confluence", "confluence_exporter"}


# --------------------------------------------------------------------------
# HITRUST
# --------------------------------------------------------------------------


def _only(path: Path) -> dict:
    """A config whose framework search finds nothing but `path`."""
    return {"frameworks": {"search_paths": [str(path)]}}


def _hitrust_control():
    """One control reference stated at two levels, mapped to 800-53."""
    from policyforge.ingest.levelled import Record, build_controls

    def record(level, mapping):
        return Record(
            category="01.0 - Example Category",
            objective="01.01 Example Objective",
            reference="01.a Example Control",
            specification="The organization shall do the example thing.",
            factor_type="Organizational",
            level=level,
            statement=f"The example thing, at {level}.",
            mapping=mapping,
        )

    return build_controls(
        [
            record("Level 1", "NIST SP 800-53 r5 AC-1\nNIST SP 800-53 r5 AC-2"),
            record("Level FedRAMP", "NIST SP 800-53 r5 AC-3"),
        ],
        version="v11.7",
    )


def test_the_hitrust_skill_explains_how_to_get_a_catalog_when_there_is_none(tmp_path):
    """HITRUST is never bundled, so an empty answer is the common case and
    has to be actionable rather than blank.

    Search paths are pinned at an empty directory because the defaults
    include `local_content/`, and a developer with their own licensed export
    parsed into it would otherwise see this test pass or fail depending on
    what is on their disk.
    """
    output = run_skill("hitrust", _state(config=_only(tmp_path)))

    assert "No HITRUST catalog on disk" in output
    assert "etl-hitrust" in output


def test_the_hitrust_skill_reports_levels_and_crosswalk_reach(tmp_path):
    import dataclasses
    import json

    directory = tmp_path / "frameworks" / "hitrust-csf"
    directory.mkdir(parents=True)
    (directory / "framework.yaml").write_text(
        "id: hitrust-csf\nlicence: licensed\n", encoding="utf-8"
    )
    (directory / "controls.json").write_text(
        json.dumps([dataclasses.asdict(c) for c in _hitrust_control()]), encoding="utf-8"
    )

    state = _state(
        controls_paths=[directory / "controls.json"],
        config=_only(tmp_path / "frameworks"),
    )
    output = run_skill("hitrust", state)

    assert "1 control references" in output
    assert "2 requirement statements" in output
    assert "Level FedRAMP" in output
    assert "v11.7" in output


def test_the_hitrust_router_hints_do_not_hijack_a_document_question():
    """Most questions naming HITRUST are about what a policy requires, and
    those belong to the documents."""
    assert route_offline("what does our HITRUST policy say about passwords?") == NO_SKILL
    assert route_offline("which hitrust levels apply to us?") == "hitrust"
