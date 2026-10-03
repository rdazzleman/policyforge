#!/usr/bin/env python3
"""Refuse committed shell that reports success for a command that failed.

WHY THIS EXISTS. `cmd | tail` takes its exit status from `tail`, so the
status of `cmd` is discarded. Written as `cmd | tail && action`, it reads as
sequencing and is a concatenation: the action runs whatever `cmd` returned.
#213 catalogued **seven instances in one day**, every one caught downstream
and none prevented at the moment of typing, and three of those were in
sessions that had the rule in front of them.

**The rule was already written down** — in the team charge, in
`docs/verifying-a-merge.md`, in four sessions' memory files. Restating it
produced no measured reduction, because a rule that must be recalled fires
when you are already being careful and `| tail` is what you type when you
are not.

WHAT IT FOUND ON THE DAY IT WAS WRITTEN. One line, and it was ours:

    .github/workflows/ci.yml:91
    run: git ls-files -z '*.md' | xargs -0 mdformat --check

Measured, in both directions, rather than argued:

    bash -e             'false | xargs -0 mdformat --check'  -> exit 0
    bash -e -o pipefail 'false | xargs -0 mdformat --check'  -> exit 1

So if `git ls-files` failed, **CI's markdown check passed having examined
nothing** — a green light the whole team had been reading as "markdown is
fine". GitHub's default shell is `bash -e` with no pipefail, and the only
`shell: bash` default in that file is on a different job.

THE SECOND PATH, WHICH PIPEFAIL DOES NOT CLOSE. Found by policyforge-9b
against the fix above, which is why it is here:

    mdformat --check      (no paths at all)  -> exit 0
      "No files have been passed in. Doing nothing."

`xargs` without `-r` runs the command once on empty input. If `git ls-files`
*succeeds* and matches nothing — a renamed directory, a pattern that stops
matching — the tool runs, checks zero files and exits 0. Nothing failed, so
pipefail never fires. **A check whose population can silently become empty
reports the absence of input as the absence of problems**, which is the same
defect `test_the_population_is_not_empty` guards in #214, one layer out.

Hence two rules, not one, and `EMPTY_INPUT_TOOLS` below is the second.

WHAT THIS DOES NOT DO, deliberately.

It does not lint the interactive shell BY ITSELF. Three of #213's first seven
instances, and the eighth, were typed at a prompt. Since #213, `--command`
checks one typed command and `--hook` does the same for a Claude Code
PreToolUse payload, so a hook can refuse a command before it runs.
**Whether that hook is installed is the user's choice, in their own
settings. This file does not install it,** so until someone does, the
typed surface is exactly as unguarded as before.

It does not see a status query before `&&` (`gh pr view ... && gh pr merge`:
a question that exits 0 whatever the answer). A list of such commands would
be the class-versus-instance trap. Nor does it see a command that exits 0
having done nothing (an inert mutation, a no-op revert). No shell rule can,
and checking the postcondition is the only defence. Both are stated limits
(#213), pinned where a test can pin them.

It does not require `set -o pipefail` in documentation snippets. A snippet is
something a reader runs by hand and watches; a script is something that runs
unattended and is believed. Only the second kind gets the requirement.

It does not forbid pipes. `PIPEFAIL_EXEMPT` takes a site and a reason for a
pipeline whose status genuinely does not matter, **pinned by the text of the
line rather than by a line number**, so an exemption cannot silently widen to
cover a line that moved underneath it.

Usage:

    python scripts/shell_status.py            # check; non-zero on a finding
    python scripts/shell_status.py --list     # print the population it derived
    python scripts/shell_status.py --command 'CMD'   # one command; 1 on a finding
    python scripts/shell_status.py --hook     # PreToolUse JSON on stdin; 2 blocks
"""

from __future__ import annotations

import argparse
import functools
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Commands that consume a stream and whose own exit status is almost never
#: the question being asked. Piping INTO one of these discards the status of
#: whatever produced the stream.
#:
#: Deliberately a list of stream consumers rather than "any pipe": `a | b`
#: where `b` is the real work is a normal pipeline, and a rule that refused
#: every pipe would be turned off within a day.
STREAM_CONSUMERS = (
    "head",
    "tail",
    "grep",
    "sed",
    "awk",
    "jq",
    "cut",
    "sort",
    "uniq",
    "wc",
    "tee",
    "cat",
    "xargs",
)

#: Tools KNOWN to treat "no input" as success, used only to sharpen the
#: failure message. **Not the rule.**
#:
#: The first version of this file made the list the rule, and
#: policyforge-ba asked the right question: the property belongs to tools,
#: the set of tools with it is larger than any list, and it grows whenever
#: someone adds a linter to CI. Measured in this venv rather than argued:
#:
#:     mdformat   exit 0      ruff       exit 0
#:     semgrep    exit 0      pip-audit  exit 0
#:     pytest     exit 0      bandit     exit 2
#:
#: **Three of those were missing from the list**, so the enumeration was
#: already incomplete on the day it was written -- the class-versus-instance
#: failure, in the file whose job is to catch that shape.
#:
#: So the rule below is keyed on the CONSTRUCT instead, which is derivable:
#: `xargs` without `-r` runs its command once on empty input. That is a
#: property of `xargs`, true for every tool it will ever be handed, and it
#: is the actual mechanism. This tuple only lets the message say which tool
#: is known to exit 0, and being absent from it changes nothing.
EMPTY_INPUT_TOOLS = ("mdformat", "ruff", "black", "shellcheck", "semgrep", "pip-audit", "pytest")

#: `xargs` with no `-r`/`--no-run-if-empty`: runs the command even when the
#: incoming list is empty. This is the derivable half of the empty-input
#: rule and does not depend on knowing the tool.
_XARGS_RUNS_ON_EMPTY = re.compile(r"\|\s*xargs\b(?![^|]*(?:-r\b|--no-run-if-empty))")

_CONSUMERS = "|".join(STREAM_CONSUMERS)

#: A pipe, including `|&`, which pipes stderr too and discards the left
#: side's status in the same way (ba on #328).
_PIPE = r"\|&?\s*"

