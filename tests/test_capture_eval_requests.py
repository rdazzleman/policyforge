"""The request capture runs the harness for real and sends nothing.

`scripts/capture_eval_requests.py` exists so a change to what the harness
sends is checked by comparing requests rather than by comparing pass rates.
These tests run it against a stubbed `litellm`, which is also how CI, with
no `litellm` extra, can exercise it: the harness builds its LiteLLM provider,
routes a real case, and every request lands in the record instead of at a
vendor.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from scripts import capture_eval_requests as capture_module

MODEL = "openrouter/deepseek/deepseek-v4-flash"


@pytest.fixture
def litellm_stub(monkeypatch):
    """What `LiteLLMProvider` reads from the module, and nothing else."""
    stub = types.ModuleType("litellm")
    stub.completion = lambda **kwargs: None
    stub.BadRequestError = type("BadRequestError", (Exception,), {})
    stub.supports_response_schema = lambda model: True
    stub.suppress_debug_info = False
    monkeypatch.setitem(sys.modules, "litellm", stub)
    return stub


def test_a_capture_records_the_harness_requests_and_reaches_nothing(litellm_stub, monkeypatch):
    sent_for_real = []
    litellm_stub.completion = lambda **kwargs: sent_for_real.append(kwargs)

    recorded = capture_module.capture(
        ["--model", MODEL, "--suite", "routing", "--limit", "1", "--repeat", "1"]
    )

    assert sent_for_real == []
    # The reachability probe and at least one call for the case.
    assert len(recorded) >= 2
    assert all(request["model"] == MODEL for request in recorded)
    assert all("messages" in request for request in recorded)
    assert all("api_key" not in request for request in recorded)
    assert all(request["_has_api_key"] is False for request in recorded)
    # The recorder is gone afterwards.
    assert litellm_stub.completion is not capture_module.capture


def test_a_capture_is_json_and_argv_is_restored(litellm_stub, tmp_path, monkeypatch):
    before = list(sys.argv)
    out = tmp_path / "before.json"
    monkeypatch.setattr(
        sys, "argv", ["capture", str(out), "--model", MODEL, "--suite", "routing", "--limit", "1"]
    )

    assert capture_module.main() == 0

    recorded = json.loads(out.read_text(encoding="utf-8"))
    assert recorded and recorded[0]["model"] == MODEL
    monkeypatch.undo()
    assert sys.argv == before


def test_compare_names_the_first_difference():
    a = [{"model": "m", "temperature": 0.0}, {"model": "m", "max_tokens": 800}]
    b = [{"model": "m", "temperature": 0.0}, {"model": "m", "max_tokens": 900}]

    assert capture_module.compare(a, a) is None
    assert capture_module.compare(a, b) == "request 1, key 'max_tokens': before=800 after=900"
    assert capture_module.compare(a, a[:1]) == "2 request(s) before, 1 after"


def test_compare_exits_non_zero_on_a_difference(tmp_path, monkeypatch, capsys):
    same = tmp_path / "a.json"
    other = tmp_path / "b.json"
    same.write_text(json.dumps([{"model": "m"}]), encoding="utf-8")
    other.write_text(json.dumps([{"model": "n"}]), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["capture", "--compare", str(same), str(same)])
    assert capture_module.main() == 0
    monkeypatch.setattr(sys, "argv", ["capture", "--compare", str(same), str(other)])
    assert capture_module.main() == 1
    assert "DIFFERENT" in capsys.readouterr().out


def test_a_capture_without_a_model_is_refused(monkeypatch, tmp_path):
    """The recorder only stands in for LiteLLM; a configured Anthropic
    provider would reach the vendor for real, so the run is refused."""
    monkeypatch.setattr(sys, "argv", ["capture", str(tmp_path / "x.json"), "--suite", "routing"])

    assert capture_module.main() == 2
