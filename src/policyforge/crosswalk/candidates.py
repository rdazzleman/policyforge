"""Which 800-53 controls a model is asked about, for one requirement.

A model cannot judge the 1,014 controls and enhancements in the catalog for
every requirement, so it judges a short list. The list decides what can be
proposed at all: a control not on it can never be suggested, however right.

Three sources, because each misses what the others find. Measured on the 75
HIPAA requirements against NIST's published mapping:

* **Word overlap** (BM25 over each control's title and statement) found 23%
  of the published pairs in its top 15. Crosswalks are written by people who
  know that "Security management process" is RA-1, and the words do not say
  so. It is still the only source that can find a pair nobody published.
* **The published pairs**, always included and never labelled as such. The
  model is asked to confirm or omit them with a quote like any other
  candidate; telling it which ones NIST chose would make agreement with NIST
  the easy answer and the measurement meaningless.
* **Each matched family's `-1` control** (AC-1, PS-1). HIPAA writes most
  standards as "implement policies and procedures to …", which is exactly
  what an 800-53 family's policy-and-procedures control requires, and word
  overlap rarely ranks it.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from policyforge.mapping.crosswalk import NIST_ANCHOR, normalize_framework


@dataclass(frozen=True)
class CatalogEntry:
    control_id: str
    title: str
    text: str


def catalog_entries(controls, anchor: str = NIST_ANCHOR) -> dict[str, CatalogEntry]:
    """Every control and enhancement of the anchor framework, by id.

    The framework is matched through `normalize_framework` rather than by
    taking its first word here. This function used to do the latter inline,
    which was a private copy of a rule defined elsewhere — and when the NIST
    family stopped sharing one key, the copy went on answering `nist` while
    the anchor had become `nist-800-53`, so the candidate list came back
    empty and every crosswalk proposal had nothing to choose from.
    """
    entries = {}
    for control in controls:
        if normalize_framework(control.framework) != anchor:
            continue
        entries[control.control_id] = CatalogEntry(
            control.control_id, control.title, control.control_statement or ""
        )
        for enhancement in control.enhancements:
            entries[enhancement.enhancement_id] = CatalogEntry(
                enhancement.enhancement_id,
                f"{control.title} | {enhancement.title}",
                enhancement.description or "",
            )
    return entries


class WordIndex:
    """BM25 over catalog entries. Small and exact, which is all this needs."""

    def __init__(self, entries: dict[str, CatalogEntry], *, k1: float = 1.4, b: float = 0.75):
        from policyforge.zardoz.retrieve import tokenize

        self._tokenize = tokenize
        self._tf = {i: Counter(tokenize(f"{e.title} {e.text}")) for i, e in entries.items()}
        self._length = {i: sum(tf.values()) for i, tf in self._tf.items()}
        self._average = sum(self._length.values()) / max(len(self._length), 1)
        frequency: Counter = Counter()
        for tf in self._tf.values():
            frequency.update(tf.keys())
        n = len(self._tf)
        self._idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in frequency.items()}
        self._k1, self._b = k1, b

    def top(self, query: str, k: int) -> list[str]:
        terms = set(self._tokenize(query))
        scores = {}
        for control_id, tf in self._tf.items():
            norm = self._k1 * (1 - self._b + self._b * self._length[control_id] / self._average)
            score = sum(
                self._idf[w] * tf[w] * (self._k1 + 1) / (tf[w] + norm) for w in terms if w in tf
            )
            if score:
                scores[control_id] = score
        # Ties broken by id, so the same catalog gives the same list every run.
        return sorted(scores, key=lambda i: (-scores[i], i))[:k]


def candidates_for(
    requirement_text: str,
    *,
    published: list[str],
    entries: dict[str, CatalogEntry],
    index: WordIndex,
    k: int = 15,
) -> list[str]:
    """The controls to ask about, sorted by id so their order says nothing."""
    matched = index.top(requirement_text, k)
    families = {control_id.split("-")[0] for control_id in matched}
    policy = {f"{family}-1" for family in families if f"{family}-1" in entries}
    known_published = {control_id for control_id in published if control_id in entries}
    return sorted(set(matched) | known_published | policy)
