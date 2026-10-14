"""The scheduled local eval (#261): $0 by construction, compared only like with
like, and quiet unless a score moves beyond a measured band.

Nothing here calls a model, fetches or touches Task Scheduler: the parts that
decide are pure functions, and the run is driven with its machine-facing
steps stubbed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from scripts import scheduled_eval as se

# --------------------------------------------------------------------------
# $0 by construction
# --------------------------------------------------------------------------

#: Written from the credentials this machine and this project actually use,
#: not from the module's pattern, so a narrowed pattern can't pass by agreeing
#: with itself.
_CREDENTIALS = {
    "ANTHROPIC_API_KEY": "x",
    "OPENROUTER_API_KEY": "x",
    "GEMINI_API_KEY": "x",
    "OPENAI_API_KEY": "x",
    "GITHUB_TOKEN": "x",
    "GH_TOKEN": "x",
    "AWS_SECRET_ACCESS_KEY": "x",
    "AWS_SESSION_TOKEN": "x",
    "GOOGLE_APPLICATION_CREDENTIALS": "x",
    "LITELLM_MASTER_KEY": "x",
}


def test_the_run_environment_carries_no_credential_and_keeps_what_a_run_needs():
    base = {**_CREDENTIALS, "PATH": "p", "SYSTEMROOT": "s", "TEMP": "t", "UNRELATED": "u"}

    env = se.run_env(base, {"POLICYFORGE_CONFIG": "c.yaml"})

    assert not set(env) & set(_CREDENTIALS)
    assert env == {"PATH": "p", "SYSTEMROOT": "s", "TEMP": "t", "POLICYFORGE_CONFIG": "c.yaml"}


@pytest.mark.parametrize("name", sorted(_CREDENTIALS))
def test_a_credential_passed_in_on_purpose_is_refused_not_dropped(name):
    with pytest.raises(ValueError, match=name):
        se.run_env({}, {name: "x"})


def test_the_config_pins_the_local_provider_and_the_model(tmp_path):
    from policyforge.llm.boundary import LOCAL, classify_provider

    config = yaml.safe_load(se.pinned_config("qwen3:14b-pf32", tmp_path / "l.jsonl"))

    assert config["llm"]["provider"] == "local"
    assert config["llm"]["model"] == "qwen3:14b-pf32"
    assert classify_provider(config["llm"]).klass == LOCAL
    assert set(config) == {"llm"}, "nothing else that could name a hosted provider"


def test_the_pinned_model_is_the_one_that_fits_on_the_gpu():
    """The user's direction, relayed by 80 (2026-10-04)."""
    assert se.PINNED_MODEL == "qwen3:14b-pf32"


# --------------------------------------------------------------------------
# What a run's scores are
# --------------------------------------------------------------------------


def _results(*cases: tuple[str, int, int, int]) -> dict:
    return {
        "cases": [
            {"suite": s, "name": f"{s}{i}", "passes": p, "runs": r, "errors": e}
            for i, (s, p, r, e) in enumerate(cases)
        ]
    }


def test_errored_runs_count_in_neither_half_of_the_rate():
    rates = se.suite_rates(_results(("routing", 2, 3, 1), ("routing", 3, 3, 0)))

    assert rates["routing"] == {"passes": 5, "graded": 5, "errors": 1, "rate": 1.0}


def test_a_suite_with_no_graded_run_has_no_rate_rather_than_zero():
    assert se.suite_rates(_results(("routing", 0, 3, 3)))["routing"]["rate"] is None


# --------------------------------------------------------------------------
# Comparing like with like
# --------------------------------------------------------------------------


def _record(sha="a" * 40, digest="d1", repeat=3, ident="r", **rates) -> dict:
    return {
        "id": ident,
        "sha": sha,
        "when": "20261005T000000Z",
        "model": {"model": "qwen3:14b-pf32", "digest": digest},
        "repeat": repeat,
        "seconds_per_run": 1.0,
        "suites": {s: {"passes": 0, "graded": 1, "errors": 0, "rate": r} for s, r in rates.items()},
    }


