"""The pre-push gate must run every check CI requires.

**The charge names `scripts/check.py` as *the* pre-push gate.** A gate that
a required CI check can fail behind is not a gate — it is a subset someone
has to remember is a subset, and nobody does.

Observed live on #211: `check.py` reported 2,921 tests passing and every
check green while CI was red on `changelog`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
GATE = ROOT / "scripts" / "check.py"

#: Scripts CI runs that the gate is NOT expected to run, each with a reason.
#:
#: **Short on purpose** (this used to say "empty", while holding an entry).
#: A name here is a decision that the gate may be weaker than CI in one
#: place, and it should cost someone an argument. Both entries are the same
#: argument: the script needs a base branch, which exists only once a pull
#: request does.
GATE_NEED_NOT_RUN: dict[str, str] = {
    "changelog_guard": (
        "needs two facts that do not exist before a pull request does: "
        "`--base origin/<base-ref>`, the branch point, and `--body-file`, the "
        "PR body it reads an exemption from. A local pre-push run has neither. "
        "Found by this test rather than named in #212 -- deriving the "
        "population from ci.yml surfaced a second instance of the same class, "
        "and this one is a genuine exception rather than an omission."
    ),
    "prompt_ledger_grows": (
        "needs `--base origin/<base-ref>`, the branch a pull request targets, "
        "and compares the ledger against THAT branch's; a local pre-push run "
        "does not know which train a branch is headed for, and guessing one "
        "would be a check answering an adjacent question (#424). The half "
        "that needs no base -- the ledger equals the registry -- does run in "
        "the gate, through tests/test_prompt_fingerprint_file.py."
    ),
}


def _ci_scripts() -> set[str]:
    text = CI.read_text(encoding="utf-8")
    return set(re.findall(r"python scripts/(\w+)\.py", text))


def test_the_population_is_not_empty():
    """Guard the population: a regex that stops matching would make every
    assertion below vacuous and green."""
    assert _ci_scripts(), (
        "no `python scripts/*.py` invocation found in ci.yml — either CI "
        "changed shape or the pattern stopped matching; check which"
    )


def test_the_gate_runs_every_script_ci_runs():
    """**#212.** `changelog_fragments.py` was required by CI and absent from
    the gate, so a fragment CI rejects passed the gate green.

    Derived from `ci.yml` rather than listed here, so a script added to CI
    later is covered without anyone remembering this file exists.
    """
    # **Comments stripped, because a comment naming the path satisfied a
    # substring search.** policyforge-9b replaced the invocation with
    # `# TODO: one day wire up scripts/changelog_fragments.py --check here`
    # and this guard stayed green — reporting #211's exact state, a
    # required check the gate does not run, as fine. The guard written to
    # make that state impossible could not see it.
    gate = re.sub(r"#.*$", "", GATE.read_text(encoding="utf-8"), flags=re.M)
    missing = sorted(
        name
        for name in _ci_scripts()
        if name not in GATE_NEED_NOT_RUN and f"scripts/{name}.py" not in gate
    )
    assert not missing, (
        f"CI runs {missing} and the pre-push gate does not. A gate a required "
        f"check can fail behind is a subset someone has to remember is a "
        f"subset. Add it to check.py, or name it in GATE_NEED_NOT_RUN with a "
        f"reason so the weakening is a decision."
    )


def test_the_fragment_check_is_not_skippable():
    """The other skips exist because a tool may be absent — semgrep,
    gitleaks, git. **This one is a script in this repository**, so *"it did
    not run"* has no honest cause and must not be acknowledgeable.
    """
    gate = GATE.read_text(encoding="utf-8")
    # Bounded by the literal's own closing brace rather than a character
    # count: 9b measured `+ 400` as 192 characters of slack over a 208
    # character literal, so about four more skip entries would push the
    # tail outside the window and this assertion would silently stop
    # checking the part just added -- with the trigger being a longer
    # literal, which is exactly when you want it checked.
    start = gate.index("SKIP_FLAGS")
    skip_block = gate[start : gate.index("}", start)]
    assert "changelog" not in skip_block, (
        "the changelog-fragment check is skippable. A check whose tool ships "
        "in this repository cannot honestly be unavailable."
    )
