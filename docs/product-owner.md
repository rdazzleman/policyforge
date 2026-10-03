# The product owner role on PolicyForge

Written for whoever takes this role next. It covers what the job turned out
to be in practice, the decisions that live nowhere else, what is in flight,
and the failure modes this project keeps producing — because they recur, and
because most of them were found the same way.

## What the job actually is

The title says product owner. What the role does, most of the time, is
**answer what a thing is for and who it is aimed at**, and then get out of the
way of people who are better at building it than you are.

The clearest example is 45 CFR Part 171. The release manager wrote the
catalog and its README. The judgement that the README had to open by saying
*this is not a control catalog — an entry is a condition of an exception,
cite it to show a practice qualifies rather than to claim a control is
implemented* was the product call, and without it the tool would have
happily produced a confidently wrong Standard in the single area a health IT
buyer is most likely to arrive asking about.

Three habits that made the role worth having:

**Rule on things that cross two people.** An engineer and the release manager
independently reached opposite answers on whether a reserved regulatory
section should be emitted as an empty control. Neither could see the other's
answer. That class of question is the role's, and the quality owner is
usually better placed to spot the second one forming — delegate it.

**Do not reorder the user's priorities to unblock your own conveniences.** A
measurement needed a catalog scheduled for a later wave. The right answer was
that the measurement waits, not that the catalog jumps. Tail, dog.

**Check the numbers you are given before you repeat them.** More below; it is
the single highest-yield habit on this project and it caught something real
roughly once an hour.

## Standing decisions that are in no other file

- **Branch names carry the owning session as a prefix** — `80/…`, `1d/…`,
  `e1/…`, `e2/…`, `9b/…`. Permission here is scoped by who owns a branch, and
  every commit is authored identically, so `git log` cannot answer it. An
  **unprefixed branch is outside every grant** until its owner says otherwise;
  the safe reading has to be automatic rather than a judgement made when
  someone is keen to merge.
- **Work handed between sessions goes as a pushed branch, never a file on
  disk.** A patch written to a path was silently replaced and the stale
  version got applied. A branch is content-addressed and `git ls-remote` will
  say what it actually is.
- **Name the tip in every message, and verify it before acting.** A branch
  moved three times after its identifier was quoted; a merge was nearly
  matched against a commit three revisions old. `--match-head-commit` on every
  merge.
- **Send work for review once.** The largest single waste was sending a branch
  for approval while knowing more was coming, then rebuilding it three times
  and spending three re-approvals from the one person who approves everything.
- **Changelog fragments per branch, assembled at release — UNBUILT, and it
  will bite you.** One `## Unreleased` section means every pair of branches
  shipping anything user-visible conflicts there. It was hand-resolved four
  times in one evening; one of those resolutions silently converted 1,768
  lines to CRLF, and another left one entry as a heading so that five
  unrelated entries nested underneath it. Build the fragments.

## In flight, and what nobody is holding

- **Local provider (Ollama/Qwen).** Works: `provider: local`,
  `base_url: http://localhost:11434/v1`, `qwen3:14b` pulled. Two defects
  queued. Ollama returns reasoning in a separate `message.reasoning` field
  that `openai_compat_provider` never reads, so `hidden_output_tokens` is
  `None` and `stripped_reasoning_chars` is `0` — a *measured zero for
  something never looked at* — while ~90% of output tokens are reasoning.
  `gemini_provider` records this correctly; two providers, same class of
  model, two accountings. Also queued: a test pinning that a down server
  raises rather than returning an empty completion (verified by hand, not yet
  asserted), and `supports_schema` for the local provider, which is honestly
  `False` because `generate_json` is unimplemented. That last one matters:
  a local model doing crosswalk proposals is the only path where licensed
  content never leaves the machine, which is the entire argument for local.
- **The prompt comparison is blocked on an unallocated catalog.** It needs
  **two** NIST-family catalogs loaded; main ships one. With one, prefix
  uniqueness still resolves a bare `NIST`, both arms produce identical
  numbers, and the measurement measures nothing. 800-171 sits in wave 2 by
  the user's own priority order and the measurement waits for it. Do not let
  anyone quietly promote it.
- **45 CFR 170.315** is roughly two hours into an estimated day, uncommitted.
  The hard part is solved: criterion letters are disambiguated by sequence,
  not by regex, because 47 paragraphs in that section open with `(i)` and
  exactly one is a criterion. Ruling on reserved entries: **omit, assert the
  absence, and allow no exception for empty statements** — an exception is a
  rule someone must keep in step with the source.
