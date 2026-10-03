# Verifying a merge

Shell and git idioms used for verification here that **answer correctly and
are read as answering something else.** Every one below caused a real error.

**Every figure here is a fact about a moment**, measured at `origin/main` =
`599bb8b`. Some are facts about a *clone* rather than about the repository,
which is said where it applies. **Re-derive before quoting.**

## `A && B` is not a gate when A is a question

```
gh pr view 151 --json mergeStateStatus … && gh pr merge 151 --squash …
```

printed `state=UNSTABLE … 2x IN_PROGRESS` — **as output of the invocation
that had already merged.**

`&&` stops B on A's **exit code**, and a status query exits 0 whichever
answer it gives. The construct reads as sequencing and is a concatenation.

`--match-head-commit` looks like the guard and is not: it binds the SHA, so
it protects against the head moving and says nothing about whether checks
finished.

The same shape reaches beyond git. `cmd | tail` takes `$?` from `tail`.
A gate script and the push that follows it are two statements, and nothing
consumes the exit code unless you write `gate && push`.

> **Read the answer in its own call.** If a question and an action are on one
> line, the action is not gated.

### What `scripts/shell_status.py` catches, and what it cannot

It runs in the pre-push gate over committed shell (`*.sh`, workflow `run:`
steps, shell fences in Markdown). With `--command` or `--hook` it checks one
typed command. The rules:

- **swallowed-status**: `cmd | tail && next`. `next` runs on `tail`'s
  status.
- **status-after-pipe**: `cmd | tail; echo $?`, or `$?` read on the line
  after such a pipe. That `$?` is `tail`'s.
- **no-pipefail**, in scripts and workflows only: a pipe into a stream
  consumer with no `set -o pipefail` earlier in the same block.
- **empty-input-passes**: `xargs` without `-r` runs its command on an empty
  list, and many linters report no input as clean.

Pipefail, turned on **before** the pipe in the same block, clears the first
three. It can be turned on with `set -o pipefail`, `set -euo pipefail`,
`set -o errexit -o pipefail` or `shopt -so pipefail`. It does not clear them
when set after the pipe, set in another block, or turned off again by any of
`set +o pipefail`, `set +eo pipefail`, `set +o errexit +o pipefail` or
`shopt -uo pipefail`. Each spelling was checked against real bash, not taken
from documentation.

