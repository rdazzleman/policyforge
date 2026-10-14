"""`scripts/forgejo_backup.py`: what it decides, with docker faked (#283).

The real runs (a dump, a weekly copy, a restore drill) need the live
container and are measured on the issue; these pin the decisions around them:
what a restore must match, what is kept, when Forgejo may be stopped, that it
is always started again, that a failure is an ALERT, and that an ALERT never
quotes a subprocess's output.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import forgejo_backup as fb


@pytest.fixture(autouse=True)
def _no_real_waits(monkeypatch):
    """The API wait polls for up to three minutes; a test that reaches it by
    mistake should fail at once, not hang (a CI-job arm waited six)."""
    monkeypatch.setattr(fb, "_wait_api", lambda container, timeout: False)


@pytest.fixture(autouse=True)
def local(tmp_path, monkeypatch):
    """The C: fallback, kept inside the test's own directory."""
    path = tmp_path / "local"
    monkeypatch.setattr(fb, "LOCAL", path)
    return path


def _repo(issues=3, pulls=2, refs=5, digest="a" * 64):
    return {"issues": issues, "pulls": pulls, "refs": refs, "refs_sha256": digest}


# --- compare: what a restore must give back ---------------------------------


def test_a_restore_equal_to_the_record_matches():
    record = {"o/r": _repo()}
    assert fb.compare({"o/r": _repo()}, record, record) == []


def test_a_count_between_before_and_after_matches():
    """Something landed while the dump ran: either side is a faithful dump."""
    before, after = {"o/r": _repo(issues=3)}, {"o/r": _repo(issues=5, digest="b" * 64)}
    assert fb.compare({"o/r": _repo(issues=4)}, before, after) == []
    assert fb.compare({"o/r": _repo(issues=5, digest="b" * 64)}, before, after) == []


@pytest.mark.parametrize(
    ("restored", "says"),
    [
        (_repo(issues=2), "issues 2, recorded 3..3"),
        (_repo(pulls=9), "pulls 9, recorded 2..2"),
        (_repo(digest="c" * 64), "refs differ"),
    ],
)
def test_a_restore_that_differs_is_named(restored, says):
    record = {"o/r": _repo()}
    (wrong,) = fb.compare({"o/r": restored}, record, record)
    assert says in wrong and wrong.startswith("o/r: ")


def test_a_repository_missing_from_the_restore_or_extra_in_it_is_named():
    record = {"o/r": _repo(), "o/s": _repo()}
    assert fb.compare({"o/r": _repo()}, record, record) == ["o/s: missing from the restore"]
    assert fb.compare({**record, "o/t": _repo()}, record, record) == [
        "o/t: in the restore, not in the backup's record"
    ]


# --- retention ---------------------------------------------------------------