#: A stream consumer whose status is then used to gate something else.
#: `cmd | tail -4 && push` is the shape; the `&&` is what turns a discarded
#: status into a wrong decision.
#: The candidate shape only. Whether the `&&` really gates THIS pipe is
#: decided by `_swallowed_at`, which reads quoting and `$( )` nesting.
_SWALLOWED = re.compile(rf"{_PIPE}(?:{_CONSUMERS})\b[^|]*&&")
_CONSUMER_PIPE = re.compile(rf"(?<!\|){_PIPE}(?:{_CONSUMERS})\b")


def _nesting(line: str) -> list[tuple[str, int]]:
    """Per character: the quote it sits in ('' / "'" / '"') and its `$( )`
    depth. **A small scanner, not a shell parser, and its limits are
    stated:** each `$( )` opens a fresh quoting context, as the shell does,
    so an awk `'... && ...'` inside `"$( ... )"` is still awk's. Plain
    parentheses are counted per context, so a subshell's `)` does not close
    the `$(` around it (policyforge-ba on #328: `$( (x | tail) && y )`).
    Backslash escapes are honoured outside single quotes. Backticks and
    `${ }` are not tracked."""
    states: list[tuple[str, int]] = []
    contexts = [["", 0]]  # per open `$(`: [quote, open plain parens]
    i = 0
    while i < len(line):
        ch, context, depth = line[i], contexts[-1], len(contexts) - 1
        quote = context[0]
        states.append((quote, depth))
        if ch == "\\" and quote != "'" and i + 1 < len(line):
            states.append((quote, depth))
            i += 2
            continue
        if quote == "'":
            if ch == "'":
                context[0] = ""
        elif line.startswith("$(", i):
            contexts.append(["", 0])
            states.append((quote, depth))
            i += 2
            continue
        elif ch == "(" and not quote:
            context[1] += 1
        elif ch == ")" and not quote and context[1]:
            context[1] -= 1
        elif ch == ")" and not quote and depth:
            contexts.pop()
        elif ch in "'\"" and not quote:
            context[0] = ch
        elif ch == quote:
            context[0] = ""
        i += 1
    return states


#: A statement that is only assignments up to a `$(`: `n=$(`, `out="$(`,
#: `a=1 b=$(`. Its exit status IS the substitution's, so an `&&` after it
#: gates the pipe inside (ba on #328: `n=$(pytest | tail -1) && git push`).
#: `local`/`export` are NOT this: their own status masks the substitution's.
_ASSIGNMENT_ONLY = re.compile(r"\s*(?:[A-Za-z_]\w*=[^\s;&|]*\s+)*[A-Za-z_]\w*=[\"']?")


def _opener_of(line: str, states: list[tuple[str, int]], start: int, depth: int) -> int:
    """Where the `$(` enclosing `start` at `depth` begins."""
    k = start
    while k >= 0 and states[k][1] >= depth:
        k -= 1
    return k - 1 if line[max(k - 1, 0) : k + 1] == "$(" else k


def _swallowed_at(line: str) -> int | None:
    """The first pipe whose status an `&&` swallows, else None. See
    `_swallowed_positions`, which the rule uses: a line can hold more than one."""
    positions = _swallowed_positions(line)
    return positions[0] if positions else None


def _swallowed_positions(line: str) -> list[int]:
    """EVERY pipe into a consumer that is followed by an `&&` gating it.

    **Every one, not the first** (policyforge-9b on #338, over 4,820 bash-judged
    cases): returning only the first let a guarded first swallow hide an
    unguarded second one, as in `set -o pipefail; a | tail && x;
    set +o pipefail; b | tail && push`.

    **The `&&` must sit at the same quoting and `$( )` depth as the pipe,
    with no `|` or `;` between them at that level.** Each clause is
    measured, not supposed. Replaying one session's 2,736 typed commands
    (#213) refused `x | head -2; y && z` (the `&&` gates `y`),
    `| awk '... n==1 && ...'` (awk's `&&`) and `"$(x | wc -l)" && y` (the
    `&&` gates `echo`). Still refused, as it must be:
    `cat log | awk '{print $2}' && rm log`, where the quote closes before the
    `&&`, and `bash -c 'x | tail && y'`, where both sit inside one quote,
    which `bash -c` runs.
    """
    if not _SWALLOWED.search(line):
        return []
    states = _nesting(line)
    found: list[int] = []
    for match in _CONSUMER_PIPE.finditer(line):
        quote, depth = states[match.start()]
        #: After leaving a substitution whose statement is assignment-only,
        #: the level is its depth with any quoting, and only a closing quote
        #: or space may stand between it and the `&&`.
        after_assignment = False
        for i in range(match.end(), len(line)):
            here = states[i]
            if here[1] < depth:
                opener = _opener_of(line, states, match.start(), depth)
                statement = re.split(r";|&&|\|\||\||\(", line[:opener])[-1]
                if not _ASSIGNMENT_ONLY.fullmatch(statement):
                    break
                quote, depth, after_assignment = None, here[1], True
            if here[1] != depth or (quote is not None and here[0] != quote):
                continue
            ch = line[i]
            if line.startswith("&&", i) and (quote is not None or here[0] == ""):
                found.append(match.start())
                break
            if ch in "|;" and (quote is not None or here[0] == ""):
                break
            if after_assignment and not (ch.isspace() or ch in "\"')&"):
                break
    return found


#: Any pipe into a stream consumer, used for the pipefail requirement.
_PIPES_TO_CONSUMER = re.compile(rf"{_PIPE}(?:{_CONSUMERS})\b")

#: A `set` or `shopt` statement, up to the end of its statement.
_SET_OR_SHOPT = re.compile(r"(?<![\w-])(set|shopt)\b([^;&|]*)")

#: What may stand before a command: an operator, or a keyword that starts
#: a command list.
_COMMAND_STARTS = re.compile(r"(?:^|[;&|({!]|\b(?:then|do|else))$")


#: Where a command runs UNCONDITIONALLY, in this shell: the start of a line
#: or quoted program, or after `;`, `(`, `{` or `!`. NOT after `&&`/`||`
#: (it runs only if the left side went one way), `|` or a lone `&` (a
#: subshell or background job), or `then`/`else`/`do` (a branch or a loop
#: body that may run zero times). policyforge-9b on #331: `false && set -o
#: pipefail`, `true || set -o pipefail` and `[ -n "$CI" ] && set -o pipefail`
#: were credited, and bash leaves pipefail OFF after each of the first two.
_UNCONDITIONAL_STARTS = re.compile(r"(?:^|[;({!])$")

