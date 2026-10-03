"""The registry, and the specific failure it was built for.

A measured run against `kimi-k3` was half finished when two commits changed
the edit planner and the clusterer. The run had imported the old modules at
process start, so it measured code that no longer existed, and nothing in
the run or its report could have said so — it was caught by a person
comparing file mtimes against a log's birth time. These tests pin the
mechanism that makes noticing unnecessary.
"""

from __future__ import annotations

import pytest

from policyforge.llm import prompts
from policyforge.llm.prompts import Prompt


def test_the_fingerprint_follows_the_text():
    a = Prompt(name="x", version=1, text="Do the thing.")
    b = Prompt(name="x", version=1, text="Do the other thing.")
    assert a.fingerprint != b.fingerprint


def test_the_fingerprint_ignores_the_declared_version():
    """The hash answers "is this the same prompt", nothing else.

    Version is a human claim about a change; the hash is the change. Tying
    the hash to the version would make a forgotten bump invisible, which is
    the failure mode this exists for.
    """
    a = Prompt(name="x", version=1, text="Same words.")
    b = Prompt(name="x", version=9, text="Same words.")
    assert a.fingerprint == b.fingerprint


def test_whitespace_is_not_normalised():
    """A trailing space is a different prompt to the API, so it is here too."""
    a = Prompt(name="x", version=1, text="Do the thing.")
    b = Prompt(name="x", version=1, text="Do the thing. ")
    assert a.fingerprint != b.fingerprint


def test_register_hands_back_the_text_unchanged():
    """Call sites keep receiving the `str` they always did."""
    text = "Some instructions."
    returned = prompts.register(Prompt(name="test.roundtrip", version=1, text=text))
    assert returned == text
    assert isinstance(returned, str)
    del prompts.REGISTRY["test.roundtrip"]


def test_two_different_prompts_cannot_share_a_name():
    """A duplicate name makes both fingerprints unreadable across runs."""
    prompts.register(Prompt(name="test.dup", version=1, text="One."))
    with pytest.raises(ValueError, match="both registered"):
        prompts.register(Prompt(name="test.dup", version=1, text="Two."))
    del prompts.REGISTRY["test.dup"]


def test_registering_the_same_prompt_twice_is_fine():
    """Modules get imported more than once; that is not a conflict."""
    prompt = Prompt(name="test.same", version=1, text="One.")
    prompts.register(prompt)
    prompts.register(prompt)
    del prompts.REGISTRY["test.same"]


# ---- the comparison that would have caught the straddled run -----------


def test_an_unchanged_prompt_set_is_comparable():
    prompts.load_all()
    assert prompts.compare(prompts.fingerprints()) == []


def test_a_changed_prompt_is_named_with_both_fingerprints():
    prompts.load_all()
    recorded = dict(prompts.fingerprints())
    recorded["zardoz.answer"] = "0" * 12
    (difference,) = prompts.compare(recorded)
    assert difference.startswith("zardoz.answer: 000000000000 -> ")


def test_the_a06_split_reads_as_a_rename_and_one_new_prompt():
    """The actual A-06 change: the cluster prompt kept its text and gained a sibling.

    `zardoz.cluster` became `zardoz.cluster.lines` with the same bytes, and
    `zardoz.cluster.json` is new. Read as "gone" plus "new", the rename alone
    marked every later run not comparable; only the JSON prompt is a change.
    """
    prompts.load_all()
    current = prompts.fingerprints()
    assert current["zardoz.cluster.lines"] == "3f58d07e4945"
    recorded = {"zardoz.cluster": "3f58d07e4945"}
    recorded |= {name: fp for name, fp in current.items() if not name.startswith("zardoz.cluster")}

    assert prompts.renames(recorded) == [("zardoz.cluster", "zardoz.cluster.lines")]
    assert prompts.compare(recorded) == [
        f"zardoz.cluster.json: new ({current['zardoz.cluster.json']})"
    ]


