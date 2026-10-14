#!/usr/bin/env python3
"""Generate a fresh corpus on a local model and compare what the checkers find
in it with what they find in the committed one (#262).

    python scripts/fresh_corpus.py run       fetch release/1.8's tip, generate, compare
    python scripts/fresh_corpus.py run --heavy C:\\policyforge-local\\bin\\heavy
                                             the same, each step through the `heavy`
                                             queue: use it when after hours is off
    python scripts/fresh_corpus.py compare --new <corpus dir>
                                             compare an existing fresh corpus again

**Why:** the checkers are tested against `tests/fixtures/deontic_corpus/`,
128 documents generated weeks ago. Several defects since were shapes only real
output shows, so fresh output on today's prompts is evidence at $0.

**What `run` does**, in its own worktree of the tip (as `scheduled_eval.py`
fetches one, so the shared clone's HEAD never moves), with the same
credential-free environment and pinned local config:

1. `policyforge map` over the six public-domain catalogs the committed corpus
   was generated from: NIST SP 800-53, the HIPAA Security Rule, FedRAMP,
   ARC-AMPE, the NIST AI RMF and its Playbook. No model calls.
2. For each of the 25 topics the tip's `init` rule gives the example profile
   (`PROFILE`: a healthcare SaaS that builds software and AI, with PHI, an
   800-53 baseline and no facilities of its own): `synthesize`, then a
   Standard, then a Procedure that names it. One topic failing is recorded
   and the rest go on.
3. `compare`: the committed corpus and the fresh one through the tip's own
   `check_tree` and placeholder reader.

The organisation is **Example Org**, as in the committed corpus, and no
licensed catalog is loaded.

**What `compare` reports**, written to `comparison.md` beside the corpus:

- every `check` finding, reduced to its **shape** (quoted text, numbers and
  bracketed labels replaced by stand-ins), counted by how many documents of
  each corpus carry it. A shape only the fresh corpus has is the point.
- every placeholder left after substitution, by the reader's three classes
  (roles left, unfilled, unresolved citations), and the labels (forms, for
  unfilled) only the fresh corpus has.
- every bracketed span the reader **didn't read**, found by a second
  instrument (any `[...]` that isn't a markdown link or image), by form, fresh
  first. The reader can't report a bracket it can't parse (#243's comma
  label was "never filled, never reported"), so a comparison through the
  reader alone can't see that class; this one can (1d on PR 265).

It says what the checkers report and nothing about whether they're right:
each new shape is read by hand, then filed as its own issue or dismissed with
a reason on #262.

**Never a graded measurement**, like every local run. The corpus stays out of
the repository unless a finding needs a minimal excerpt as a fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.scheduled_eval import (
    BRANCH,
    PINNED_MODEL,
    _tip,
    pinned_config,
    run_env,
)

STATE = Path(r"C:\policyforge-local\fresh-corpus")

#: The public-domain catalogs the committed corpus was generated from (its
#: README, "Where they came from"), and the two HIPAA rules the starter's PHI
#: topics anchor (#420; the committed corpus has no document for those two
#: topics). Each topic picks its anchors out of these.
CATALOGS = (
    "nist-800-53-r5",
    "hipaa-security-rule",
    "hipaa-privacy-rule",
    "hipaa-breach-notification-rule",
    "fedramp",
    "arc-ampe",
    "nist-ai-rmf",
    "nist-ai-rmf-playbook",
)
ORG_NAME = "Example Org"
COMMITTED = Path("tests") / "fixtures" / "deontic_corpus" / "documents"


#: The scope facts the run's registry is built from (22, part 3): the example
#: organisation, as 80's R2 ruling describes it. 25 topics at the tip.
PROFILE = {
    "builds_software": True,
    "own_facilities": False,
    "personal_data": "phi",
    "baseline": "800-53",
    "ai": "builder",
}


def registry_program() -> str:
    """Python source that prints the registry `init` would write for PROFILE.

    Run with the tip's own sources on the path: the rule and the pool are the
    tip's, not this script's, so a run measures the tree it was asked about.
    """
    return (
        "import sys\n"
        "from policyforge.topics.build import load_pool, render\n"
        "from policyforge.topics.rule import select\n"
        "from policyforge.topics.scope_facts import ScopeFacts\n"
        f"facts = ScopeFacts(**{PROFILE!r})\n"
        "sys.stdout.write(render(load_pool(), facts, select(facts)))\n"
    )


def topic_names(registry: str) -> list[str]:
    """The topic names in a registry, in order."""
    import yaml

    return [topic["name"] for topic in yaml.safe_load(registry)["topics"]]


def slug(name: str) -> str:
    """A file name for a topic: what `synthesize` names its output."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def policy_names(registry: str) -> list[str]:
    """The Policies a registry declares, in order (22, part 2b)."""
    import yaml

    return [policy["name"] for policy in yaml.safe_load(registry).get("policies") or []]