#: Block openers and closers, read only at a command position in the top-level
#: unquoted shell, so a block's depth carries across lines. A `set` inside an
#: `if`/`for`/`while`/`until`/`case` block, or inside a function body (which
#: runs only when called), is not credited. A plain `{ ...; }` group runs, so
#: it is tracked only so that its `}` is not mistaken for a function's.
_BLOCK_WORD = re.compile(
    r"\b(?P<open>if|for|while|until|case|function)\b|\b(?P<close>fi|done|esac)\b"
    r"|(?P<fn>\(\)\s*\{)|(?P<brace>\{)(?=\s)|(?P<end>\})"
)
_CONDITIONAL_BLOCKS = frozenset({"if", "for", "while", "until", "case", "function", "fn"})


def _open_quote_after(line: str) -> str:
    """The quote still open at the end of `line` in the top-level shell, or "".

    A quote left open inside `$( )` is not carried, a stated limit: the next
    line is then read as commands, as it was before #331.
    """
    quote, depth = _nesting(line + " ")[-1]
    return quote if depth == 0 else ""


def _statement_prefix(line: str, states: list[tuple[str, int]], start: int) -> str:
    """The text before `start` in its own quoting context, right-stripped."""
    k = start
    while k > 0 and states[k - 1] == states[start]:
        k -= 1
    return line[k:start].rstrip()


def _at_command_position(line: str, states: list[tuple[str, int]], start: int) -> bool:
    """Whether the word at `start` is where a command begins, reading only
    the text in its own quoting context (so a quoted program's first word
    counts, and an `echo` argument does not)."""
    return bool(_COMMAND_STARTS.search(_statement_prefix(line, states, start)))


def _blocks_at(
    line: str, states: list[tuple[str, int]], blocks: tuple[str, ...]
) -> tuple[dict[int, tuple[str, ...]], tuple[str, ...]]:
    """The open-block stack at each block word in `line`, and after the line.

    Carried across lines by `findings`, so a multi-line
    `if ...; then` / `set -o pipefail` / `fi` is seen as conditional.
    **A small tracker, not a parser**, and its limits are stated: keywords
    are read only in the top-level unquoted shell (inside a quoted `bash -c`
    program only the operator rule applies), and a CALLED function's `set`
    is not credited either (safe direction: a false alarm, never a false
    clear).
    """
    stack = list(blocks)
    after: dict[int, tuple[str, ...]] = {}
    for match in _BLOCK_WORD.finditer(line):
        at = match.start()
        if states[at] != ("", 0):
            continue
        if not match.group("fn") and not _at_command_position(line, states, at):
            continue
        if match.group("open"):
            stack.append(match.group("open"))
        elif match.group("close"):
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] in ("if", "for", "while", "until", "case"):
                    del stack[i]
                    break
        elif match.group("fn"):
            stack.append("fn")
        elif match.group("brace"):
            if stack and stack[-1] == "function":
                stack[-1] = "fn"
            else:
                stack.append("group")
        elif match.group("end") and stack and stack[-1] in ("fn", "group"):
            stack.pop()
        after[at] = tuple(stack)
    return after, tuple(stack)


def _in_conditional_block(after: dict[int, tuple[str, ...]], blocks, start: int) -> bool:
    """Whether `start` sits inside an if/loop/case block or a function body,
    given the stack recorded after each block word (`_blocks_at`)."""
    earlier = [p for p in after if p < start]
    stack = after[max(earlier)] if earlier else tuple(blocks)
    return any(kind in _CONDITIONAL_BLOCKS for kind in stack)


def _reaches(line: str, states: list[tuple[str, int]], change: int, pipe: int) -> bool:
    """Whether a pipefail change at `change` still governs the shell at `pipe`.

    Its context must stay open in between. An unquoted change lasts until its
    `$( )` or `( )` subshell closes, and a quoted one (a `bash -c` program)
    until its quote closes. A pipe inside a quoted program is reached only
    from inside that same program, because `bash -c` is a new shell that does
    not inherit the outer one's pipefail. Each rule was checked against real
    bash on #330: `x=$(set -o pipefail)`, `( set -o pipefail )` and an outer
    `set` before `bash -c '...'` all leave the pipe without pipefail.
    """
    quote, depth = states[change]
    parens = 0
    for i in range(change, min(pipe, len(states))):
        here = states[i]
        if here[1] < depth or (quote and here[1] == depth and here[0] != quote):
            return False
        if here == (quote, depth):
            if line[i] == "(" and not (i > 0 and line[i - 1] == "$"):
                parens += 1
            elif line[i] == ")":
                parens -= 1
                if parens < 0:
                    return False
    # A pipe inside a quoted program is reached only from that same program.
    return not (pipe < len(states) and states[pipe][0] and states[pipe] != (quote, depth))


def _pipefail_changes(line: str, blocks: tuple[str, ...] = ()) -> list[tuple[int, bool]]:
    """Every place in `line` that turns pipefail on (True) or off (False).

    **On and off are read by ONE interpreter of the options, not two
    regexes**, because two regexes drifted. #328 taught the ON regex
    `set -o errexit -o pipefail` and left OFF knowing only
    `set +o pipefail`, so `set +eo pipefail`, `set +o errexit +o pipefail`
    and `shopt -uo pipefail` (bash-verified to turn it OFF) left the lint
    believing it was still on, and it cleared `x | tail && y`. That was
    found by policyforge-9b, in the unsafe direction.

    `set`: a flag word containing `o` (`-o`, `+o`, `-euo`, `+eo`) takes the
    next word as the option name. The word's sign decides on or off.
    `shopt`: the flags must include `o` (set-style options); `s` turns the
    option on and `u` turns it off.

    **Only a `set`/`shopt` that RUNS counts** (#330, policyforge-ba): one at
    a command position (the start of its line or quoted program, or after
    `;`, `&&`, `||`, `|`, `(`, `{`, `then`, `do`, `else`, `!`). A mention
    does not count: `git commit -m "set -o pipefail in ci"` or
    `echo set -o pipefail` left the lint believing pipefail was on, and it
    cleared a real swallow. Which pipes a change reaches is `_pipefail_at`'s
    question.
    """
    states = _nesting(line)
    after, _ = _blocks_at(line, states, blocks)
    changes: list[tuple[int, bool]] = []
    for match in _SET_OR_SHOPT.finditer(line):
        at = match.start()
        # Unconditional, and not inside a block that may not run (#331, 9b).
        if not _UNCONDITIONAL_STARTS.search(_statement_prefix(line, states, at)):
            continue
        if _in_conditional_block(after, blocks, at):
            continue
        # Nor a `set` that is the left side of a pipe or backgrounded: each
        # runs in a subshell. Bash-verified on #331 (ba): `set -o pipefail | cat`,
        # `|& cat` and `& wait` leave it OFF; `&&`/`||` after it do not.
        after_set = line[match.end() :].lstrip()
        if after_set.startswith(("|", "&")) and not after_set.startswith(("||", "&&")):
            continue
        command, words = match.group(1), match.group(2).split()
        if command == "set":
            j = 0
            while j < len(words):
                word = words[j]
                if word[:1] in "-+" and len(word) > 1 and "o" in word[1:]:
                    if j + 1 < len(words) and words[j + 1] == "pipefail":
                        changes.append((match.start(), word[0] == "-"))
                    j += 2
                    continue
                j += 1
        else:
            flags = "".join(w[1:] for w in words if w.startswith("-"))
            if "o" in flags and "pipefail" in words and ("s" in flags) != ("u" in flags):
                changes.append((match.start(), "s" in flags))
    return changes