def test_prune_keeps_the_newest_and_removes_a_failed_run(tmp_path):
    for stamp in ("20261001T030000Z", "20261002T030000Z", "20261003T030000Z"):
        (tmp_path / stamp).mkdir()
    (tmp_path / "20261004T030000Z.partial").mkdir()
    gone = fb.prune(tmp_path, keep=2)
    assert sorted(p.name for p in gone) == ["20261001T030000Z", "20261004T030000Z.partial"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["20261002T030000Z", "20261003T030000Z"]


def test_the_retention_is_14_daily_and_8_weekly():
    assert fb.KEEP == {"daily": 14, "weekly": 8}


# --- latest.md: one line per kind --------------------------------------------


def test_each_kind_rewrites_only_its_own_line(tmp_path):
    fb.report(tmp_path, "daily", "ok daily 1: first")
    fb.report(tmp_path, "drill", "ok drill 1: fine")
    fb.report(tmp_path, "daily", "ALERT daily 2: broke")
    assert (tmp_path / "latest.md").read_text(encoding="utf-8").splitlines() == [
        "ALERT daily 2: broke",
        "ok drill 1: fine",
    ]


# --- the runs, with docker faked ---------------------------------------------


class FakeDocker:
    """Records each docker call; answers from `answers` by its first words,
    raises for any listed in `fail`."""

    def __init__(self, answers=None, fail=()):
        self.calls: list[tuple[str, ...]] = []
        self.answers = answers or {}
        self.fail = fail

    def __call__(self, *args, stdout=None, stdin=None, timeout=600):
        self.calls.append(args)
        for prefix in self.fail:
            if args[: len(prefix)] == prefix:
                raise fb.BackupError(f"docker {' '.join(args[:2])}: exit 1: no such container")
        for prefix, answer in self.answers.items():
            if args[: len(prefix)] == prefix:
                return answer
        return ""

    def verbs(self):
        return [call[0] for call in self.calls]


def _args(dest: Path, **extra) -> argparse.Namespace:
    values = {
        "dest": dest,
        "container": "forgejo",
        "runner": "forgejo-runner",
        "volume": "forgejo_forgejo-data",
        "secrets": dest / "no-secrets",
        "clone": dest / "no-clone",
    }
    return argparse.Namespace(**{**values, **extra})


def test_off_does_nothing_at_all(tmp_path, monkeypatch):
    docker = FakeDocker()
    monkeypatch.setattr(fb, "_docker", docker)
    (tmp_path / "OFF").touch()
    for command in (fb.cmd_daily, fb.cmd_weekly, fb.cmd_drill):
        assert command(_args(tmp_path)) == 0
    assert docker.calls == []
    assert not (tmp_path / "latest.md").exists()


def test_a_wrong_container_is_an_alert(tmp_path, monkeypatch, capsys):
    """The issue's deliberately broken run."""
    monkeypatch.setattr(fb, "_docker", FakeDocker(fail=[("exec",)]))
    assert fb.cmd_daily(_args(tmp_path, container="nope")) == 1
    latest = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert latest.startswith("ALERT daily ") and "no such container" in latest
    assert len(list((tmp_path / "alerts").iterdir())) == 1
    assert next(p.name for p in (tmp_path / "daily").iterdir()).endswith(".partial")


def test_an_alert_never_quotes_what_a_subprocess_printed():
    exc = subprocess.CalledProcessError(
        128, ["git", "bundle", "create", "x"], output=b"SECRET_KEY=abc", stderr=b"token xyz"
    )
    text = fb._safe(exc)
    assert "SECRET" not in text and "token" not in text and "exit 128" in text


def _weekly(tmp_path: Path, age_days: float | None) -> None:
    if age_days is not None:
        when = datetime.now(timezone.utc) - timedelta(days=age_days)
        (tmp_path / "weekly" / fb._stamp(when)).mkdir(parents=True)


def test_weekly_does_nothing_until_seven_days_have_passed(tmp_path, monkeypatch):
    docker = FakeDocker()
    monkeypatch.setattr(fb, "_docker", docker)
    _weekly(tmp_path, age_days=3)
    assert fb.cmd_weekly(_args(tmp_path)) == 0
    assert docker.calls == []


def test_weekly_never_stops_forgejo_under_a_ci_job(tmp_path, monkeypatch):
    docker = FakeDocker(answers={("ps",): "forgejo\nFORGEJO-ACTIONS-TASK-1_JOB-x\n"})
    monkeypatch.setattr(fb, "_docker", docker)
    monkeypatch.setattr(fb, "snapshot", lambda container=fb.CONTAINER: {"o/r": _repo()})
    _weekly(tmp_path, age_days=7.5)
    assert fb.cmd_weekly(_args(tmp_path)) == 0
    assert "stop" not in docker.verbs()
    assert (tmp_path / "latest.md").read_text(encoding="utf-8").startswith("skipped weekly ")


def test_an_overdue_weekly_under_a_ci_job_is_an_alert(tmp_path, monkeypatch):
    docker = FakeDocker(answers={("ps",): "FORGEJO-ACTIONS-TASK-1_JOB-x\n"})
    monkeypatch.setattr(fb, "_docker", docker)
    # A working snapshot, so only the CI-job check can keep it from stopping.
    monkeypatch.setattr(fb, "snapshot", lambda container=fb.CONTAINER: {"o/r": _repo()})
    _weekly(tmp_path, age_days=10)
    assert fb.cmd_weekly(_args(tmp_path)) == 1
    assert "stop" not in docker.verbs()
    assert (tmp_path / "latest.md").read_text(encoding="utf-8").startswith("ALERT weekly ")


def test_forgejo_is_started_again_when_the_copy_fails(tmp_path, monkeypatch):
    docker = FakeDocker(answers={("ps",): "forgejo\n"}, fail=[("run",)])
    monkeypatch.setattr(fb, "_docker", docker)
    monkeypatch.setattr(fb, "snapshot", lambda container=fb.CONTAINER: {"o/r": _repo()})
    assert fb.cmd_weekly(_args(tmp_path)) == 1
    verbs = docker.verbs()
    assert verbs.index("stop") < verbs.index("run") < verbs.index("start")


def test_the_drill_has_no_network_and_always_cleans_up(tmp_path, monkeypatch):
    daily = tmp_path / "daily" / "20261006T030000Z"
    daily.mkdir(parents=True)
    (daily / "forgejo-dump.zip").write_bytes(b"")
    record = {"o/r": _repo()}
    (daily / "manifest.json").write_text(
        fb.json.dumps({"before": record, "after": record}), encoding="utf-8"
    )
    docker = FakeDocker(answers={("inspect",): "forgejo:test\n"})
    monkeypatch.setattr(fb, "_docker", docker)
    monkeypatch.setattr(fb, "_wait_api", lambda container, timeout: True)
    monkeypatch.setattr(fb, "snapshot", lambda container=fb.CONTAINER: {"o/r": _repo(issues=1)})
    assert fb.cmd_drill(_args(tmp_path)) == 1, "a mismatch is an alert"
    runs = [call for call in docker.calls if call[0] == "run"]
    assert len(runs) == 2 and all(
        call[call.index("--network") :][:2] == ("--network", "none") for call in runs
    )
    assert ("rm", "-f", fb.DRILL) in docker.calls
    assert any(call[:2] == ("volume", "rm") for call in docker.calls)
    assert "issues 1, recorded 3..3" in (tmp_path / "latest.md").read_text(encoding="utf-8")


# --- a failure is never silent (ba on PR 302) ---------------------------------


def test_a_destination_that_cant_be_written_alerts_on_c(tmp_path, monkeypatch, local):
    """The issue's wrong-path run: the ALERT can't go where the backup can't."""
    monkeypatch.setattr(fb, "_docker", FakeDocker())
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    assert fb.cmd_daily(_args(blocker / "dest")) == 1
    assert (local / "latest.md").read_text(encoding="utf-8").startswith("ALERT daily ")
    assert len(list((local / "alerts").iterdir())) == 1


def test_a_stop_that_fails_is_still_followed_by_a_start(tmp_path, monkeypatch):
    docker = FakeDocker(answers={("ps",): "forgejo\n"}, fail=[("stop",)])
    monkeypatch.setattr(fb, "_docker", docker)
    monkeypatch.setattr(fb, "snapshot", lambda container=fb.CONTAINER: {"o/r": _repo()})
    assert fb.cmd_weekly(_args(tmp_path)) == 1
    verbs = docker.verbs()
    assert verbs.index("stop") < verbs.index("start")


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def _line(kind: str, age: timedelta, state: str = "ok") -> str:
    return f"{state} {kind} {fb._stamp(NOW - age)}: something"


def test_a_kind_older_than_its_cadence_or_never_seen_is_stale():
    lines = [_line("daily", timedelta(hours=2)), _line("weekly", timedelta(days=10))]
    assert fb.stale(lines, NOW) == [
        f"STALE weekly: last {fb._stamp(NOW - timedelta(days=10))}",
        "STALE drill: last never",
    ]


def test_every_kind_within_its_cadence_is_not_stale():
    lines = [
        _line("daily", timedelta(hours=25)),
        _line("weekly", timedelta(days=8)),
        _line("drill", timedelta(days=31), state="ALERT"),
    ]
    assert fb.stale(lines, NOW) == []


def test_status_fails_on_an_alert_or_a_stale_kind(tmp_path, local, monkeypatch, capsys):
    now = datetime.now(timezone.utc)
    fresh = [f"ok {kind} {fb._stamp(now)}: fine" for kind in fb.CADENCE]
    fb._write(tmp_path / "latest.md", "\n".join(fresh) + "\n")
    assert fb.cmd_status(_args(tmp_path)) == 0
    later = now + timedelta(minutes=1)
    fb._write(local / "latest.md", f"ALERT daily {fb._stamp(later)}: Z: missing\n")
    assert fb.cmd_status(_args(tmp_path)) == 1
    assert "ALERT daily" in capsys.readouterr().out


def test_an_old_fallback_alert_is_superseded_once_the_destination_is_back(tmp_path, local, capsys):
    """ba on PR 302: the C: line is never rewritten once Z: is back, so a
    status that counted every line would fail forever."""
    now = datetime.now(timezone.utc)
    fb._write(local / "latest.md", f"ALERT daily {fb._stamp(now - timedelta(hours=3))}: Z: gone\n")
    fresh = [f"ok {kind} {fb._stamp(now)}: fine" for kind in fb.CADENCE]
    fb._write(tmp_path / "latest.md", "\n".join(fresh) + "\n")
    assert fb.cmd_status(_args(tmp_path)) == 0
    assert "ALERT" not in capsys.readouterr().out
