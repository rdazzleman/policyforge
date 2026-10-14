"""What was sent to which model, when, and at what cost.

Nothing persisted this. `LLMResponse` carries a model string and sometimes a
price, `_Metered` in `scripts/eval_zardoz.py` totals a sweep, and both end
with the process. So two questions that a compliance tool of all things
should be able to answer had no answer at all: *which documents did this
model touch*, and *did licensed content ever leave the boundary*.

The second is why this is a control rather than a log. `llm/boundary.py`
refuses a pairing before the call; this records the call that was allowed.
A rule with no record of its operation is a rule nobody can evidence, which
is the exact criticism this project's own generated Standards make of an
organization that has a policy and no logs.

**It records metadata and never content.** Provider, provider class, model,
subject, content class, token counts, cost, and a SHA-256 prefix of the
prompt. Not the prompt, not the reply. A ledger that quoted what it saw
would take a licensed HITRUST export that correctly went to a local model
and copy it into a file under `output/` — recreating, in the audit trail,
precisely the leak the audit trail exists to disprove. The prompt hash is
enough to say "the same prompt" or "a different prompt" without holding
either.

**A subject is a scope, not an argument.** `LLMProvider.generate()` takes a
system prompt, a user prompt and a budget; it has never been told which
document it is working on and giving it that would mean changing every
provider. So the caller names the subject around the work:

    with ledger.about("standard/authenticator-mgmt", site="generate") as scope:
        document = generate_standard(...)
    record_version(..., metadata=scope.provenance())

Every call made inside that block is attributed to the subject, however deep
in the stack it happens. Calls outside one are recorded with no subject
rather than guessed at — an unattributed entry is a true statement about a
run, and an invented one is not.

**Failing to write is failing.** A ledger that swallowed its own write
errors would report a clean history of a run it did not observe, and the
day that matters is the day somebody is relying on it. Configuration can
turn the ledger off, which is a decision somebody made and can be seen in a
file; a silent drop is neither.
"""

from __future__ import annotations

import contextvars
import dataclasses
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import escalation
from .base import COST_ESTIMATE, COST_PROVIDER, LLMProvider, LLMResponse
from .boundary import LICENSED, LOCAL, ceilings, classify_provider, licensed_agreement
from .boundary import _agreement_allows as agreement_allows
from .boundary import exposure as _exposure

#: Under `output/`, which is gitignored, because this names every document
#: an organization has drafted and how much each cost.
DEFAULT_LEDGER_PATH = Path("output/.model-log/calls.jsonl")


@dataclass(frozen=True)
class CallRecord:
    """One request to one model."""

    timestamp: str
    provider: str
    provider_class: str
    model: str
    #: What the call was about — a document slug, a control id — or None
    #: when it happened outside any `about()` scope.
    subject: str | None
    #: Which command or code path made it: "generate", "ssp", "zardoz.answer".
    site: str | None
    #: The content class of the subject, when the caller knew it. Recorded
    #: here so the boundary question can be answered from this file alone
    #: rather than by re-deriving it from paths that have since moved.
    content_class: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    #: Where this row's cost came from (#87): `COST_PROVIDER` when the vendor
    #: reported what it billed, `COST_ESTIMATE` when LiteLLM priced it from
    #: its model map. It describes `cost_usd`, or `last_cost_usd` when that
    #: is the row's known part. None when the row has no cost, and on every
    #: row written before #87, which reads as "source not recorded", never
    #: as billed.
    cost_source: str | None = None
    #: First 16 hex characters of SHA-256 over system + prompt. Enough to
    #: tell two prompts apart; not enough to reconstruct either.
    prompt_sha: str = ""
    #: Set when the call raised. It reached the vendor and was billed even
    #: though nothing came back, and a ledger that omitted those would
    #: undercount both the spend and the exposure.
    error: str | None = None
    #: How many texts one embedding or rerank batch carried; None for a
    #: model call. Those channels send a batch rather than a prompt, and the
    #: count is what says how much of the corpus a request exposed.
    items: int | None = None
    #: Why the model stopped, when the provider says. `max_tokens` is the
    #: one worth keeping: the document that call produced is short and looks
    #: finished, and nothing downstream can tell afterwards.
    stop_reason: str | None = None
    #: Input tokens served from the prompt cache. Zero and None differ —
    #: zero is a call that could have hit the cache and did not, which is
    #: what a silently-too-short cacheable prefix looks like from here.
    cached_input_tokens: int | None = None
    #: Output tokens the operator paid for and cannot read — a model's own
    #: reasoning, where the provider reports it. None where the provider
    #: does not report it at all, which is every provider but Gemini today,
    #: so a reader can tell "none happened" from "cannot say". Without this
    #: a reasoning call records a fraction of what it cost, silently, by a
    #: ratio that varies per call.
    hidden_output_tokens: int | None = None
    #: Characters of inline reasoning removed before the reply was
    #: recorded. None where the provider does not strip; zero where it
    #: stripped nothing. Pairs with `hidden_output_tokens`: one is what the
    #: vendor billed and withheld, the other is what it sent and we cut.
    stripped_reasoning_chars: int | None = None
    #: The provider's own id for the request. Free on the response, the
    #: first thing a vendor asks for, and unrecoverable afterwards.
    request_id: str | None = None
    #: Where the call was sent (#328): the base URL the provider resolved,
    #: base-URL overrides such as ANTHROPIC_BASE_URL included, as scheme,
    #: host, port and path. Userinfo, the query, the fragment and path
    #: parameters are dropped; the rest of the path is kept as given, so a
    #: key carried as a path segment would be recorded (`recordable_endpoint`).
    #: None where the provider can't say (LiteLLM with no `api_base`, a
    #: fake client), on a call that raised before a reply, on an embedding
    #: or rerank batch, and on every row written before #328.
    endpoint: str | None = None
    #: Every bigger-budget re-send inside this call, with the billed attempt
    #: before it: its request id, cost and output tokens (#361). The row's
    #: tokens and request id are the LAST attempt's; this is where the
    #: earlier, billed ones are kept, on a failed call as on a good one (#343).
    #: Their `first_cost_usd` is already in the row's cost: never an addend
    #: to it, error rows included (#372, #343).
    escalations: tuple = ()
    #: The last attempt's own known cost, kept only on a row whose
    #: `cost_usd` is None because another attempt's cost is unknown (#382):
    #: with `escalations`' known `first_cost_usd`, every known figure has
    #: one place. None on a row with a known total, so it is never added to
    #: one.
    last_cost_usd: float | None = None
    #: The organisation's declared data agreement (provider, model,
    #: reference), when THIS call sent licensed content to a provider outside
    #: the licensed ceiling on the strength of it (#11). None on every other
    #: row, so an auditor can list which licensed calls left the machine and
    #: on what stated basis from this file alone.
    licensed_agreement: dict | None = None

    def as_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))