#: `$?` read anywhere on a line.
_READS_STATUS = re.compile(r"\$\?")

#: A pipe into a stream consumer, then `;`, then a statement reading `$?`:
#: `x | tail ; echo "exit: $?"`. The `$?` is the consumer's.
_STATUS_AFTER_PIPE = re.compile(rf"{_PIPE}(?:{_CONSUMERS})\b[^;&|]*;[^;&|]*\$\?")

#: A heredoc opener, `<<EOF`, `<<-'EOF'`, `<< "EOF"`, but not a here-string
#: `<<<`. Its body is data, unless the command it feeds is a shell, and then
#: the body is shell and is scanned (`bash <<'EOF'`).
#:
#: **Measured, not supposed (#213):** replaying one session's 2,736 typed
#: commands, review bodies and commit messages QUOTING the pattern were
#: refused as if they ran it, which would make a hook block every PR comment
#: that explains the rule. **Not handled, a stated limit:** a multi-line
#: quoted string (`python -c "..."` over several lines) is still read line
#: by line as shell. A single-line quoted string is scanned on purpose, as
#: `bash -c 'x | tail && y'` runs it.
_HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\1(?!<)")
_SHELL_READS_HEREDOC = re.compile(r"(?:^|[\s;&|(])(?:bash|sh|zsh|ksh|dash)\b[^;&|]*$")

#: A line whose LAST statement is a pipe into a stream consumer, so a `$?`
#: at the start of the next line reads the consumer's status.
_ENDS_IN_CONSUMER_PIPE = re.compile(rf"{_PIPE}(?:{_CONSUMERS})\b[^;&|]*$")

#: Exemptions, pinned by the line's own text. A reason is required: an
#: exemption with no reason is indistinguishable from an oversight, and the
#: next reader cannot tell whether removing it is safe.
#:
#: Empty today, and that is the honest state — the one real finding was
#: fixed rather than exempted. Kept because a guard with no way to say
#: "this one is fine" gets deleted the first time it is wrong.
PIPEFAIL_EXEMPT: dict[str, str] = {}


#: How a block says "I checked that the list was not empty". Kept as a set of
#: shapes rather than one, because the natural spelling differs between a
#: bash array and a `find -print -quit`, and a rule that accepts only the
#: spelling its author used is the class-versus-instance failure again.
_NON_EMPTY_ASSERTIONS = (
    re.compile(r"\$\{#\w+\[@\]\}"),  # bash array length
    re.compile(r"\bwc\s+-l\b[^\n]*\b(?:-eq|-gt|-ne|\[)"),
    re.compile(r"\bxargs\b[^\n]*(?:-r\b|--no-run-if-empty)"),
    re.compile(r"\bgrep\s+-q\b"),
)


def _asserts_non_empty(block: str) -> bool:
    """Does this block prove it had input before believing a clean result?

    `xargs -r` counts: it declines to run the tool at all on empty input, so
    the tool cannot report "nothing to do" as success.
    """
    return any(pattern.search(block) for pattern in _NON_EMPTY_ASSERTIONS)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    text: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  [{self.rule}]\n    {self.text.strip()}\n    {self.detail}"


@dataclass(frozen=True)
class Source:
    """A committed thing that a shell will execute."""

    path: str
    kind: str  # "script" | "workflow" | "doc"
    lines: list[tuple[int, str]]

    @property
    def requires_pipefail(self) -> bool:
        """A doc snippet is watched by a person; a script is believed."""
        return self.kind in ("script", "workflow")


def tracked(pattern: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", pattern],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def _workflow_run_blocks(path: str, text: str) -> list[Source]:
    """Every `run:` block in a workflow, with its real file line numbers.

    Parsed by indentation rather than with a YAML library on purpose: the
    line numbers have to survive into the failure message, and a YAML load
    discards them. The cost is that this sees a `run:` inside a string; the
    benefit is that a finding can be pasted into an editor and found.
    """
    sources: list[Source] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)-?\s*run:\s*(\S.*)?$", lines[i])
        if not match:
            i += 1
            continue
        indent, inline = match.group(1), match.group(2)
        body: list[tuple[int, str]] = []
        if inline and inline not in ("|", ">", "|-", ">-"):
            body.append((i + 1, inline))
            i += 1
        else:
            i += 1
            while i < len(lines):
                line = lines[i]
                if line.strip() and not line.startswith(indent + " "):
                    break
                body.append((i + 1, line))
                i += 1
        if body:
            sources.append(Source(path=path, kind="workflow", lines=body))
    return sources


_FENCE = re.compile(r"^```\s*(bash|sh|shell|console)\s*$")


def _doc_shell_blocks(path: str, text: str) -> list[Source]:
    sources: list[Source] = []
    body: list[tuple[int, str]] = []
    inside = False
    for number, line in enumerate(text.splitlines(), 1):
        if not inside and _FENCE.match(line.strip()):
            inside, body = True, []
            continue
        if inside and line.strip().startswith("```"):
            if body:
                sources.append(Source(path=path, kind="doc", lines=body))
            inside = False
            continue
        if inside:
            body.append((number, line))
    return sources


