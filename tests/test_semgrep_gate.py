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
    # The file that timed out under load scans in 15 to 18 s alone (#348).
    assert sg.RETRY_TIMEOUT_SECONDS == 300


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


# --- a timeout is retried once, on the timed-out files only (#348) ----------


def _scripted_semgrep(monkeypatch, *reports):
    """A fake semgrep that writes `reports` in order, one per call (None
    writes no report)."""
    calls = []
    queue = list(reports)

    def run(cmd, **kwargs):
        calls.append(cmd)
        report = queue.pop(0)
        if report is not None:
            out = Path(cmd[cmd.index("--output") + 1])
            out.write_text(json.dumps(report), encoding="utf-8")
        return sg.subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sg.subprocess, "run", run)
    monkeypatch.setattr(sg, "tracked_sizes", lambda root: SMALL)
    return calls


FIRST = _report(scanned=("src\\a.py", "Dockerfile", "scripts\\release.py"), errors=[_timeout()])


def test_a_timeout_the_retry_clears_passes_and_says_so(monkeypatch, capsys):
    """What it must ALLOW: a rule that timed out under load and finishes
    when given longer. Only that file is re-scanned, at the longer limit."""
    calls = _scripted_semgrep(monkeypatch, FIRST, _report(scanned=("scripts\\release.py",)))

    assert sg.main(["--semgrep", "semgrep"]) == 0
    assert len(calls) == 2
    assert calls[0][-1] == "." and "--timeout=30" in calls[0]
    assert calls[1][-1] == "scripts/release.py" and "--timeout=300" in calls[1]
    assert calls[1].count(".") == 0, "the retry scans the timed-out file, not the tree"
    out = capsys.readouterr().out
    assert "RETRIED  1 rule timeout(s) at 30 s on 1 file(s)" in out
    assert "0 timeout(s) remain (scripts/release.py)" in out


def test_a_rule_that_times_out_again_still_fails(monkeypatch, capsys):
    """The guarantee from #43 that this must keep: a rule that doesn't
    finish is never reported as checked."""
    retry = _report(scanned=("scripts\\release.py",), errors=[_timeout()])
    _scripted_semgrep(monkeypatch, FIRST, retry)

    assert sg.main(["--semgrep", "semgrep"]) == 1
    out = capsys.readouterr().out
    assert "1 timeout(s) remain" in out
    assert "TIMEOUT  python.lang.security.audit.dangerous-subprocess-use-tainted-env-args" in out
    assert "scripts/release.py: that rule was NOT checked there" in out


def test_a_finding_the_retry_makes_fails(monkeypatch, capsys):
    """The rule that timed out may find something once it finishes."""
    found = {"check_id": "python.lang.x", "path": "scripts\\release.py", "start": {"line": 9}}
    retry = _report(scanned=("scripts\\release.py",), results=[found])
    _scripted_semgrep(monkeypatch, FIRST, retry)

    assert sg.main(["--semgrep", "semgrep"]) == 1
    assert "FINDING  python.lang.x  scripts/release.py:9" in capsys.readouterr().out


def test_a_retried_file_the_retry_did_not_scan_fails(monkeypatch, capsys):
    _scripted_semgrep(monkeypatch, FIRST, _report(scanned=("src\\a.py",)))

    assert sg.main(["--semgrep", "semgrep"]) == 1
    assert "ERROR  NotRescanned on scripts/release.py" in capsys.readouterr().out


def test_a_retry_that_writes_no_report_exits_2(monkeypatch, capsys):
    _scripted_semgrep(monkeypatch, FIRST, None)

    assert sg.main(["--semgrep", "semgrep"]) == 2
    assert "COULD NOT READ semgrep's retry report" in capsys.readouterr().err


def test_a_run_with_no_timeout_scans_once(monkeypatch, capsys):
    calls = _scripted_semgrep(monkeypatch, _report())

    assert sg.main(["--semgrep", "semgrep"]) == 0
    assert len(calls) == 1
    assert "RETRIED" not in capsys.readouterr().out


