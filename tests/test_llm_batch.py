"""A-04's second half — many requests submitted together, matched back by id.

`ssp` drafts one narrative per control, several hundred in a run, and nobody
watches it happen. That is the Batch API's case exactly, at half the price.

The test that matters most is the one about order. Batch results come back in
whatever order the API finished them, so a run that matched them to controls
by position would attribute one control's narrative to another — and the
workbook would look entirely plausible, cell for cell, while being wrong
about which control says what.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from policyforge.llm.batch import BatchError, BatchRequest


@dataclass
class FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 4
    cache_read_input_tokens: int | None = None


@dataclass
class FakeBlock:
    text: str
    type: str = "text"


@dataclass
class FakeMessage:
    content: list
    usage: FakeUsage = field(default_factory=FakeUsage)
    model: str = "claude-x"
    stop_reason: str = "end_turn"
    id: str = "msg_1"


@dataclass
class FakeResult:
    message: FakeMessage | None = None
    type: str = "succeeded"


@dataclass
class FakeEntry:
    custom_id: str
    result: FakeResult


@dataclass
class FakeBatch:
    id: str = "batch_1"
    processing_status: str = "ended"


class FakeBatches:
    """The three calls the batch path makes, and what it was asked to send."""

    def __init__(self, entries, *, statuses=None):
        self.entries = entries
        self.statuses = list(statuses or ["ended"])
        self.submitted: list = []
        self.polls = 0

    def create(self, *, requests):
        self.submitted = requests
        return FakeBatch()

    def retrieve(self, batch_id):
        self.polls += 1
        status = self.statuses[min(self.polls - 1, len(self.statuses) - 1)]
        return FakeBatch(id=batch_id, processing_status=status)

    def results(self, batch_id):
        return iter(self.entries)


class FakeClient:
    def __init__(self, entries, *, statuses=None):
        self.messages = type("M", (), {})()
        self.messages.batches = FakeBatches(entries, statuses=statuses)


def _answered(custom_id, text):
    return FakeEntry(custom_id, FakeResult(FakeMessage(content=[FakeBlock(text)])))


def _submit(client, requests, **kwargs):
    from policyforge.llm._anthropic_compat import submit_batch

    return submit_batch(client, requests, model="claude-x", sleep=lambda _: None, **kwargs)


def _requests(*ids):
    return [BatchRequest(custom_id=i, system="s", prompt=f"draft {i}") for i in ids]


# --------------------------------------------------------------------------
# Matching results to requests
# --------------------------------------------------------------------------


def test_results_are_keyed_by_the_id_that_went_out_not_by_position():
    """Returned in the reverse order, which the API is free to do."""
    client = FakeClient([_answered("AC-2", "second"), _answered("AC-1", "first")])

    answers = _submit(client, _requests("AC-1", "AC-2"))

    assert answers["AC-1"].text == "first"
    assert answers["AC-2"].text == "second"


def test_every_request_carries_its_id_and_its_parameters():
    client = FakeClient([_answered("AC-1", "x")])

    _submit(client, [BatchRequest(custom_id="AC-1", system="sys", prompt="p", max_tokens=900)])

    (sent,) = client.messages.batches.submitted
    assert sent["custom_id"] == "AC-1"
    assert sent["params"]["max_tokens"] == 900
    assert sent["params"]["system"] == "sys"


def test_a_cacheable_prefix_survives_into_the_batch_entry():
    """The organization block repeats across every control in the run, which
    is as true in a batch as it is one call at a time."""
    client = FakeClient([_answered("AC-1", "x")])

    _submit(client, [BatchRequest(custom_id="AC-1", system="s", prompt="p", cache_prefix="ORG\n")])

    blocks = client.messages.batches.submitted[0]["params"]["messages"][0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "".join(b["text"] for b in blocks) == "ORG\np"


def test_effort_survives_into_the_batch_entry():
    client = FakeClient([_answered("AC-1", "x")])

    _submit(client, [BatchRequest(custom_id="AC-1", system="s", prompt="p", effort="high")])

    assert client.messages.batches.submitted[0]["params"]["output_config"] == {"effort": "high"}


def test_the_response_facts_survive_the_batch():
    message = FakeMessage(
        content=[FakeBlock("hi")],
        stop_reason="max_tokens",
        usage=FakeUsage(cache_read_input_tokens=800),
    )
    client = FakeClient([FakeEntry("AC-1", FakeResult(message))])

    answer = _submit(client, _requests("AC-1"))["AC-1"]

    assert answer.stop_reason == "max_tokens"
    assert answer.cached_input_tokens == 800


# --------------------------------------------------------------------------
# Refusing rather than half-finishing
# --------------------------------------------------------------------------


def test_a_failed_entry_is_named_rather_than_silently_dropped():
    """A caller handed half a batch and no exception writes half a workbook
    and calls it done."""
    client = FakeClient([_answered("AC-1", "ok"), FakeEntry("AC-2", FakeResult(type="errored"))])

    with pytest.raises(BatchError, match="AC-2"):
        _submit(client, _requests("AC-1", "AC-2"))


def test_a_batch_that_never_ends_is_given_up_on():
    client = FakeClient([], statuses=["in_progress"])

    with pytest.raises(BatchError, match="still in_progress"):
        _submit(client, _requests("AC-1"), timeout_seconds=0)


def test_it_waits_for_a_batch_that_is_still_running():
    client = FakeClient([_answered("AC-1", "x")], statuses=["in_progress", "in_progress", "ended"])

    answers = _submit(client, _requests("AC-1"))

    assert client.messages.batches.polls == 3
    assert answers["AC-1"].text == "x"


def test_an_empty_batch_is_not_submitted_at_all():
    client = FakeClient([])

    assert _submit(client, []) == {}
    assert client.messages.batches.submitted == []


# --------------------------------------------------------------------------
# The ledger still sees every request
# --------------------------------------------------------------------------


def test_a_batch_is_recorded_one_entry_per_request(tmp_path):
    """`__getattr__` would have found the inner provider's method and sent
    several hundred narratives with nothing in the ledger to say so."""
    from policyforge.llm import ledger
    from policyforge.llm.base import LLMResponse

    class Batching:
        def generate(self, **kwargs):
            raise AssertionError("the batch path should not call generate")

        def supports_batch(self):
            return True

        def generate_batch(self, requests, **kwargs):
            return {r.custom_id: LLMResponse(text="n", model="m") for r in requests}

        def check(self):
            return True

    path = tmp_path / "calls.jsonl"
    wrapped = ledger.wrap(Batching(), {"llm": {"ledger": {"path": str(path)}}})
    with ledger.about("ssp/run", site="ssp", content_class="organization-internal"):
        wrapped.generate_batch(_requests("AC-1", "AC-2"))

    records = ledger.load(path)
    # Attributed per control, not per submission: "which controls did that
    # model write narratives for" is the question a baseline of several
    # hundred makes somebody ask.
    assert sorted(r.subject for r in records) == ["AC-1", "AC-2"]
    assert all(r.site == "ssp" and r.content_class == "organization-internal" for r in records)


def test_a_batch_that_raised_is_recorded_because_it_was_sent(tmp_path):
    from policyforge.llm import ledger

    class Failing:
        def generate_batch(self, requests, **kwargs):
            raise RuntimeError("submission refused")

        def check(self):
            return True

    path = tmp_path / "calls.jsonl"
    wrapped = ledger.wrap(Failing(), {"llm": {"ledger": {"path": str(path)}}})
    with pytest.raises(RuntimeError):
        wrapped.generate_batch(_requests("AC-1"))

    (record,) = ledger.load(path)
    assert record.error == "RuntimeError"


def _ssp_fixtures(*control_ids):
    from policyforge.generate.policy_writer import OrgContext
    from policyforge.ingest.schema import Control
    from policyforge.ssp.narrative import SystemProfile

    controls = [
        Control(
            control_id=cid,
            title=f"Control {cid}",
            framework="NIST 800-53",
            framework_version="Rev 5",
            control_statement=f"Do the thing {cid} requires.",
        )
        for cid in control_ids
    ]
    org = OrgContext(name="Acme Health", industry="Healthcare", vendors=["Okta"])
    system = SystemProfile(name="Acme Health Platform", identifier="AHP-001")
    return controls, org, system


def test_narratives_are_matched_to_controls_by_id_not_by_order():
    """The failure this prevents is invisible in the finished workbook:
    every cell filled, each describing the wrong control."""
    from policyforge.llm.base import LLMResponse
    from policyforge.ssp.narrative import draft_narratives

    controls, org, system = _ssp_fixtures("AC-1", "AC-2", "AU-6")

    class Reversing:
        def supports_batch(self):
            return True

        def generate_batch(self, requests, **kwargs):
            # Finished in the opposite order, which the API is free to do.
            return {
                request.custom_id: LLMResponse(text=f"narrative for {request.custom_id}", model="m")
                for request in reversed(requests)
            }

    drafted = draft_narratives(controls, org, system, Reversing(), batch=True)

    for control in controls:
        assert control.control_id in drafted[control.control_id]


def test_the_batch_carries_the_same_split_prompt_as_one_call_at_a_time():
    from policyforge.llm.base import LLMResponse
    from policyforge.ssp.narrative import draft_narratives

    controls, org, system = _ssp_fixtures("AC-1")
    seen = []

    class Capturing:
        def supports_batch(self):
            return True

        def generate_batch(self, requests, **kwargs):
            seen.extend(requests)
            return {r.custom_id: LLMResponse(text="drafted", model="m") for r in requests}

    draft_narratives(controls, org, system, Capturing(), batch=True)

    (request,) = seen
    assert "Acme Health" in request.cache_prefix, "the repeating half is the org block"
    assert "AC-1" in request.prompt, "the varying half is the control"
    assert request.effort == "high"


def test_asking_for_a_batch_a_provider_cannot_do_says_so(tmp_path):
    from policyforge.ssp.narrative import draft_narratives

    controls, org, system = _ssp_fixtures("AC-1")

    class Plain:
        def generate(self, **kwargs):
            raise AssertionError("no fallback")

    with pytest.raises(BatchError, match="--batch"):
        draft_narratives(controls, org, system, Plain(), batch=True)


def test_a_provider_that_cannot_batch_refuses_rather_than_falling_back():
    """Somebody who asked for a batch asked for its price; quietly spending
    twice that is not a smaller failure than stopping."""
    from policyforge.llm.base import LLMProvider

    class Plain(LLMProvider):
        def generate(self, **kwargs):
            raise AssertionError("no fallback")

        def check(self):
            return True

    assert Plain().supports_batch() is False
    with pytest.raises(NotImplementedError, match="supports_batch"):
        Plain().generate_batch(_requests("AC-1"))