_SCRIPT_HAS_CONTENT = re.compile(r"\A\s*\S", re.S)


def _count_script(text: str) -> int:
    """One block per non-empty script: a script is one source.

    Matches at most ONCE per file, because the census counts BLOCKS. `\\S`
    alone would have counted every non-space character as a block the
    moment a *.sh was committed.
    """
    return len(_SCRIPT_HAS_CONTENT.findall(text))


def _count_workflow_steps(text: str) -> int:
    """`run:` steps as GitHub reads them: a real YAML load, not a line regex (#256).

    **Why a different MECHANISM, not a better regex.** The census used to be
    `^\\s*-?\\s*run:` and the parser an indentation walk: two regexes of the
    same shape. policyforge-b5 built `- { name: x, run: "cmd | tail" }`,
    flow-style YAML, and both counted 0. They agreed, the floor could not
    fire, and a block containing exactly the pattern this lint exists for was
    examined by nothing. Two derivations that fail on the same input cannot
    disagree about it. A YAML load sees flow and block style identically, so
    that step is counted here and missed by the parser, and the disagreement
    is the alarm.

    **Only `jobs.<id>.steps[*].run` holding a string**, which is where GitHub
    executes a shell. Two lookalikes are deliberately not counted, and both
    are measured on this tree:

    - `defaults: run: shell: bash` is a mapping, not a script. The line
      parser takes it as a block whose body is `shell: bash`, so on `ci.yml`
      the parser yields 15 and this yields 14. The floor only fires when the
      parser has FEWER, so this over-count on the parser side is harmless.
    - A step input named `run` under `with:` belongs to the action, not
      to a shell.

    **The census agrees on COUNT, not on CONTENT** (policyforge-9b, on #326).
    With an anchor, `run: &cmd "x | tail && y"` in one step and `run: *cmd`
    in another, this counts both, and so does the parser, 2 = 2. But the
    parser lints the literal text `*cmd`, not the resolved command, so the
    aliased step's swallowed status is not reported.
    `test_an_aliased_run_agrees_on_count_and_is_not_linted` pins that. The
    tree has 0 anchors today (5 workflows). Closing it means handing the
    parser the YAML-resolved strings, which gives up the line numbers the
    parser exists to keep.

    A workflow that does not parse raises `yaml.YAMLError`. `main` reports
    that as exit 2, because the census cannot vouch for a file it cannot
    read, and GitHub would refuse that file too.
    """
    import yaml

    document = yaml.safe_load(text) or {}
    jobs = document.get("jobs") if isinstance(document, dict) else None
    count = 0
    for job in (jobs or {}).values():
        steps = job.get("steps") if isinstance(job, dict) else None
        for step in steps or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                count += 1
    return count


_SHELL_FENCES = frozenset({"bash", "sh", "shell", "console"})


def _count_doc_fences(text: str) -> int:
    """Shell fences as a CommonMark parser reads them, not as a line regex (#256).

    Same reasoning as `_count_workflow_steps`: the census regex and
    `_FENCE` were the same shape, so any fence both missed was invisible to
    both. markdown-it reads `~~~bash`, `` ```bash title=x ``, and a fence
    inside a list or a block quote. The parser's `_FENCE` reads only an exact
    `` ```bash ``, so each of those is now a disagreement, and so a failure,
    rather than a silence. The first word of the info string decides, as
    it does for a renderer.
    """
    from markdown_it import MarkdownIt

    return sum(
        1
        for token in MarkdownIt("commonmark").parse(text)
        if token.type == "fence" and (token.info.split() or [""])[0].lower() in _SHELL_FENCES
    )


#: An independent count of how many blocks each kind OUGHT to yield, used
#: only to decide whether the parser's count is suspicious. **Independent
#: in MECHANISM, not only in call graph** (#256): the workflow and doc
#: counters are a YAML load and a CommonMark parse, while the parsers above
#: are line walks. A census that is a second regex of the parser's shape
#: fails on the same inputs, and agrees with it exactly where both are blind.
_SIGNALS = {
    "script": (("*.sh",), _count_script),
    # Both extensions, matching `population()`. With only `*.yml` a
    # `.yaml` workflow is read by the parser and invisible to the census,
    # so the one file the census exists to notice could be the one it
    # cannot see.
    "workflow": (
        (".github/workflows/*.yml", ".github/workflows/*.yaml"),
        _count_workflow_steps,
    ),
    "doc": (("*.md",), _count_doc_fences),
}


def census() -> dict[str, dict[str, int]]:
    """How many files of each kind *look like* they should yield sources.

    **This is the second derivation, and it exists because of a real hole.**
    `main` originally refused only an entirely empty population, so breaking
    a single pathspec left the other kinds intact and the tool printed
    `clean` — while `ci.yml`, this lint's one real finding, went unexamined
    and exit was 0. Found by policyforge-9b by mutating one pathspec.

    **`0 script` is today's correct state**, since the repository has no
    committed `*.sh` files, so a bare "no kind may be zero" rule would be
    wrong on arrival. Hence a census: a kind may be zero only when nothing
    of that kind looks like it should have produced anything.

    **What this defends against is an EDITED PATHSPEC, not a renamed
    directory** — policyforge-9b's correction, and the distinction is not
    pedantic. GitHub reads workflows only from `.github/workflows/`, so
    renaming that directory means those files stop being workflows and
    reporting zero is *right* rather than blind. The plausible event is
    someone editing `population()` and not `_SIGNALS`, which is exactly
    what was measured.

    **The residual, so it is a known floor rather than a surprise.** The
    two derivations are independent in *method* but share a *pathspec
    prefix*, and they sit about forty lines apart. Point both at
    `.github/ci/*` and this reports `clean across 26 source(s)` and exits
    0, with five workflow files still tracked and unexamined — measured,
    not supposed. That takes two coordinated edits rather than one, so it
    is narrower than what the census catches, but it is not zero.
    **A second derivation raises the cost of going blind; it does not make
    it impossible.** #256 closed the other shared-blindness, a shared
    *pattern shape*, by moving the counters onto a different mechanism. It
    did not touch the shared pathspec.
    """
    found: dict[str, dict[str, int]] = {}
    for kind, (patterns, count) in _SIGNALS.items():
        hits: dict[str, int] = {}
        for pattern in patterns:
            for path in tracked(pattern):
                text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
                blocks = count(text)
                if blocks:
                    hits[path] = blocks
        found[kind] = hits
    return found