def prompt_digest(system: str, prompt: str) -> str:
    """A short, stable fingerprint of what was sent.

    Includes both halves and a separator, so moving text from the system
    prompt into the user prompt changes the digest — which it should, since
    for several of the models measured here that change moves the score.
    """
    payload = f"{system}\x00{prompt}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


# ---- the subject scope ----------------------------------------------------


@dataclass
class Scope:
    """A named piece of work, and the calls made inside it."""

    subject: str
    site: str | None = None
    content_class: str | None = None
    records: list[CallRecord] = field(default_factory=list)
    #: What set `content_class`, in words a refusal can show: the passage, the
    #: target file, the synthesis (#327). "" when the scope was opened with a
    #: class and no reason.
    content_reason: str = ""

    @property
    def models(self) -> list[str]:
        """Every model that actually answered, in first-seen order.

        A list rather than a string because a cascade can answer one
        document with two different models, and "which model wrote this"
        then has two correct answers. Reporting only the configured one
        would name a model that may not have written a word of it.
        """
        seen: list[str] = []
        for record in self.records:
            if record.model and record.model not in seen:
                seen.append(record.model)
        return seen

    @property
    def cost_usd(self) -> float | None:
        """Total spend, or None unless every call is priced (#379, 80's ruling).

        None and 0.0 are different answers: a local model is genuinely free,
        and a provider that does not price its calls is unknown. Until #379
        this summed the priced calls whenever any was priced, so one call of
        unknown cost left the rest standing as the whole bill.
        """
        known, unpriced = cost_parts(self.records)
        return known if self.records and not unpriced else None

    @property
    def cost_known_usd(self) -> float:
        return cost_parts(self.records)[0]

    @property
    def calls_unpriced(self) -> int:
        return cost_parts(self.records)[1]

    def provenance(self) -> dict:
        """The stamp to record alongside a generated document.

        Small on purpose — this goes into every version-history entry, and
        the ledger holds the detail. What it has to answer is the question
        that gets asked after a model is found to systematically weaken
        cited requirements: which documents did it touch.
        """
        stamp: dict = {
            "models": self.models,
            "calls": len(self.records),
            "prompt_shas": sorted({r.prompt_sha for r in self.records if r.prompt_sha}),
        }
        if self.records:
            stamp["provider"] = self.records[0].provider
            stamp["provider_class"] = self.records[0].provider_class
        # 80's ruling on #379: `cost_usd` only when every call is priced;
        # the known part and the count of unpriced calls always, so a stamp
        # never presents the known part as the whole. A stamp written before
        # #379 has neither key: a missing `calls_unpriced` is "not recorded".
        if self.records:
            known, unpriced = cost_parts(self.records)
            if not unpriced:
                stamp["cost_usd"] = round(known, 6)
            stamp["cost_known_usd"] = round(known, 6)
            stamp["calls_unpriced"] = unpriced
            # #87: the part of that cost LiteLLM estimated from its model map
            # because the vendor didn't report one. Written on every stamp
            # since #87, 0.0 included, so a stamp WITHOUT the key is one
            # written before the difference was kept (not recorded), never
            # one that was all billed. A part whose source a row didn't
            # record is its own key, not counted as either.
            split = cost_split(self.records)
            stamp["cost_estimated_usd"] = round(split.estimated, 6)
            if split.unrecorded:
                stamp["cost_unrecorded_source_usd"] = round(split.unrecorded, 6)
        if self.content_class:
            stamp["content_class"] = self.content_class
        return stamp


#: The scope in force, if any. A ContextVar rather than a module global so
#: that a threaded or async caller does not attribute one document's calls
#: to another's — the failure that would make the record worse than nothing.
_scope: contextvars.ContextVar[Scope | None] = contextvars.ContextVar("policyforge_scope")
_scope.set(None)


@contextmanager
def about(subject: str, *, site: str | None = None, content_class: str | None = None):
    """Attribute every model call made inside this block to `subject`.

    Nests: an inner scope takes over and the outer one resumes afterwards,
    so a command that drafts three documents can name each of them without
    the third inheriting the first's subject.
    """
    scope = Scope(subject=subject, site=site, content_class=content_class)
    token = _scope.set(scope)
    # Escalations are scoped with it (#372): one announced by a call that
    # recorded no row is dropped here, not taken by an unrelated later row.
    pending = escalation.begin()
    try:
        yield scope
    finally:
        escalation.end(pending)
        _scope.reset(token)


