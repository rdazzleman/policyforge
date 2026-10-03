"""Every graded prompt, with a fingerprint that changes when its text does.

MEASUREMENTS.md rests on one rule: *a number without an epoch is not
comparable with anything, which makes it worse than no number at all.* The
epoch is what says which prompts produced a result. Until now the epoch was
maintained by hand — somebody changed a prompt, and somebody remembered to
open a new epoch.

That failed the first time two people ran at once. A measured run against
`kimi-k3` was half finished when two commits landed that changed the edit
planner and the clusterer; the run had imported the old modules at process
start, so it was measuring code that no longer existed, and nothing in the
run or the report could have said so. It was caught by one person noticing
the timestamps. The number would otherwise have gone into the file looking
exactly like a number produced by current code.

A fingerprint removes the noticing. The hash is over the prompt *text*, not
over a version somebody declares, because the failure is never "I changed
the prompt and lied about the version" — it is "I changed the prompt and
forgot there was a version." Text is the thing that actually reached the
model, so text is the thing that gets hashed.

`version` is still here, and it is still written by hand, because the hash
answers "is this the same prompt" and cannot answer "is this a deliberate
change or a typo". The two are checked against each other: a text change
without a version bump is reported, since one of the two is wrong and the
report should not guess which.

Registering a prompt does not change how it is used. The constants stay
plain strings and every call site passes them exactly as before — a
registry that required rewriting eleven call sites to gain a hash would be
paid for in the currency it is trying to save.

**It does change how a prompt is found by a static check, and that has
already bitten once.** `SYSTEM_PROMPT = register(Prompt(...))` is an
`ast.Call`, where it used to be a bare `ast.Constant`. Anything that
locates prompts by matching the *shape* of the assignment — an AST walk
looking for a module-level string constant, a grep for `_PROMPT = "` —
walks straight past a registered prompt and reports success on the ones it
still finds. A credential-containment check written against the old shape
silently skipped `edit/plan.py`, `zardoz/answer.py` and
`entail/llm_entailer.py`: 14 constants found across 11 modules, with the
three most security-relevant prompts in the project missing and nothing
saying so.

The fix for such a check is to gather every string constant *underneath* the
assigned value rather than matching the assignment itself, which finds both
forms. Runtime access is unaffected — `getattr(module, "SYSTEM_PROMPT")`
still returns the `str`, which is why `scripts/mutate_zardoz.py` keeps
working unchanged, verified rather than assumed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: How much of the digest to show. Twelve hex characters is 48 bits, which
#: is far past collision for a registry that will never hold more than a few
#: dozen prompts, and short enough to sit in a table cell in MEASUREMENTS.md
#: without wrapping — which is the only place a human reads one.
FINGERPRINT_CHARS = 12


@dataclass(frozen=True)
class Prompt:
    """One prompt's identity: what it is called, its declared version, its text."""

    name: str
    version: int
    text: str

    @property
    def fingerprint(self) -> str:
        """A stable short hash of the text as the model receives it.

        Whitespace is not normalised and nothing is stripped. A prompt that
        differs only in trailing spaces is a different prompt as far as the
        API is concerned, and a fingerprint that hid that would be lying
        about exactly the kind of change nobody remembers making.
        """
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        return digest[:FINGERPRINT_CHARS]

    @property
    def label(self) -> str:
        return f"{self.name} v{self.version} {self.fingerprint}"


#: Every registered prompt, by name. Module-level because registration
#: happens at import: a prompt is registered beside the constant it
#: describes, so the two cannot drift apart in a way a reader would miss.
REGISTRY: dict[str, Prompt] = {}


def register(prompt: Prompt) -> str:
    """Record `prompt` and hand back its text.

    Returns the text rather than the `Prompt` so a call site reads:

        SYSTEM_PROMPT = register(Prompt(name=..., version=1, text=\"\"\"...\"\"\"))

    and every existing consumer keeps receiving the `str` it always did.
    The alternative — exporting a `Prompt` object and touching every call
    site and every test that compares against one — would be a large diff
    whose entire content is a type change, and large diffs are where changes
    to prompts hide.
    """
    existing = REGISTRY.get(prompt.name)
    if existing is not None and existing != prompt:
        raise ValueError(
            f"Two different prompts are both registered as {prompt.name!r}. "
            f"Names are how a fingerprint is matched to a prompt across runs, "
            f"so a duplicate makes both unreadable."
        )
    REGISTRY[prompt.name] = prompt
    return prompt.text


def fingerprints() -> dict[str, str]:
    """Every registered prompt's fingerprint, by name."""
    return {name: prompt.fingerprint for name, prompt in sorted(REGISTRY.items())}


def versions() -> dict[str, int]:
    return {name: prompt.version for name, prompt in sorted(REGISTRY.items())}


