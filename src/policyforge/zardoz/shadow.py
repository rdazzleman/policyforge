"""Run a candidate retriever beside the real one and record where they differ.

Dense retrieval, reranking and entailment are all built and all parked on
evidence, which is the right posture: `embed/dense.py`'s similarity floor
was calibrated against a single document, and shipping a retriever tuned on
one corpus to every corpus is how a search box quietly gets worse. The
blocker is calibration data, and calibration data is exactly what nobody has
because the feature is off.

Shadow mode breaks that circle. The candidate runs on every real question,
its result is compared against the lexical result the user actually gets,
the disagreement is recorded, and **nothing the user sees changes**. After a
few weeks of real questions the threshold is measured rather than guessed.

Three properties, in the order they matter:

* **A shadow can never change an answer.** Every entry point returns the
  live result untouched, and `run_shadow` catches everything a candidate can
  raise. A retriever that is being evaluated is by definition not trusted
  yet, and a shadow that could break the tool would be a worse bargain than
  staying uncalibrated. This is the property the tests are mostly about.
* **A shadow can never cost an answer.** It runs after the live result is in
  hand. If the embedder is down, the question is still answered.
* **Disagreement is the measurement, not agreement.** A candidate that
  always agrees adds nothing and the record should make that obvious; the
  interesting rows are where the two retrievers disagree on what the
  question was about.

What this deliberately does not do is decide anything. There is no threshold
here at which dense retrieval turns itself on. The output is evidence for a
person, which is the same standard the rest of this project holds: a number
with an epoch, not a model grading its own homework.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The shadows a config may ask for. Closed, so a typo in config is an
#: error rather than a silently absent measurement — the failure mode of a
#: feature that reports nothing is that it looks like agreement.
SHADOWS = ("dense", "rerank", "entail")


@dataclass(frozen=True)
class PassageRef:
    """How a passage is identified when comparing two rankings.

    Title and section rather than object identity, because the two
    retrievers build their own `Passage` objects over the same corpus and
    comparing instances would report total disagreement every time.
    """

    title: str
    section: str

    def __str__(self) -> str:
        return f"{self.title} § {self.section}" if self.section else self.title


def passage_ref(passage) -> PassageRef:
    return PassageRef(
        title=getattr(getattr(passage, "document", None), "title", "") or "",
        section=getattr(getattr(passage, "chunk", None), "section", "") or "",
    )


@dataclass
class Comparison:
    """Where two rankings of the same question disagreed."""

    name: str
    live: list[PassageRef] = field(default_factory=list)
    shadow: list[PassageRef] = field(default_factory=list)
    #: Set when the candidate could not run at all. A shadow that failed and
    #: a shadow that agreed are entirely different facts, and a report that
    #: rendered both as "no disagreement" would manufacture the evidence it
    #: exists to gather.
    error: str = ""

    @property
    def ran(self) -> bool:
        return not self.error

    @property
    def only_live(self) -> list[PassageRef]:
        return [ref for ref in self.live if ref not in self.shadow]

    @property
    def only_shadow(self) -> list[PassageRef]:
        return [ref for ref in self.shadow if ref not in self.live]

    @property
    def agreed(self) -> bool:
        return self.ran and self.live == self.shadow

    @property
    def same_set(self) -> bool:
        """Same passages, possibly reordered.

        Worth separating from `agreed`: two retrievers that choose the same
        evidence and rank it differently is a much weaker disagreement than
        two that choose different evidence, and conflating them would make
        reordering look like a retrieval failure.
        """
        return self.ran and set(self.live) == set(self.shadow)

    def summary(self) -> str:
        if not self.ran:
            return f"{self.name}: did not run — {self.error}"
        if self.agreed:
            return f"{self.name}: agreed exactly ({len(self.live)} passage(s))"
        if self.same_set:
            return f"{self.name}: same {len(self.live)} passage(s), different order"
        return (
            f"{self.name}: {len(self.only_shadow)} passage(s) it would have added, "
            f"{len(self.only_live)} it would have dropped"
        )

    def render(self) -> str:
        lines = [self.summary()]
        if not self.ran:
            return "\n".join(lines)
        for ref in self.only_shadow:
            lines.append(f"  + {ref}")
        for ref in self.only_live:
            lines.append(f"  - {ref}")
        if self.same_set and not self.agreed:
            lines.append(f"  live:   {', '.join(str(r) for r in self.live)}")
            lines.append(f"  shadow: {', '.join(str(r) for r in self.shadow)}")
        return "\n".join(lines)


@dataclass
class ShadowReport:
    """Every shadow run for one question."""

    question: str = ""
    comparisons: list[Comparison] = field(default_factory=list)

    @property
    def ran(self) -> bool:
        return bool(self.comparisons)

    def render(self) -> str:
        if not self.comparisons:
            return (
                "No shadow retrievers are configured. Set `zardoz.shadow` to any of "
                f"{', '.join(SHADOWS)} to run a candidate beside the live retriever "
                "without changing what you see."
            )
        lines = [f"Shadow comparison for: {self.question}", ""]
        lines += [c.render() for c in self.comparisons]
        lines += [
            "",
            "Nothing above changed the answer you were given. These are candidates "
            "being measured, not results.",
        ]
        return "\n".join(lines)


def configured_shadows(config: dict | None) -> list[str]:
    """The shadows `config` asks for, validated.

    An unknown name raises rather than being skipped. A measurement that
    silently does not run reads exactly like a candidate that always agreed,
    and that is the one wrong answer this module must never give.
    """
    requested = ((config or {}).get("zardoz") or {}).get("shadow") or []
    if isinstance(requested, str):
        requested = [requested]

    unknown = [name for name in requested if name not in SHADOWS]
    if unknown:
        raise ValueError(
            f"Unknown zardoz.shadow entr{'ies' if len(unknown) > 1 else 'y'}: "
            f"{', '.join(repr(u) for u in unknown)}. Known shadows: {', '.join(SHADOWS)}."
        )
    return list(requested)


def compare_rankings(name: str, live: list, shadow: list) -> Comparison:
    """One comparison between the passages shown and the passages a candidate chose."""
    return Comparison(
        name=name,
        live=[passage_ref(p) for p in live],
        shadow=[passage_ref(p) for p in shadow],
    )


def run_shadow(name: str, live: list, candidate) -> Comparison:
    """Run one candidate and compare it, or record why it could not run.

    `candidate` is a zero-argument callable returning its own ranking. It is
    called inside the try deliberately: building the retriever is as likely
    to fail as querying it — a missing embedding model, an endpoint that is
    down — and a shadow whose construction could raise into the answering
    path would violate the one property this module has.
    """
    try:
        return compare_rankings(name, live, list(candidate()))
    except Exception as exc:  # noqa: BLE001 - a shadow may never break an answer
        return Comparison(
            name=name, live=[passage_ref(p) for p in live], error=f"{type(exc).__name__}: {exc}"
        )


def shadow_retrieval(
    question: str,
    live: list,
    candidates: dict,
) -> ShadowReport:
    """Compare every configured candidate against the live result.

    `candidates` maps a shadow's name to a zero-argument callable. Passing
    callables rather than retrievers keeps this module free of any import
    from `embed/` or `rerank/`, which are optional dependencies — importing
    them here would make an uncalibrated feature a hard requirement of the
    shell.
    """
    return ShadowReport(
        question=question,
        comparisons=[run_shadow(name, live, build) for name, build in candidates.items()],
    )
