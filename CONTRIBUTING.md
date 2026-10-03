# Contributing

## Before you start

Run the gate once so you know it passes on a clean tree:

```bash
python scripts/check.py
```

It runs ruff (lint and format), pytest, bandit, semgrep, pip-audit and
mdformat, plus gitleaks if the binary is on your PATH.

semgrep is not in the `dev` extra. It pins its own dependencies exactly, and
installed beside the project those pins became the project's, so it has its
own hashed lock in `requirements/semgrep/` and its own environment:

```bash
python -m venv .tools/semgrep
.tools/semgrep/Scripts/pip install --require-hashes -r requirements/semgrep/semgrep.txt
```

(`bin/` rather than `Scripts/` outside Windows.) The gate finds it there or on
your PATH, and skips it with a note otherwise; CI always runs it. It exits non-zero, so
it works as a pre-push hook. `pre-commit install` wires the fast subset to
run on commit.

Python 3.12 in a project `.venv`. Dependencies come from three hashed locks,
each regenerated with the command in its own header, never edited by hand:

| Lock                               | Installed by                            | Header command                                                                                                                           |
| ---------------------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `requirements/ci.txt`              | CI, the dev container                   | `uv pip compile pyproject.toml --extra dev --extra mcp --universal --python-version 3.12 --generate-hashes -o requirements/ci.txt`       |
| `requirements/runtime.txt`         | the container image                     | `uv pip compile pyproject.toml --extra mcp --universal --python-version 3.12 --generate-hashes -o requirements/runtime.txt`              |
| `requirements/semgrep/semgrep.txt` | CI, the dev container, `.tools/semgrep` | `uv pip compile requirements/semgrep/semgrep.in --universal --python-version 3.12 --generate-hashes -o requirements/semgrep/semgrep.txt` |

**A change to the dependencies in `pyproject.toml` means regenerating
`ci.txt` and `runtime.txt` together.** The runtime lock must pin exactly the
versions CI tests (`tests/test_container.py` checks), so regenerate `ci.txt`
first and seed `runtime.txt` from it: `cp requirements/ci.txt requirements/runtime.txt`, then run
the runtime command. uv keeps the versions already in its output file.
Dependabot's own edits to these locks are Linux-only and are regenerated the
same way rather than merged. To move semgrep, change the version in
`requirements/semgrep/semgrep.in`, regenerate `semgrep.txt`, and change the
`rev:` in `.pre-commit-config.yaml` with it.

## The one rule that is not about style

**Some behaviour in this repository is a stated commitment, not an
implementation detail.** [docs/commitments.md](docs/commitments.md) lists
eight of them, each with the test that fails when it stops being true. If
your change makes one of those tests fail, the fix is almost never to edit
the test.

Changing a commitment deliberately is allowed and is a **breaking change**:
it needs a major version bump and a `Security` entry in
[CHANGELOG.md](CHANGELOG.md) saying what changed and why.

The paths where this bites most often:

| If you touch                                                   | Read first                                                                                                                          |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `llm/boundary.py`, `llm/channel.py`                            | What may be sent where, and why it raises rather than warns                                                                         |
| `llm/ledger.py`                                                | It records metadata and never content, on purpose                                                                                   |
| `llm/fence.py`, anything building a prompt from retrieved text | The fence is measured; changing it changes a security property                                                                      |
| `ingest/parser_gate.py`                                        | Gating of model-written code                                                                                                        |
| `cli/etl.py`'s `_run_licensed`, and `plugins.py`               | The licensed-framework plugin only reads; the public command owns the write, refuses a public path, and writes the licence envelope |
| `export/publisher.py`, `export/publish.py`, `export/pull.py`   | Dry run is the default; a moved page is not overwritten; the guards are generic                                                     |
| Anything adding a URL or an env read                           | `tests/test_no_undeclared_endpoints.py`, `tests/test_credential_containment.py`                                                     |

## Adding a CLI command

Commands live in `src/policyforge/cli/`, one module per area. A command
module must import `load_config` and `get_provider` from
`policyforge.cli._common`, **never directly** — `tests/test_cli_seam.py`
enforces this over the AST. The test suite patches those names on
`policyforge.cli`, and a direct import in a submodule puts the command out
of the patch's reach: a test that believes it holds a fake provider would
build a real one, and could make real, billed calls.

## Adding an outbound endpoint

Adding a host to `ALLOWED_HOSTS` in
[`tests/test_no_undeclared_endpoints.py`](tests/test_no_undeclared_endpoints.py)
is a deliberate act, which is the point. Say in the PR what the endpoint is,
what is sent to it, and whether any of it is the operator's content. If it
receives content rather than serving public catalog data, it also needs a
row in [docs/subprocessors.md](docs/subprocessors.md).