def policy_steps(
    registry: str, generated: set[str], out: Path, policyforge: list[str]
) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """(one command per Policy whose topics were all generated, the Policies
    left out).

    A Policy is drafted from every Standard that names it, so one whose
    topics were not all generated this run (a `--limit`, or a step that
    failed) would be refused by `generate` rather than drafted over a subset:
    it is left out here and named, so a run's report never reads as covering
    a Policy it didn't.
    """
    import yaml

    data = yaml.safe_load(registry)
    members: dict[str, list[str]] = {}
    for topic in data["topics"]:
        key = str(topic.get("policy") or "").strip().casefold()
        if key:
            members.setdefault(key, []).append(topic["name"])
    commands: list[tuple[str, list[str]]] = []
    left_out: list[str] = []
    for name in policy_names(registry):
        topics = members.get(name.strip().casefold(), [])
        if topics and all(topic in generated for topic in topics):
            target = out / "policies" / f"{slug(name)}.md"
            generate = [*policyforge, "generate", "--tier", "policy", "--policy", name]
            commands.append(("policy", [*generate, "--out", str(target)]))
        else:
            left_out.append(name)
    return commands, left_out


def steps(name: str, out: Path, policyforge: list[str]) -> list[tuple[str, list[str]]]:
    """The three commands that make one topic's Standard and Procedure."""
    controls = [a for c in CATALOGS for a in ("--controls", f"data/frameworks/{c}/controls.json")]
    synthesis = out / "synthesis"
    standard = out / "standards" / f"{slug(name)}.md"
    procedure = out / "procedures" / f"{slug(name)}.md"
    return [
        ("synthesize", [*policyforge, "synthesize", "--topic-name", name, *controls,
                        "--out-dir", str(synthesis)]),
        ("standard", [*policyforge, "generate", "--tier", "standard",
                      "--synthesis", str(synthesis / f"{slug(name)}.md"), "--out", str(standard)]),
        ("procedure", [*policyforge, "generate", "--tier", "procedure",
                       "--synthesis", str(synthesis / f"{slug(name)}.md"),
                       "--standard", str(standard), "--out", str(procedure)]),
    ]  # fmt: skip


# --------------------------------------------------------------------------
# Comparing what the checkers report
# --------------------------------------------------------------------------