def _band(width: float, **basis) -> dict:
    record = _record(**basis)
    return {
        "suites": {"routing": width},
        "basis": {k: record[k] for k in ("sha", "model", "repeat")},
    }


def test_a_move_inside_the_band_is_not_reported_and_one_outside_is():
    """A move exactly the band's width is inside it: the band is the widest
    spread already seen at one SHA. Binary fractions, so the edge is exact."""
    band = _band(0.25)
    was = _record(routing=1.0)

    edge, _ = se.compare(was, _record(sha="b" * 40, routing=0.75), band)
    outside, _ = se.compare(was, _record(sha="b" * 40, routing=0.625), band)

    assert edge == []
    assert len(outside) == 1
    assert outside[0].startswith("routing: 100% -> 62%")
    assert "a" * 12 in outside[0] and "b" * 12 in outside[0]


def test_a_rise_beyond_the_band_is_reported_too():
    moves, _ = se.compare(_record(routing=0.5), _record(routing=0.9), _band(0.1))

    assert moves and "+40%" in moves[0]


@pytest.mark.parametrize(
    "changed", [{"digest": "d2"}, {"repeat": 5}], ids=["recreated model", "repeat"]
)
def test_runs_of_a_different_model_digest_or_repeat_are_never_compared(changed):
    moves, notes = se.compare(_record(routing=1.0), _record(routing=0.0, **changed), _band(0.0))

    assert moves == []
    assert notes and "not compared" in notes[0]


def test_without_a_band_nothing_is_compared():
    moves, notes = se.compare(_record(routing=1.0), _record(routing=0.0), None)

    assert moves == [] and "no noise band" in notes[0]


def test_a_band_measured_on_another_model_does_not_apply():
    moves, notes = se.compare(
        _record(digest="d2", routing=1.0), _record(digest="d2", routing=0.0), _band(0.0)
    )

    assert moves == [] and "no noise band" in notes[0]


# --------------------------------------------------------------------------
# The band is measured
# --------------------------------------------------------------------------


def test_the_band_is_the_widest_spread_at_one_sha():
    runs = [_record(routing=r, answering=a) for r, a in ((0.9, 1.0), (0.8, 1.0), (0.95, 0.9))]

    band = se.measure_band(runs)

    assert band["suites"]["routing"] == pytest.approx(0.15)
    assert band["suites"]["answering"] == pytest.approx(0.1)


def test_one_run_has_no_spread_to_measure():
    with pytest.raises(ValueError, match="two runs"):
        se.measure_band([_record(routing=1.0)])


@pytest.mark.parametrize("other", [{"sha": "b" * 40}, {"digest": "d2"}, {"repeat": 5}])
def test_a_band_is_measured_at_one_sha_model_and_repeat(other):
    with pytest.raises(ValueError, match="ONE sha"):
        se.measure_band([_record(routing=1.0), _record(routing=0.5, **other)])


def test_a_later_spread_wider_than_the_band_widens_it_and_says_so():
    band, lines = se.widened(_band(0.05), [_record(routing=0.9), _record(routing=0.7)])

    assert band["suites"]["routing"] == pytest.approx(0.2)
    assert lines and "widened" in lines[0]


def test_a_spread_inside_the_band_leaves_it_alone():
    band, lines = se.widened(_band(0.5), [_record(routing=0.9), _record(routing=0.7)])

    assert band["suites"]["routing"] == 0.5 and lines == []


# --------------------------------------------------------------------------
# The run, with the machine stubbed
# --------------------------------------------------------------------------