## Reading an environment variable

Declare it in `DECLARED_ENV_READERS` in
[`tests/test_credential_containment.py`](tests/test_credential_containment.py)
with what it reads and why. **Never read one in a module that defines prompt
text** — the test refuses it, and the reason is that the accident this
prevents is an env value landing in an f-string three lines from a prompt
constant.

If it is a new *credential*, it also belongs in `_CREDENTIALS` in
`tests/conftest.py`, so the suite cannot reach a paid endpoint with it.

## Prompts

Prompt text is registered in [`llm/prompts.py`](src/policyforge/llm/prompts.py)
so a measured run can say which text produced it. Changing a prompt changes
what [MEASUREMENTS.md](MEASUREMENTS.md) is comparable with — bump the
prompt's `version` and open a new epoch rather than editing numbers recorded
under the old text.

Note for anyone writing tooling over prompts: they are bound as
`SYSTEM_PROMPT = register(...)`, an `ast.Call`, not a bare string constant.
Anything matching on the assignment shape must look *underneath* the assigned
value or it will silently miss them.

## Tests

Match the house style: the module docstring says **why the test exists and
what failure it prevents**, not what it asserts. Where a rule is currently
prose in a docstring, prefer moving it into a test — several of the checks
here exist because prose was the wrong form for a rule.

