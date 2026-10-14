#!/usr/bin/env python3
"""Run the eval suite on a local model against release/1.8's tip, on a schedule,
and say something only when a score moves (#261).

    python scripts/scheduled_eval.py run       fetch the tip, run, compare, record
    python scripts/scheduled_eval.py band      set the noise band from runs at one SHA
    python scripts/scheduled_eval.py install   register it with Task Scheduler (every 4 h)
    python scripts/scheduled_eval.py uninstall remove that task
    python scripts/scheduled_eval.py status    the last run, the band, and whether it is off

**$0 by construction, not by configuration.** The run's environment is built
from an allow-list that holds no credential, and the config it is handed pins
`provider: local` and the model; the run writes that config itself and reads no
other. A config that named a hosted provider would find no key to use. The
session's ambient `ANTHROPIC_API_KEY` never reaches it.

**A local run measures the local model, not our prompts against production's.**
It is an early warning and never a graded epoch: nothing here is quoted in
`docs/measurements/epochs.md` (charge: "good for probes, never a substitute for a graded
epoch").

**The model stays fixed between compared runs.** Each run records the model's
name and its Ollama digest, and is compared only with the last run of the same
model, digest and `--repeat`. A recreated model with the same name has a new
digest, so a score that moved because the model changed is never reported as a
regression: the run says the comparison was skipped and why.

**The noise band is measured, not guessed.** `band` takes every recorded run at
one SHA (same model, digest and repeat) and sets each suite's band to the
widest spread between them. Until a band exists, runs are recorded and nothing
is compared; the second run at one SHA sets it unattended, since a tip that
hasn't moved between scheduled runs is exactly that measurement. A later pair
at one SHA that spreads wider than the band widens it and says so, since that
is the band having been wrong.

**What it writes**, under the state directory (default
`C:\\policyforge-local\\scheduled-eval`):

    runs/<when>-<sha>/   record.json (scores, model, digest, timing), report.txt
    band.json            each suite's band and the runs it came from
    latest.md            one line about the last run, always rewritten
    alerts/<when>.md     only when something moved or the run could not run
    OFF                  if present, every run exits at once and does nothing

**Off switch:** create `OFF` in the state directory (it takes effect at the next
run), or `uninstall` to remove the task.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

#: The local model the runs pin (the user, relayed by 80 on 2026-10-04: one that
#: fits wholly on the GPU). qwen3:14b-pf with num_ctx 32768.
PINNED_MODEL = "qwen3:14b-pf32"
OLLAMA = "http://localhost:11434"
REPO_URL = "http://localhost:3000/policyforge/policyforge.git"
BRANCH = "release/1.8"
STATE = Path(r"C:\policyforge-local\scheduled-eval")
CLONE = Path(r"C:\policyforge")
TASK_NAME = r"PolicyForge\scheduled-eval"
#: Hours between scheduled runs ("every 4 hour scans", the user via 80).
INTERVAL_HOURS = 4
#: Runs per case. 3 didn't fit: on qwen3:14b-pf32, 576 calls took 2.98 and
#: 3.40 h with no other run of ours on the GPU, and timed out at 4 h sharing it (the first
#: scheduled run, 2026-10-05). 1 is 192 calls, about a third of that, so a run
#: finishes inside the interval even when other local work shares the GPU.
REPEAT = 1
#: A run is stopped before the next one is due, so a stuck run never costs
#: the next scan. Under `--heavy` that takes `within()`: see there.
TIMEOUT = int((INTERVAL_HOURS - 0.5) * 3600)

#: The only variables a run inherits. Every one is about locating the system,
#: the user's profile or a temporary directory; none is a credential.
_ALLOWED_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
)  # fmt: skip
_CREDENTIAL = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)


def run_env(base: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    """The run's environment: the allow-list from `base`, then `extra`.

    Refuses rather than filters if `extra` names a credential, since that is a
    caller's mistake and quietly dropping it would hide it.
    """
    env = {name: base[name] for name in _ALLOWED_ENV if name in base}
    named = [name for name in extra if _CREDENTIAL.search(name)]
    if named:
        raise ValueError(f"a scheduled eval never carries a credential: {', '.join(named)}")
    env.update(extra)
    return env


def pinned_config(model: str, ledger: Path) -> str:
    """The config the run reads: the local provider and one model, nothing else."""
    return (
        "llm:\n"
        "  provider: local\n"
        f"  model: {model}\n"
        f"  base_url: {OLLAMA}/v1\n"
        "  ledger:\n"
        f"    path: {ledger.as_posix()}\n"
    )


def model_identity(model: str, tags: dict) -> dict | None:
    """The model's name and digest as Ollama lists them, or None if absent."""
    for entry in tags.get("models", []):
        if entry.get("name") == model:
            return {
                "model": model,
                "digest": entry.get("digest"),
                "modified": entry.get("modified_at"),
            }
    return None