def current_scope() -> Scope | None:
    return _scope.get(None)


@contextmanager
def carrying(content_class: str | None, reason: str = ""):
    """For the calls inside, raise the current scope's class to the most
    guarded of its own and `content_class`; put it back afterwards (#327).

    One scope can make calls that send different things. A Zardoz turn
    rewrites a follow-up from the earlier exchange, routes the question, and
    answers from passages, all under one scope. A call's class is the most
    guarded of everything THAT call sends (80's ruling on 327), so the class
    is raised around the call, not for the whole turn. `reason` says what
    raised it, and a refusal shows it. Unknown or empty classes raise
    nothing. With no scope open, one is opened for the block.
    """
    from .boundary import CONTENT_CLASSES

    klass = str(content_class or "").strip().lower()
    scope = current_scope()
    if scope is None:
        with about("unscoped", content_class=klass or None) as opened:
            opened.content_reason = reason if klass else ""
            yield opened
        return
    before = (scope.content_class, scope.content_reason)
    rank = CONTENT_CLASSES.index
    current = scope.content_class if scope.content_class in CONTENT_CLASSES else None
    if klass in CONTENT_CLASSES and (current is None or rank(klass) > rank(current)):
        scope.content_class, scope.content_reason = klass, reason
    try:
        yield scope
    finally:
        scope.content_class, scope.content_reason = before


def scope_content():
    """The class of what the current call sends, and what said so (#327).

    The scope's class when a caller named one, otherwise the organization's
    own material: what a policy corpus is, and what `classify_path` calls
    anything that isn't a declared catalog. The one reading for every
    channel: the model wrapper below and the embed/rerank `Channel`. An
    unknown class is an error naming it, never a guess.
    """
    from .boundary import CONTENT_CLASSES, ORG_INTERNAL, ContentClassification

    scope = current_scope()
    if scope is not None and scope.content_class:
        if scope.content_class not in CONTENT_CLASSES:
            raise ValueError(
                f"The ledger scope for {scope.subject!r} names content class "
                f"{scope.content_class!r}; known: {', '.join(CONTENT_CLASSES)}."
            )
        return ContentClassification(
            scope.content_class,
            scope.content_reason or f"the scope for {scope.subject!r} says so",
        )
    return ContentClassification(ORG_INTERNAL, "the organization's own documents")


# ---- what a set of calls cost (#379) ----------------------------------------


@dataclass(frozen=True)
class CostSplit:
    """A set of rows' known cost, by where it came from, and what's unknown (#87).

    `billed` is what vendors reported, `estimated` what LiteLLM priced from
    its model map, `unrecorded` the known cost of rows written before the
    difference was kept. `unpriced` counts billed requests of unknown cost,
    as `cost_parts` always has.
    """

    billed: float = 0.0
    estimated: float = 0.0
    unrecorded: float = 0.0
    unpriced: int = 0

    @property
    def known(self) -> float:
        return self.billed + self.estimated + self.unrecorded

    def add(self, cost: float, source: str | None) -> CostSplit:
        if source == COST_PROVIDER:
            return dataclasses.replace(self, billed=self.billed + cost)
        if source == COST_ESTIMATE:
            return dataclasses.replace(self, estimated=self.estimated + cost)
        return dataclasses.replace(self, unrecorded=self.unrecorded + cost)


def describe_cost(split: CostSplit) -> str:
    """The known cost, said as what it is (#87, 80's ruling).

    A bare `$X` only when every dollar of it was reported by a vendor.
    Otherwise each part is named: an estimate is never said as billed, a
    total that mixes them shows both parts, and a row written before #87
    (no source kept) is never counted as billed.
    """
    if not split.estimated and not split.unrecorded:
        return f"${split.billed:.4f}"
    parts = []
    if split.billed:
        parts.append(f"${split.billed:.4f} billed")
    if split.estimated:
        parts.append(f"${split.estimated:.4f} estimated")
    if split.unrecorded:
        parts.append(f"${split.unrecorded:.4f} of unrecorded source")
    return " + ".join(parts)


def _then(split: CostSplit, suffix: str) -> str:
    """`describe_cost`, followed by more, bracketed when it has parts."""
    said = describe_cost(split)
    return f"({said}){suffix}" if " + " in said else f"{said}{suffix}"


def cost_split(records) -> CostSplit:
    """`cost_parts`, with the known part split by source (#87). The same
    walk over the same figures: `cost_parts` is this, summed."""
    row_ids = {r.request_id for r in records if r.request_id}
    split = CostSplit()
    for record in records:
        if record.cost_usd is not None:
            split = split.add(record.cost_usd, record.cost_source)
            continue
        if record.provider_class == LOCAL:
            continue
        if record.last_cost_usd is not None:
            split = split.add(record.last_cost_usd, record.cost_source)
        else:
            split = dataclasses.replace(split, unpriced=split.unpriced + 1)
        for step in record.escalations:
            if step.get("first_request_id") in row_ids:
                continue
            if step.get("first_cost_usd") is None:
                split = dataclasses.replace(split, unpriced=split.unpriced + 1)
            else:
                split = split.add(step["first_cost_usd"], step.get("first_cost_source"))
    return split


