#!/usr/bin/env python3
"""Delete one prompt rule at a time and see which eval cases notice.

    python scripts/mutate_zardoz.py --suite routing
    python scripts/mutate_zardoz.py --suite answering --rule 6
    python scripts/mutate_zardoz.py --dry-run

The eval suite measures the model. This measures the eval suite. A rule
nothing catches is unguarded: either it does not do anything and should be
deleted, or the cases have a hole exactly where their evidence should be.

Both answers have shown up already. Stripping the grounding rules from the
answering prompt left every refusal case green, because half of them
retrieved nothing and never called the model at all.

A baseline runs first, and a case counts as guarding a rule only if it
passed clean and failed mutated. Without that, a flaky case reads as
evidence for whichever rule happened to be removed when it misfired.

One rule at a time cannot see a rule that another rule repeats. The
router's "prefer documents" and "if unsure, say documents" both survive
their own deletion and neither is dead weight: removing both misroutes an
ambiguous question every time. `--pairs` retries the survivors two at a
time, which is the difference between "delete this rule" and "these two
rules are one rule written twice".

Costs real money, more than the eval does — one full suite per rule.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.mutate import rules, summarize, without_rule
from evals.runner import load_cases, load_corpora, run_case

#: Which prompt each suite is graded against. `conversation` drives the
#: whole shell and so depends on two; it is swept under answering, where
#: the rules it can actually fail on live.
TARGETS = {
    "routing": ("policyforge.zardoz.skills", "ROUTER_SYSTEM_PROMPT"),
    "paraphrase": ("policyforge.zardoz.skills", "ROUTER_SYSTEM_PROMPT"),
    "resolution": ("policyforge.zardoz.conversation", "RESOLVE_SYSTEM_PROMPT"),
    "expansion": ("policyforge.zardoz.paraphrase", "EXPANSION_SYSTEM_PROMPT"),
    "answering": ("policyforge.zardoz.answer", "SYSTEM_PROMPT"),
    "answer_paraphrase": ("policyforge.zardoz.answer", "SYSTEM_PROMPT"),
}


#: Text outside the numbered rules that says the same thing one of them
#: says. A rule duplicated here survives its own deletion and reads as dead
#: weight: the answering prompt's refusal rule looked unguarded until the
#: sentence on the user turn was deleted alongside it, at which point five
#: of six refusal cases failed. Swept like a pair.
COMPANIONS = {
    "answering": ("policyforge.zardoz.answer", "USER_TURN_INSTRUCTION"),
    "answer_paraphrase": ("policyforge.zardoz.answer", "USER_TURN_INSTRUCTION"),
}


def _run(suite, cases, provider, corpora, repeat):
    return {
        case.get("name", "?"): run_case(suite, case, provider, repeat=repeat, corpora=corpora)
        for case in cases
    }


def _sweep_pairs(unguarded, plan, provider, corpora, repeat):
    """Retry the unguarded rules two at a time.

    A rule can be load-bearing and still survive its own deletion, because
    another rule says the same thing. The router's "prefer documents" and
    "if unsure, say documents" are exactly that: either one alone holds the
    line on an ambiguous question, and it takes removing both to misroute
    it. Reported one at a time they look like dead weight, and deleting
    either on that evidence would leave a prompt that still passed the suite
    and had quietly lost a rule.

    Only the rules that survived alone are paired, which keeps this to a few
    dozen calls rather than every combination.
    """
    from itertools import combinations

    still = []
    by_suite: dict[str, list] = {}
    for suite, number, label in unguarded:
        by_suite.setdefault(suite, []).append((number, label))

    for suite, module_path, attribute, prompt, cases, _ in plan:
        pending = by_suite.get(suite)
        if not pending:
            continue

        module = importlib.import_module(module_path)
        print(f"=== {suite}: pairing {len(pending)} rules that survived alone")
        joint: set[int] = set()

        # First against the companion text, if this suite has any. A rule
        # restated outside the numbered list is invisible to every other
        # mutation here.
        companion = COMPANIONS.get(suite)
        if companion:
            companion_module = importlib.import_module(companion[0])
            companion_text = getattr(companion_module, companion[1])

            # What the companion breaks on its own, with every rule intact.
            # Without subtracting this, a rule is credited for failures the
            # companion caused by itself: the answering prompt's rule about
            # contradictions was credited by a refusal case that fails
            # whenever the companion goes, whichever rule went with it.
            setattr(companion_module, companion[1], "")
            try:
                alone = _run(suite, cases, provider, corpora, repeat)
            finally:
                setattr(companion_module, companion[1], companion_text)
            confounded = {n for n, r in alone.items() if r.rate < 1.0}
            if confounded:
                print(
                    f"  {companion[1]} alone breaks {len(confounded)}: "
                    f"{', '.join(sorted(confounded)[:3])} — discounted below"
                )

            for number, _ in pending:
                setattr(module, attribute, without_rule(prompt, number))
                setattr(companion_module, companion[1], "")
                try:
                    results = _run(suite, cases, provider, corpora, repeat)
                finally:
                    setattr(module, attribute, prompt)
                    setattr(companion_module, companion[1], companion_text)
                caught = sorted(
                    n for n, r in results.items() if r.rate < 1.0 and n not in confounded
                )
                if caught:
                    joint.add(number)
                    print(
                        f"  rule {number} + {companion[1]}  CAUGHT by "
                        f"{len(caught)}: {', '.join(caught[:3])}"
                    )
            pending = [(n, label) for n, label in pending if n not in joint]

        if len(pending) < 2:
            still.extend((suite, n, label) for n, label in pending)
            print()
            continue
        for (first, _), (second, _) in combinations(pending, 2):
            mutated = without_rule(without_rule(prompt, first), second)
            setattr(module, attribute, mutated)
            try:
                results = _run(suite, cases, provider, corpora, repeat)
            finally:
                setattr(module, attribute, prompt)
            caught = sorted(n for n, r in results.items() if r.rate < 1.0)
            if caught:
                joint |= {first, second}
                print(
                    f"  rules {first} + {second}  CAUGHT by {len(caught)}: {', '.join(caught[:3])}"
                )
        still.extend((suite, n, label) for n, label in pending if n not in joint)
        print()
    return still


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=sorted(TARGETS), action="append", default=None)
    parser.add_argument("--rule", type=int, action="append", default=None)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--pairs",
        action="store_true",
        help="after the sweep, retry the unguarded rules two at a time",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    all_cases = load_cases()
    corpora = load_corpora()
    wanted = args.suite or ["routing", "resolution", "expansion", "answering"]

    plan = []
    for suite in wanted:
        module_path, attribute = TARGETS[suite]
        prompt = getattr(importlib.import_module(module_path), attribute)
        cases = all_cases.get(suite, [])[: args.limit]
        numbers = args.rule or [n for n, _ in rules(prompt)]
        plan.append((suite, module_path, attribute, prompt, cases, numbers))

    total = sum(len(c) * (len(n) + 1) * args.repeat for _, _, _, _, c, n in plan)
    if args.dry_run:
        for suite, _, attribute, _, cases, numbers in plan:
            print(f"{suite}: {len(cases)} case(s) x {len(numbers)} rule(s) of {attribute}")
        print(f"\n{total} calls including baselines")
        return 0

    from evals.provider import EVAL_SITE, build_provider, eval_config
    from policyforge.llm import ledger

    # The same construction path as the eval harness and production: the
    # configured provider, through get_provider, into the evals ledger.
    provider = build_provider(eval_config())
    try:
        with ledger.about("probe", site=EVAL_SITE):
            provider.generate(system="Reply with one word.", prompt="ok?", max_tokens=64)
    except Exception as exc:  # noqa: BLE001 - any failure means do not proceed
        print(f"The API is not reachable, so nothing was run:\n  {exc}")
        return 2

    print(f"{total} calls across {len(plan)} suite(s)...\n")
    unguarded = []
    for suite, module_path, attribute, prompt, cases, numbers in plan:
        module = importlib.import_module(module_path)
        baseline = _run(suite, cases, provider, corpora, args.repeat)
        clean = {name for name, r in baseline.items() if r.rate == 1.0}
        print(f"=== {suite}: {attribute}, {len(clean)}/{len(cases)} clean at baseline")
        if len(clean) < len(cases):
            noisy = sorted(set(baseline) - clean)
            print(f"    excluded as already failing: {', '.join(noisy)}")

        for number in numbers:
            try:
                mutated = without_rule(prompt, number)
            except KeyError:
                continue
            setattr(module, attribute, mutated)
            try:
                results = _run(suite, cases, provider, corpora, args.repeat)
            finally:
                setattr(module, attribute, prompt)

            caught = sorted(n for n, r in results.items() if r.rate < 1.0 and n in clean)
            label = summarize(dict(rules(prompt))[number])
            if caught:
                print(f"  rule {number:2}  CAUGHT by {len(caught)}: {label}")
                for name in caught[:4]:
                    print(f"             {name}: {results[name].failures[0].detail[:70]}")
            else:
                print(f"  rule {number:2}  UNGUARDED    {label}")
                unguarded.append((suite, number, label))
        print()

    if unguarded and args.pairs:
        unguarded = _sweep_pairs(unguarded, plan, provider, corpora, args.repeat)

    if unguarded:
        print("Unguarded rules — nothing in the suite fails when they are removed:")
        for suite, number, label in unguarded:
            print(f"  {suite:18} rule {number:2}  {label}")
        return 1
    print("Every rule is guarded by at least one case.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