_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`|“[^”]*”")
_BRACKETED = re.compile(r"\[[^\]]*\]")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def shape(message: str) -> str:
    """A finding's message with what varies between documents taken out:
    quoted text, bracketed labels and numbers. Two findings of one kind on two
    documents then have one shape."""
    text = _QUOTED.sub("'…'", message)
    text = _BRACKETED.sub("[…]", text)
    return _NUMBER.sub("N", text)


def compare_shapes(old: dict[str, list[str]], new: dict[str, list[str]]) -> list[dict]:
    """Each shape with the number of documents carrying it in each corpus, and
    one example from the fresh one. `old` and `new` map a document to its
    findings' messages. Shapes only the fresh corpus has come first."""

    def per_shape(corpus):
        found = defaultdict(dict)
        for doc, messages in corpus.items():
            for message in messages:
                found[shape(message)].setdefault(doc, message)
        return found

    was, now = per_shape(old), per_shape(new)
    rows = []
    for key in set(was) | set(now):
        example = next(iter(now[key].items()), None) if key in now else None
        rows.append({
            "shape": key,
            "old": len(was.get(key, {})),
            "new": len(now.get(key, {})),
            "example": example,
        })  # fmt: skip
    return sorted(rows, key=lambda r: (r["old"] > 0, -r["new"], r["shape"]))


_WORD = re.compile(r"[A-Za-z]+")


def _word_class(word: str) -> str:
    if word.isupper() and len(word) > 1:
        return "A"
    return "Aa" if word[0].isupper() else "a"


def label_form(label: str) -> str:
    """A placeholder label's form: each word as its case (`Aa`, `a`, `A`), runs
    of one case collapsed, digits as `N`, punctuation kept. The model names
    most unfilled labels itself, so two corpora share few labels but should
    share forms: `[Access Review Frequency]` and `[Retention Period]` are both
    `Aa`, and `[Assignment: organization-defined frequency]` is `Aa: a-a`
    (`defined frequency` is one run). The committed corpus's 835 unfilled
    labels have 33 forms."""
    form = _NUMBER.sub("N", _WORD.sub(lambda m: _word_class(m.group()), label))
    return re.sub(r"\b(Aa|a|A)(?: \1\b)+", r"\1", form)


def placeholder_classes(text: str) -> dict[str, list[str]]:
    """What the generator's own placeholder reader leaves in a document with no
    organisation configured: roles left, unfilled, unresolved citations, and the
    bracketed prose the reader leaves as written (267)."""
    from policyforge.org.context import OrgProfile, apply_substitutions

    result = apply_substitutions(text, OrgProfile(name=ORG_NAME))
    return {
        "roles left": result.roles_left,
        "unfilled": result.unfilled,
        "unresolved citations": result.unresolved_citations,
        "bracketed prose": result.prose,
    }


def _check_findings(documents: dict[str, str]) -> dict[str, list[str]]:
    """`check_tree`'s findings per document, over a temporary tree of `.md`."""
    from policyforge.content.check import check_tree

    with tempfile.TemporaryDirectory() as root:
        for name, text in documents.items():
            path = Path(root) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        report = check_tree(Path(root), org_actors=(ORG_NAME,))
        found: dict[str, list[str]] = {name: [] for name in documents}
        for finding in report.findings:
            name = Path(finding.path).as_posix()
            key = next((d for d in documents if name.endswith(d)), name)
            found.setdefault(key, []).append(finding.message)
    return found


def _read(directory: Path, suffix: str) -> dict[str, str]:
    """Every document under `directory`, keyed `<tier dir>/<name>.md`."""
    return {
        f"{path.parent.name}/{path.name.removesuffix('.txt')}": path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob(f"*{suffix}"))
        if path.parent.name in ("policies", "standards", "procedures")
    }


def comparison_report(old_docs: dict[str, str], new_docs: dict[str, str]) -> str:
    rows = compare_shapes(_check_findings(old_docs), _check_findings(new_docs))
    lines = [
        f"# Checker findings: committed {len(old_docs)} documents vs fresh {len(new_docs)}",
        "",
        "Documents carrying each finding shape. Shapes only the fresh corpus has come first.",
        "",
        "| old | new | shape | example (fresh) |",
        "| --: | --: | ----- | --------------- |",
    ]
    for row in rows:
        example = f"{row['example'][0]}: {row['example'][1][:160]}" if row["example"] else ""
        lines.append(f"| {row['old']} | {row['new']} | {row['shape'][:160]} | {example} |")

    lines += ["", "# Placeholders left after substitution", ""]
    old_classes = [placeholder_classes(t) for t in old_docs.values()]
    new_classes = {doc: placeholder_classes(t) for doc, t in new_docs.items()}
    for klass in ("roles left", "unfilled", "unresolved citations", "bracketed prose"):
        # Unfilled labels are mostly the model's own names, so they're
        # compared by form; the other two classes are small, named sets.
        key = label_form if klass == "unfilled" else (lambda label: label)
        was = Counter(key(label) for c in old_classes for label in c[klass])
        now = defaultdict(list)
        for doc, classes in new_classes.items():
            for label in classes[klass]:
                now[key(label)].append(f"`[{label}]` in {doc}")
        only_new = sorted(k for k in now if k not in was)
        unit = "forms" if klass == "unfilled" else "labels"
        lines.append(
            f"- **{klass}:** {sum(was.values())} in the committed corpus "
            f"({len(was)} {unit}), {sum(len(v) for v in now.values())} in the fresh one "
            f"({len(now)} {unit}); {len(only_new)} {unit} only in the fresh one"
        )
        lines += [f"  - `{k}`: {len(now[k])}, e.g. {now[k][0]}" for k in only_new]

    was = Counter(label_form(span) for t in old_docs.values() for span in unread_brackets(t))
    now = defaultdict(list)
    for doc, text in new_docs.items():
        for span in unread_brackets(text):
            now[label_form(span)].append(f"`[{span}]` in {doc}")
    only_new = sorted(k for k in now if k not in was)
    lines += [
        "",
        "# Brackets the placeholder reader didn't read",
        "",
        f"Every `[...]` that isn't a markdown link or image, and that the reader neither "
        f"filled, left outstanding nor took as a citation tag: {sum(was.values())} in the "
        f"committed corpus ({len(was)} forms), {sum(len(v) for v in now.values())} in the "
        f"fresh one ({len(now)} forms); {len(only_new)} forms only in the fresh one.",
        "",
    ]
    lines += [f"- `{k}`: {len(now[k])}, e.g. {now[k][0]}" for k in only_new]
    return "\n".join(lines) + "\n"


#: Any bracketed span on one line: the second instrument, which knows nothing
#: of labels. A markdown link (`[text](url)` or `[text][ref]`), an image
#: (`![alt](src)`) and a wiki-style `[[...]]` aren't brackets a reader of
#: placeholders is meant to read, so they're not counted.
_ANY_BRACKET = re.compile(r"(?<![!\[])\[([^\[\]\n]+)\](?![(\[\]])")


def unread_brackets(text: str) -> list[str]:
    """The bracketed spans in `text` that are neither read by the placeholder
    reader nor a citation tag. The reader reads a span its own pattern
    (`org.context._PLACEHOLDER_RE`, which `apply_substitutions` runs) matches
    whole, filled, outstanding or a tag. A citation tag the pattern can't
    match, such as `[NIST AC-7(3)]` (a parenthesis) or a `|`-joined one, is
    still a citation, read by `check` (`content.tags.is_citation_tag`), not a
    placeholder: measured, those are 4,616 of the committed corpus's 6,878
    spans the pattern doesn't match, leaving 2,262."""
    from policyforge.content.tags import is_citation_tag
    from policyforge.org.context import _PLACEHOLDER_RE

    return [
        match.group(1)
        for match in _ANY_BRACKET.finditer(text)
        if not _PLACEHOLDER_RE.fullmatch(match.group(0)) and not is_citation_tag(match.group(0))
    ]


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def queued(command: list[str], heavy: Path | None, bash: Path, handle: str) -> list[str]:
    """`command`, through the machine's one-slot `heavy` queue when `heavy` names
    it. Each step takes the slot for itself, so a corpus run never holds it for
    the hour and a half the whole run takes."""
    if heavy is None:
        return command
    return [str(bash), str(heavy), handle, "fresh-corpus", "--", *command]


def _org_config(model: str, ledger: Path, content_dir: Path | None = None) -> str:
    text = pinned_config(model, ledger) + f'org:\n  name: "{ORG_NAME}"\n'
    if content_dir is not None:
        # Where `generate --policy` looks for the Standards a Policy governs:
        # the run's own, not the checkout's `docs/`. Single-quoted, so a
        # Windows path's backslashes are not read as YAML escapes.
        quoted = str(content_dir).replace("'", "''")
        text += f"zardoz:\n  content_dir: '{quoted}'\n"
    return text


def cmd_run(args) -> int:
    state: Path = args.state
    when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tree, sha = _tip(state, args.handle, args.fj, args.bash, args.branch)
    out = state / "corpora" / f"{when}-{sha[:12]}"
    config = out / "config.yaml"
    out.mkdir(parents=True)
    config.write_text(
        _org_config(args.model, state / "ledger" / "llm.jsonl", content_dir=out),
        encoding="utf-8",
    )
    env = run_env(
        dict(os.environ),
        {
            "POLICYFORGE_CONFIG": str(config),
            "PYTHONPATH": os.pathsep.join([str(tree / "src"), str(tree)]),
            "PYTHONIOENCODING": "utf-8",
        },
    )
    policyforge = [sys.executable, "-c", "from policyforge.cli import cli; cli()"]

    def call(command: list[str]) -> subprocess.CompletedProcess:
        command = queued(command, args.heavy, args.bash, args.handle)
        return subprocess.run(command, cwd=tree, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=args.timeout)  # fmt: skip

    built = call([sys.executable, "-c", registry_program()])
    if built.returncode != 0 or not built.stdout.strip():
        (out / "registry.log").write_text(built.stdout + built.stderr, encoding="utf-8")
        print(f"building the registry failed (exit {built.returncode}); see {out / 'registry.log'}")
        return 1
    (tree / "config" / "topics.yaml").write_text(built.stdout, encoding="utf-8", newline="\n")
    log = []
    mapped = call([*policyforge, "map", *[a for c in CATALOGS for a in ("--controls", f"data/frameworks/{c}/controls.json")]])  # fmt: skip # noqa: E501
    if mapped.returncode != 0:
        (out / "map.log").write_text(mapped.stdout + mapped.stderr, encoding="utf-8")
        print(f"map failed (exit {mapped.returncode}); see {out / 'map.log'}")
        return 1
    registry = (tree / "config" / "topics.yaml").read_text(encoding="utf-8")
    names = topic_names(registry)

    def run_step(name: str, step: str, command: list[str]) -> int:
        started = time.monotonic()
        done = call(command)
        log.append({"topic": name, "step": step, "exit": done.returncode,
                    "seconds": round(time.monotonic() - started, 1),
                    "tail": (done.stdout + done.stderr).strip().splitlines()[-3:]})  # fmt: skip
        print(f"{name} {step}: exit {done.returncode} in {log[-1]['seconds']} s", flush=True)
        return done.returncode

    generated: set[str] = set()
    for name in names[: args.limit] if args.limit else names:
        done = (run_step(name, step, command) for step, command in steps(name, out, policyforge))
        if all(code == 0 for code in done):
            generated.add(name)
    # Each Policy last: it is drafted from the Standards just generated.
    policy_commands, left_out = policy_steps(registry, generated, out, policyforge)
    for command_name, command in policy_commands:
        run_step(command[command.index("--policy") + 1], command_name, command)
    if left_out:
        print(
            f"{len(left_out)} Polic{'y' if len(left_out) == 1 else 'ies'} not drafted, "
            "because not every topic under it was generated this run: " + ", ".join(left_out)
        )
    manifest = {"sha": sha, "model": args.model, "when": when, "steps": log}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    # In a process whose policyforge is the tip's, so the checkers compared are
    # the ones the corpus was generated beside, not this script's checkout's.
    compared = call(
        [sys.executable, str(Path(__file__).resolve()), "compare",
         "--new", str(out), "--old", str(tree / COMMITTED)]
    )  # fmt: skip
    print((compared.stdout + compared.stderr).strip())
    return compared.returncode


def cmd_compare(args) -> int:
    old_dir = args.old or Path(__file__).resolve().parent.parent / COMMITTED
    report = comparison_report(_read(old_dir, ".md.txt"), _read(args.new, ".md"))
    (args.new / "comparison.md").write_text(report, encoding="utf-8")
    print(f"wrote {args.new / 'comparison.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("run", "compare"))
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--model", default=PINNED_MODEL)
    parser.add_argument("--branch", default=BRANCH)
    parser.add_argument("--handle", default="b5", help="the bot the read-only fetch uses")
    parser.add_argument("--limit", type=int, default=None, help="only the first N topics")
    parser.add_argument("--new", type=Path, help="compare: the fresh corpus directory")
    parser.add_argument("--old", type=Path, default=None, help="compare: the committed corpus")
    parser.add_argument("--fj", type=Path, default=Path(r"C:\forgejo\bin\fj"))
    parser.add_argument("--bash", type=Path, default=Path(r"C:\Program Files\Git\bin\bash.exe"))
    parser.add_argument("--timeout", type=int, default=3600, help="seconds per command")
    parser.add_argument(
        "--heavy", type=Path, default=None, help="run each step through this heavy queue script"
    )
    args = parser.parse_args(argv)
    return {"run": cmd_run, "compare": cmd_compare}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