def population() -> list[Source]:
    """Derive what a shell will execute, from the repository rather than a list.

    **Derived, not listed**, which is the difference between a guard that
    covers a new file and one that covers the files someone remembered. The
    non-empty assertion in `main` is the other half: a derivation that stops
    matching enumerates zero and every assertion over it becomes vacuous.
    """
    sources: list[Source] = []
    for path in tracked("*.sh"):
        text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        sources.append(
            Source(path=path, kind="script", lines=list(enumerate(text.splitlines(), 1)))
        )
    for path in tracked(".github/workflows/*.yml") + tracked(".github/workflows/*.yaml"):
        text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        sources.extend(_workflow_run_blocks(path, text))
    for path in tracked("*.md"):
        text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        sources.extend(_doc_shell_blocks(path, text))
    return sources


def _strip_comment(line: str) -> str:
    """Drop a trailing `#` comment so prose about a rule is not a violation.

    Written after two tests in this repository failed on their own
    explanation — a docstring naming the strings it asserts are absent. A
    checker that flags the comment describing it has the same defect.
    """
    out, quote = [], ""
    for char in line:
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            continue
        if char == "#":
            break
        out.append(char)
    return "".join(out)


def _pipefail_at(state: bool, line: str, position: int, blocks: tuple[str, ...] = ()) -> bool:
    """Whether pipefail is on at `position` in `line`, given its state
    before the line and the blocks open at its start: each `set`/`shopt`
    that RUNS UNCONDITIONALLY earlier in the line and still governs the shell
    at `position` changes it, in order (#330, #331). A pipe inside a quoted
    program starts from off, because `bash -c` is a new shell."""
    states = _nesting(line)
    if position < len(states) and states[position][0]:
        state = False
    for start, value in sorted(_pipefail_changes(line, blocks)):
        if start < position and _reaches(line, states, start, position):
            state = value
    return state


def _pipefail_at_position(state: bool, line: str, blocks: tuple[str, ...], position: int) -> bool:
    """`_pipefail_at` with the line-level arguments first, for `functools.partial`."""
    return _pipefail_at(state, line, position, blocks)


def _pipefail_after(state: bool, line: str, blocks: tuple[str, ...] = ()) -> bool:
    """Pipefail in the top-level shell AFTER `line`, carried to the next line.

    Only a change made in the top-level unquoted shell carries, and only if
    no `( )` subshell around it closes later on the line. A `set` inside a
    quote never does: it is text, or a `bash -c` program that ends with the
    quote. **Separate from `_pipefail_at` on purpose** (#331, 9b): asking
    `_pipefail_at` about a position past the end missed a quote that closes
    on the line's last character, so the continuation line of a multi-line
    `git commit -m \"...\"` that began `set -o pipefail` was carried as ON.
    """
    states = _nesting(line)
    for start, value in sorted(_pipefail_changes(line, blocks)):
        if states[start] != ("", 0):
            continue
        parens = 0
        for i in range(start, len(line)):
            if states[i] != ("", 0):
                continue
            if line[i] == "(" and not (i > 0 and line[i - 1] == "$"):
                parens += 1
            elif line[i] == ")":
                parens -= 1
                if parens < 0:
                    break
        else:
            state = value
    return state


def _first_statement(line: str) -> str:
    """The text before the first `;`, `&&` or `||`: the statement whose `$?`
    still refers to whatever ran on the line above."""
    return re.split(r";|&&|\|\|", line, maxsplit=1)[0]