def cost_parts(records) -> tuple[float, int]:
    """(the known cost in USD, how many billed requests' cost is unknown).

    **The one rule** for the provenance stamp, `Totals` and `run_summary`
    (80's ruling on #379): a total is a total only when the second part is
    0. A local model's call counts as priced at 0.0 by its boundary class,
    since it is free although its endpoint reports no price. Never add the
    known part up as the whole: that was #378's defect one level above the
    row: 6 of 27 session ledgers on 2026-09-26 mixed them, counting a local
    call as free (b5 on #379; 9b corrected a first count of 7).

    **A row whose total is unknown still has known parts** (#382): its last
    attempt's `last_cost_usd`, and each escalation's `first_cost_usd`. Both
    count here, and each unknown one counts as one request of unknown cost.
    An escalation whose billed first attempt is a row of its own in
    `records` (`effort`'s 2x path) is counted there and not again, so this
    reads the whole set at once, never one row at a time.
    """
    # One walk for both (#87): a row's cost has every attempt's inside it
    # (#343), so it is never added to again; see `cost_split`.
    split = cost_split(records)
    return split.known, split.unpriced


# ---- a run's summary (#379) ------------------------------------------------

#: Every row recorded while a `run()` block is open, whatever scope it was
#: in: `ssp` nests one scope per control, so no single scope sees a run.
_run: contextvars.ContextVar[list[CallRecord] | None] = contextvars.ContextVar(
    "policyforge_run", default=None
)


#: The spend meter of the `run()` block in force, once a call under a ceiling
#: has made one (#125). A holder rather than the meter itself: the ceiling is
#: read from the provider's config at its first call, and the planned count
#: is the command's, set when the block is opened.
_run_spend: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "policyforge_run_spend", default=None
)

#: What a ceiling's stop is recorded as in the ledger file. Those rows aren't
#: calls: `load` keeps them out of every total and `load_stops` returns them.
SPEND_STOP = "spend_ceiling"


@contextmanager
def run(report=None, *, planned: int | None = None):
    """Collect every row a command records, and pass `run_summary` of them
    to `report` at the end, **whether the run succeeded or not**: a run that
    failed after re-sending is the one whose bill most needs saying.

    `planned` is how many requests the command meant to make, where it knows
    that up front: a spend ceiling's stop says REQUESTED beside COMPLETED
    from it (#125)."""
    records: list[CallRecord] = []
    token = _run.set(records)
    spend_token = _run_spend.set({"planned": planned, "meter": None})
    try:
        yield records
    finally:
        _run.reset(token)
        _run_spend.reset(spend_token)
        if report is not None:
            report(run_summary(records))


def new_spend() -> dict:
    """A spend total for `session_spend`: no meter until a call under a
    ceiling makes one, as a `run()` block's."""
    return {"planned": None, "meter": None}


@contextmanager
def session_spend(holder: dict):
    """Draw every call in the block on `holder`'s total, across blocks.

    `run()` holds one total for one command. A session (the Zardoz shell, the
    MCP server) makes calls over many turns, and from more than one provider:
    the answerer and, under `entail.answering`, the judge. Each provider kept
    its own total, so a session could spend the ceiling once per provider
    (1d, PR 408, measured at twice it). The session keeps `new_spend()` and
    opens this around each turn, so every provider draws on one total that
    lasts the session.
    """
    token = _run_spend.set(holder)
    try:
        yield holder
    finally:
        _run_spend.reset(token)


def run_summary(records: list[CallRecord]) -> str:
    """One line a user reads once, at the end of a run, whether or not they
    watched stderr (#379): requests, cost, and re-sends.

    - **A total is a total only when every row is priced.** Otherwise the
      known part and the count of rows of unknown cost are both said and
      never merged, since the sum of the known part alone reads as the whole
      bill (#378's rule, at the level of a run).
    - **Requests** are the rows plus each re-send's billed first attempt that
      is not already a row of its own (`effort` records its first attempt as
      a row and as an escalation; a provider's own re-send does not).
    - **Re-sends are always named, "0 re-sends" included.** What they cost is
      the attempts they replaced, part of the total and never added to it.
    """
    row_ids = {r.request_id for r in records if r.request_id}
    escalations = [e for r in records for e in r.escalations]
    extra = [e for e in escalations if e.get("first_request_id") not in row_ids]
    requests = len(records) + len(extra)
    split = cost_split(records)
    unknown = split.unpriced
    if not records:
        # Not "no model calls": with the ledger off (`wrap`), calls are made
        # and none is recorded, and this line would be a false zero.
        cost = "no calls recorded in the ledger"
    elif not unknown:
        cost = describe_cost(split)
    else:
        cost = _then(split, f" known + {unknown} request(s) of unknown cost")
    resends = f"{len(escalations)} re-send(s)"
    if escalations:
        replaced = [e for e in escalations if e.get("first_cost_usd") is not None]
        unpriced = len(escalations) - len(replaced)
        parts = CostSplit()
        for e in replaced:
            parts = parts.add(e["first_cost_usd"], e.get("first_cost_source"))
        spent = describe_cost(parts) + (f" + {unpriced} unknown" if unpriced else "")
        # "cost", not "billed": `spent` may be an estimate (#87, 1d).
        resends += f", whose replaced attempts cost {spent} of that"
    return f"Run: {requests} request(s), {cost}; {resends}."


# ---- writing --------------------------------------------------------------


def ledger_config(config: dict | None = None) -> dict:
    return ((config or {}).get("llm") or {}).get("ledger") or {}


