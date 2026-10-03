"""The semgrep step refuses what semgrep did not check (#43).

Reports are shaped like semgrep 1.177.0's `--json` output as measured on this
repository (`errors[].type`, `paths.scanned`, Windows backslash paths), not
built from `semgrep_gate`'s own reading of them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import semgrep_gate as sg  # noqa: E402

SMALL = {"src/a.py": 100, "Dockerfile": 900, "tests/test_a.py": 200}


def _report(scanned=("src\\a.py", "Dockerfile"), errors=(), results=()):
    return {
        "version": "1.177.0",
        "paths": {"scanned": list(scanned)},
        "errors": list(errors),
        "results": list(results),
    }


def _timeout(path="scripts\\release.py"):
    rule = "python.lang.security.audit.dangerous-subprocess-use-tainted-env-args"
    return {"level": "warn", "type": "Timeout", "path": path, "rule_id": rule}


def _partial(path):
    return {"level": "warn", "type": ["PartialParsing", [{"path": path}]], "path": path}


def test_a_clean_report_passes():
    """What it must ALLOW. `tests/` isn't scanned (semgrep's default ignore),
    and that is scope, not a skip."""
    failures, notes = sg.classify(_report(), SMALL)
    assert failures == [] and notes == []


def test_a_rule_timeout_fails_naming_the_rule_and_the_file():
    failures, _ = sg.classify(_report(errors=[_timeout()]), SMALL)
    assert len(failures) == 1
    assert "TIMEOUT" in failures[0] and "tainted-env-args" in failures[0]
    assert "scripts/release.py" in failures[0]


def test_a_listed_partial_parse_is_printed_not_failed():
    failures, notes = sg.classify(_report(errors=[_partial(".devcontainer\\Dockerfile")]), SMALL)
    assert failures == []
    why = sg.ALLOWED_PARTIAL[".devcontainer/Dockerfile"]
    assert notes == [f"partially scanned (allowed): .devcontainer/Dockerfile: {why}"]


def test_an_unlisted_partial_parse_fails():
    failures, _ = sg.classify(_report(errors=[_partial("src\\a.py")]), SMALL)
    assert failures and "PARTIAL PARSE  src/a.py" in failures[0]


def test_an_error_of_a_kind_it_does_not_know_fails():
    error = {"level": "warn", "type": "SomethingNew", "path": "src/a.py", "message": "?"}
    failures, _ = sg.classify(_report(errors=[error]), SMALL)
    assert failures and "SomethingNew" in failures[0]


def test_a_finding_fails():
    result = {"check_id": "python.lang.x", "path": "src\\a.py", "start": {"line": 7}}
    failures, _ = sg.classify(_report(results=[result]), SMALL)
    assert failures == ["FINDING  python.lang.x  src/a.py:7"]


def test_an_oversized_tracked_file_semgrep_did_not_scan_fails():
    """The size skip is absent from the JSON report (measured), so it is
    derived from the tracked files' sizes."""
    sizes = {**SMALL, "data/big/controls.json": sg.MAX_TARGET_BYTES + 1}
    failures, _ = sg.classify(_report(), sizes)
    assert failures and "TOO LARGE  data/big/controls.json" in failures[0]


def test_an_oversized_file_semgrep_did_scan_passes():
    sizes = {**SMALL, "data/big/controls.json": sg.MAX_TARGET_BYTES + 1}
    report = _report(scanned=("src\\a.py", "Dockerfile", "data\\big\\controls.json"))
    assert sg.classify(report, sizes)[0] == []


def test_a_run_that_scanned_nothing_fails():
    failures, _ = sg.classify(_report(scanned=()), SMALL)
    assert any("ZERO" in f for f in failures)


def test_the_limits_are_the_measured_ones():
    """Written from the measurement, not read back from the module: 5 s timed
    out on release.py, 30 s did not; 1 MB skipped the 800-53 catalog."""
    assert sg.TIMEOUT_SECONDS == 30
    assert sg.MAX_TARGET_BYTES >= 5_000_000


# --- through main(), the path check.py and CI take ---------------------------


def _fake_semgrep(monkeypatch, report, returncode=0):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if report is not None:
            out = Path(cmd[cmd.index("--output") + 1])
            out.write_text(json.dumps(report), encoding="utf-8")
        return sg.subprocess.CompletedProcess(cmd, returncode)

    monkeypatch.setattr(sg.subprocess, "run", run)
    monkeypatch.setattr(sg, "tracked_sizes", lambda root: SMALL)
    return calls


def test_main_passes_a_clean_run_with_the_measured_flags(monkeypatch, capsys):
    calls = _fake_semgrep(monkeypatch, _report())
    assert sg.main(["--semgrep", "semgrep"]) == 0
    cmd = calls[0]
    assert "--timeout=30" in cmd and f"--max-target-bytes={sg.MAX_TARGET_BYTES}" in cmd
    assert "--json" in cmd


def test_main_fails_a_timeout(monkeypatch, capsys):
    _fake_semgrep(monkeypatch, _report(errors=[_timeout()]))
    assert sg.main(["--semgrep", "semgrep"]) == 1
    assert "TIMEOUT" in capsys.readouterr().out


def test_main_exits_2_when_semgrep_wrote_no_report(monkeypatch, capsys):
    _fake_semgrep(monkeypatch, None, returncode=2)
    assert sg.main(["--semgrep", "semgrep"]) == 2
    assert "COULD NOT READ" in capsys.readouterr().err


@pytest.mark.parametrize("path", sorted(sg.ALLOWED_PARTIAL))
def test_every_allowed_partial_parse_names_a_tracked_file(path):
    """An allow-list entry for a file that no longer exists would wave
    through a partial parse of whatever takes its name later."""
    assert (ROOT / path).is_file(), path