def findings(sources: list[Source]) -> list[Finding]:
    """Every rule, per source, reading pipefail as a STATE along the block.

    **pipefail counts only from where it is set (#213, 80's ruling).** The
    first version asked whether `set -o pipefail` appeared anywhere in the
    block, so a `set` written AFTER the pipe cleared it, and
    `swallowed-status` ignored pipefail altogether, refusing
    `set -o pipefail; x | tail && y`, which gates correctly, while the
    `no-pipefail` message recommended exactly that fix. Now pipefail, set
    earlier in the same block and not since turned off with
    `set +o pipefail`, clears `swallowed-status`, `no-pipefail` and
    `status-after-pipe` for that pipe. A `set` in another block (another
    workflow step, another fence) does not carry over, because that is
    another shell.
    """
    results: list[Finding] = []
    for source in sources:
        text = "\n".join(line for _, line in source.lines)
        pipefail = False
        #: The previous command line ended in a pipe into a stream consumer
        #: while pipefail was off, so a `$?` at the start of the next line
        #: reads the consumer's status.
        unguarded_pipe_above = False
        #: The delimiter of a heredoc whose body is DATA (a review, a commit
        #: message, a Python script), so its lines are not commands.
        heredoc_end = ""
        #: Blocks open at the start of the line (if/loop/case/function), carried
        #: so a `set` inside a multi-line conditional block is not credited (#331).
        blocks: tuple[str, ...] = ()
        #: A quote left open at the end of a line (a multi-line `-m "..."`), carried
        #: so its continuation is read as quoted text, not as commands (#331, 9b).
        carried_quote = ""
        for number, raw in source.lines:
            if heredoc_end:
                if raw.strip() == heredoc_end:
                    heredoc_end = ""
                continue
            line = _strip_comment(carried_quote + raw)
            if not line.strip():
                continue
            opened = _HEREDOC.search(line)
            if opened and not _SHELL_READS_HEREDOC.search(line[: opened.start()]):
                heredoc_end = opened.group("tag")
            piped = _PIPES_TO_CONSUMER.search(line)
            # **Every rule asks about the pipe it is judging** (#334, policyforge-ba).
            # One `guarded`, taken at the line's FIRST consumer pipe, answered for
            # all of them, so `set -o pipefail; a | tail; set +o pipefail;
            # b | tail && c` was cleared although bash has pipefail OFF at `b`.
            # The line-start state is kept, since the carry below updates it.
            on_at = functools.partial(_pipefail_at_position, pipefail, line, blocks)

            # `$?` read where it can only mean a stream consumer's status:
            # right after `x | tail ;` on the same line, or at the start of
            # the line after one ending in such a pipe. Instances 3 and 8 on
            # #213. Applies to docs too: a snippet that reads `$?` there
            # teaches the misreading.
            status = _READS_STATUS.search(line)
            same_line = _STATUS_AFTER_PIPE.search(line)
            if status and (
                (same_line and not on_at(same_line.start()))
                or (unguarded_pipe_above and _READS_STATUS.search(_first_statement(line)))
            ):
                results.append(
                    Finding(
                        source.path,
                        number,
                        raw,
                        "status-after-pipe",
                        "`$?` here is the stream consumer's status, not the command's; "
                        "capture first (`cmd > log 2>&1; rc=$?`), or `set -o pipefail` "
                        "before the pipe.",
                    )
                )

            if any(not on_at(position) for position in _swallowed_positions(line)):
                results.append(
                    Finding(
                        source.path,
                        number,
                        raw,
                        "swallowed-status",
                        "the `&&` runs whatever the left of the pipe returned; "
                        "capture first (`cmd > log 2>&1; rc=$?`) and branch on `$rc`.",
                    )
                )
            # Carry the state to the next line: every set/unset on this one,
            # and whether it ENDS in an unguarded pipe into a consumer -- asked
            # at that LAST pipe, whose status a `$?` on the next line reads.
            ends = _ENDS_IN_CONSUMER_PIPE.search(line)
            unguarded_pipe_above = ends is not None and not on_at(ends.start())
            pipefail = _pipefail_after(pipefail, line, blocks)
            blocks = _blocks_at(line, _nesting(line), blocks)[1]
            carried_quote = _open_quote_after(line)

            if not (source.requires_pipefail and piped):
                continue

            unguarded = [m for m in _PIPES_TO_CONSUMER.finditer(line) if not on_at(m.start())]
            if unguarded and line.strip() not in PIPEFAIL_EXEMPT:
                results.append(
                    Finding(
                        source.path,
                        number,
                        raw,
                        "no-pipefail",
                        "this pipeline's status comes from the right-hand command. "
                        "Add `set -o pipefail` to the block, or name it in "
                        "PIPEFAIL_EXEMPT with a reason.",
                    )
                )

            # **Deliberately NOT nested under the pipefail branch above, and
            # the first version of this file got that wrong.** Adding
            # `set -o pipefail` silenced this rule, which is precisely the
            # mistake it exists to prevent: the two failures are
            # independent, and the empty-input one survives the fix for the
            # other. The test that caught it is named after the property.
            # Keyed on the construct, not on a list of tool names: `xargs`
            # without `-r` runs its command once on empty input, whatever
            # that command is. Naming the tool only sharpens the message.
            if _XARGS_RUNS_ON_EMPTY.search(line) and not _asserts_non_empty(text):
                known = next(
                    (t for t in EMPTY_INPUT_TOOLS if re.search(rf"\b{re.escape(t)}\b", line)),
                    "",
                )
                because = (
                    f"`{known}` is known to exit 0 when given no paths, so "
                    if known
                    else "the command then runs with no paths, so "
                )
                results.append(
                    Finding(
                        source.path,
                        number,
                        raw,
                        "empty-input-passes",
                        "`xargs` without `-r` runs its command even when the list "
                        f"is empty. {because}an empty list reads as 'all clean'. "
                        "pipefail does not fire here -- nothing failed. Assert the "
                        "list is non-empty, or pass `xargs -r`.",
                    )
                )
    return results


def check_command(command: str) -> list[Finding]:
    """The same rules over one command, as typed (#213).

    **Why this exists:** three of #213's first seven instances, and the
    eighth, were typed at a prompt, which is the surface the repository scan
    cannot see. A rule that has to be recalled at the moment of typing fires
    only when you are already being careful. Run from a pre-execution hook,
    this fires anyway.

    A typed command is treated like a doc snippet, not a script:
    `no-pipefail` does not apply, because `git log | head` at a prompt is
    watched by the person who typed it. `swallowed-status`,
    `status-after-pipe` and `empty-input-passes` (the latter only where
    pipefail is required, so not here) are about a status being BELIEVED,
    and the first two apply.
    """
    lines = list(enumerate(command.splitlines() or [command], 1))
    return findings([Source(path="<command>", kind="command", lines=lines)])