def load_all() -> None:
    """Import the modules that register prompts.

    Registration happens at import, so a registry read before the modules
    load reports fewer prompts than exist — which would make a run look
    comparable to a recorded epoch it does not match. The eval harness calls
    this before reading fingerprints, and the import list is explicit so
    that a module which stops registering shows up as an import that stopped
    being needed rather than as a silently smaller registry.
    """
    from policyforge.crosswalk import propose  # noqa: F401
    from policyforge.edit import apply, plan  # noqa: F401
    from policyforge.entail import llm_entailer  # noqa: F401
    from policyforge.generate import policy_writer  # noqa: F401
    from policyforge.synthesis import merge  # noqa: F401
    from policyforge.zardoz import answer, conversation, discover, paraphrase, skills  # noqa: F401


def renames(recorded: dict[str, str]) -> list[tuple[str, str]]:
    """`(old, new)` name pairs whose text is identical across a rename.

    A name that went away and a name that appeared with the same fingerprint
    are one prompt under a new name: the model receives the same bytes, so a
    run is still comparable. Reported as a difference, a rename would mark
    every run after it "NOT comparable" for a change that did not happen —
    and a warning that is always on stops being read. Paired only when the
    fingerprint is unambiguous on both sides.
    """
    current = fingerprints()
    gone = [name for name in recorded if name not in current]
    new = [name for name in current if name not in recorded]
    pairs = []
    for old in sorted(gone):
        same_old = [n for n in gone if recorded[n] == recorded[old]]
        same_new = [n for n in new if current[n] == recorded[old]]
        if len(same_old) == 1 and len(same_new) == 1:
            pairs.append((old, same_new[0]))
    return pairs


def ledger_problems(ledger: dict[str, dict[str, list[dict]]]) -> list[str]:
    """Where the registry disagrees with the version ledger.

    `ledger` is `{name: {version: [{"fingerprint": ...}, ...]}}`, as
    `evals/prompt-versions.json` holds it. **A version keeps one text for
    life.** Every registered prompt's current version must be in the ledger,
    holding exactly its current fingerprint. A declared version is the only
    part of a prompt's identity a person writes by hand, so it is the part
    that gets forgotten: #117 changed the text of `generate.standard` and
    `generate.procedure` and kept both at their version (#236).

    **Against the ledger, not the epoch file** (1d on #236). The epoch file
    lags until the next epoch on purpose, so a guard against it catches only
    the first unbumped edit after each epoch: v6 -> v7, then a second edit
    that stays at v7, compares v7 with the epoch's v3 and passes. The ledger
    records v7's text the moment v7 exists, so the second edit is caught.
    One line per problem; empty means every prompt's version names one text.

    **What this cannot see** (1d on #422): an edit to the ledger in the same
    change. Rewrite a recorded fingerprint to match a changed text and this
    returns nothing. It compares the registry with the ledger, not the ledger
    with its own history; that the ledger only grows is held by review of
    its diff (`scripts/prompt_versions.py` never rewrites an entry).
    """
    problems = []
    for name, prompt in sorted(REGISTRY.items()):
        entries = ledger.get(name, {}).get(str(prompt.version))
        if not entries:
            problems.append(
                f"{name} v{prompt.version} ({prompt.fingerprint}) is not in the ledger: "
                "record it with scripts/prompt_versions.py"
            )
        elif [e["fingerprint"] for e in entries] != [prompt.fingerprint]:
            recorded = ", ".join(e["fingerprint"] for e in entries)
            problems.append(
                f"{name} v{prompt.version}: text changed ({recorded} -> {prompt.fingerprint}) "
                "without a new version"
            )
    return problems


def compare(recorded: dict[str, str]) -> list[str]:
    """What changed between `recorded` fingerprints and the current ones.

    Returns one line per difference, in the order a reader cares about:
    prompts that changed, then prompts that appeared, then prompts that went
    away. An empty list means this run is comparable with whatever produced
    `recorded`, which is the only claim MEASUREMENTS.md lets a number make.
    Renames with unchanged text are not differences; see `renames`.
    """
    current = fingerprints()
    renamed = renames(recorded)
    renamed_old = {old for old, _ in renamed}
    renamed_new = {new for _, new in renamed}
    changed = [
        f"{name}: {recorded[name]} -> {current[name]}"
        for name in sorted(set(recorded) & set(current))
        if recorded[name] != current[name]
    ]
    added = [
        f"{name}: new ({current[name]})"
        for name in sorted(set(current) - set(recorded) - renamed_new)
    ]
    removed = [
        f"{name}: gone (was {recorded[name]})"
        for name in sorted(set(recorded) - set(current) - renamed_old)
    ]
    return changed + added + removed