def _store(state: Path, record: dict) -> None:
    path = state / "runs" / record["id"] / "record.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def test_the_first_run_is_recorded_and_says_there_is_nothing_to_compare(tmp_path):
    record = _record(ident="1", routing=1.0)
    _store(tmp_path, record)

    assert se._judge(tmp_path, record) == 0
    assert "nothing to compare" in (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert not (tmp_path / "alerts").exists()


def test_a_move_beyond_the_band_writes_an_alert(tmp_path):
    (tmp_path / "band.json").write_text(json.dumps(_band(0.05)), encoding="utf-8")
    _store(tmp_path, _record(ident="1", routing=1.0))
    record = _record(ident="2", sha="b" * 40, routing=0.5)
    _store(tmp_path, record)

    assert se._judge(tmp_path, record) == 1
    (alert,) = (tmp_path / "alerts").iterdir()
    assert "routing: 100% -> 50%" in alert.read_text(encoding="utf-8")
    assert (tmp_path / "latest.md").read_text(encoding="utf-8").startswith("ALERT")


def test_a_quiet_run_writes_no_alert(tmp_path):
    (tmp_path / "band.json").write_text(json.dumps(_band(0.5)), encoding="utf-8")
    _store(tmp_path, _record(ident="1", routing=1.0))
    record = _record(ident="2", sha="b" * 40, routing=0.9)
    _store(tmp_path, record)

    assert se._judge(tmp_path, record) == 0
    assert not (tmp_path / "alerts").exists()
    assert "no move beyond the band" in (tmp_path / "latest.md").read_text(encoding="utf-8")


def test_errored_runs_are_alerted_even_when_no_score_moved(tmp_path):
    record = _record(ident="1", routing=1.0)
    record["suites"]["routing"]["errors"] = 2
    _store(tmp_path, record)

    assert se._judge(tmp_path, record) == 1
    assert "errored" in (tmp_path / "latest.md").read_text(encoding="utf-8")


def test_the_off_switch_runs_nothing(tmp_path, monkeypatch):
    (tmp_path / "OFF").write_text("", encoding="utf-8")
    monkeypatch.setattr(se, "_run", lambda *_: pytest.fail("ran while OFF"))

    assert se.main(["run", "--state", str(tmp_path)]) == 0


def test_a_run_that_fails_unattended_leaves_an_alert_and_releases_its_lock(tmp_path, monkeypatch):
    def broken(*_):
        raise ConnectionError("Ollama is not listening")

    monkeypatch.setattr(se, "_run", broken)

    assert se.main(["run", "--state", str(tmp_path)]) == 1
    assert "Ollama is not listening" in (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert not (tmp_path / "lock").exists()


def test_a_second_run_while_one_holds_the_lock_does_nothing(tmp_path, monkeypatch):
    import os

    (tmp_path / "lock").mkdir()
    (tmp_path / "lock" / "pid").write_text(str(os.getpid()), encoding="utf-8")  # alive
    monkeypatch.setattr(se, "_run", lambda *_: pytest.fail("ran over a held lock"))

    assert se.main(["run", "--state", str(tmp_path)]) == 0
    assert (tmp_path / "lock").exists(), "someone else's lock stays"


# --------------------------------------------------------------------------
# What the eval harness hands over
# --------------------------------------------------------------------------


def test_eval_zardoz_writes_per_case_counts_for_the_scheduler(monkeypatch, capsys, tmp_path):
    from evals.runner import CaseResult, Outcome, load_cases
    from scripts import eval_zardoz

    class Fake:
        model = "fake"

        def generate(self, **_):
            return "ok"

        def summary(self):
            return "0 model call(s); fake"

    def run_case(suite, case, *_a, **_k):
        return CaseResult(
            suite, case["name"], [Outcome(True), Outcome(False), Outcome(False, errored=True)]
        )

    cases = tmp_path / "cases.yaml"
    cases.write_text(yaml.safe_dump({"routing": load_cases()["routing"][:2]}), encoding="utf-8")
    out = tmp_path / "results.json"
    monkeypatch.setattr(eval_zardoz, "eval_config", lambda **_: {"llm": {"provider": "fake"}})
    monkeypatch.setattr(eval_zardoz, "build_provider", lambda _config: Fake())
    monkeypatch.setattr(eval_zardoz, "run_case", run_case)
    monkeypatch.setattr(
        sys, "argv", ["eval_zardoz.py", "--cases", str(cases), "--repeat", "3", "--json", str(out)]
    )

    eval_zardoz.main()

    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["repeat"] == 3 and record["requested"] == sorted(eval_zardoz.SUITES)
    assert [(c["suite"], c["passes"], c["runs"], c["errors"]) for c in record["cases"]] == [
        ("routing", 1, 3, 1),
        ("routing", 1, 3, 1),
    ]
    assert se.suite_rates(record)["routing"]["rate"] == pytest.approx(0.5)


def test_the_second_run_at_one_sha_sets_the_band_unattended(tmp_path):
    _store(tmp_path, _record(ident="1", routing=1.0))
    record = _record(ident="2", routing=0.75)
    _store(tmp_path, record)

    assert se._judge(tmp_path, record) == 0
    band = json.loads((tmp_path / "band.json").read_text(encoding="utf-8"))
    assert band["suites"] == {"routing": 0.25} and band["runs"] == ["1", "2"]
    assert "band set from 2 runs" in (tmp_path / "latest.md").read_text(encoding="utf-8")


def test_runs_at_two_different_shas_set_no_band(tmp_path):
    _store(tmp_path, _record(ident="1", routing=1.0))
    record = _record(ident="2", sha="b" * 40, routing=0.75)
    _store(tmp_path, record)

    assert se._judge(tmp_path, record) == 0
    assert not (tmp_path / "band.json").exists()
    assert "no noise band" in (tmp_path / "latest.md").read_text(encoding="utf-8")


def test_the_installed_copy_is_compared_with_the_tips(tmp_path):
    installed, tip = tmp_path / "installed.py", tmp_path / "tip.py"
    installed.write_bytes(b"one")

    assert se.scheduler_is_current(installed, tip) is None, "no such file at the tip"
    tip.write_bytes(b"one")
    assert se.scheduler_is_current(installed, tip) is True
    tip.write_bytes(b"two")
    assert se.scheduler_is_current(installed, tip) is False


def test_a_stale_installed_copy_is_said_in_latest(tmp_path):
    record = _record(ident="1", routing=1.0)
    record["scheduler_is_current"] = False
    _store(tmp_path, record)

    assert se._judge(tmp_path, record) == 0, "a note, not an alert"
    assert "re-run `install`" in (tmp_path / "latest.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# A run fits its interval, and a dead run's lock doesn't stop the schedule
# --------------------------------------------------------------------------


def test_a_run_is_sized_and_stopped_inside_the_interval():
    """The first scheduled run took over 4 h at --repeat 3 and timed out."""
    assert se.REPEAT == 1
    assert se.TIMEOUT < se.INTERVAL_HOURS * 3600


def test_the_installed_command_carries_the_repeat_and_the_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(se.subprocess, "run", lambda *a, **k: None)

    se.main(["install", "--state", str(tmp_path)])

    command = (tmp_path / "bin" / "run.cmd").read_text(encoding="utf-8")
    assert f"--repeat {se.REPEAT} " in command
    assert f"--timeout {se.TIMEOUT}" in command


def test_a_free_lock_is_taken_and_records_its_holder(tmp_path):
    import os

    assert se.take_lock(tmp_path / "lock") == ""
    assert (tmp_path / "lock" / "pid").read_text(encoding="utf-8") == str(os.getpid())


def test_a_lock_a_live_run_holds_is_left_alone(tmp_path):
    import os

    (tmp_path / "lock").mkdir()
    (tmp_path / "lock" / "pid").write_text(str(os.getpid()), encoding="utf-8")

    assert se.take_lock(tmp_path / "lock") is None


def _dead_pid() -> int:
    import subprocess

    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    assert not se.alive(done.pid), "premise: the process has exited"
    return done.pid


@pytest.mark.parametrize("holder", ["dead", "unrecorded"])
def test_a_dead_runs_lock_is_taken_over_and_said(tmp_path, holder):
    """A killed run can't release its lock; left in place, every later run
    skipped and exited 0, a silent stop (80 ended a run on 2026-10-05)."""
    import os

    (tmp_path / "lock").mkdir()
    if holder == "dead":
        (tmp_path / "lock" / "pid").write_text(str(_dead_pid()), encoding="utf-8")

    taken = se.take_lock(tmp_path / "lock")

    assert taken and "no longer running" in taken
    assert (tmp_path / "lock" / "pid").read_text(encoding="utf-8") == str(os.getpid()), (
        "the new holder is recorded, or the next contender takes over a live run"
    )


def test_alive_tells_a_running_process_from_an_exited_one():
    import os

    assert se.alive(os.getpid())
    assert not se.alive(_dead_pid())


def test_a_run_over_a_stale_lock_runs_and_alerts(tmp_path, monkeypatch):
    (tmp_path / "lock").mkdir()
    ran = []
    monkeypatch.setattr(se, "_run", lambda *_: ran.append(1) or 0)

    assert se.main(["run", "--state", str(tmp_path)]) == 0
    assert ran == [1]
    (alert,) = (tmp_path / "alerts").iterdir()
    assert "stale lock" in alert.read_text(encoding="utf-8")
    assert not (tmp_path / "lock").exists(), "released after the run"


def test_a_lock_older_than_any_run_is_taken_over_even_if_its_pid_is_live(tmp_path):
    """A PID is reused (1d on PR 269): after a reboot, a dead run's PID can name
    a live process. Age settles it: no run outlasts MAX_LOCK_AGE."""
    import os

    (tmp_path / "lock").mkdir()
    (tmp_path / "lock" / "pid").write_text(str(os.getpid()), encoding="utf-8")  # alive
    (tmp_path / "lock" / "since").write_text("1000", encoding="utf-8")

    young = se.take_lock(tmp_path / "lock", now=1000 + se.MAX_LOCK_AGE)
    old = se.take_lock(tmp_path / "lock", now=1000 + se.MAX_LOCK_AGE + 1)

    assert young is None, "within any run's length, a live holder keeps it"
    assert old and "older than any run can last" in old


def test_a_lock_with_no_start_time_is_aged_by_its_directory(tmp_path):
    import os
    import time

    lock = tmp_path / "lock"
    lock.mkdir()
    (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")
    os.utime(lock, (time.time() - se.MAX_LOCK_AGE - 60,) * 2)

    assert se.take_lock(lock) and (lock / "since").exists()


def test_the_lock_outlasts_the_longest_run():
    assert se.MAX_LOCK_AGE > se.TIMEOUT + 300, "the eval's timeout plus the fetch's"


def test_two_runs_reclaiming_at_once_leave_one_holder_and_no_traceback(tmp_path, monkeypatch):
    """ba on PR 269: the loser's mkdir raised FileExistsError out of take_lock."""
    (tmp_path / "lock").mkdir()
    real_rmtree = se.shutil.rmtree

    def the_other_run_got_there_first(path, **kwargs):
        real_rmtree(path, **kwargs)
        Path(path).mkdir()

    monkeypatch.setattr(se.shutil, "rmtree", the_other_run_got_there_first)

    assert se.take_lock(tmp_path / "lock") is None


def test_the_task_repeats_at_the_interval_the_timeout_is_sized_to(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(se.subprocess, "run", lambda command, **_: calls.append(command))

    se.main(["install", "--state", str(tmp_path)])

    (command,) = calls
    at = command.index("/MO")
    assert command[at - 2 : at + 2] == ["/SC", "HOURLY", "/MO", str(se.INTERVAL_HOURS)]


# --------------------------------------------------------------------------
# Stopping a run under --heavy (ba on PR 269)
# --------------------------------------------------------------------------


def _bash() -> str:
    """Git's bash on Windows, where a bare `bash` can be WSL's."""
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    return str(git_bash) if git_bash.exists() else "bash"


def _bash_has_timeout() -> bool:
    """Whether the bash these tests drive has GNU `timeout`: asked of that bash,
    not of this process's PATH (Windows' own `timeout.exe` is another program).
    macOS has none (#374)."""
    import subprocess

    done = subprocess.run(
        [_bash(), "-c", "command -v timeout"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return done.returncode == 0


#: The tests that need a real `timeout` to stop something. On a Mac they skip
#: with this reason, and `within` itself refuses (below). The skip gate reads
#: only CI's Linux job, where `timeout` exists, so no list entry is needed.
needs_timeout = pytest.mark.skipif(
    not _bash_has_timeout(),
    reason="GNU `timeout` is not on this machine's PATH (macOS has none; gtimeout is "
    "coreutils'), so `within` cannot stop a command here",
)


def _sleeper(tmp_path: Path) -> list[str]:
    """A child that records its PID, then outlives any deadline here. The path
    goes as / because Git bash re-reads backslashes in a native child's argument."""
    pid_file = tmp_path / "child.pid"
    return [
        sys.executable,
        "-c",
        f"import os, time; open({pid_file.as_posix()!r}, 'w').write(str(os.getpid()))"
        "; time.sleep(60)",
    ]


#: Budgets, in seconds, for `test_within_stops_the_command_at_its_deadline`.
#: The first keeps the test quick; the second is reached only when the child
#: never started inside the first, which is load, not `within` (#347).
_DEADLINE_BUDGETS = (3, 20)


@needs_timeout
def test_within_stops_the_command_at_its_deadline(tmp_path):
    """`within` stops a running command at its deadline, and the command is
    dead afterwards.

    Asserted by behaviour, not by wall-clock (#347, 80's ruling). The
    deadline is absolute and covers the child's start-up, so under load the
    child can be stopped before it writes its PID: `within`'s documented
    "too late, exits 124 without starting". That outcome is asserted as
    itself, not read as a missing file, and the run is repeated with a
    longer budget so the KILL is still proven on a loaded machine; only the
    retry costs time. The child sleeps far longer than any budget, so a
    `within` that let it run (the arm: no `timeout`) never returns 124 and
    hits `subprocess.run`'s own limit.
    """
    import os
    import signal
    import subprocess
    import time

    pid_file = tmp_path / "child.pid"
    try:
        for budget in _DEADLINE_BUDGETS:
            pid_file.unlink(missing_ok=True)
            deadline = int(time.time()) + budget
            done = subprocess.run(
                [_bash(), *se.within(deadline, _sleeper(tmp_path))[1:]], timeout=budget + 30
            )

            assert done.returncode == 124, "stopped at the deadline, not when the child ended"
            # Created and then written: stopped between the two, the file is
            # empty, and the child never got as far as running.
            written = pid_file.read_text(encoding="utf-8").strip() if pid_file.exists() else ""
            if written:
                assert not se.alive(int(written)), "the command outlived its deadline"
                return
            # Never started inside this budget: the documented outcome, as
            # itself. Try again with room for a slow start.
        pytest.fail(
            f"the child never started inside {_DEADLINE_BUDGETS[-1]} s, so the kill was "
            "not exercised; that is a machine this test can't measure on"
        )
    finally:
        # A failing run must not leave a sleeping child behind.
        written = pid_file.read_text(encoding="utf-8").strip() if pid_file.exists() else ""
        if written and se.alive(int(written)):
            os.kill(int(written), signal.SIGTERM)


@needs_timeout
def test_within_runs_nothing_once_the_deadline_has_passed(tmp_path):
    import subprocess
    import time

    done = subprocess.run(
        [_bash(), *se.within(int(time.time()) - 1, _sleeper(tmp_path))[1:]], timeout=50
    )

    assert done.returncode == 124
    assert not (tmp_path / "child.pid").exists()


def _run_within(tmp_path, deadline: int):
    """`within` over a command that leaves a marker if it ever runs, in the bash
    these tests drive."""
    import subprocess

    marker = tmp_path / "ran.marker"
    command = [sys.executable, "-c", f"open({marker.as_posix()!r}, 'w').write('ran')"]
    done = subprocess.run(
        [_bash(), *se.within(deadline, command)[1:]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    return done, marker


def test_without_timeout_within_refuses_and_runs_nothing(tmp_path, monkeypatch):
    """#374, 80's ruling: no `timeout` is a refusal with a message, never a run
    with no deadline. Measured on any machine by naming a program that does
    not exist, since Git bash on Windows always has /usr/bin on its PATH."""
    import time

    monkeypatch.setattr(se, "TIMEOUT_COMMAND", "no-such-timeout-b5")

    done, marker = _run_within(tmp_path, int(time.time()) + 60)

    assert done.returncode == se.NO_TIMEOUT_EXIT == 127, done.stderr
    assert "`no-such-timeout-b5` is not on PATH" in done.stderr
    assert "gtimeout" in done.stderr and "brew install coreutils" in done.stderr
    assert "nothing was run" in done.stderr
    assert not marker.exists(), "the command ran with no deadline"


def test_without_timeout_the_refusal_comes_before_the_too_late_exit(tmp_path, monkeypatch):
    """A Mac reads "no timeout", not "124, too late": the check is first."""
    import time

    monkeypatch.setattr(se, "TIMEOUT_COMMAND", "no-such-timeout-b5")

    done, marker = _run_within(tmp_path, int(time.time()) - 1)

    assert done.returncode == 127 and "is not on PATH" in done.stderr
    assert not marker.exists()


def test_the_real_message_names_timeout_and_its_mac_spelling():
    script = se._within_script("timeout")

    assert "`timeout` is not on PATH" in script
    assert "`gtimeout`" in script and "brew install coreutils" in script
    # In single quotes: a backtick in a double-quoted shell string is RUN.
    assert "echo '" in script and 'echo "' not in script


@needs_timeout
def test_with_timeout_present_the_command_runs_as_before(tmp_path):
    import time

    done, marker = _run_within(tmp_path, int(time.time()) + 60)

    assert done.returncode == 0, done.stderr
    assert marker.read_text(encoding="utf-8") == "ran"


def _drive_run(tmp_path, monkeypatch, *extra: str) -> tuple[list[str], dict]:
    """`run` with every machine-facing step stubbed, returning what it ran."""
    import io
    from types import SimpleNamespace

    ran = []
    monkeypatch.setattr(se.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"{}"))
    monkeypatch.setattr(se, "model_identity", lambda *a: {"name": "m"})
    monkeypatch.setattr(se, "_tip", lambda *a: (tmp_path, "a" * 40))
    monkeypatch.setattr(se, "scheduler_is_current", lambda *a: True)

    def fake_run(command, **kwargs):
        ran.append((command, kwargs))
        return SimpleNamespace(stdout="", stderr="", returncode=124)

    monkeypatch.setattr(se.subprocess, "run", fake_run)
    se.main(["run", "--state", str(tmp_path / "state"), "--timeout", "600", *extra])
    ((command, kwargs),) = ran
    return command, kwargs


def test_under_heavy_the_time_limit_runs_inside_the_slot_not_around_it(tmp_path, monkeypatch):
    import time

    before = int(time.time())
    command, kwargs = _drive_run(tmp_path, monkeypatch, "--heavy", "heavy")

    assert kwargs["timeout"] is None, "a timeout here kills only bash, and strands heavy's slot"
    slot = command.index("--") + 1
    assert command[slot : slot + 4] == ["bash", "-c", se._WITHIN, "within"]
    assert before + 600 <= int(command[slot + 4]) <= int(time.time()) + 600
    assert command[slot + 5 : slot + 7] == [sys.executable, "scripts/eval_zardoz.py"]


def test_without_heavy_the_run_itself_is_stopped(tmp_path, monkeypatch):
    command, kwargs = _drive_run(tmp_path, monkeypatch)

    assert kwargs["timeout"] == 600
    assert command[:2] == [sys.executable, "scripts/eval_zardoz.py"]