def suite_rates(results: dict) -> dict[str, dict]:
    """Each suite's graded pass rate: passes over the runs that reached a verdict.

    Errored runs (the model was never asked) count in neither half, and are
    reported beside the rate rather than folded into it.
    """
    suites: dict[str, dict] = {}
    for case in results["cases"]:
        s = suites.setdefault(case["suite"], {"passes": 0, "graded": 0, "errors": 0})
        s["passes"] += case["passes"]
        s["graded"] += case["runs"] - case["errors"]
        s["errors"] += case["errors"]
    for s in suites.values():
        s["rate"] = s["passes"] / s["graded"] if s["graded"] else None
    return suites


def comparable(a: dict, b: dict) -> bool:
    """Two runs can be compared only on one model, digest and repeat."""
    keys = ("model", "digest")
    return all(a["model"][k] == b["model"][k] for k in keys) and a["repeat"] == b["repeat"]


def compare(previous: dict, current: dict, band: dict | None) -> tuple[list[str], list[str]]:
    """(moves, notes): the suites whose rate moved beyond the band, and the
    reasons a comparison wasn't made. A move is a line naming the suite, both
    rates and both SHAs."""
    if not comparable(previous, current):
        return [], [
            f"not compared with {previous['sha'][:12]}: model, digest or repeat differs "
            f"({previous['model']['digest'][:12]} x{previous['repeat']} vs "
            f"{current['model']['digest'][:12]} x{current['repeat']})"
        ]
    if band is None or not comparable(band["basis"], current):
        return [], ["no noise band for this model and repeat yet, so nothing is compared"]
    moves = []
    for suite, now in sorted(current["suites"].items()):
        was = previous["suites"].get(suite)
        if was is None or was["rate"] is None or now["rate"] is None:
            continue
        width = band["suites"].get(suite)
        if width is None:
            continue
        delta = now["rate"] - was["rate"]
        if abs(delta) > width:
            moves.append(
                f"{suite}: {was['rate']:.0%} -> {now['rate']:.0%} ({delta:+.0%}, band "
                f"{width:.0%}), {previous['sha'][:12]} -> {current['sha'][:12]}"
            )
    return moves, []


def measure_band(runs: list[dict]) -> dict:
    """Each suite's widest spread across runs at one SHA (one model, digest and
    repeat). Two runs at least: one run has no spread to measure."""
    if len(runs) < 2:
        raise ValueError(f"a band needs two runs at one SHA; there are {len(runs)}")
    first = runs[0]
    if any(r["sha"] != first["sha"] or not comparable(first, r) for r in runs):
        raise ValueError("a band is measured at ONE sha, model, digest and repeat")
    suites = {}
    for suite in first["suites"]:
        rates = [r["suites"][suite]["rate"] for r in runs if r["suites"].get(suite)]
        rates = [x for x in rates if x is not None]
        if len(rates) >= 2:
            suites[suite] = max(rates) - min(rates)
    return {
        "suites": suites,
        "basis": {"sha": first["sha"], "model": first["model"], "repeat": first["repeat"]},
        "runs": [r["id"] for r in runs],
    }


