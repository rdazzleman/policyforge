#!/usr/bin/env python3
"""Start an MCP server command and hold a real conversation with it.

    python scripts/mcp_smoke.py -- docker run -i --rm policyforge:local mcp
    python scripts/mcp_smoke.py -- policyforge mcp

Sends `initialize`, `tools/list` and `tools/call topics` over stdio as raw
JSON-RPC, and exits non-zero unless each gets a well-formed answer. Raw
JSON-RPC rather than the SDK's client, so the check does not depend on the
same library whose breakage it exists to catch.

`tests/test_mcp_server.py::test_the_server_answers_a_client_over_stdio` does
the same for the server in the test environment. This is for a server
somewhere else, most often the container image, where no test suite runs.
"""

from __future__ import annotations

import contextlib
import json
import queue
import subprocess
import sys
import threading

EXPECTED_TOOLS = {
    "ask_documents",
    "coverage",
    "team_bundle",
    "addresses",
    "parameters",
    "corpus",
    "topics",
}


class SmokeFailure(Exception):
    pass


def handshake(command: list[str], *, timeout: float = 120) -> list[str]:
    """Run the conversation. Returns the tool names; raises SmokeFailure."""
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: queue.Queue = queue.Queue()
    stderr: list[str] = []

    def pump_stdout() -> None:
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def pump_stderr() -> None:
        stderr.extend(proc.stderr)

    threading.Thread(target=pump_stdout, daemon=True).start()
    threading.Thread(target=pump_stderr, daemon=True).start()

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def reply(want_id: int) -> dict:
        while True:
            try:
                line = lines.get(timeout=timeout)
            except queue.Empty:
                raise SmokeFailure(f"no reply to request {want_id} within {timeout}s") from None
            if line is None:
                proc.wait(timeout=10)
                raise SmokeFailure(
                    f"server exited ({proc.returncode}) before replying to {want_id}:\n"
                    + "".join(stderr)
                )
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                raise SmokeFailure(f"non-JSON on the protocol channel: {line!r}") from None
            if message.get("id") == want_id:
                if "error" in message:
                    raise SmokeFailure(f"request {want_id} returned an error: {message['error']}")
                return message["result"]

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp_smoke", "version": "0"},
                },
            }
        )
        server = reply(1)["serverInfo"]
        if server.get("name") != "policyforge":
            raise SmokeFailure(f"unexpected server: {server}")

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in reply(2)["tools"]]
        if set(names) != EXPECTED_TOOLS:
            raise SmokeFailure(f"tools/list returned {names}, expected {sorted(EXPECTED_TOOLS)}")

        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "topics", "arguments": {}},
            }
        )
        result = reply(3)
        if result.get("isError") or not result.get("content", [{}])[0].get("text", "").strip():
            raise SmokeFailure(f"tools/call topics did not answer: {result}")
        return names
    finally:
        with contextlib.suppress(OSError):
            proc.stdin.close()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main(argv: list[str]) -> int:
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    if not argv:
        print(__doc__)
        return 2
    try:
        names = handshake(argv)
    except SmokeFailure as exc:
        print(f"MCP smoke FAILED: {exc}")
        return 1
    print(f"MCP smoke passed: initialize, tools/list ({len(names)} tools), tools/call topics")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