def _hook(stdin_text: str) -> int:
    """A Claude Code PreToolUse hook: exit 2 blocks the call and feeds the
    findings back on stderr; exit 0 lets it run.

    **Reads the payload itself** so the configured hook needs no `jq` pipe,
    which would be the very shape this guards. A payload it cannot read
    exits 1: a non-blocking error the user sees, rather than silently
    allowing (a check that cannot run is not a pass) or blocking every
    command (a hook that blocks everything gets removed).
    """
    import json

    try:
        payload = json.loads(stdin_text)
        command = payload.get("tool_input", {}).get("command")
    except (ValueError, AttributeError) as error:
        print(f"shell_status --hook: unreadable payload ({error}); not checked", file=sys.stderr)
        return 1
    if payload.get("tool_name") != "Bash":
        return 0
    if not isinstance(command, str):
        print(
            "shell_status --hook: a Bash call with no command string; not checked", file=sys.stderr
        )
        return 1
    found = check_command(command)
    if not found:
        return 0
    print(
        f"shell_status: {len(found)} finding(s) in this command. It would believe a "
        "status that belongs to a stream consumer.\n",
        file=sys.stderr,
    )
    for finding in found:
        print(f"{finding}\n", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the derived population")
    parser.add_argument("--command", help="check one command string instead of the repository")
    parser.add_argument(
        "--hook",
        action="store_true",
        help="read a Claude Code PreToolUse payload on stdin; exit 2 blocks the command",
    )
    args = parser.parse_args(argv)

    if args.hook:
        return _hook(sys.stdin.read())
    if args.command is not None:
        found = check_command(args.command)
        for finding in found:
            print(f"{finding}\n", file=sys.stderr)
        return 1 if found else 0

    sources = population()

    # The assertion that keeps every other assertion meaningful, and it is
    # **per kind, not over the union.**
    #
    # The first version asked only `if not sources`. A derivation over three
    # kinds has three ways to go vacuous and a union has one, so breaking a
    # single pathspec left the other kinds intact, printed `clean`, and
    # exited 0 -- with `ci.yml`, this lint's one real finding, unexamined.
    # Measured by policyforge-9b, confirmed here: the tool reported clean on
    # a tree that still contained the violation.
    #
    # `0 script` is today's CORRECT state (no committed *.sh), so the rule
    # cannot be "no kind may be zero". The census is an independent, cruder
    # derivation of "should this kind have produced anything", which is what
    # separates *nothing to find* from *stopped looking*. See `census()` for
    # what this does and does not defend against -- notably that a renamed
    # `.github/workflows/` is a legitimate zero, and that two coordinated
    # edits still defeat both derivations.
    # **Compared as SETS OF FILES, not as counts per kind.** The first
    # version compared `census()` against the parser per kind and fired
    # only when the parser produced zero of a kind, so a 56% loss of the
    # workflow population reported `clean` and exited 0 -- measured on the
    # train. The cause was not the comparison but the UNITS: the census
    # counted files (3 workflows) and the parser counted run-blocks (34),
    # and the only question answerable across those two numbers is whether
    # both are zero. Extent was unreachable by construction, not by
    # oversight. See #228.
    #
    # Naming the files rather than counting them puts both sides in the
    # same unit, and it needs no pinned bound: the population may grow
    # freely, and a file that stops being examined is named rather than
    # absorbed into a smaller total.
    #
    # **What this does NOT close, measured rather than reasoned about.**
    # policyforge-b5's formulation of #228 is that a shrink is invisible
    # *because* a check was made more specific -- so the same question
    # has to be asked of this fix. Asked and answered:
    #
    #     edit the parser pathspec only     before: exit 0, silent
    #                                        after: exit 2, names both files
    #     edit census AND parser pathspecs  before: exit 0
    #                                        after: exit 0      <- unchanged
    #
    # The one-edit case is closed; the coordinated two-edit case is not,
    # and is the residual `census()` already discloses. It is unchanged
    # by this, not introduced by it. Both derivations reach the tree
    # through a pathspec, so narrowing both in step defeats them both --
    # which is an argument for deriving from the whole tracked set and
    # classifying, so a narrowed spec moves files to "unclassified"
    # rather than out of existence. Not done here: one edit is the
    # accident-shaped case, two coordinated edits in the same direction
    # forty lines apart is not.
    # **Compared in BLOCKS, per file.** The previous version compared FILE
    # SETS -- census files minus produced files. That catches a file falling
    # out of the derivation entirely and is blind to a file producing FEWER
    # BLOCKS than it holds: capping run-blocks at one per file took the
    # population 34 -> 3, a 91% loss, every file still produced something,
    # so the set difference was empty and this exited 0 saying `clean`. A
    # real violation inside a dropped block was invisible, not merely
    # uncounted.
    #
    # Found by policyforge-9b; confirmed by policyforge-ba, who seeded a
    # real `| tail -4 && git push` inside a dropped block and watched it go
    # from exit 1 to exit 0.
    #
    # **The unit is the whole fix, and ba named the trap that was waiting
    # for me**: classifying FILES and counting BLOCKS reproduces this a
    # third time, and it would look like a remedy. Both sides count blocks.
    produced: dict[str, dict[str, int]] = {kind: {} for kind in _SIGNALS}
    for source in sources:
        produced[source.kind][source.path] = produced[source.kind].get(source.path, 0) + 1

    # A census that cannot read a file cannot vouch for it, so that is a
    # failure, not a skip (#256: the workflow census is now a YAML load).
    # Only the YAML error: markdown-it accepts any text, and anything else
    # is a bug in this file that should crash, not become an exit 2.
    import yaml

    try:
        signalled = census()
    except yaml.YAMLError as error:
        print(
            f"shell_status: the census could not count a file, so it cannot say\n"
            f"  whether the parser saw everything: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2

    # A FLOOR rather than equality: the parser finding MORE than the census
    # is not a shrink, and the danger has one direction. Measured on this
    # tree after #256: content.yml 8/8 and framework-drift.yml 15/15, while
    # ci.yml is 14 against the parser's 15. The one extra is the parser
    # reading `defaults: run: shell: bash` as a block; see
    # `_count_workflow_steps`. Docs are 25/25.
    silent = {
        kind: rows
        for kind, rows in (
            (
                kind,
                sorted(
                    (path, blocks, produced[kind].get(path, 0))
                    for path, blocks in counts.items()
                    if produced[kind].get(path, 0) < blocks
                ),
            )
            for kind, counts in signalled.items()
        )
        if rows
    }
    if silent:
        for kind, rows in silent.items():
            missing = sum(blocks - got for _, blocks, got in rows)
            print(
                f"shell_status: {missing} {kind} block(s) went missing "
                f"across {len(rows)} file(s).\n"
                f"  That is not 'no problems'; it is a broken derivation\n"
                f"  -- a pathspec, or a block parser, that stopped matching --\n"
                f"  or a block written in a form the parser does not read:\n"
                f"  a flow-style `{{ run: ... }}` step, or a `~~~bash` or\n"
                f"  ```bash-with-attributes fence. Rewrite it in block style or\n"
                f"  as a plain ```bash fence, or teach the parser the form.\n"
                f"  Each row is file, blocks signalled, blocks parsed:\n"
                + "\n".join(f"    {path}  {blocks} -> {got}" for path, blocks, got in rows),
                file=sys.stderr,
            )
        return 2
    if not sources:
        print(
            "shell_status: derived ZERO shell sources from this repository.\n"
            "  That is not 'no problems'; it is a broken derivation.",
            file=sys.stderr,
        )
        return 2

    if args.list:
        for source in sources:
            first = source.lines[0][0]
            print(f"  {source.kind:9} {source.path}:{first}  ({len(source.lines)} lines)")
        print(f"\n{len(sources)} shell source(s)")
        return 0

    results = findings(sources)
    if results:
        print(f"shell_status: {len(results)} finding(s)\n", file=sys.stderr)
        for finding in results:
            print(f"{finding}\n", file=sys.stderr)
        return 1

    scripts = sum(1 for s in sources if s.kind == "script")
    workflows = sum(1 for s in sources if s.kind == "workflow")
    docs = sum(1 for s in sources if s.kind == "doc")
    print(
        f"shell_status: clean across {len(sources)} source(s) "
        f"({scripts} script, {workflows} workflow run-block, {docs} doc block)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
