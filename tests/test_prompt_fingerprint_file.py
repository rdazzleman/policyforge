"""The two prompt records (#236).

`evals/prompt-fingerprints.json` is the newest MEASUREMENTS.md epoch's
fingerprints, by the runner's own definition. It lags on purpose, so it is
held to its SHAPE only: it names the epoch, the PR, the pull ref and the
commits the epoch ran at.

`evals/prompt-versions.json` is the version ledger, and it is held EQUAL to
the registry: every prompt's current version is recorded with the text the
ledger has for it. That is the guard for #117's shape (text changed,
version kept). It sits on the ledger rather than the epoch file because the
epoch file only catches the first unbumped edit after each epoch (1d on
#236); the two-edit test below is the case that tells them apart.

**Not held here:** that the ledger only grows. A change that edits a prompt
and rewrites its recorded fingerprint in the ledger passes these tests
(1d on #422). That is held by review of the ledger's diff.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from policyforge.llm import prompts

EVALS = Path(__file__).resolve().parent.parent / "evals"
SHA = re.compile(r"[0-9a-f]{40}")


def _epoch() -> dict:
    return json.loads((EVALS / "prompt-fingerprints.json").read_text(encoding="utf-8"))


def _ledger() -> dict:
    return json.loads((EVALS / "prompt-versions.json").read_text(encoding="utf-8"))


# ---- the epoch file: shape only ----------------------------------------------


def test_the_epoch_file_names_its_epoch_and_where_it_ran():
    """80's condition on #236: the PR, its pull ref, a squash commit reachable
    from a branch, and which commit the fingerprints were computed at."""
    record = _epoch()
    assert re.fullmatch(r"\d+", record["epoch"])
    assert isinstance(record["pr"], int)
    assert record["pull_ref"] == f"refs/pull/{record['pr']}/head"
    assert SHA.fullmatch(record["computed_at"]) and SHA.fullmatch(record["merge_commit"])
    assert record["merge_commit_on"]
    assert isinstance(record["recorded_in"]["pr"], int)
    assert SHA.fullmatch(record["recorded_in"]["merge_commit"])
    for name, entry in record["prompts"].items():
        assert re.fullmatch(r"[0-9a-f]{12}", entry["fingerprint"]), name
        assert isinstance(entry["version"], int) and entry["version"] >= 1, name


def test_the_epoch_file_agrees_with_the_ledger():
    """Every version the epoch ran on is one the ledger records with that
    same text: the two records cannot tell different stories."""
    ledger = _ledger()["prompts"]
    for name, entry in _epoch()["prompts"].items():
        texts = [e["fingerprint"] for e in ledger[name][str(entry["version"])]]
        assert entry["fingerprint"] in texts, name


def test_the_runner_reads_the_file_and_names_the_epoch():
    """The eval report compares against the epoch file's prompts and says
    which epoch that is, not "the last recorded epoch" (#236)."""
    import sys

    sys.path.insert(0, str(EVALS.parent))
    from evals import runner

    record = _epoch()
    assert runner.recorded_fingerprints() == {
        name: entry["fingerprint"] for name, entry in record["prompts"].items()
    }
    report = "\n".join(runner.prompt_epoch_report())
    assert f"epoch {record['epoch']} (PR #{record['pr']}, {record['computed_at'][:7]})" in report


# ---- the version ledger: equal to the registry -------------------------------


def test_every_prompt_version_names_exactly_one_text():
    prompts.load_all()
    assert prompts.ledger_problems(_ledger()["prompts"]) == []


def test_the_only_reused_versions_are_117s_and_they_are_named():
    """#117 kept two texts under one number each. The ledger says so rather
    than picking one, and no other version holds more than one text."""
    record = _ledger()
    reused = {
        f"{name} {version}"
        for name, versions in record["prompts"].items()
        for version, entries in versions.items()
        if len(entries) > 1
    }
    assert (
        reused
        == set(record["reused_versions"])
        == {
            "generate.standard 3",
            "generate.procedure 2",
        }
    )


def test_every_gap_in_a_prompts_versions_is_explained():
    """The ledger is walked along main and the train, so a version declared
    only on a pull request's own commits (generate.standard v5, on #359) is
    not in it, and nor are numbers the registry started above (edit.plan v1,
    zardoz.answer v1-5: never declared in code). Each gap is named with its
    reason (9b on #422), and a version skipped from now on fails here until
    someone says why."""
    record = _ledger()
    gaps = {
        f"{name} {missing}"
        for name, versions in record["prompts"].items()
        for missing in range(1, max(int(v) for v in versions) + 1)
        if str(missing) not in versions
    }
    assert gaps == set(record["unrecorded_versions"])


def _edit(monkeypatch, name: str, text: str, version: int) -> None:
    monkeypatch.setitem(
        prompts.REGISTRY, name, replace(prompts.REGISTRY[name], text=text, version=version)
    )


def _fingerprint(name: str, text: str, version: int) -> str:
    return prompts.Prompt(name=name, version=version, text=text).fingerprint


def _epoch_guard(epoch_prompts: dict) -> list[str]:
    """The design 1d rejected, kept here only to show what it misses: text
    changed since the EPOCH while the version did not."""
    return [
        name
        for name, entry in epoch_prompts.items()
        if name in prompts.REGISTRY
        and prompts.REGISTRY[name].fingerprint != entry["fingerprint"]
        and prompts.REGISTRY[name].version == entry["version"]
    ]


def test_a_second_unbumped_edit_after_a_bumped_one_is_caught(monkeypatch):
    """The arm (1d on #236). Edit 1 changes the text and bumps v6 -> v7, and
    is recorded. Edit 2 changes the text again and stays at v7. Against the
    epoch file (at v3), edit 2 passes; against the ledger, it is caught."""
    prompts.load_all()
    name = "generate.standard"
    base = prompts.REGISTRY[name]
    ledger = json.loads(json.dumps(_ledger()["prompts"]))
    v7 = base.version + 1

    first = base.text + "\nEdit one."
    _edit(monkeypatch, name, first, v7)
    ledger[name][str(v7)] = [{"fingerprint": _fingerprint(name, first, v7)}]
    assert prompts.ledger_problems(ledger) == []

    _edit(monkeypatch, name, first + "\nEdit two.", v7)
    assert _epoch_guard(_epoch()["prompts"]) == [], "the epoch design passes edit two"
    (problem,) = prompts.ledger_problems(ledger)
    assert problem.startswith(f"{name} v{v7}: text changed") and "without a new version" in problem


def test_a_first_unbumped_edit_is_caught_too(monkeypatch):
    prompts.load_all()
    name = "generate.policy"
    _edit(monkeypatch, name, prompts.REGISTRY[name].text + "!", prompts.REGISTRY[name].version)
    assert [p.split(" ")[0] for p in prompts.ledger_problems(_ledger()["prompts"])] == [name]


def test_the_current_version_may_not_name_two_texts():
    """A reused version, as #117 left two, is history only: if the version a
    prompt is at now holds two texts in the ledger, a reader by version cannot
    tell which ran, so it is refused even when one of them is the current."""
    prompts.load_all()
    name = "generate.policy"
    now = prompts.REGISTRY[name]
    ledger = json.loads(json.dumps(_ledger()["prompts"]))
    ledger[name][str(now.version)].insert(0, {"fingerprint": "0" * 12})
    (problem,) = prompts.ledger_problems(ledger)
    assert problem.startswith(f"{name} v{now.version}: text changed")


def test_a_new_version_must_be_recorded_before_it_passes(monkeypatch):
    """Bumping is not enough on its own: the new version goes in the ledger,
    which is what makes the next unbumped edit visible."""
    prompts.load_all()
    name = "generate.policy"
    base = prompts.REGISTRY[name]
    _edit(monkeypatch, name, base.text + "!", base.version + 1)
    (problem,) = prompts.ledger_problems(_ledger()["prompts"])
    assert "is not in the ledger" in problem