Only a `set` that runs counts, and only in the shell where it runs. A
mention does not count, as in `git commit -m "set -o pipefail in ci"` or
`echo set -o pipefail`. A `set` inside `$( )` or `( )` does not leak out of
that subshell. An outer `set` does not reach a `bash -c '…'` program, which
is a new shell. Each of these rules was checked against bash (#330).

**Measured before shipping (#213):** replaying one session's 2,742 typed
commands found 57 findings. 25 were consequential: a push gated on `tail`, a
stale container run after a failed build, and five `$?` read from a stream
consumer. 32 gated only another read. That same pass found and removed
three false alarms: heredoc bodies quoting the pattern, an `&&` in a later
statement, and an `&&` inside awk's own quoted program.

**What it cannot see, stated rather than hidden:**

- a status query before `&&`, such as `gh pr view … && gh pr merge`. The
  query exits 0 whatever it answers, and no list of such commands would be
  complete;
- a command that exits 0 having done nothing, such as a mutation that
  changed no value or a revert that matched nothing. Only checking the
  postcondition catches these;
- a multi-line quoted string, such as `python -c "…"` over several lines,
  which is read line by line as if it were shell;
- a workflow `run: *alias`, which is counted but linted as the literal
  alias;
- `cmd | tail || handle`, and a pipe used as a condition, such as
  `if n=$(cmd | tail); then` or `while cmd | grep -q x`. Each branches on the
  consumer's status too, but only `&&` is refused today;
- the pattern inside a `printf` or `echo` argument that nothing executes.
  It is refused as if it ran, because a single-line quoted string is
  scanned on purpose (`bash -c '…'` does run it).

**The typed surface is guarded only if a hook calls `--hook`.** Installing
one is a change to the user's own Claude Code settings. The repository does
not make it.

## "Merged" and "in main" are different facts

Squash merges rewrite the commit, so **a merged branch's tip is never an
ancestor of main.** Measured here:

```
origin/1d/coverage-scope            ancestor-of-main = NO   (#149, merged)
origin/1d/citable-framework-names   ancestor-of-main = NO   (#146, merged)
```

**And the flag answers a question you did not ask.** `git branch -r --merged` with no argument means *merged into whatever this clone's HEAD is
standing on* — not into main. Measured on one clone at one instant:

```
git branch -r --merged                 6
git branch -r --merged origin/main     3
git fetch --prune; both again          6 and 3, UNCHANGED
```

**Prune changes nothing.** Sessions stand on different branches, so the
same command gives each of them a different number, and every one of those
numbers is correct about a question nobody meant to ask.

**And the 3 is not three merged branches.** It is `origin/HEAD` (a symref),
`origin/main` itself, and `origin/passages-as-data`. **Not one is a merged
feature branch: the real count is zero**, which is a stronger statement
than any of the readings.

**The durable statement, which does not drift:** every squash-merged branch
reports unmerged, so **the flag's count is a lower bound and never the
answer.** Sampled across ten merged PRs, the flag names **none of them.**
Prefer that sentence to any count.

`git merge-base --is-ancestor <reviewed-sha> origin/main` agrees with the
flag and is wrong for the same reason. **Both fail toward alarm**, so a
cleanup driven by them keeps merged branches forever — the opposite of the
mistake people expect.

### The content check is not a fix either

The obvious remedy — diff the branch's own files against main and call an
empty diff *landed* — **does not survive main moving.** Measured on the two
branches above, both long since merged:

```
git diff --shortstat origin/1d/coverage-scope origin/main -- <its own files>
  -> 3 files changed, 459 insertions(+), 47 deletions(-)   reads as NOT LANDED
```

Measured at `origin/main` = `599bb8b`. **The figure moves as main moves; the
sign does not.** Re-derive rather than quoting this one.

Later work touched the same files. The check is sound only when nothing else
has, which in an active repository is rarely true and never checkable from
the diff itself.

### A branch head is not what was merged either

The next remedy after the content check is to diff the branch against its
own merge commit. **That fails too, and for a reason worth knowing.**

```
#149  merge commit  16eee00d   committed 10:58
      branch head   bc622fec   committed 11:01     three minutes LATER

commits on the branch and not in the merge:
  bc622fe  Guard the population the zero-row test loops over
  3af68b9  Cover the zero-row else-branch, and stop splitting on full stops
  b27c921  Scope /coverage to the set the registry anchors to
```

**The branch kept moving after it was merged** — `bc622fe` went on to become
#151. So a branch head is whatever was last pushed, which may be more, less
or different from what landed. **It is not a stable identifier for anything.**

Found by policyforge-80 while testing its own recommendation rather than
defending it.

### Content IS checkable — from the reviewed SHA, not the branch head

Both failures above share one cause, which this document named and did not
act on: **the branch head is not a stable identifier.** Start from one that
is — the SHA that was actually reviewed — and content-checking works.

```
git diff --stat <REVIEWED-SHA> <merge-commit> -- <the reviewed files>
```

Measured on #149, the same PR as both failures:

```
branch-head  vs main            459 insertions, 47 deletions
branch-head  vs merge-commit      3 insertions, 12 deletions
3af68b97     vs merge-commit    EMPTY
```

`3af68b97` is the commit the squash was built from. **Empty means exactly
what was reviewed is exactly what landed** — which is a different and
stronger statement than *something landed*.

**Ancestry says something landed. Content says what landed.** Use both: the
merge commit's ancestry proves it reached main, and this diff proves it is
the thing you read.

**And this is where the review convention earns its keep.** The reviewed
SHA is not recoverable from the branch — the branch has moved — nor from
the merge commit, which is a new object. It is recoverable from the
`Reviewed-SHA:` line, which exists precisely because nothing else records
it. **A review line that names a fabricated or abbreviated SHA destroys the
only proof that what was approved is what shipped.**

Found by policyforge-80, which read this document and noticed that it
proves content-checking impossible while its own two failures shared a
cause it had already identified.

### What actually works

Ask GitHub for the merge commit, then test *that* for ancestry:

```
gh pr view <N> --json mergeCommit -q .mergeCommit.oid
git merge-base --is-ancestor <that-sha> origin/main
```

```
#149   MERGED   16eee00   ancestor-of-main = YES
#146   MERGED   17fd4c6   ancestor-of-main = YES
#168   MERGED   6b7a295   ancestor-of-main = NO    <-- stranded
```

**#168 is why this matters.** It was merged into its own stacked base after
that base had landed, so `gh pr merge` reported success and the content
reached nothing. `state=MERGED` was true the whole time. It had to be
recovered by cherry-picking onto a fresh branch as #169.

> **`MERGED` is a fact about a pull request. Being an ancestor of main is a
> fact about the release.**

### "Not an ancestor of main" is two different facts

**Never answer this with a boolean.** A boolean has to pick one of the two
to be wrong about:

```
landed      the merge commit is an ancestor of main
pending     its base has not merged yet — says nothing either way
stranded    its base HAS merged, and did not carry it
```

Measured on this repository:

```
#149   base main                  ancestor of main              LANDED
#200   base release/1.6.1         base still open               PENDING
#168   base 1d/airmf-loader-wip   base merged as #167 (72bdbb6)
                                  which does not contain it     STRANDED
```

**Ask what the base did afterwards.** That is derivable from git and the
API alone — it needs no knowledge of which integration branch is open, or
whether one is.

**While this train was open, seven of the ten most recently merged PRs
here were not ancestors of main, and every one of them was `pending`,
not stranded.** During a release train the naive check fires on nearly
everything.

**That matters more than a false alarm usually does, because of the
remedy.** A stranded PR is recovered by cherry-picking it onto a fresh
branch — which is what #168 actually required. **An alarm whose remedy
duplicates work that already landed has to be right.**

### Two shortcuts that look equivalent and are not

**Comparing against the PR's own base.** `ANC-OWN-BASE` is `YES` for
#168 — that is what merging into a base *means* — so it reads identically
for the defect and the healthy case.

**Comparing against the open train.** This does separate them, but only if
you already know a train is open and what it is called, and nothing about
a merged PR tells you that.

**And #168's branch looks alive.** `origin/1d/airmf-loader-wip` exists,
and its tip **is** `6b7a295` — the merge commit that went nowhere. Nothing
about the branch is visibly wrong. You have to ask what its base did next,
which is why it survived a day.

Locally, without the API, the squash subject carries the number:

```
git log origin/main --oneline --grep="(#149)"
  16eee00 Scope /coverage to the set the registry anchors to (#149)
```

## Half of a shortstat is the opposite of a finding

`git diff <commit> <its PR's merge-commit> -- <the commit's own files>` is
the right test, and it is **directional**:

```
insertions(+)   the merge commit has MORE     -> superset, nothing was lost
deletions(-)    lines the merge did NOT carry -> WHERE TO LOOK, not the finding
```

Auditing eight orphaned commits, `2 files changed, 127 insertions(+)` was
read as evidence of lost work. It is evidence of the opposite: the merge
commit contained everything the orphan had, plus later changes to the same
files. **Filter to deletions before reporting**, or the instrument reports
"landed cleanly" as an alarm and every clean commit looks like a loss.

## A deletion has two causes, and only one of them is lost work

**"Read deletions only" was the first version of the rule above and it is
wrong**, for the same reason *not an ancestor of main* is not a boolean. A
line present in the orphan and absent from the merge is either:

- **lost work** — it never reached the merge; or
- **a deliberate removal during review** — a reviewer asked for it to go.

These are indistinguishable in a diff and opposite in consequence.

Measured on **#204, the pull request that shipped this document**:

```
git diff --shortstat 518889b 50b26a8 -- <its own files>
  2 files changed, 161 insertions(+), 17 deletions(-)
```

**Every one of those 17 was content three reviewers asked to have
removed.** The rule as first written would have reported this document's own
history as lost work.

**So deletions are where to look, not what to report.** Classify each by
asking what the review thread says about it:

```
deletion appears in a review comment as requested   -> deliberate, not a finding
deletion nobody discussed                           -> candidate lost work
```

**And note where the first version's validation went wrong**, because the
mistake is reusable: the method was checked against three commits that had
no review revisions between orphan and merge. **That is exactly the sample
that cannot expose this** — a sample chosen because the answer was easy to
check is selected for the property that makes it uninformative.

## For changelog fragments, absent-from-main is what success looks like

Fragments in `changelog.d/` are **consumed and deleted** when the changelog
assembles at release. So any sweep for unlanded content flags **every
shipped fragment** as lost work — the audit's natural reading is exactly
backwards for a whole class of file.

Three of eight orphaned commits in one audit were fragments and nothing
else. All three had shipped.

**Exclude `changelog.d/` from a content-landed sweep, or invert the reading
there.** A file whose whole lifecycle ends in deletion cannot be checked by
asking whether it still exists.

## The method that survives, stated so it can be run without thinking

For each commit you cannot account for:

1. find the PR whose head it was;
1. take that PR's `mergeCommit`;
1. `git diff <sha> <mergeCommit> -- <the commit's own files>`;
1. **read the deletions, then classify each one.**

Not by subject — squash rewrites it. Not against `origin/main` — main has
moved, and the diff then measures later work rather than missing work. Both
wrong methods were tried first on the same eight commits, and **both gave
confident false answers**: subjects reported all eight as lost, and the
content diff against main reported 165 unlanded lines in a commit that had
been reviewed and approved into a merged PR.

**A commit no PR claims is the only kind worth escalating.** Seven of the
eight were superseded; the eighth held 262 lines existing nowhere. The cheap
move there is a pushed branch — not a local tag, which is reachability on
one clone rather than preservation.

## `gh pr merge` merges into the PR's own base

A stacked PR merged after its base has landed goes somewhere irrelevant
**and reports success.** `--match-head-commit` binds the head and says
nothing about the destination.

> **Check `baseRefName` before merging**, not only the head.

## A `git fetch` into an existing ref can be a no-op

```
git fetch origin pull/N/head:refN
```

git declines the non-fast-forward, and everything downstream is then
correct about the wrong object. **A diff against a stale ref looks equally
clean.**

> Always `--force`, and verify against `gh pr view --json headRefOid` rather
> than against the ref you just asked for.

## A staleness check that reads a cache

`release_check.py` fetches the tap formula from `raw.githubusercontent.com`.
During the v1.6.0 cut that served `v1.5.0` twelve polls after `v1.6.0` was
pushed, while a `git clone` said `v1.6.0`:

```
git ls-remote tap refs/heads/main          605e00a
git clone --depth 1  -> Formula/*.rb       v1.6.0
raw.githubusercontent.com/.../*.rb         v1.5.0   <- what the check read
```

**`brew tap` clones the repository; it does not read the CDN.** So the check
read something no user reads, through a cache with its own propagation
delay. It reported a correct release broken — and with different timing it
would report a broken one fine.

> **Read the artefact the user receives, by the route they receive it.**

## The shape they share

Each is an instrument giving a correct answer to a question nobody asked,
and each is read as having answered the question that mattered. That is the
same substitution as a cheap check standing in for an expensive one: **a
skipped check leaves a gap, a substituted one leaves a false assurance.**

When an instrument disagrees with your expectation, the first question is
not *which is wrong* but **what exactly did this measure.**