**No test may touch the network.** This is a rule, not an enforced
property — there is no socket guard. What backs it is `conftest.py`
stripping every credential that could reach a provider (and disarming
dotenv's loader, so a dependency cannot put one back), which makes an
accidental paid call fail rather than succeed quietly. Hitting an
unauthenticated endpoint would still work, so this one is on the author.

## Running evals

Evals call a real model and cost real money, so they are not part of
`scripts/check.py`. Two things about running them honestly:

- **Run from a worktree pinned to a commit.** A suite started before a merge
  imports modules at process start, so it can finish having measured code
  that no longer exists, and nothing in the result would say so. The eval
  report prints the commit it ran from and whether the tree was dirty —
  a dirty tree means the number is not attributable to anything.
- **In a worktree, put its `src/` first on the path.** The editable install
  pins the checkout it was made from, so any interpreter from the
  virtualenv imports *that* checkout's `policyforge` — a gate or a bare
  `pytest` run in a worktree would report on code it never loaded.
  `scripts/check.py` and `pytest` both refuse to start when the package
  they resolve is not under the tree they were run from, and print the
  `PYTHONPATH=<tree>/src` invocation that fixes it (`scripts/tree_guard.py`).
- **A number without an epoch is not comparable and is worse than no
  number.** If you changed a prompt, open a new epoch in
  [MEASUREMENTS.md](MEASUREMENTS.md) rather than filing results under the
  old text. Prompt fingerprints will tell you when this has happened.

Record the runs that got worse. Several entries in MEASUREMENTS.md are
changes that were measured, found harmful, and reverted — that is the file
working, not a blemish on it.

**A change to what the harness sends is checked with a capture, not a
run.** Two runs at the same score can differ in what they asked for — the
harness once sent an effort level production did not, for five epochs, and
no pass rate could show it. `scripts/capture_eval_requests.py` replaces
`litellm.completion` with a recorder and runs the harness for real, so it
costs nothing and reaches no vendor; the recorded keyword arguments are the
request. Capture on `main`, capture on the branch, compare:

```bash
python scripts/capture_eval_requests.py before.json --model openrouter/deepseek/deepseek-v4-flash --suite routing --limit 2 --repeat 1
# switch to the branch
python scripts/capture_eval_requests.py after.json --model openrouter/deepseek/deepseek-v4-flash --suite routing --limit 2 --repeat 1
python scripts/capture_eval_requests.py --compare before.json after.json
```

Identical means the numbers in MEASUREMENTS.md stay comparable. A
difference is either the point of the change, in which case the pull
request says so and a new epoch follows, or a finding. Do this for any
change under `evals/`, `scripts/eval_zardoz.py`, `llm/`, or a prompt's
call site. The capture stands in for `litellm.completion` and nothing
else, so it sees only LiteLLM-backed runs — the `--model` path, which is
what MEASUREMENTS.md was measured with. A change to the Anthropic,
Vertex, Bedrock or OpenAI-compatible providers is not covered by it;
say in the pull request how that request shape was checked instead.

## Regenerating a catalog

Know the build steps before re-running an `etl-*` command. **The HIPAA
catalog is built by two commands in order** — `etl-hipaa` fetches the
regulation, then `etl-hipaa-crosswalk` attaches NIST's CPRT mappings to the
same file. Running the first alone returns a complete-looking catalog with
its mappings emptied, and HIPAA-to-NIST mapping silently stops working.
Stage to a scratch path and diff before touching the committed file.

## Changes a Windows gate cannot check

CI runs every check on Linux with Python 3.12. The workflow also runs the
test suite alone on Windows and macOS (3.12), but **only on github.com**: on
the team's Forgejo those two jobs are skipped. So nothing currently
tests macOS, and Windows is covered only by `scripts/check.py` run locally.
`scripts/check.py` on a Windows machine
still passes some things that fail on Linux. CI was red on this branch from
its first push for two such reasons: provenance stamps were hashed from
catalogs with Windows line endings, and a help-text fixture relied on the
docstring dedenting Python 3.13 does and 3.12 does not.

A third reached main with CI green: mcp 2.0 broke `policyforge mcp` on
startup, and no test started the server.

So if you **restamp a catalog, regenerate a fixture or a lock, touch a
Dockerfile, or move a dependency**, run CI's checks on Linux 3.12 before
pushing:

```bash
python scripts/ci_in_docker.py
```

It needs Docker and nothing else installed. It clones your committed HEAD
into a container, so uncommitted changes are listed and left out, and runs
CI's steps in CI's order. Then it runs what CI does not check yet: every lock
must reproduce from its header command, the runtime lock must pin what CI
tests, and `policyforge mcp` must answer a real client. Last, it builds the
runtime image from a clean export with private files planted in it (a
licensed catalog, a `.env`, an org config), checks that none reached the
build context or the image, and runs the same MCP client against the server inside it.
`--no-image` skips that last part.

### Working in the dev container

Opening the repository in VS Code with the Dev Containers extension ("Reopen
in Container") gives the environment CI runs in. Linux and Python 3.12,
dependencies from the hashed locks, semgrep in its own environment on PATH,
and `python scripts/check.py` running every check with nothing skipped but
gitleaks. The environments live in `/opt`, outside the workspace, so your
host's `.venv` is not touched. A Windows checkout works as it is: the
container has been run against one with CRLF line endings.

## Suppressing a scanner finding

Two scanners will stop you: `bandit` (`# nosec B123`) and `semgrep`
(`# nosemgrep`). Suppressing either is a claim, and the claim has a shape.

**Put the comment on the offending line, or the line directly above it.**
Semgrep honours `# nosemgrep` in those two places and nowhere else. A
suppression five lines up, above a block comment explaining it, reads as
applied and suppresses nothing — the gate catches that immediately, but only
because the gate runs semgrep. Write the reason as prose *above*, and put the
marker *on the line*:

```python
# The default is a fixed https URL to this project's own tap. The override
# is maintainer-supplied and unreachable from any user-facing command.
with urllib.request.urlopen(url, timeout=30) as response:  # nosec B310  # nosemgrep
```

**State the bound, not the verdict.** "False positive" is not a reason; it is
a conclusion with its argument missing. Say what the scanner would have to be
right about for this to be exploitable, and why it is not — which host, whose
input, what the worst outcome is. `ingest/info_blocking.py` is the long-form
example: it records that XXE and external-DTD fetches were *measured* as
refused and that entity expansion was measured as working, so the comment
argues the real residual rather than claiming there is none.

**Name the premise a future change could break.** That file also says the
bound holds *because* `ecfr._API` is a constant, and a test asserts it — so
the day someone parameterises that URL, something goes red rather than the
reasoning silently becoming false.

**When two scanners flag the same line, test before you silence the second.**
One suppression is a judgement; two independent rule authors disagreeing with
you is a reason to run the attack. Doing that on the XML parser found that the
rules' headline risk did not reach the code at all while a different one did.

### Comments wearing the costume of a control

A misplaced `nosemgrep` belongs to a family worth recognising, because every
member of it *looks* like a mechanism and is only prose:

- a suppression outside the range the tool reads
- a docstring claiming a test that does not exist
- a stated invariant with nothing asserting it

The next person copies the shape they see rather than reading the tool's
rules, so a broken one propagates. If a comment is doing the work of a check,
add the check.

## Documentation

Markdown is a real deliverable here, not a nicety. `mdformat` runs in CI over
every tracked `*.md`. If you change behaviour described in [docs/](docs/),
change the doc in the same PR — the docs claim to be checkable, and a claim
pointing at code that moved is worse than no claim.

## Adding a package or a catalog

`pyproject.toml` lists packages and bundled files by hand, because the
catalogs are mapped into the package from `data/frameworks/`, outside `src/`.
A new subpackage goes into `[tool.setuptools] packages`. A new
**public-domain** catalog goes into `policyforge.scaffold.BUNDLED_CATALOGS`
and into the package-data list file by file. Never add a glob there.
`tests/test_scaffold.py` fails until both lists agree with the repository.

The sdist is a build input, not a test distribution: `MANIFEST.in` prunes
`tests/`, because setuptools would otherwise ship the test modules without
their fixtures. Run the suite from a clone. Don't add `tests/` back to it.

## Cutting a release

**Run `python scripts/release.py X.Y.Z`** (a dry run) and then with
`--execute`. It walks the steps below in order, and each step checks that the
one before it happened, so none can be skipped. It never merges to `main`:
that is the user's approval, and the script waits for it and resumes when
re-run. The list below says what each step does and why.

**A local release** (`--publish local --forge forgejo --formula PATH --tracking-issue N`) cuts on Forgejo alone: the user merges the release PR on
Forgejo, the tag goes to Forgejo, and both installs run from Forgejo archives
that the script proves are the commit's tree before the container sees them.
Every step that reaches GitHub (the public commit, GitHub `main`, the GitHub
Release, the tap and the published-formula check) is deferred: printed, and
recorded on the tracking issue for the public release to start from. A later
public run of the same version refuses if the tag's tree has moved (#90).

**Publishing a local release** (`python scripts/publish_public.py X.Y.Z --tracking-issue N --local-issue M --identity "Name <email>" --formula PATH`)
puts it on the release-only public repo, `policyforge/policyforge-public`,
whose Forgejo push mirror carries it to GitHub (#115). It builds the public
commit P from the local tag: P's tree is the tag's tree exactly, and a
licensed path in it is a refusal. It installs P from an archive built on
this machine, then waits: nothing is pushed until the user's own account
posts `Publish-approved: <P> release=X.Y.Z` on the tracking issue. After
that it pushes P and a new tag (fast-forward only, never forced), reads
GitHub signed out until the mirror has carried both (or times out loudly),
and proves GitHub's tag archive is P's tree before recording its sha256.

1. Move the `## Unreleased` changelog entries under the new version. Bump
   `version` in `pyproject.toml` and `__version__` in
   `src/policyforge/__init__.py` together; a test compares them.
1. Merge that to `main`, then tag the merge commit `vX.Y.Z` and publish a
   GitHub Release from the tag.
1. Update `Formula/policyforge.rb` in
   [rdazzleman/homebrew-tap](https://github.com/rdazzleman/homebrew-tap):
   point `url` at
   `https://github.com/rdazzleman/policyforge/archive/refs/tags/vX.Y.Z.tar.gz`
   and set `sha256` to that tarball's hash. Compute it from the URL **the
   formula names**, not from a tarball you already have: a hash that matches
   the wrong archive fails nothing and is wrong in the direction nobody sees.
1. Regenerate the `resource` blocks with `brew update-python-resources policyforge`, not by hand — every release, not only when a dependency
   changed, since the command resolves what PyPI serves today.
1. **Reconcile every resource that differs back to `requirements/ci.txt`.**
   `update-python-resources` proposes; the lock decides. It resolves PyPI's
   current latest, so it will offer versions that are in no lock, in no CI
   run, absent from the container image and never seen by this project's
   `pip-audit` — and the divergence would be invisible, since it reaches
   Homebrew users alone while the PyPI install and the image stay on the
   locked version. A tool whose claim is that its assertions are checkable
   cannot ship dependencies it did not audit. Dependabot moves the lock, and
   the formula follows at the next release, so all three move together.
   1.3.0 met this with `idna` 3.19 against a proposed 3.20.
1. Before pushing the formula, run `brew install --build-from-source`,
   `brew test policyforge` and `brew audit --strict policyforge` on macOS or
   Linux. None of these run on Windows; the `homebrew/brew` Docker image has
   Linux Homebrew.
1. Check the built install two ways and report them apart, because they are
   different claims and only the second is what a user experiences:
   - **what the archive holds** — `policyforge/crosswalk/`,
     `_bundled/frameworks/*/controls.json`, `_bundled/config/`. The two
     `_bundled` packages are mapped from outside `src/`, so
     `tests/test_scaffold.py` cannot tell whether they shipped.
   - **what the installed tool does** — `policyforge init`, then
     `frameworks` naming the public catalogs, and `--help` on any new
     command. A package can be present and still fail to import; only
     running it sees that. Read an installed dependency's version with
     `importlib.metadata.version` rather than from the resource block: one
     says what pip was told to fetch, the other what a user runs.

## Licensed content

Never commit HITRUST CSF, GovRAMP, or any licensed export. Keep it in
`local_content/` (gitignored). `allow_licensed_in_repo` stays `false` unless
your own licence permits otherwise, and that is your decision to make in your
own fork, not one to change here.

Never commit organization-specific content: `config/config.yaml`,
`config/topics.yaml`, anything under `output/`.

## Security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).