def test_the_retry_replaces_only_the_retried_files_timeouts():
    """A timeout on a file the retry wasn't asked about stands, and a finding
    both runs made is counted once."""
    found = {"check_id": "python.lang.x", "path": "src\\a.py", "start": {"line": 3}}
    first = _report(
        errors=[_timeout(), _timeout("src\\a.py")],
        results=[found],
    )
    retry = _report(scanned=("scripts\\release.py",), results=[found])

    merged = sg.merge_retry(first, retry, ["scripts/release.py"])

    failures, _ = sg.classify(merged, SMALL)
    assert [f for f in failures if f.startswith("TIMEOUT")] == [
        "TIMEOUT  python.lang.security.audit.dangerous-subprocess-use-tainted-env-args "
        "did not finish on src/a.py: that rule was NOT checked there"
    ]
    assert [f for f in failures if f.startswith("FINDING")] == [
        "FINDING  python.lang.x  src/a.py:3"
    ]


@pytest.mark.parametrize("path", sorted(sg.ALLOWED_PARTIAL))
def test_every_allowed_partial_parse_names_a_tracked_file(path):
    """An allow-list entry for a file that no longer exists would wave
    through a partial parse of whatever takes its name later."""
    assert (ROOT / path).is_file(), path


# --- the local gate: the pinned image, over the tracked tree (#83) -----------


def _repo(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

    git("init", "-q")
    (repo / "kept.py").write_bytes(b"committed\n")
    (repo / "sub").mkdir()
    (repo / "sub" / "staged.py").write_text("staged\n", encoding="utf-8")
    git("add", "kept.py", "sub/staged.py")
    git("-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", "one")
    (repo / "kept.py").write_bytes(b"edited after the commit\n")
    (repo / "untracked.py").write_text("never added\n", encoding="utf-8")
    return repo


def test_the_tree_holds_the_tracked_files_as_they_are_on_disk(tmp_path):
    """What semgrep scans in the container: git's tracked set, read from the
    working tree (an uncommitted edit included, as a native scan would see
    it), and nothing untracked."""
    import io
    import tarfile

    tree = sg.tracked_tree(_repo(tmp_path))

    with tarfile.open(fileobj=io.BytesIO(tree)) as tar:
        names = sorted(tar.getnames())
        kept = tar.extractfile("kept.py").read()
    assert names == ["kept.py", "sub/staged.py"]
    assert kept == b"edited after the commit\n"


def _container_run(monkeypatch, *reports):
    """A fake `docker run`: it records the command and the stdin, and writes
    each report where the container would, under the scratch dir mounted at
    /out."""
    calls = []
    queue = list(reports)

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input")))
        mount = cmd[cmd.index("-v") + 1]
        scratch = mount.rsplit(":/out", 1)[0]
        name = cmd[cmd.index("--output") + 1].removeprefix("/out/")
        Path(scratch, name).write_text(json.dumps(queue.pop(0)), encoding="utf-8")
        return sg.subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sg.subprocess, "run", run)
    monkeypatch.setattr(sg, "tracked_sizes", lambda root: SMALL)
    monkeypatch.setattr(sg, "tracked_tree", lambda root: b"THE-TREE")
    return calls


def test_image_mode_runs_the_pinned_image_over_the_tree(monkeypatch, capsys):
    calls = _container_run(monkeypatch, _report())

    assert sg.main(["--image"]) == 0
    ((cmd, stdin),) = calls
    assert cmd[:5] == ["docker", "run", "--rm", "-i", "-v"]
    assert sg.SEMGREP_IMAGE in cmd, "the pinned reference, tag and digest"
    assert cmd[cmd.index(sg.SEMGREP_IMAGE) + 1 : cmd.index(sg.SEMGREP_IMAGE) + 3] == [
        "sh",
        "-c",
    ]
    assert sg.IN_CONTAINER in cmd
    assert "git add -A" in sg.IN_CONTAINER and "tar -xf -" in sg.IN_CONTAINER
    assert cmd[-1] == ".", "semgrep chooses the files itself, from the streamed tree"
    assert "--timeout=30" in cmd and stdin == b"THE-TREE"


def test_image_mode_retries_in_the_image_too(monkeypatch, capsys):
    """The retry (#348) is a second container run over the same tree, on the
    timed-out files only."""
    calls = _container_run(monkeypatch, FIRST, _report(scanned=("scripts/release.py",)))

    assert sg.main(["--image"]) == 0
    (_, first_in), (retry, retry_in) = calls
    assert sg.SEMGREP_IMAGE in retry and retry[-1] == "scripts/release.py"
    assert "--timeout=300" in retry and retry_in == first_in == b"THE-TREE"


def test_one_of_semgrep_or_image_is_required(capsys):
    with pytest.raises(SystemExit):
        sg.main([])
    with pytest.raises(SystemExit):
        sg.main(["--semgrep", "semgrep", "--image"])