def test_a_rename_that_also_changed_the_text_is_not_a_rename():
    prompts.load_all()
    recorded = {name: fp for name, fp in prompts.fingerprints().items() if name != "zardoz.route"}
    recorded["zardoz.router"] = "0" * 12
    assert prompts.renames(recorded) == []
    assert "zardoz.route: new" in "\n".join(prompts.compare(recorded))
    assert "zardoz.router: gone" in "\n".join(prompts.compare(recorded))


def test_an_ambiguous_rename_is_left_as_differences():
    """Two old names with one hash cannot say which became the new one."""
    prompts.load_all()
    current = prompts.fingerprints()
    recorded = {name: fp for name, fp in current.items() if name != "zardoz.route"}
    recorded["old.a"] = current["zardoz.route"]
    recorded["old.b"] = current["zardoz.route"]
    assert prompts.renames(recorded) == []
    assert len(prompts.compare(recorded)) == 3


def test_load_all_registers_every_prompt_it_claims_to():
    prompts.load_all()
    names = set(prompts.fingerprints())
    assert {
        "zardoz.answer",
        "crosswalk.propose",
        "edit.plan",
        "edit.apply",
        "entail.judge",
        "generate.standard",
        "generate.policy",
        "generate.procedure",
        "zardoz.cluster.lines",
        "zardoz.cluster.json",
        "zardoz.resolve",
        "zardoz.expand",
        "zardoz.route",
        "zardoz.route.arguments",
        "zardoz.route.also",
        "synthesis.merge",
    } <= names


#: Prompts no eval suite grades. A change to one of these says nothing about
#: any recorded number, so registering it would mark runs "NOT comparable"
#: for nothing. Each needs its reason; a new prompt defaults to registered.
_UNGRADED = {
    "ingest/parser_codegen.py:_SYSTEM_PROMPT": "generate-parser; output is reviewed code, no suite",
    "ssp/narrative.py:_SYSTEM_PROMPT": "SSP narrative drafting; no suite",
    "zardoz/shell.py:PROMPT": "the terminal's input prompt, never sent to a model",
}


def test_every_prompt_constant_is_registered_or_named_ungraded():
    """The registry only protects the prompts in it.

    Until epoch 16 the edit rewriter, the three generation prompts, resolution,
    expansion and all three routing prompts were plain strings, so a change to
    any of them was invisible to the fingerprint report. Found
    by a person reading the registry listing against the suites, not by any
    check. This is the check.

    Found by name (`*PROMPT` at module level), then read inverted: anything
    that is not a `register(...)` call counts as unregistered. Matching the
    unregistered shape instead — a bare string — missed `parser_codegen`,
    whose prompt is a `.replace()` call; see the note in
    `policyforge.llm.prompts` on checks that match only one shape.
    """
    import ast
    from pathlib import Path

    import policyforge

    root = Path(policyforge.__file__).parent
    unregistered = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            target = node.targets[0]
            if not (isinstance(target, ast.Name) and target.id.endswith("PROMPT")):
                continue
            value = node.value
            registered = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in {"register", "_register"}
            )
            if not registered:
                unregistered.append(f"{path.relative_to(root).as_posix()}:{target.id}")
    assert sorted(unregistered) == sorted(_UNGRADED)


# ---- the prompts themselves --------------------------------------------


def test_registration_did_not_alter_a_prompt():
    """Registering wraps a constant; it must not touch what it wraps.

    A registry that silently changed a prompt while claiming to fingerprint
    it would be worse than no registry, so the two prompts whose text is
    load-bearing are checked against a known opening and closing.
    """
    from policyforge.zardoz.answer import SYSTEM_PROMPT

    assert isinstance(SYSTEM_PROMPT, str)
    assert SYSTEM_PROMPT.startswith("You answer questions about an organization's own")
    assert SYSTEM_PROMPT.rstrip().endswith("Name no candidate values at all.")


def test_the_two_cluster_prompts_have_different_fingerprints():
    """They differ in one paragraph, which is exactly what must be visible."""
    prompts.load_all()
    current = prompts.fingerprints()
    assert current["zardoz.cluster.lines"] != current["zardoz.cluster.json"]