def widened(band: dict, runs_at_sha: list[dict]) -> tuple[dict, list[str]]:
    """The band, widened wherever runs at one SHA spread wider than it."""
    if len(runs_at_sha) < 2:
        return band, []
    spread = measure_band(runs_at_sha)["suites"]
    lines, suites = [], dict(band["suites"])
    for suite, width in spread.items():
        if width > suites.get(suite, 0.0):
            lines.append(
                f"{suite}: runs at {runs_at_sha[0]['sha'][:12]} spread {width:.0%}, wider "
                f"than the band's {suites.get(suite, 0.0):.0%}; band widened"
            )
            suites[suite] = width
    return {**band, "suites": suites}, lines


# --------------------------------------------------------------------------
# The parts that touch the machine
# --------------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def _records(state: Path) -> list[dict]:
    runs = []
    for path in sorted((state / "runs").glob("*/record.json")):
        runs.append(json.loads(path.read_text(encoding="utf-8")))
    return runs


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tip(state: Path, handle: str, fj: Path, bash: Path, branch: str = BRANCH) -> tuple[Path, str]:
    """Fetch `branch` (release/1.8) into the run's own worktree and check it out there.

    The worktree is this job's alone, so moving its HEAD moves nobody else's.
    The fetch is read-only, as `handle`'s bot.
    """
    tree = state / "tree"
    if not (tree / ".git").exists():
        _git("-C", str(CLONE), "worktree", "add", "--no-checkout", "--detach", str(tree))
    subprocess.run(
        [
            str(bash),
            str(fj),
            handle,
            "git",
            "-C",
            str(tree),
            "fetch",
            "-q",
            "--no-tags",
            REPO_URL,
            branch,
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    sha = _git("rev-parse", "FETCH_HEAD", cwd=tree)
    _git("checkout", "-q", "-f", "--detach", sha, cwd=tree)
    _git("clean", "-q", "-fdx", cwd=tree)
    return tree, sha


def alive(pid: int) -> bool:
    """Whether process `pid` is running. On Windows `os.kill(pid, 0)` would
    TERMINATE it (signal 0 is passed to TerminateProcess), so ask the kernel."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


#: The oldest a live run's lock can be: the eval's own timeout, plus the fetch
#: (at most 300 s) and the recording either side of it, with room to spare.
MAX_LOCK_AGE = TIMEOUT + 30 * 60


def take_lock(lock: Path, *, now: float | None = None) -> str | None:
    """Take the run's lock. "" when it was free; None when a live run holds it;
    otherwise why the lock was reclaimed.

    A lock is reclaimed when its holder isn't running, or when it is older
    than any run can last (`MAX_LOCK_AGE`). The age matters because a PID is
    reused: 1d measured 36 reused PIDs among 400 short processes here within
    seconds (PR 269), so after a reboot a dead holder's PID can name a live,
    unrelated process, and a PID check alone would skip every run, exit 0,
    forever."""
    now = time.time() if now is None else now
    try:
        lock.mkdir()
    except FileExistsError:
        owner, since = lock / "pid", lock / "since"
        held = owner.read_text(encoding="utf-8").strip() if owner.exists() else ""
        try:
            started = float(since.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            started = lock.stat().st_mtime
        age = now - started
        if held.isdigit() and alive(int(held)) and age <= MAX_LOCK_AGE:
            return None
        why = (
            f"pid {held}, {age / 3600:.1f} h old, older than any run can last"
            if held.isdigit() and alive(int(held))
            else f"pid {held or '(none recorded)'}, no longer running"
        )
        shutil.rmtree(lock, ignore_errors=True)
        try:
            lock.mkdir()
        except FileExistsError:  # another run reclaimed it first, and holds it now
            return None
        _hold(lock, now)
        return why
    _hold(lock, now)
    return ""


def _hold(lock: Path, now: float) -> None:
    (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")
    (lock / "since").write_text(str(now), encoding="utf-8")


def cmd_run(args) -> int:
    state: Path = args.state
    if (state / "OFF").exists():
        print(f"{state / 'OFF'} exists: scheduled evals are off, nothing run")
        return 0
    lock = state / "lock"
    state.mkdir(parents=True, exist_ok=True)
    reclaimed = take_lock(lock)
    if reclaimed is None:
        print(f"another run holds {lock}; nothing run")
        return 0
    if reclaimed:
        # Its holder died without releasing it (a kill, a logoff, a reboot).
        # Left alone, every later run would skip and exit 0: a silent stop.
        when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        _write(state / "alerts" / f"{when}-lock.md", f"took over a stale lock: {reclaimed}\n")
    try:
        return _run(args, state)
    except Exception as exc:  # noqa: BLE001 - an unattended run must say why it stopped
        when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return _alert(state, when, f"could not run: {type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(lock, ignore_errors=True)


#: The program that stops the command. GNU coreutils' `timeout`; macOS ships
#: none (Homebrew's coreutils installs it as `gtimeout`), so a Mac can't run
#: this and says so (#374). Not vendored: a second implementation of a
#: deadline is one more thing to get wrong (80's ruling).
TIMEOUT_COMMAND = "timeout"
#: What `within` exits with when there is no such program: shell convention for
#: "command not found", and not 124, which means the deadline was reached.
NO_TIMEOUT_EXIT = 127


def _within_script(timeout_command: str) -> str:
    """Stops the command at an absolute deadline (epoch seconds, `$1`), or
    exits 124 at once if it has passed. `timeout` sends TERM, then KILL 60 s
    later. With no `timeout_command` on PATH it prints why and exits
    `NO_TIMEOUT_EXIT` BEFORE running anything: a run with no deadline is the
    one outcome this must never produce, and it is checked first so a Mac
    says "no timeout" and not "too late" (#374)."""
    # The message is in SINGLE quotes: it names `timeout` and `gtimeout` in
    # backticks, which a double-quoted shell string would RUN.
    return (
        f"command -v {timeout_command} >/dev/null 2>&1 || {{ echo 'scheduled_eval: "
        f"`{timeout_command}` is not on PATH (GNU coreutils; on macOS it is `gtimeout`, "
        f"from `brew install coreutils`), so no deadline can be set and nothing was run.' >&2; "
        f"exit {NO_TIMEOUT_EXIT}; }}; "
        'left=$(( $1 - $(date +%s) )); shift; [ "$left" -gt 0 ] || exit 124; '
        f'exec {timeout_command} -k 60 "$left" "$@"'
    )


_WITHIN = _within_script(TIMEOUT_COMMAND)


def within(deadline: int, command: list[str]) -> list[str]:
    """`command`, stopped at `deadline`, for running inside heavy's slot.

    Under `--heavy` the eval is heavy's child's child. A timeout here would
    kill only `bash`, and ba measured on PR 269 that `subprocess.run` then
    waits for the eval to end by itself, holding the pipes, so nothing is
    stopped. A hard kill of heavy's `bash` would also skip its trap and leave
    the machine-wide slot held. So the limit runs inside the slot: `timeout`
    ends the eval, and heavy releases the slot as it does for any exit
    (measured on a copy of heavy: a 25 s child stopped at 3 s, exit 124, slot
    released). The deadline is fixed when the run starts, so the wait for the
    slot counts against it, and a run that gets the slot too late exits 124
    without starting. A machine with no `timeout` (a Mac) exits
    `NO_TIMEOUT_EXIT` with a message instead, and runs nothing."""
    return ["bash", "-c", _within_script(TIMEOUT_COMMAND), "within", str(deadline), *command]


def _run(args, state: Path) -> int:
    when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # semgrep's dynamic-urllib rule flags any urlopen, a literal URL included
    # (measured). This one is the local Ollama's model list, a fixed http URL.
    url = f"{OLLAMA}/api/tags"
    with urllib.request.urlopen(url, timeout=30) as reply:  # nosec B310  # nosemgrep
        identity = model_identity(args.model, json.load(reply))
    if identity is None:
        return _alert(state, when, f"model {args.model} is not in Ollama; nothing run")

    tree, sha = _tip(state, args.handle, args.fj, args.bash, args.branch)
    current = scheduler_is_current(Path(__file__), tree / "scripts" / "scheduled_eval.py")
    run_dir = state / "runs" / f"{when}-{sha[:12]}"
    config = run_dir / "config.yaml"
    _write(config, pinned_config(args.model, state / "ledger" / "llm.jsonl"))
    env = run_env(
        dict(os.environ),
        {
            "POLICYFORGE_CONFIG": str(config),
            "PYTHONPATH": os.pathsep.join([str(tree / "src"), str(tree)]),
            "PYTHONIOENCODING": "utf-8",
        },
    )
    results_path = run_dir / "results.json"
    command = [
        sys.executable,
        "scripts/eval_zardoz.py",
        "--repeat",
        str(args.repeat),
        "--json",
        str(results_path),
    ]
    timeout: int | None = args.timeout
    if args.heavy:
        command = [
            str(args.bash), str(args.heavy), args.handle, "scheduled-eval", "--",
            *within(int(time.time()) + args.timeout, command),
        ]  # fmt: skip
        timeout = None
    started = time.monotonic()
    done = subprocess.run(
        command,
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    wall = time.monotonic() - started
    _write(run_dir / "report.txt", done.stdout + done.stderr)
    if not results_path.exists():
        tail = " | ".join((done.stdout + done.stderr).strip().splitlines()[-3:])
        return _alert(
            state, when, f"run at {sha[:12]} could not run (exit {done.returncode}): {tail}"
        )

    results = json.loads(results_path.read_text(encoding="utf-8"))
    runs = sum(c["runs"] for c in results["cases"])
    record = {
        "id": run_dir.name,
        "sha": sha,
        "when": when,
        "model": identity,
        "repeat": args.repeat,
        "exit": done.returncode,
        "wall_seconds": round(wall, 1),
        "seconds_per_run": round(wall / runs, 2) if runs else None,
        "suites": suite_rates(results),
        "scheduler_is_current": current,
    }
    _write(run_dir / "record.json", json.dumps(record, indent=1) + "\n")
    return _judge(state, record)


def _judge(state: Path, record: dict) -> int:
    """Compare a recorded run with the last comparable one, and write what it found."""
    history = [r for r in _records(state) if r["id"] != record["id"]]
    band_path = state / "band.json"
    band = json.loads(band_path.read_text(encoding="utf-8")) if band_path.exists() else None
    lines: list[str] = []
    notes: list[str] = []
    same = [r for r in history if r["sha"] == record["sha"] and comparable(r, record)]
    if band is not None and comparable(band["basis"], record):
        band, widenings = widened(band, [*same, record])
        if widenings:
            _write(band_path, json.dumps(band, indent=1) + "\n")
            lines += widenings
    elif same:
        # No band for this model and repeat, and now two runs at one SHA:
        # that is the measurement a band is, so it is taken unattended.
        band = measure_band([*same, record])
        _write(band_path, json.dumps(band, indent=1) + "\n")
        widths = ", ".join(f"{s} {w:.0%}" for s, w in sorted(band["suites"].items()))
        notes.append(f"band set from {len(same) + 1} runs at {record['sha'][:12]}: {widths}")
    errored = {s: v["errors"] for s, v in record["suites"].items() if v["errors"]}
    if errored:
        lines.append(f"errored runs (never graded): {errored}")
    previous = next((r for r in reversed(history) if comparable(r, record)), None)
    if record.get("scheduler_is_current") is False:
        notes.append(
            "the installed scheduled_eval.py differs from the tip's; re-run `install` to take it"
        )
    if previous is None:
        notes.append("first run of this model, digest and repeat; nothing to compare")
    else:
        moves, skipped = compare(previous, record, band)
        lines += moves
        notes += skipped
    summary = (
        f"{record['when']} {record['sha'][:12]} {record['model']['model']} "
        f"x{record['repeat']}: {record['seconds_per_run']} s per run; "
        + ("; ".join(lines) if lines else "no move beyond the band")
        + ("" if not notes else f" ({'; '.join(notes)})")
    )
    _write(state / "latest.md", summary + "\n")
    print(summary)
    if lines:
        return _alert(state, record["when"], summary)
    return 0


def _alert(state: Path, when: str, text: str) -> int:
    _write(state / "alerts" / f"{when}.md", text + "\n")
    _write(state / "latest.md", f"ALERT {when}: {text}\n")
    print(f"ALERT: {text}")
    return 1


def cmd_band(args) -> int:
    runs = [r for r in _records(args.state) if r["sha"].startswith(args.sha)]
    band = measure_band(runs)
    _write(args.state / "band.json", json.dumps(band, indent=1) + "\n")
    widths = ", ".join(f"{s} {w:.0%}" for s, w in sorted(band["suites"].items()))
    print(f"band from {len(runs)} runs at {runs[0]['sha'][:12]}: {widths}")
    return 0


def cmd_status(args) -> int:
    state: Path = args.state
    print("OFF" if (state / "OFF").exists() else "on")
    for name in ("latest.md", "band.json"):
        path = state / name
        print(f"{name}: {path.read_text(encoding='utf-8').strip() if path.exists() else '(none)'}")
    return 0


def scheduler_is_current(installed: Path, at_tip: Path) -> bool | None:
    """Whether the copy the task runs is the tip's. `install` copies the script,
    so a merged change reaches the schedule only when someone re-installs; each
    run says when that's due. None when the tip has no such file."""
    if not at_tip.exists():
        return None
    return installed.read_bytes() == at_tip.read_bytes()


def cmd_install(args) -> int:
    """Copy this script beside its state and register a 4-hourly task that runs
    it as the current user, only while they are logged on (no stored password).
    The task runs that copy; each run notes when the tip's has moved on."""
    bin_dir = args.state / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), bin_dir / "scheduled_eval.py")
    heavy = f' --heavy "{args.heavy}"' if args.heavy else ""
    _write(
        bin_dir / "run.cmd",
        f'@"{sys.executable}" "%~dp0scheduled_eval.py" run --state "{args.state}" '
        f"--handle {args.handle} --model {args.model} --repeat {args.repeat} "
        f"--timeout {args.timeout}{heavy} "
        f'>> "{args.state / "task.log"}" 2>&1\r\n',
    )
    subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "HOURLY", "/MO", str(INTERVAL_HOURS),
         "/TR", str(bin_dir / "run.cmd"), "/F"],
        check=True,
    )  # fmt: skip
    print(f"installed {TASK_NAME}: every 4 hours, {bin_dir / 'run.cmd'}")
    return 0


