# Changelog fragments

One file per branch. At release, `scripts/changelog_fragments.py --version X.Y.Z` assembles every file here into a new section of `CHANGELOG.md` and
deletes them.

**Why not edit `CHANGELOG.md` directly.** One `## Unreleased` section means
every pair of branches shipping anything user-visible edits the same place in
the same file, so every pair conflicts. In one sitting that produced four
hand-resolved conflicts, a resolution that silently converted 1,768 lines to
CRLF, and a resolution that left one entry as a heading so five unrelated
entries nested underneath it — no conflict markers, no failing check, every
merge individually correct. **A new file cannot conflict with another
branch's new file.**

## Writing one

Name it after your branch — `1d-changelog-fragments.md` for
`1d/changelog-fragments` — so two branches cannot pick the same name.

Write the prose you would have put under `## Unreleased`: a bold lead saying
what a user does differently, then the reason. No `## ` heading; the release
heading is added at assembly. LF endings.

```markdown
**`etl-hipaa` now refuses to overwrite a catalog whose crosswalk it would
drop.** Running it alone used to rewrite the bundled catalog from 65 mappings
to 0, leaving a file that still loaded and reported nothing wrong. It now
stops and names both commands in order. **If you script `etl-hipaa` on its
own it will exit non-zero** where it previously succeeded.
```

Say what someone does differently, not what the code now does. If the change
is genuinely invisible to a user, write no fragment and put
`No changelog entry: <reason>` in the pull request body instead —
`scripts/changelog_guard.py` accepts that and records the reason.

## Ordering

Assembly emits fragments in filename order and has no opinion about what
belongs first. **Arrange the assembled section by hand at release** — once,
in one file, with no conflict to resolve. Entries that change someone's exit
codes belong at the top. That judgement was never the expensive part; the
conflict was.