- **An efficiency item**, queued behind the above: `entail/base.py:218`
  evaluates every verdict before `any()` sees the first, so a *supported*
  sentence citing N passages costs N judge calls where one would do. The full
  list is only needed when nothing supports the sentence. Lazy evaluation with
  a fallback would cut the bill and change no reported finding.

## The failure modes, and why they matter more than the fixes

Nearly every defect found in a long evening was caught by **someone other than
its author, or by a check that required nobody to notice anything**. Almost
none were caught by an author thinking harder. That is the argument for the
two-reader arrangement, and it is an argument about structure rather than
anyone's diligence — the two people who caught each other most made the same
class of error within hours, in the same direction.

**The tell that generalises, and where it stops working.** A rebase artefact
reads as a real change, and the sign is **a number far larger than the change
could possibly be**. Five instances across three people in one day: a keying
fix that appeared to grow a whole catalog, a phantom 2,134-line diff, a
baseline taken from the wrong parent.

**Its floor is worth more than the heuristic.** It fires only when the branch
is far enough behind that the artefact is conspicuous. A branch one or two
commits behind produces a wrong-base diff that is small, plausible, and
indistinguishable from the real change — nothing looks odd, so nothing
prompts the check. The heuristic is a smoke alarm, not a proof, and treating
it as a proof means trusting a signal precisely where it is quietest.

**The file list reaches where the count cannot.** A documentation branch whose
diff names `pyproject.toml` and `__init__.py` is impossible on its face,
whatever the line count says — and the count in that case was unremarkable.
So read *which* files a diff claims changed, not only how many lines: a wrong
base shows up as files the branch has no business touching long before it
shows up as a suspicious total. This one surfaced during the review of this
very section, on a branch based before the release that followed it.

So the tell is what makes you look, and this is what answers it: **compare
each commit's own patch against its own base, never a tip against a tip.** Do
that when nothing looks wrong, because that is the case the alarm cannot
reach.

**A check that reassures needs the adversarial test; a check that accuses
does not.** A false alarm gets investigated, so it costs time and not
correctness. A false reassurance ends the looking. Before trusting a check,
make it fail on purpose.

**Count a function's callers before reporting its behaviour as the system's.**
`git grep -c` is the whole check. Two separate people concluded things about
this codebase from functions with zero and one callers respectively, and one
of those nearly caused a change to the most-shared pattern in the repository
to fix something that was not broken.

**Prefer the mechanical half of any rule.** Three people in one evening failed
to apply a rule they had personally written, hours earlier, about the exact
situation they were in. The common factor is not carelessness: all three were
rules you must remember at the moment you are finishing something, which is
when attention is lowest and the rule is due. A written rule fires only if
recalled. `scripts/changelog_guard.py` is what the mechanical form looks like,
and its opt-out demands a *reason* rather than a marker, because a bare marker
is a silent exemption wearing a word.

**An accurate record consulted for the wrong question still gives the wrong
answer.** For most of a day, four sessions believed seven branches were
blocked on permission. The permission document was accurate, corrected three
times, with relayed parts marked unconfirmed. It simply never applied to their
case — it governed one session acting on another's behalf — and meanwhile all
four were opening pull requests for their own work without treating it as a
permission question. Nobody checked the rule's scope against the case.

**A relayed decision is not authorisation**, and the reason is not that
relays are usually wrong. It is that the receiver cannot tell a good relay
from a bad one, and neither can the relayer. This held four times in a day,
including once where the relay was accurate and the delay cost an hour.
The corollary keeps it from becoming absurd: separate what depends on the
decision from what does not. Coordination, rebasing and review need no
authorisation. Merging documentation that *describes* a scheduling decision
needs none either — if the decision turns out different, one sentence changes.
Writing code that *depends* on it does.

## Two errors of mine worth inheriting

**A decision with no check attached.** The user approved a prompt change; it
was sequenced behind other work and never allocated, and then reported twice
as unblocked. Nothing could have caught it — code has gates and claims have
commands, but an unallocated decision produces no artefact, so there is
nothing to be red. **A decision is not recorded until it has a named owner or
sits on a list someone reads back.**

**Naming one prerequisite as though it were the only one.** The same
measurement was declared unblocked twice, on two different prerequisites,
when there were three. That is a claim about *completeness*, which nothing
checks. Write prerequisites down as a list before anyone asks whether you are
ready.
