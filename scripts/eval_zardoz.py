#!/usr/bin/env python3
"""Run the graded eval cases against your configured model.

    python scripts/eval_zardoz.py --repeat 5
    python scripts/eval_zardoz.py --suite routing --repeat 20
    python scripts/eval_zardoz.py --dry-run

Comparing models is the other reason to run this. `--model` takes a LiteLLM
model string and overrides config.yaml's `llm` block with it, so a sweep is a
shell loop rather than a config file per candidate. It is an override, not a
second constructor: the provider is built the way every command builds one,
through `get_provider`, ledger wrapper and all — see `evals/provider.py` for
why that has to be true of a harness whose numbers describe production:

    for m in anthropic/claude-sonnet-5 ollama_chat/qwen3:14b; do
        python scripts/eval_zardoz.py --model "$m" --suite routing --repeat 3
    done

The report then carries what the run cost as well as what it scored, because
a pass rate without a price does not decide anything. A local model reports
0.0 and a provider that cannot say reports nothing at all — those are
different answers and the report keeps them apart.

Costs real money and needs network, which is why it is not part of
`scripts/check.py`. The prompts are the only part of this project that
cannot be tested against fixtures — whether the answerer refuses, whether
the router picks correctly, whether the rewriter invents a detail are all
properties of a model's behaviour.

`--repeat` is the point. A routing bug measured at one failure in eight came
back clean on its first two probes; graded once per case it would have
shipped. The report is a rate, and a case that passes seven times in eight
is reported as flaky rather than as passing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.provider import EVAL_SITE, Metered, build_provider, eval_config
from evals.runner import (
    GENERATED_SUITES,
    SUITES,
    format_report,
    load_cases,
    load_corpora,
    run_case,
    set_entailer,
)
from policyforge.llm import ledger

# The report quotes what the model said, and models return characters a
# Windows console cannot encode — a non-breaking hyphen is enough. Printing
# one raised UnicodeEncodeError *after* every call had been made and billed,
# destroying the report of a run that had completed: 78 requests spent, one
# traceback, no numbers. This project keeps a list of runs that say nothing;
# losing one to a console codec does not belong on it.
#
# Reconfigured rather than left to PYTHONIOENCODING, because the person who
# needs it is the one who has not hit this yet.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# The meter lives beside the construction path, in evals/provider.py, so the
# harness and the mutation sweep build and count providers the same way. Kept
# importable here under its old name for the tests that reach for it.
_Metered = Metered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=sorted(SUITES), action="append", default=None)
    parser.add_argument("--repeat", type=int, default=3, help="runs per case (default: 3)")
    parser.add_argument("--limit", type=int, default=None, help="cap cases per suite")
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would run, and call nothing"
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=0.0,
        help=(
            "seconds to hold between requests, for a model whose per-minute cap is low "
            "enough that retries alone still land inside the same window "
            "(cohere/command-a needs about 2)"
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "LiteLLM model string to grade instead of the configured provider, "
            "e.g. anthropic/claude-sonnet-5, ollama_chat/qwen3:14b, gemini/gemini-2.0-flash. "
            'Needs the litellm extra: pip install "policyforge[litellm]"'
        ),
    )
    parser.add_argument(
        "--entail-model",
        default=None,
        help=(
            "judge every cited sentence in the answering suites for entailment, using "
            "this LiteLLM model string: config's entail.answering, but for one run. "
            "One extra model call per cited sentence on top of the answer, priced "
            "separately in the summary. Name a model other than the one being graded"
        ),
    )
    args = parser.parse_args()

    cases = load_cases(args.cases)
    corpora = load_corpora(args.cases) if args.cases else load_corpora()
    requested = args.suite or sorted(SUITES)
    # Once each, in the order first named (#276). Naming a suite twice
    # planned its cases twice: double the spend for no extra information,
    # since spread comes from --repeat, and each case counted twice in the
    # epoch.
    wanted = list(dict.fromkeys(requested))
    planned = [
        (suite, case)
        for suite in wanted
        for case in (cases.get(suite, [])[: args.limit] if args.limit else cases.get(suite, []))
    ]

    # Every way the plan differs from the command line gets said, rather than
    # fixed quietly (#276, #275): a silent de-duplication, or a suite a
    # `--cases` file does not name, is a population change a reader of the
    # plan cannot see. Printed before the refusal below, so a refused run
    # says why its suite is empty.
    notices = [
        f"{suite} named {requested.count(suite)} times; planned once"
        for suite in wanted
        if requested.count(suite) > 1
    ]
    if args.cases is not None:
        notices += [
            f"{suite}  0  (not in --cases file)"
            for suite in GENERATED_SUITES
            if suite in wanted and not cases.get(suite)
        ]
    for line in notices:
        print(line)

    # Per suite, not over the union (#223). `if not planned` alone is
    # satisfied by any one suite's cases, so a requested suite contributing
    # none dropped out of the run and the report without a word, and the
    # totals stayed true about what did run. Derived from `planned` rather
    # than from `cases`, so whatever emptied a suite — the file, `--limit` —
    # is what gets counted.
    empty = [suite for suite in wanted if not any(s == suite for s, _ in planned)]
    if args.suite and empty:
        # Asked for by name: a run without it is not the run that was asked
        # for, so refuse before anything is spent rather than report around
        # the hole.
        print(f"No cases for requested suite(s), so nothing was run: {', '.join(empty)}")
        return 1
    if not planned:
        print("No cases selected.")
        return 1
    # Not asked for by name, so a `--cases` file covering some suites is a
    # legitimate run of those — but the rest are named here and in the
    # report, so the run cannot be read as covering them. Whether the
    # SHIPPED cases fill every suite is a test, not a runtime judgement.
    not_run = f"NOT RUN, no cases: {', '.join(empty)}" if empty else ""

    if args.dry_run:
        print(f"{len(planned)} case(s) x {args.repeat} run(s) = {len(planned) * args.repeat} calls")
        for suite, case in planned:
            print(f"  {suite:11} {case.get('name') or case.get('question')}")
        if not_run:
            print(not_run)
        return 0

    # One construction path, whether or not --model was given: the options
    # become overrides on production's config, and get_provider builds from
    # that. Nothing here constructs a provider class by name.
    config = eval_config(model=args.model, min_interval=args.min_interval)
    provider = build_provider(config)
    if args.model:
        grading = args.model
    else:
        model = getattr(provider, "model", config["llm"].get("model", "?"))
        grading = f"{config['llm']['provider']} / {model}"

    # One cheap call before spending a whole run. Several of the paths under
    # test swallow provider failures on purpose — route() falls back to
    # keyword matching, expand_query returns nothing — so a dead API does
    # not raise. It quietly grades the fallback and reports a clean pass,
    # which is worse than an error because it looks like evidence. Measured:
    # with an exhausted balance the routing suite reported 11 of 11.
    try:
        # Labelled like every other eval call: the ledger should say this
        # was the reachability probe, not leave one unattributed line at the
        # top of every run.
        with ledger.about("probe", site=EVAL_SITE):
            provider.generate(system="Reply with one word.", prompt="ok?", max_tokens=64)
    except Exception as exc:  # noqa: BLE001 - any failure means do not proceed
        print(f"The API is not reachable, so nothing was run:\n  {exc}")
        return 2

    # Built after the reachability probe, so a dead API is reported once, by
    # the probe, rather than as a judge that would not construct.
    judge = None
    if args.entail_model:
        from policyforge.entail.llm_entailer import LLMEntailer, entailment_provider

        # Built the way `get_entailer` builds it, through
        # `entailment_provider`, so the judge's calls are classified and
        # ledgered like every other — and metered in its own right, because
        # the judge's calls are what the check costs, and folding them into
        # the answering total would leave no way to say what entailment cost
        # against what it caught. Its ledger settings come from `config`, so
        # they land in evals.jsonl with the rest of the run.
        entail_block = {"model": args.entail_model}
        judge_meter = Metered(entailment_provider(entail_block, config))
        judge = LLMEntailer(model=args.entail_model, provider=judge_meter)
        set_entailer(judge)

    print(f"Running {len(planned)} case(s) x {args.repeat} against {grading}...")
    if not_run:
        print(not_run)
    if judge is not None:
        print(f"Entailment judged by {args.entail_model}, one call per cited sentence.")
    print()

    results = []
    for suite, case in planned:
        result = run_case(suite, case, provider, repeat=args.repeat, corpora=corpora)
        results.append(result)
        mark = (
            "!"
            if result.errors
            else ("." if result.rate == 1.0 else ("~" if result.flaky else "x"))
        )
        print(mark, end="", flush=True)
    print("\n")
    print(format_report(results, repeat=args.repeat, requested=wanted))
    print(f"\n{provider.summary()}")
    if judge is not None:
        print(f"entailment: {judge_meter.summary()}")

    # Flaky is a failure. The whole reason this exists is that a case which
    # is right most of the time is indistinguishable, from one run, from a
    # case that is right always.
    # An exit code of 2 for "could not run" so a caller can tell a dead key
    # from a regression. 1 stays the graded failure.
    if any(r.errors for r in results):
        return 2
    return 1 if any(r.rate < 1.0 for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
