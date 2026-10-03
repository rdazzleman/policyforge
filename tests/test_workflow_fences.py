"""The fences on workflow jobs that hold a credential (#244).

`content.yml`'s two publish jobs were fenced with
`github.repository == 'rdazzlebot/policyforge'`. The repository was
transferred to `rdazzleman`, and **a string comparison is not redirected**:
from the transfer on, the condition was false on every run and neither job
could ever publish. Nothing went red, because both jobs also skip while their
destination variable is unset -- so "skipped" read the same in both states.

**What these tests prove, and what they do not.** They prove the TEXT of each
fence and the LOGIC of that text, evaluated here by a deliberately small
evaluator against synthetic contexts. They do not prove how GitHub evaluates
it. The one live observation available -- a push to this repository -- is
uninformative while no publish variable is set, since the job skips whether
the fence is right or wrong.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _workflows() -> dict[str, dict]:
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
    }


def _privileged_jobs() -> dict[str, dict]:
    """Every job holding a credential the platform does not scope for it.

    Privileged means: it reads a secret other than `GITHUB_TOKEN`, or it
    declares an `environment`. `GITHUB_TOKEN` is left out on purpose -- GitHub
    already makes it read-only on a fork's pull request, which is the case a
    fence exists for. Derived from every workflow, so a new credentialed job
    joins the population without anyone listing it.
    """
    jobs = {}
    for workflow, body in _workflows().items():
        for name, job in (body.get("jobs") or {}).items():
            secrets = set(re.findall(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)", json.dumps(job)))
            if (secrets - {"GITHUB_TOKEN"}) or job.get("environment"):
                jobs[f"{workflow}:{name}"] = job
    return jobs


def test_the_population_contains_the_jobs_this_was_written_for():
    """Extent, not just consistency: if the derivation stops finding the two
    publish jobs, every test below passes over an empty set."""
    assert {"content.yml:publish", "content.yml:publish-wiki"} <= set(_privileged_jobs())


# -- a small evaluator ------------------------------------------------------
#
# It understands exactly the grammar these fences use: atoms joined by `&&`,
# each one of `X == 'lit'`, `X != 'lit'` or `!X`. Anything else RAISES, so a
# fence written in some other shape fails here by name instead of being
# misread as true or false.

_ATOM = re.compile(r"^(?P<neg>!)?(?P<lhs>[a-z_][\w.]*)(?:\s*(?P<op>==|!=)\s*'(?P<lit>[^']*)')?$")


def _lookup(context: dict, dotted: str):
    value = context
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return ""  # GitHub's own rule: a missing property is empty
        value = value[part]
    return value


def evaluate(expression: str, context: dict) -> bool:
    if "||" in expression or "(" in expression:
        raise ValueError(f"outside the evaluator's grammar: {expression!r}")
    result = True
    for raw in expression.split("&&"):
        match = _ATOM.match(raw.strip())
        if not match:
            raise ValueError(f"outside the evaluator's grammar: {raw.strip()!r}")
        value = _lookup(context, match["lhs"])
        if match["op"] == "==":
            atom = value == match["lit"]
        elif match["op"] == "!=":
            atom = value != match["lit"]
        else:
            atom = bool(value)
        result = result and (not atom if match["neg"] else atom)
    return result


def _context(*, event="push", repository="rdazzleman/policyforge", fork=False, variable="set"):
    return {
        "github": {
            "event_name": event,
            "repository": repository,
            "event": {"repository": {"fork": fork}},
        },
        "vars": {"CONFLUENCE_HOST": variable, "WIKI_REPOSITORY": variable},
    }


def test_the_evaluator_can_say_no():
    """The must-fail arm for the instrument itself. The OLD fence, against the
    repository as it is now, must evaluate false -- that is the defect."""
    old = (
        "github.event_name == 'push' && github.repository == 'rdazzlebot/policyforge'"
        " && vars.CONFLUENCE_HOST != ''"
    )
    assert evaluate(old, _context()) is False
    assert evaluate(old, _context(repository="rdazzlebot/policyforge")) is True


def test_the_evaluator_refuses_what_it_does_not_understand():
    with pytest.raises(ValueError):
        evaluate("github.event_name == 'push' || true", _context())
    with pytest.raises(ValueError):
        evaluate("contains(github.ref, 'main')", _context())


# (name, context, should the job run?)
CASES = [
    ("a push to the canonical repository, destination set", _context(), True),
    ("the same, destination unset", _context(variable=""), False),
    ("a push to a fork, destination set", _context(fork=True), False),
    ("a pull request, destination set", _context(event="pull_request"), False),
    ("a pull_request_target, destination set", _context(event="pull_request_target"), False),
    # The regression case. The owner has moved once already; the fence must
    # not depend on what it is called.
    ("a push after another transfer", _context(repository="someone/policyforge"), True),
]


@pytest.mark.parametrize("job", sorted(_privileged_jobs()))
@pytest.mark.parametrize(("case", "context", "runs"), CASES, ids=[c[0] for c in CASES])
def test_a_privileged_job_runs_only_where_it_should(job, case, context, runs):
    fence = _privileged_jobs()[job].get("if")
    assert fence, f"{job} holds a credential and has no `if:` at all"
    assert evaluate(fence, context) is runs, f"{job}: {case}"


def test_no_workflow_names_its_own_owner():
    """The class, not the instance: a comparison of the repository or its
    owner against a literal is exactly what broke, and nothing redirects it."""
    pattern = re.compile(r"github\.repository(?:_owner)?\s*[!=]=\s*['\"]")
    found = [
        f"{path.name}:{number}"
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line) and not line.lstrip().startswith("#")
    ]
    assert not found, f"compared as a string, so a transfer breaks it silently: {found}"
