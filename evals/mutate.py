"""Removing one rule from a prompt, to find out which cases were guarding it.

A suite that passes tells you nothing until you know it *can* fail. That
turned out not to be rhetorical: stripping the grounding rules out of the
answering prompt left every refusal case still green, because half of them
never reached the model and the rest were too easy to need the rules. Six
passing cases were measuring nothing, and no amount of running them would
have said so.

This inverts the question. Instead of asking whether a case passes, delete
one numbered rule from the prompt it depends on and ask which cases notice.
A rule that no case notices is unguarded — either the rule does not matter
and should go, or the suite has a hole where its evidence should be.

The mutation is surgical on purpose. Blunt ones are useless: cutting the
answering prompt down to its preamble broke the citation format too, so
every case failed with "no citation marker" and none of them said anything
about the rule under test. One rule out, everything else intact.
"""

from __future__ import annotations

import re

#: A rule opens with its number at the start of a line and runs until the
#: next one. Continuation lines are indented, which is what keeps a rule
#: that happens to contain "1." in its prose from splitting in two.
_RULE_RE = re.compile(r"^(\d+)\.[ \t]", re.MULTILINE)


def rules(prompt: str) -> list[tuple[int, str]]:
    """The numbered rules in `prompt`, as `(number, text)` in order."""
    starts = [(int(m.group(1)), m.start()) for m in _RULE_RE.finditer(prompt)]
    found = []
    for index, (number, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(prompt)
        found.append((number, prompt[start:end].rstrip()))
    return found


def without_rule(prompt: str, number: int) -> str:
    """`prompt` with one rule removed and the rest left exactly as it was.

    The remaining rules keep their original numbers. Renumbering would be
    tidier and would also change every rule after the hole, which is a
    second edit the experiment did not ask for — and the answering prompt
    says "in priority order", so a rule's number is part of what it means.
    """
    for found, text in rules(prompt):
        if found == number:
            return prompt.replace(text + "\n", "", 1).replace(text, "", 1).rstrip() + "\n"
    raise KeyError(f"no rule {number}")


def summarize(rule_text: str, width: int = 64) -> str:
    """The first clause of a rule, for a report that has to fit on a line."""
    body = re.sub(r"\s+", " ", rule_text.split(".", 1)[1] if "." in rule_text else rule_text)
    body = body.strip()
    clause = re.split(r"(?<=[.;:])\s", body, maxsplit=1)[0]
    return clause[: width - 1] + "…" if len(clause) > width else clause