def ledger_path(config: dict | None = None) -> Path:
    configured = ledger_config(config).get("path")
    return Path(str(configured)) if configured else DEFAULT_LEDGER_PATH


def ledger_enabled(config: dict | None = None) -> bool:
    """On unless config says otherwise.

    Default-on because the record is the point. Someone who does not want
    one can say so, and their config then carries the statement that they
    decided against it.
    """
    setting = ledger_config(config).get("enabled")
    return True if setting is None else bool(setting)


def append(record: CallRecord, path: Path | None = None) -> None:
    """Append one record. Raises if it cannot be written."""
    destination = path or DEFAULT_LEDGER_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(record.as_json() + "\n")


def load(path: Path | None = None) -> list[CallRecord]:
    """Every record on file, oldest first. Empty when nothing is recorded."""
    source = path or DEFAULT_LEDGER_PATH
    if not source.exists():
        return []
    records = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # One torn line — a run killed mid-write — should not make the
            # whole history unreadable. Skipped rather than repaired,
            # because inventing a record is worse than missing one.
            continue
        known = {f.name for f in dataclasses.fields(CallRecord)}
        record = CallRecord(**{k: v for k, v in data.items() if k in known})
        if record.stop_reason == SPEND_STOP:
            continue  # a stop is not a request: see `load_stops`
        records.append(record)
    return records


def load_stops(path: Path | None = None) -> list[CallRecord]:
    """The runs a spend ceiling stopped (#125), oldest first. Each row holds
    the cost reached in `cost_usd` and the stop's account in `error`."""
    source = path or DEFAULT_LEDGER_PATH
    if not source.exists():
        return []
    known = {f.name for f in dataclasses.fields(CallRecord)}
    stops = []
    for line in source.read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line) if line.strip() else None
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("stop_reason") == SPEND_STOP:
            stops.append(CallRecord(**{k: v for k, v in data.items() if k in known}))
    return stops


# ---- the recording provider ----------------------------------------------


