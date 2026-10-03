"""Many requests, submitted together, for the paths nobody waits on.

`ssp` drafts one narrative per control: several hundred requests, each
grounded in a single control's text so the narrative stays attributable to
that control. That shape is right and is not changing. What is wrong is
paying interactive prices for it — nobody sits watching a System Security
Plan build, and the Batch API serves exactly this case at half the cost.

The unit here is a request with an id the caller chooses. Results come back
in *any* order, so they are returned keyed by that id rather than by
position: a run that matched narratives to controls by index would attribute
every narrative to the wrong control the first time the API reordered two,
and the workbook would look entirely plausible.

Batching is opt-in per run rather than a default. A batch is not free of
consequence: it takes as long as it takes, and a caller who wanted an answer
now would be left waiting on a queue.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BatchRequest:
    """One request in a batch, and the id its result will carry back."""

    #: The caller's own key — a control id, a document slug. Returned
    #: unchanged on the result, and the only safe way to match the two.
    custom_id: str
    system: str
    prompt: str
    max_tokens: int = 4096
    temperature: float = 0.2
    effort: str | None = None
    #: The leading text this request shares with its siblings, marked so the
    #: API can serve it from the prompt cache. Same meaning as `generate`'s.
    cache_prefix: str | None = None


class BatchError(RuntimeError):
    """A batch that could not be submitted, or did not finish in time.

    Raised rather than returning partial results, because a caller that got
    half a batch back and no exception would write half a workbook and call
    it done.
    """
