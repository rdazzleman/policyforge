#!/usr/bin/env python3
"""Record every request the eval harness would send, without sending one.

    m=openrouter/deepseek/deepseek-v4-flash
    python scripts/capture_eval_requests.py before.json --model $m --suite routing --limit 2
    git switch the-branch
    python scripts/capture_eval_requests.py after.json --model $m --suite routing --limit 2
    python scripts/capture_eval_requests.py --compare before.json after.json

Everything after the output file is passed to `scripts/eval_zardoz.py`
unchanged; `--model` is required, since only a LiteLLM-backed run is caught
by the recorder. `litellm.completion` is replaced with a recorder that answers a
canned reply, so the harness runs its real construction path and its real
prompts and nothing reaches a vendor — no key is read, and a key that is
set is stripped from the record. The recorded keyword arguments *are* the
request: model, messages, temperature, budget, `response_format` on the
schema route, `reasoning_effort` where the provider sends one.

Why it exists: the harness once built its provider by a different path from
production, and when the ledger wrapper silently dropped every capability
flag, the harness kept sending effort while the CLI did not. Five recorded
epochs described a request production never made, and a pass rate cannot
show that — two runs at the same score can differ in what they asked for.
Comparing two captures can. A change to the harness, the provider layer or
a prompt's call site is checked with a capture on `main` and one on the
branch: identical means the numbers stay comparable, and a difference is
either the point of the change or a finding.

Only LiteLLM-backed runs are captured, which is what `--model` builds and
what `MEASUREMENTS.md` was measured with. `--compare` prints the first
differing request and key, and exits non-zero on any difference.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5
    prompt_tokens_details = None


class _Message:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Message(content)
        self.finish_reason = "stop"


class CannedResponse:
    """The shape `LiteLLMProvider` reads off a litellm ModelResponse.

    `choices[0].message.content`, `choices[0].finish_reason`, `model`,
    `usage` and `_hidden_params`, and nothing else.
    """

    def __init__(self, content: str, model: str):
        self.choices = [_Choice(content)]
        self.model = model
        self.usage = _Usage()
        self._hidden_params = {"response_cost": 0.0001}


def capture(argv: list[str], *, reply: str = "documents") -> list[dict]:
    """Run `eval_zardoz.main()` with `argv` and return every request it made.

    The recorder is installed on the `litellm` module before the harness
    imports anything, and `LiteLLMProvider` binds `litellm.completion` at
    construction, which happens inside `main()` — so there is no window in
    which a real call could be made.
    """
    import litellm

    recorded: list[dict] = []

    def fake_completion(**kwargs):
        request = {key: value for key, value in kwargs.items() if key != "api_key"}
        request["_has_api_key"] = "api_key" in kwargs
        recorded.append(request)
        return CannedResponse(reply, kwargs.get("model", "?"))

    original = litellm.completion
    saved_argv = sys.argv
    litellm.completion = fake_completion
    sys.argv = ["eval_zardoz.py", *argv]
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from scripts import eval_zardoz

        # The harness exits non-zero when a case fails, and the canned reply
        # fails most cases; the exit code says nothing about the capture.
        with contextlib.suppress(SystemExit):
            eval_zardoz.main()
    finally:
        litellm.completion = original
        sys.argv = saved_argv
    return recorded


def compare(before: list[dict], after: list[dict]) -> str | None:
    """None when identical; otherwise one line naming the first difference."""
    if len(before) != len(after):
        return f"{len(before)} request(s) before, {len(after)} after"
    for index, (x, y) in enumerate(zip(before, after, strict=True)):
        for key in sorted(set(x) | set(y)):
            if x.get(key) != y.get(key):
                return f"request {index}, key {key!r}: before={x.get(key)!r} after={y.get(key)!r}"
    return None


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    if args[0] == "--compare":
        if len(args) != 3:
            print("usage: capture_eval_requests.py --compare before.json after.json")
            return 2
        before = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        after = json.loads(Path(args[2]).read_text(encoding="utf-8"))
        difference = compare(before, after)
        if difference is None:
            print(f"identical: {len(before)} request(s)")
            return 0
        print(f"DIFFERENT: {difference}")
        return 1

    out = Path(args[0])
    harness_args = args[1:]
    if "--model" not in harness_args:
        # The recorder replaces litellm.completion, so only a LiteLLM-backed
        # run is captured; a configured Anthropic provider would go to the
        # vendor for real. Refused rather than allowed to spend.
        print(
            "capture needs --model <litellm model string>; the configured provider is not captured."
        )
        return 2

    recorded = capture(harness_args)
    out.write_text(json.dumps(recorded, indent=2, default=str), encoding="utf-8")
    print(f"\n[capture] {len(recorded)} request(s) recorded -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