class RecordingProvider(LLMProvider):
    """Wraps a provider and writes a ledger entry per call.

    A wrapper rather than a line at each call site, because there are
    fourteen call sites across nine modules and the fifteenth is the one
    that would be missed. Everything a caller can do to this object it can
    do to the provider inside it; the only difference is that the file grows.
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        provider_name: str,
        provider_class: str,
        path: Path | None = None,
        agreement: dict | None = None,
        classification=None,
        config: dict | None = None,
        recording: bool = True,
    ):
        self._inner = inner
        self._provider_name = provider_name
        self._provider_class = provider_class
        self._path = path or DEFAULT_LEDGER_PATH
        #: The declared agreement this provider sends licensed content under,
        #: or None: decided once, from config, in `wrap` (#11).
        self._agreement = agreement
        #: The provider's boundary classification and the config whose
        #: ceilings and agreement decide each call (#327). None for a wrapper
        #: built by hand without them: nothing to decide against.
        self._classification = classification
        self._config = config or {}
        #: False when `llm.ledger.enabled` is false: calls are still DECIDED,
        #: only not written down (#327: a check that lived in the recording
        #: half switched off with the ledger).
        self._recording = recording
        #: The running total when no `run()` block is open (#125).
        self._own_meter = None

    def _tag_http(self, exc: BaseException) -> None:
        """Say which service an HTTP failure came from (#413): this provider,
        and the variable `llm.api_key_env` names. The wrapper made the
        request, so the status code is never what decides whose credential
        to point at."""
        import requests

        from .base import ProviderHttpError

        if not isinstance(exc, (requests.exceptions.RequestException, ProviderHttpError)):
            return
        from policyforge.http_service import LLM, Service, tag

        llm = self._config.get("llm") if isinstance(self._config.get("llm"), dict) else {}
        tag(
            exc,
            Service(
                LLM,
                f"the {self._provider_name} model provider",
                str((llm or {}).get("api_key_env") or ""),
            ),
        )

    def _spend_meter(self):
        """The running total of this run, or None when no ceiling is set.

        The ceiling is `budget.max_usd` in the config this provider was
        built with (`--max-cost` is applied over it by the command), so every
        command that goes through `get_provider` is covered the day it is
        written. The total belongs to the `run()` block in force, and to
        this provider instance when there is none.
        """
        from . import spend

        ceiling = spend.ceiling_of(self._config)
        if ceiling is None:
            return None
        holder = _run_spend.get()
        if holder is not None:
            if holder["meter"] is None:
                holder["meter"] = spend.Meter(ceiling, holder["planned"])
            return holder["meter"]
        if self._own_meter is None:
            self._own_meter = spend.Meter(ceiling)
        return self._own_meter

    def _meter_cost(
        self, meter, system, prompt, response, billed, cost, last_cost, max_tokens, extra_chars
    ) -> None:
        """Put one request's cost on the running total.

        A cost the provider reported goes on as it is. One it did not (only
        LiteLLM reports a cost; the rest return tokens, or nothing) is priced
        here from the tokens at the model map's rates, never added as $0: a
        ceiling that held for one provider and not another is a setting that
        does nothing where it matters (#125, 1d on PR 406).
        """
        from . import spend

        if cost is not None:
            meter.add(cost)
            return
        rates = spend.rates_for(
            response.model if response is not None and response.model else self.model,
            self._provider_class,
        )
        # A request that raised after billing carries what it used on the
        # exception (`ReasoningBudgetExhausted`: two attempts' tokens, #343),
        # and the limit it was given where the output was spent against it.
        if response is not None:
            input_tokens, output_tokens = response.input_tokens, response.output_tokens
            replied, limit = True, max_tokens
        else:
            input_tokens = getattr(billed, "input_tokens", None)
            output_tokens = getattr(billed, "output_tokens", None)
            spent_against = getattr(billed, "max_tokens", None)
            replied = output_tokens is not None or spent_against is not None
            limit = spent_against if spent_against is not None else max_tokens
        priced = spend.cost_of_response(
            rates=rates,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_tokens=spend.input_tokens_of(system, prompt)
            + -(-extra_chars // spend.CHARS_PER_TOKEN),
            max_tokens=limit,
            replied=replied,
        )
        if priced is None:
            # No price at all: what is known is the last attempt's part.
            meter.add(last_cost, whole=False)
            return
        if last_cost is not None and last_cost > priced:
            priced = last_cost  # the known part is never counted as less
        meter.add(priced, estimated=not rates.free)

    def spend_meter(self):
        """The running total of the run in force, or None when no ceiling is
        set. For a harness that swallows provider failures on purpose and so
        cannot learn of a stop from the exception (`evals`, #125)."""
        return self._spend_meter()

    def _admit(self) -> None:
        """Refuse a call that would break a spend ceiling, then one that may
        not go to this provider."""
        self._admit_spend()
        self._admit_boundary()

    def _admit_spend(self) -> None:
        """Under a ceiling (#125): refuse a hosted model that can't be priced,
        and make no further call once the run's total has passed it."""
        from . import spend

        meter = self._spend_meter()
        if meter is None:
            return
        spend.refuse_unpriced(spend.rates_for(self.model, self._provider_class), meter.ceiling)
        try:
            meter.check()
        except spend.SpendStopped as stopped:
            self._record_stop(stopped)
            raise

    def _record_stop(self, stopped) -> None:
        """Write the stop into the ledger file (not into the run's rows: it
        is not a request)."""
        if not self._recording:
            return
        scope = current_scope()
        append(
            CallRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                provider=self._provider_name,
                provider_class=self._provider_class,
                model=self.model,
                subject=scope.subject if scope else None,
                site=scope.site if scope else None,
                content_class=scope.content_class if scope else None,
                cost_usd=round(stopped.spent, 6),
                stop_reason=SPEND_STOP,
                error=str(stopped),
            ),
            self._path,
        )

    def _admit_boundary(self) -> None:
        """Refuse before any byte leaves, if what this call sends may not go
        to this provider (#327, CR-01/02/03/10).

        THE decision for every language-model call: a call site can't forget
        it, because every provider `get_provider` builds comes through here.
        Read from the scope (`scope_content`): the class a caller named, or
        the organization's own material. A command's own earlier check
        (`boundary.enforce`) may still refuse first, with its own message;
        the two agree, because both ask `boundary.check`.
        """
        if self._classification is None:
            return
        from .boundary import ENFORCE_ADVICE, BoundaryViolation, check, refusal_advice

        decision = check(scope_content(), self._classification, self._config)
        if not decision.allowed:
            raise BoundaryViolation(
                "Refusing to send this to the configured model.\n"
                f"{decision.explain()}\n"
                + refusal_advice(decision, self._config, otherwise=ENFORCE_ADVICE)
            )

    @property
    def inner(self) -> LLMProvider:
        """The provider underneath, for callers that need the real thing."""
        return self._inner

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "?")

    def __getattr__(self, name: str):
        """Anything not defined here comes from the provider inside.

        Wrapping every provider in `get_provider` made this necessary rather
        than merely tidy. `_Metered.summary()` in `scripts/eval_zardoz.py`
        reaches for `escalations` and `primary_calls` to report how often a
        cascade needed its stronger half, and an opaque wrapper would have
        made a working cascade indistinguishable from a dead one again —
        the exact regression that counter was added to fix.

        **It does not reach anything `LLMProvider` already defines.** Python
        consults `__getattr__` only after normal lookup fails, and every
        `supports_*` flag exists on the base class as `return False`. So a
        flag this class does not override answers False for every provider,
        whatever the one inside would say — which is how native citations,
        effort, prompt caching and batching all went dark on Anthropic and
        Vertex for two days while every test passed. Each flag is therefore
        delegated explicitly below, and `tests/test_llm_ledger.py` discovers
        the flags from the base class rather than from a list here, so a new
        one cannot be added without the wrapper learning to forward it.
        """
        # Only reached for attributes this class does not define, so the
        # recursion guard is on the one attribute that could be missing
        # during unpickling before __init__ has run.
        if name.startswith("__") or name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    def _record(
        self,
        *,
        system: str,
        prompt: str,
        response: LLMResponse | None,
        error: str | None = None,
        billed: BaseException | None = None,
        max_tokens: int | None = None,
        extra_chars: int = 0,
    ) -> None:
        # A call that raised after billing (a re-send that came back empty
        # again, `ReasoningBudgetExhausted`) carries what was billed on the
        # exception: the summed cost and the last attempt's id and tokens,
        # which would otherwise reach no row at all (#343).
        def _billed(name: str):
            return getattr(billed, name, None) if billed is not None else None

        cost = response.cost_usd if response else _billed("cost_usd")
        last_cost = response.last_cost_usd if response else _billed("last_cost_usd")
        # The running total reads the cost whether or not the ledger is on:
        # a ceiling that stopped holding when recording was switched off
        # would be a setting that quietly stops applying (#125).
        meter = self._spend_meter()
        if meter is not None:
            self._meter_cost(
                meter, system, prompt, response, billed, cost, last_cost, max_tokens, extra_chars
            )
        if not self._recording:
            return
        scope = current_scope()
        record = CallRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            provider=self._provider_name,
            provider_class=self._provider_class,
            # The model that *answered*, not the one configured. A cascade
            # that escalated wrote this document with its stronger half, and
            # a record naming the cheap one would be false in exactly the
            # situation the record exists for.
            model=(response.model if response is not None else self.model),
            subject=scope.subject if scope else None,
            site=scope.site if scope else None,
            content_class=scope.content_class if scope else None,
            input_tokens=response.input_tokens if response else _billed("input_tokens"),
            output_tokens=response.output_tokens if response else _billed("output_tokens"),
            cost_usd=cost,
            cost_source=response.cost_source if response else _billed("cost_source"),
            stop_reason=response.stop_reason if response else None,
            cached_input_tokens=response.cached_input_tokens if response else None,
            hidden_output_tokens=response.hidden_output_tokens if response else None,
            stripped_reasoning_chars=(response.stripped_reasoning_chars if response else None),
            request_id=response.request_id if response else _billed("request_id"),
            endpoint=response.endpoint if response else None,
            prompt_sha=prompt_digest(system, prompt),
            error=error,
            escalations=escalation.take(),
            last_cost_usd=last_cost if cost is None else None,
            licensed_agreement=(
                self._agreement if scope is not None and scope.content_class == LICENSED else None
            ),
        )
        append(record, self._path)
        if scope is not None:
            scope.records.append(record)
        collecting = _run.get()
        if collecting is not None:
            collecting.append(record)

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        **kwargs,
    ) -> LLMResponse:
        # `**kwargs` carries `effort`, `cache` and `cache_prefix`, which
        # `llm/effort.py` passes only after asking the flags above. With the
        # flags forwarded, a fixed signature here would turn every such call
        # into a TypeError — the flags and the arguments travel together.
        self._admit()
        try:
            response = self._inner.generate(
                system=system,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
        except Exception as exc:
            self._tag_http(exc)
            # Recorded before re-raising. The request reached the vendor and
            # was billed, and the content in it was exposed whether or not a
            # reply came back — which is the half a spend-only meter misses.
            self._record(
                system=system,
                prompt=prompt,
                response=None,
                error=type(exc).__name__,
                billed=exc,
                max_tokens=max_tokens,
            )
            raise
        self._record(system=system, prompt=prompt, response=response, max_tokens=max_tokens)
        return response

    def generate_batch(self, requests, **kwargs) -> dict:
        """A batch is recorded too, one entry per request.

        Without this, `__getattr__` would have found the inner provider's
        method and several hundred narratives would have reached a vendor
        with nothing in the ledger to say so — which is the one thing the
        wrapper exists to make impossible.
        """
        self._admit()
        try:
            answers = self._inner.generate_batch(requests, **kwargs)
        except Exception as exc:
            self._tag_http(exc)
            # Submitted and billed or not, the content left this machine.
            for request in requests:
                self._record(
                    system=request.system,
                    prompt=request.prompt,
                    response=None,
                    error=type(exc).__name__,
                    max_tokens=getattr(request, "max_tokens", None),
                )
            raise
        scope = current_scope()
        for request in requests:
            # Attributed to the request's own id, not to the submission. The
            # per-call scope is what answers "which controls did that model
            # write narratives for", and a batch would otherwise file several
            # hundred narratives under one subject.
            with about(
                request.custom_id,
                site=scope.site if scope else None,
                content_class=scope.content_class if scope else None,
            ):
                self._record(
                    system=request.system,
                    prompt=request.prompt,
                    response=answers.get(request.custom_id),
                    max_tokens=getattr(request, "max_tokens", None),
                )
        return answers

    # ---- capability flags, each delegated by hand -----------------------
    #
    # See `__getattr__`: a flag left out here is answered by the base class,
    # not by the provider inside, and the answer is always False. Asked
    # through `getattr`, as `llm/effort.py` asks, because providers are
    # duck-typed: the fakes in the test suite implement `generate` and
    # little else, and one that never heard of a flag does not support it.

    def _inner_supports(self, flag: str) -> bool:
        ask = getattr(self._inner, flag, None)
        return bool(ask and ask())

    def supports_schema(self) -> bool:
        return self._inner_supports("supports_schema")

    def supports_effort(self) -> bool:
        return self._inner_supports("supports_effort")

    def supports_caching(self) -> bool:
        return self._inner_supports("supports_caching")

    def supports_batch(self) -> bool:
        return self._inner_supports("supports_batch")

    def supports_grounding(self) -> bool:
        return self._inner_supports("supports_grounding")

    def generate_json(self, *, system: str, prompt: str, schema: dict, **kwargs) -> LLMResponse:
        self._admit()
        try:
            response = self._inner.generate_json(
                system=system, prompt=prompt, schema=schema, **kwargs
            )
        except Exception as exc:
            self._tag_http(exc)
            self._record(
                system=system,
                prompt=prompt,
                response=None,
                error=type(exc).__name__,
                billed=exc,
                max_tokens=kwargs.get("max_tokens"),
                extra_chars=len(str(schema)),
            )
            raise
        self._record(
            system=system,
            prompt=prompt,
            response=response,
            max_tokens=kwargs.get("max_tokens"),
            extra_chars=len(str(schema)),
        )
        return response

    def generate_grounded(
        self, *, system: str, prompt: str, documents: list, **kwargs
    ) -> LLMResponse:
        """Recorded like `generate_json`.

        The passages go to the vendor as document blocks rather than inside
        the prompt, so they are exposed exactly as a prompt would be — and
        the digest covers only `system` and `prompt`, because hashing the
        documents would make the record depend on the retrieved text
        without saying anything more about which call this was.
        """
        self._admit()
        try:
            response = self._inner.generate_grounded(
                system=system, prompt=prompt, documents=documents, **kwargs
            )
        except Exception as exc:
            self._tag_http(exc)
            self._record(
                system=system,
                prompt=prompt,
                response=None,
                error=type(exc).__name__,
                billed=exc,
                max_tokens=kwargs.get("max_tokens"),
                extra_chars=len(str(documents)),
            )
            raise
        self._record(
            system=system,
            prompt=prompt,
            response=response,
            max_tokens=kwargs.get("max_tokens"),
            extra_chars=len(str(documents)),
        )
        return response

    def check(self) -> bool:
        """Not recorded.

        `llm-check` sends a fixed two-word probe and is run to find out
        whether a key works. Filling the ledger with those would bury the
        calls that saw real content under the ones that saw none.
        """
        return self._inner.check()


def wrap(provider: LLMProvider, config: dict) -> LLMProvider:
    """Wrap `provider` so every call is decided against the boundary, and
    recorded in the ledger unless config turned that off.

    Always wrapped (#327). This returned the bare provider when the ledger
    was off, and the boundary decision now lives in the wrapper, so turning
    off the record would have turned off the check with it.
    """
    llm = config.get("llm") or {}
    classified = classify_provider(llm)
    return RecordingProvider(
        provider,
        provider_name=str(llm.get("provider", "?")),
        provider_class=classified.klass,
        path=ledger_path(config),
        agreement=_agreement_relied_on(classified, config),
        classification=classified,
        config=config,
        recording=ledger_enabled(config),
    )


def _agreement_relied_on(classified, config: dict) -> dict | None:
    """The agreement a licensed call through this provider depends on: set
    only when the provider is OUTSIDE the licensed ceiling and the declared
    agreement covers it, so a row never claims an agreement it did not need
    (a local model's row carries none)."""
    agreement = licensed_agreement(config)
    if agreement is None:
        return None
    ceiling = ceilings(config)[LICENSED]
    if _exposure(classified.klass) <= _exposure(ceiling):
        return None
    return agreement.as_record() if agreement_allows(classified, ceiling, agreement) else None


# ---- reading it back ------------------------------------------------------


@dataclass
class Totals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: Summed like the others, but `hidden_reported` says whether any call
    #: in the group could report it. A zero here with nothing reporting
    #: means "unknown", not "none".
    hidden_output_tokens: int = 0
    hidden_reported: bool = False
    errors: int = 0
    #: The group's rows, so its cost is `cost_parts` over the whole group:
    #: an escalation repeating another row's attempt (`effort`) is only seen
    #: as a repeat with that row in view. Until #379 the cost was one sum
    #: over whichever calls were priced, so one unpriced call left the rest
    #: standing as the group's whole cost.
    records: list = field(default_factory=list, repr=False)

    def add(self, record: CallRecord) -> None:
        self.calls += 1
        self.input_tokens += record.input_tokens or 0
        self.output_tokens += record.output_tokens or 0
        if record.hidden_output_tokens is not None:
            self.hidden_output_tokens += record.hidden_output_tokens
            self.hidden_reported = True
        self.records.append(record)
        if record.error:
            self.errors += 1

    @property
    def cost_known_usd(self) -> float:
        return cost_parts(self.records)[0]

    @property
    def calls_unpriced(self) -> int:
        """Billed requests in the group whose cost is unknown (`cost_parts`)."""
        return cost_parts(self.records)[1]

    @property
    def cost_usd(self) -> float | None:
        """The group's cost, only when every call's is known."""
        return self.cost_known_usd if self.calls and not self.calls_unpriced else None

    @property
    def cost(self) -> str:
        split = cost_split(self.records)
        if self.cost_usd is not None:
            return describe_cost(split)
        if not self.cost_known_usd:
            return f"unpriced ({self.calls_unpriced})"
        return _then(split, f" + {self.calls_unpriced} unpriced")


def summarize(records: list[CallRecord], by: str) -> dict[str, Totals]:
    """Group records by one of their fields and total each group."""
    grouped: dict[str, Totals] = {}
    for record in records:
        key = getattr(record, by, None) or "(unattributed)"
        grouped.setdefault(str(key), Totals()).add(record)
    return grouped


def format_summary(records: list[CallRecord], by: str) -> str:
    grouped = summarize(records, by)
    if not grouped:
        return "No model calls recorded."
    width = max(len(key) for key in grouped)
    order = sorted(grouped.items(), key=lambda kv: (-kv[1].calls, kv[0]))
    lines = []
    for key, totals in order:
        line = (
            f"  {key.ljust(width)}  {totals.calls:>5} call(s)  "
            f"{totals.input_tokens:>9} in  {totals.output_tokens:>8} out"
            # Printed only where a provider reported it. A column of zeros
            # against providers that cannot tell would be the false-zero
            # this field exists to avoid.
            + (f"  {totals.hidden_output_tokens:>8} hidden" if totals.hidden_reported else "")
            + f"  {totals.cost:>10}"
        )
        if totals.errors:
            line += f"  ({totals.errors} failed)"
        lines.append(line)
    return "\n".join(lines)
