"""Whether a quote a model gave is really in the text it quoted.

A proposed mapping carries two quotes — the words of the requirement it
addresses and the words of the control that address them — and a mapping
whose quotes are not in the texts is refused before anybody reviews it. The
quotes are what a reviewer reads instead of both documents, so a quote the
text does not contain would put a fabricated reason in front of the person
deciding.

Exact substring matching was the first version, and measured on 75 HIPAA
requirements it refused 73 mappings, most of them right. SP 800-53 writes
its parameters as `[Selection (one or more): confidentiality; integrity]`,
and a model quoting that control writes "protect the confidentiality and
integrity", which is the text read aloud rather than a fabrication. Models
also join two clauses with an ellipsis. So matching is over words, in
order: bracketed parameter syntax is set aside, a few missing or inserted
words are tolerated, and an ellipsis splits a quote into fragments that must
each be found, in order. What stays refused is what matters — words the text
does not contain, or its words out of order.
"""

from __future__ import annotations

import re

_PARAMETER = re.compile(r"\[(?:Assignment|Selection)[^\]]*\]", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
_ELLIPSIS = re.compile(r"\.\.\.|…")

#: A fragment of fewer words than this proves little on its own, so a quote
#: built from several must give each at least this many.
MIN_FRAGMENT_WORDS = 3
#: The shortest quote `grounded` accepts. A specification shorter than this —
#: HIPAA's "Periodic security updates." — is quoted whole instead; see
#: `is_whole_text`.
MIN_QUOTE_WORDS = 4
#: Words of a fragment that may be absent from the text: one in six or seven.
#: None for a fragment under six words, where one missing word is a sixth of
#: the evidence or more.
MISSES_PER_WORD = 0.15


def words(text: str, *, drop_parameters: bool = False) -> list[str]:
    if drop_parameters:
        text = _PARAMETER.sub(" ", text)
    return _WORD.findall(text.lower())


def _allowed_misses(length: int) -> int:
    return max(1, int(length * MISSES_PER_WORD)) if length >= 6 else 0


def _aligned(fragment: list[str], window: list[str]) -> tuple[int, int]:
    """(words of `fragment` found in order in `window`, index just past the last).

    The longest common subsequence, so a word the quote adds that happens to
    appear later in the text cannot pull the match past the words in between —
    the way a greedy scan does.
    """
    rows, cols = len(fragment), len(window)
    best = [[0] * (cols + 1) for _ in range(rows + 1)]
    for r in range(rows - 1, -1, -1):
        for c in range(cols - 1, -1, -1):
            if fragment[r] == window[c]:
                best[r][c] = best[r + 1][c + 1] + 1
            else:
                best[r][c] = max(best[r + 1][c], best[r][c + 1])
    r = c = last = 0
    while r < rows and c < cols:
        if fragment[r] == window[c]:
            last = c + 1
            r, c = r + 1, c + 1
        elif best[r + 1][c] >= best[r][c + 1]:
            r += 1
        else:
            c += 1
    return best[0][0], last


def _match_after(fragment: list[str], tokens: list[str], start_at: int) -> int:
    """Index just past `fragment` found in order in `tokens`, or -1.

    Two kinds of slack, and both are needed. The text may have words the
    quote leaves out — a dropped article — so the fragment is aligned against
    a window of twice its length. The quote may have a word the text lacks —
    an inserted "and" — so up to `_allowed_misses` of its words, including the
    first, may go unmatched. Words out of order are never matched.
    """
    needed = len(fragment) - _allowed_misses(len(fragment))
    heads = set(fragment[: len(fragment) - needed + 1])
    for start in range(start_at, len(tokens)):
        if tokens[start] not in heads:
            continue
        window = tokens[start : start + 2 * len(fragment)]
        found, last = _aligned(fragment, window)
        if found >= needed:
            return start + last
    return -1


def _without_heading(tokens: list[str], title: list[str]) -> list[str]:
    """`tokens` with the title removed from the front, where it opens them."""
    return tokens[len(title) :] if title and tokens[: len(title)] == title else tokens


def grounded(quote: str, text: str, *, min_words: int = MIN_QUOTE_WORDS, heading: str = "") -> bool:
    """Whether `quote` is words of `text`, in order.

    `heading` is the title `text` opens with. When a quote opens with that
    title as its own fragment — "Personnel Termination ... Disable system
    access within" — the title names the item rather than quoting it, so it is
    set aside and held to nothing. What follows must then stand on its own:
    at least `MIN_FRAGMENT_WORDS` words, found in the text *after* the title,
    and never the title again. Measured: glm-5.3-flash left PS-4 unmapped on
    all three runs of the termination case, and on two direct calls its PS-4
    quote had this shape and was refused by the fragment minimum.

    Each condition closes a hole review found in a looser version. Counting
    the title's words toward the minimum let "Policy and Procedures ...
    policy" verify a mapping to any `-1` control; matching the rest against
    text that still began with the title let "Policy and Procedures ...
    Policy and Procedures" verify itself. `-1` controls are the ones prompt
    rule 5 points models at.
    """
    fragments = [words(f, drop_parameters=True) for f in _ELLIPSIS.split(quote)]
    fragments = [f for f in fragments if f]
    title = words(heading, drop_parameters=True)
    named = bool(title) and len(fragments) > 1 and fragments[0] == title
    if named:
        fragments = fragments[1:]
        if any(f == title for f in fragments):
            return False
        if sum(len(f) for f in fragments) < max(MIN_FRAGMENT_WORDS, min_words - len(title)):
            return False
    elif not fragments or sum(len(f) for f in fragments) < min_words:
        return False
    if len(fragments) > 1 and any(len(f) < MIN_FRAGMENT_WORDS for f in fragments):
        return False
    # Against the text both without and with its parameter brackets: a model
    # may read the brackets aloud or quote their contents.
    for tokens in (words(text, drop_parameters=True), words(text)):
        if named:
            tokens = _without_heading(tokens, title)
        position = 0
        for fragment in fragments:
            position = _match_after(fragment, tokens, position)
            if position < 0:
                break
        if position >= 0:
            return True
    return False


def is_whole_text(quote: str, text: str) -> bool:
    """Whether `quote` is all of `text`'s words and nothing else.

    For a specification too short to quote four words from. Matched against
    the specification's own text only — not its title or the standard it
    sits under, which are longer — and an empty text has no whole to quote,
    so a one-word quote cannot pass by borrowing a title's length.
    """
    expected = words(text, drop_parameters=True)
    return bool(expected) and words(quote, drop_parameters=True) == expected