def cmd_uninstall(args) -> int:
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=True)
    print(f"removed {TASK_NAME}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("run", "band", "status", "install", "uninstall"))
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--model", default=PINNED_MODEL)
    parser.add_argument(
        "--repeat",
        type=int,
        default=REPEAT,
        help="runs per case; 1 fits the 4-hour cadence (see REPEAT)",
    )
    parser.add_argument("--handle", default="b5", help="the bot the read-only fetch uses")
    parser.add_argument(
        "--branch",
        default=BRANCH,
        help="the branch run; another only for a demonstration, in its own --state",
    )
    parser.add_argument("--sha", help="band: the SHA (or prefix) whose runs set the band")
    parser.add_argument(
        "--heavy", type=Path, default=None, help="run the eval through this heavy queue script"
    )
    parser.add_argument("--fj", type=Path, default=Path(r"C:\forgejo\bin\fj"))
    parser.add_argument("--bash", type=Path, default=Path(r"C:\Program Files\Git\bin\bash.exe"))
    parser.add_argument(
        "--timeout",
        type=int,
        default=TIMEOUT,
        help="seconds before a run is stopped, any wait for --heavy's slot included; under"
        " the interval",
    )
    args = parser.parse_args(argv)
    return {
        "run": cmd_run,
        "band": cmd_band,
        "status": cmd_status,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
