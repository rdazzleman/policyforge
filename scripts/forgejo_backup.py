#!/usr/bin/env python3
"""Back up the local Forgejo, and prove the backup restores (#283).

    python scripts/forgejo_backup.py daily      dump, runner files, secrets, clone bundle
    python scripts/forgejo_backup.py weekly     a byte copy of the data volume, when due
    python scripts/forgejo_backup.py drill      restore the newest dump in a sealed container
    python scripts/forgejo_backup.py install    the folder, its ACL and the three tasks
    python scripts/forgejo_backup.py uninstall  remove the three tasks (the backups stay)
    python scripts/forgejo_backup.py status     each kind's last line, and whether it is off

**daily** streams `forgejo dump` out of the container (`--file -`), so no copy
is left inside it, and writes beside it the runner's top-level files
(`config.yml`, `token.txt`), a copy of `C:\\forgejo\\secrets`, and a `git bundle` of every ref in
the shared clone (ag's unpushed branches and `refs/archive/wt/*` included). It
records what the instance held just before and just after the dump: each
repository's issue and pull counts and a digest of its refs, read from the
database and the bare repositories. No downtime.

**weekly** runs daily and acts once seven days have passed since the last
copy: it stops `forgejo` for about a minute, tars the volume, and starts it
again, whatever happened in between. It never stops Forgejo under a running
CI job: it skips and tries again at its next run, and an overdue copy (nine
days) is an ALERT.

**drill** restores the newest dump into a throwaway volume and container
with **no network at all** (`--network none`): the dump's `app.ini` has
mirroring on, and a restored copy must never push anywhere. It rebuilds the
database from the dump's SQL, starts Forgejo, waits for its API to answer
inside the container, and compares each repository's counts and ref digest
with what `daily` recorded. A count outside the before/after pair, or a
digest that matches neither, is an ALERT. **A backup that has never been
restored is not evidence.**

**Nothing secret is printed.** A dump holds `app.ini`'s secret key, every bot's
token and the users' password hashes; the secrets copy holds the tokens. The
output and `latest.md` carry only names, counts, sizes and digests. `install`
gives the folder to the current user alone (`icacls /inheritance:r`), and
Z: is a local disk (measured: DriveType 3). Off-machine copies belong to the
user's homelab project, not here.

**What it writes**, under the destination (default `Z:\\backups\\forgejo`):

    daily/<when>/      forgejo-dump.zip, manifest.json, runner/, secrets/, clone.bundle
    weekly/<when>/     forgejo-data.tar.gz, manifest.json
    drills/<when>.md   each drill's numbers
    latest.md          one line per kind (daily, weekly, drill), each rewritten by its run
    alerts/<when>.md   only when a run failed
    OFF                if present, every run exits at once and does nothing

On C:, in `C:\\policyforge-local\\forgejo-backup`: the tasks' copy of this
script (`bin/`), their `task.log`, and, when the destination itself can't be
written, the ALERT (`latest.md`, `alerts/`). `status` reads both places and
prints a STALE line for any kind whose newest line is older than its cadence
(daily 26 h, weekly 9 days, drill 32 days), since a run that never happened
writes nothing; it exits 1 on any ALERT or STALE.

Retention: 14 daily, 8 weekly. A run that fails leaves `<when>.partial`,
which the next run of its kind removes.

**Off switch:** create `OFF` in the destination (it takes effect at the next
run), or `uninstall` to remove the tasks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEST = Path(r"Z:\backups\forgejo")
CONTAINER = "forgejo"
RUNNER = "forgejo-runner"
VOLUME = "forgejo_forgejo-data"
CONFIG = "/data/gitea/conf/app.ini"
DATABASE = "/data/gitea/gitea.db"
REPOSITORIES = "/data/git/repositories"
SECRETS = Path(r"C:\forgejo\secrets")
CLONE = Path(r"C:\policyforge")
TASK = r"PolicyForge\forgejo-backup"
DRILL = "forgejo-backup-drill"
KEEP = {"daily": 14, "weekly": 8}
WEEKLY_DUE = timedelta(days=7)
WEEKLY_OVERDUE = timedelta(days=9)
#: On C:, off the destination: the tasks' copy of this script, their log, and
#: where an ALERT goes when the destination can't be written.
LOCAL = Path(r"C:\policyforge-local\forgejo-backup")
#: How old each kind's newest line may be before `status` calls it STALE.
CADENCE = {
    "daily": timedelta(hours=26),
    "weekly": WEEKLY_OVERDUE,
    "drill": timedelta(days=32),
}
#: A CI job's container: Forgejo Actions names every one this way.
CI_JOB = "FORGEJO-ACTIONS-TASK"

_COUNTS_SQL = (
    "select lower(owner_name)||'/'||lower_name, "
    "(select count(*) from issue i where i.repo_id = r.id and i.is_pull = 0), "
    "(select count(*) from issue i where i.repo_id = r.id and i.is_pull = 1) "
    "from repository r order by 1;"
)


class BackupError(Exception):
    """A step failed; the message names it and holds nothing secret."""


def _docker(*args: str, stdout=None, stdin=None, timeout: int = 600) -> str:
    """Run docker. With `stdout` (a file), the output streams there as bytes;
    otherwise it is returned as text. A failure raises with docker's own
    first error line, which names the step, never the data."""
    try:
        done = subprocess.run(
            ["docker", *args],
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stdin=stdin,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"docker {args[0]}: {exc.__class__.__name__}") from exc
    if done.returncode != 0:
        err = done.stderr.decode("utf-8", "replace").strip().splitlines()
        last = err[-1][:200] if err else ""
        raise BackupError(f"docker {' '.join(args[:2])}: exit {done.returncode}: {last}")
    return "" if stdout is not None else done.stdout.decode("utf-8", "replace")


def snapshot(container: str = CONTAINER) -> dict[str, dict]:
    """Each repository's issue and pull counts, from the database, and a
    digest of its refs, from its bare repository: what a restore must give
    back. Read inside `container`, as the `git` user, read-only."""
    rows = _docker("exec", "-u", "git", container, "sqlite3", "-readonly", DATABASE, _COUNTS_SQL)
    repos: dict[str, dict] = {}
    for line in rows.splitlines():
        name, issues, pulls = line.rsplit("|", 2)
        refs = _docker(
            "exec", "-u", "git", container, "git", f"--git-dir={REPOSITORIES}/{name}.git",
            "for-each-ref", "--format=%(objectname) %(refname)",
        )  # fmt: skip
        lines = sorted(refs.splitlines())
        repos[name] = {
            "issues": int(issues),
            "pulls": int(pulls),
            "refs": len(lines),
            "refs_sha256": hashlib.sha256("\n".join(lines).encode()).hexdigest(),
        }
    if not repos:
        raise BackupError(f"snapshot: no repositories read from {container}")
    return repos


def compare(
    restored: dict[str, dict], before: dict[str, dict], after: dict[str, dict]
) -> list[str]:
    """What a restore got wrong, against the pair `daily` recorded around its
    dump. A count may be anywhere from before to after (something landed
    while the dump ran); a ref digest must equal one of the two."""
    wrong = []
    for name in sorted(set(before) | set(after) | set(restored)):
        got, lo, hi = restored.get(name), before.get(name), after.get(name)
        if got is None:
            wrong.append(f"{name}: missing from the restore")
            continue
        if lo is None and hi is None:
            wrong.append(f"{name}: in the restore, not in the backup's record")
            continue
        lo, hi = lo or hi, hi or lo
        for field in ("issues", "pulls"):
            low, high = sorted((lo[field], hi[field]))
            if not low <= got[field] <= high:
                wrong.append(f"{name}: {field} {got[field]}, recorded {low}..{high}")
        if got["refs_sha256"] not in (lo["refs_sha256"], hi["refs_sha256"]):
            wrong.append(f"{name}: refs differ ({got['refs']} restored, {hi['refs']} recorded)")
    return wrong


def prune(kind_dir: Path, keep: int) -> list[Path]:
    """Remove a failed run's `.partial` and all but the newest `keep` runs.
    Names are UTC stamps, so they sort in time order."""
    if not kind_dir.exists():
        return []
    gone = [p for p in kind_dir.iterdir() if p.name.endswith(".partial")]
    runs = sorted(p for p in kind_dir.iterdir() if p.is_dir() and not p.name.endswith(".partial"))
    gone += runs[: max(len(runs) - keep, 0)]
    for path in gone:
        shutil.rmtree(path)
    return gone


def _stamp(when: datetime) -> str:
    return when.strftime("%Y%m%dT%H%M%SZ")


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _newest(kind_dir: Path) -> Path | None:
    runs = sorted(
        p for p in kind_dir.glob("*") if p.is_dir() and not p.name.endswith(".partial")
    ) if kind_dir.exists() else []  # fmt: skip
    return runs[-1] if runs else None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def report(dest: Path, kind: str, line: str) -> None:
    """Rewrite `kind`'s line in latest.md, keeping the other kinds' lines.
    Every line's second word is its kind (`ok daily …`, `ALERT drill …`)."""
    latest = dest / "latest.md"
    keep = []
    if latest.exists():
        keep = [
            existing
            for existing in latest.read_text(encoding="utf-8").splitlines()
            if existing.split(" ")[1:2] != [kind]
        ]
    _write(latest, "\n".join(sorted([*keep, line])) + "\n")
    print(line)


def alert(dest: Path, kind: str, when: str, text: str) -> int:
    """Record a failure in the destination, or, when the destination itself
    can't be written (Z: missing, a wrong `--dest`), in `LOCAL` on C:, so a
    failure is never silent (ba on PR 302)."""
    for where in (dest, LOCAL):
        try:
            _write(where / "alerts" / f"{when}-{kind}.md", text + "\n")
            report(where, kind, f"ALERT {kind} {when}: {text}")
            break
        except OSError as exc:
            print(f"ALERT {kind} {when}: {text} (and {where} can't be written: {exc})")
    return 1


def _off(dest: Path) -> bool:
    if (dest / "OFF").exists():
        print(f"OFF: {dest / 'OFF'} exists; nothing done")
        return True
    return False


def _verify_dump(path: Path) -> int:
    with zipfile.ZipFile(path) as dump:
        names = dump.namelist()
        bad = dump.testzip()
    if bad is not None:
        raise BackupError(f"dump: {bad} fails its CRC")
    missing = [n for n in ("app.ini", "forgejo-db.sql") if n not in names]
    if missing or not any(n.startswith("repos/") for n in names):
        raise BackupError(f"dump: lacks {', '.join(missing) or 'repos/'}")
    return len(names)


def cmd_daily(args) -> int:
    dest: Path = args.dest
    if _off(dest):
        return 0
    when = _stamp(datetime.now(timezone.utc))
    try:
        prune(dest / "daily", KEEP["daily"])
        out = dest / "daily" / f"{when}.partial"
        out.mkdir(parents=True)
        before = snapshot(args.container)
        with open(out / "forgejo-dump.zip", "wb") as dump:
            _docker(
                "exec", "-u", "git", args.container, "forgejo", "dump", "-c", CONFIG,
                "--skip-log", "--quiet", "--type", "zip", "--file", "-",
                stdout=dump, timeout=1800,
            )  # fmt: skip
        after = snapshot(args.container)
        entries = _verify_dump(out / "forgejo-dump.zip")
        (out / "runner").mkdir()
        # Every top-level file, not a fixed list: the runner keeps
        # `config.yml` and its `token.txt` there (measured; there is no
        # `.runner`), and its `.cache/` is rebuilt on its own.
        names = _docker(
            "exec", args.runner, "find", "/data", "-maxdepth", "1", "-type", "f"
        ).split()
        if not any(name.endswith("/config.yml") for name in names):
            raise BackupError(f"{args.runner}: no /data/config.yml")
        for name in names:
            with open(out / "runner" / Path(name).name, "wb") as f:
                _docker("exec", args.runner, "cat", name, stdout=f)
        shutil.copytree(args.secrets, out / "secrets")
        bundle = out / "clone.bundle"
        subprocess.run(
            ["git", "-C", str(args.clone), "bundle", "create", "-q", str(bundle), "--all"],
            check=True, capture_output=True, timeout=1800,
        )  # fmt: skip
        subprocess.run(
            ["git", "-C", str(args.clone), "bundle", "verify", "-q", str(bundle)],
            check=True, capture_output=True, timeout=1800,
        )  # fmt: skip
        manifest = {"when": when, "before": before, "after": after, "dump_entries": entries}
        _write(out / "manifest.json", json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        final = out.with_name(when)
        out.rename(final)
        prune(dest / "daily", KEEP["daily"])
    except (BackupError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        return alert(dest, "daily", when, f"{exc.__class__.__name__}: {_safe(exc)}")
    size = (final / "forgejo-dump.zip").stat().st_size
    bundled = (final / "clone.bundle").stat().st_size
    report(
        dest, "daily",
        f"ok daily {when}: dump {size / 1e6:.1f} MB, {entries} entries, {len(after)} repositories, "
        f"bundle {bundled / 1e6:.1f} MB",
    )  # fmt: skip
    return 0


def _safe(exc: BaseException) -> str:
    """An error's text, without anything a subprocess printed: a failed git
    or docker call's output could quote what it was reading."""
    if isinstance(exc, subprocess.CalledProcessError):
        return f"{Path(str(exc.cmd[0])).name} {exc.cmd[1:3]} exit {exc.returncode}"
    return str(exc)[:300]


def _ci_jobs() -> list[str]:
    names = _docker("ps", "--format", "{{.Names}}").split()
    return [name for name in names if name.startswith(CI_JOB)]


def _image(container: str) -> str:
    return _docker("inspect", container, "--format", "{{.Config.Image}}").strip()


def _wait_api(container: str, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            # healthz, not /api/v1/version: this instance requires sign-in, so
            # the version endpoint answers 403 to a healthy server (measured).
            health = _docker(
                "exec", container, "wget", "-qO-", "http://127.0.0.1:3000/api/healthz",
                timeout=30,
            )  # fmt: skip
            if '"status": "pass"' in health:
                return True
            time.sleep(3)
        except BackupError:
            time.sleep(3)
    return False


def cmd_weekly(args) -> int:
    dest: Path = args.dest
    if _off(dest):
        return 0
    now = datetime.now(timezone.utc)
    when = _stamp(now)
    last = _newest(dest / "weekly")
    since = now - _parse(last.name) if last else None
    if since is not None and since < WEEKLY_DUE:
        print(f"weekly not due: the last copy is {since.days} day(s) old ({last.name})")
        return 0
    try:
        jobs = _ci_jobs()
        if jobs:
            if since is None or since > WEEKLY_OVERDUE:
                return alert(dest, "weekly", when, f"overdue, and {len(jobs)} CI job(s) running")
            report(dest, "weekly", f"skipped weekly {when}: {len(jobs)} CI job(s) running")
            return 0
        prune(dest / "weekly", KEEP["weekly"])
        out = dest / "weekly" / f"{when}.partial"
        out.mkdir(parents=True)
        image = _image(args.container)
        before = snapshot(args.container)
        stopped = time.monotonic()
        try:
            # Inside the try: a stop that errors after taking effect must
            # still be followed by a start (ba on PR 302).
            _docker("stop", "-t", "60", args.container, timeout=120)
            with open(out / "forgejo-data.tar.gz", "wb") as tar:
                _docker(
                    "run", "--rm", "--network", "none", "-v", f"{args.volume}:/data:ro",
                    "--entrypoint", "tar", image, "-czf", "-", "-C", "/data", ".",
                    stdout=tar, timeout=1800,
                )  # fmt: skip
        finally:
            _docker("start", args.container, timeout=120)
            down = time.monotonic() - stopped
        if not _wait_api(args.container, 180):
            raise BackupError(f"{args.container} started but its API didn't answer in 180 s")
        with tarfile.open(out / "forgejo-data.tar.gz") as tar:
            members = tar.getnames()
        if "./gitea/gitea.db" not in members:
            raise BackupError("weekly: the tarball has no gitea/gitea.db")
        manifest = {"when": when, "before": before, "members": len(members), "down_s": down}
        _write(out / "manifest.json", json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        final = out.with_name(when)
        out.rename(final)
        prune(dest / "weekly", KEEP["weekly"])
    except (BackupError, OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
        return alert(dest, "weekly", when, f"{exc.__class__.__name__}: {_safe(exc)}")
    size = (final / "forgejo-data.tar.gz").stat().st_size
    report(
        dest, "weekly",
        f"ok weekly {when}: {size / 1e6:.1f} MB, {len(members)} members, Forgejo down {down:.0f} s",
    )  # fmt: skip
    return 0


#: The restore, run as root in a sealed helper container, the dump on stdin.
#: The dump's own `data/gitea.db` is a live file copied mid-write; the
#: database is rebuilt from its consistent SQL export instead, in ONE
#: transaction: the export has no BEGIN, and 56,646 INSERTs committed one at a
#: time took 14 minutes on the volume (measured). Ownership mirrors the live
#: volume: `/data` stays root's, `git` (1000) owns `gitea/` and `git/`, and
#: `git/.ssh` (not in the dump) exists as git's, or Forgejo exits at start on
#: `authorized_keys: permission denied` (measured).
_RESTORE = """set -e
cd /tmp && cat > d.zip && mkdir r && cd r && unzip -q ../d.zip
mkdir -p /data/gitea/conf /data/git/repositories /data/git/.ssh
cp app.ini /data/gitea/conf/app.ini
rm -f data/gitea.db data/gitea.db-shm data/gitea.db-wal
cp -a data/. /data/gitea/
cp -a repos/. /data/git/repositories/
{ echo 'PRAGMA synchronous=OFF; BEGIN;'; cat forgejo-db.sql; echo 'COMMIT;'; } >all.sql
sqlite3 /data/gitea/gitea.db <all.sql
touch /data/git/.ssh/authorized_keys
chmod 700 /data/git/.ssh && chmod 600 /data/git/.ssh/authorized_keys
chown -R 1000:1000 /data/gitea /data/git
"""


def cmd_drill(args) -> int:
    dest: Path = args.dest
    if _off(dest):
        return 0
    when = _stamp(datetime.now(timezone.utc))
    newest = _newest(dest / "daily")
    if newest is None:
        return alert(dest, "drill", when, "no daily backup to restore")
    volume = f"{DRILL}-{when.lower()}"
    try:
        manifest = json.loads((newest / "manifest.json").read_text(encoding="utf-8"))
        image = _image(args.container)
        _docker("volume", "create", volume)
        started = time.monotonic()
        with open(newest / "forgejo-dump.zip", "rb") as dump:
            _docker(
                "run", "--rm", "-i", "--network", "none", "-v", f"{volume}:/data",
                "--entrypoint", "sh", image, "-c", _RESTORE, stdin=dump, timeout=1800,
            )  # fmt: skip
        restore_s = time.monotonic() - started
        _docker("run", "-d", "--name", DRILL, "--network", "none", "-v", f"{volume}:/data", image)
        if not _wait_api(DRILL, 180):
            raise BackupError("the restored Forgejo's API didn't answer in 180 s")
        start_s = time.monotonic() - started - restore_s
        restored = snapshot(DRILL)
        wrong = compare(restored, manifest["before"], manifest["after"])
    except (BackupError, OSError, ValueError, KeyError) as exc:
        return alert(dest, "drill", when, f"{exc.__class__.__name__}: {_safe(exc)}")
    finally:
        for cleanup in (("rm", "-f", DRILL), ("volume", "rm", "-f", volume)):
            try:
                _docker(*cleanup)
            except BackupError as exc:
                print(f"cleanup: {exc}")
    issues = sum(r["issues"] for r in restored.values())
    pulls = sum(r["pulls"] for r in restored.values())
    refs = sum(r["refs"] for r in restored.values())
    table = "\n".join(
        f"    {name:40} issues {r['issues']:5}  pulls {r['pulls']:5}  refs {r['refs']:6}"
        for name, r in sorted(restored.items())
    )
    summary = (
        f"drill {when}: restored {newest.name}: {len(restored)} repositories, "
        f"{issues} issues, {pulls} pulls, {refs} refs (restore {restore_s:.0f} s, "
        f"start {start_s:.0f} s)"
    )
    _write(
        dest / "drills" / f"{when}.md",
        summary + "\n\n" + table + "\n\n" + ("\n".join(wrong) or "every repository matches") + "\n",
    )
    if wrong:
        return alert(dest, "drill", when, f"{len(wrong)} mismatch(es): " + "; ".join(wrong)[:600])
    report(dest, "drill", f"ok {summary}, every repository matches")
    return 0


def newest(lines: list[str]) -> dict[str, tuple[datetime, str]]:
    """Each kind's newest line, by its stamp, across every place read: a
    fallback ALERT on C: is superseded by a later line in the destination
    once it is back, and never stands on its own (ba on PR 302)."""
    found: dict[str, tuple[datetime, str]] = {}
    for line in lines:
        words = line.split(" ")
        if len(words) >= 3 and words[1] in CADENCE:
            try:
                when = _parse(words[2].rstrip(":"))
            except ValueError:
                continue
            if words[1] not in found or when > found[words[1]][0]:
                found[words[1]] = (when, line)
    return found


def stale(lines: list[str], now: datetime) -> list[str]:
    """Each kind whose newest line is older than its cadence allows, or that
    has none: a run that never happened writes nothing, so silence has to be
    read as a failure here (ba on PR 302)."""
    latest = newest(lines)
    found = []
    for kind, cadence in CADENCE.items():
        last = latest[kind][0] if kind in latest else None
        if last is None or now - last > cadence:
            found.append(f"STALE {kind}: last {_stamp(last) if last else 'never'}")
    return found


def cmd_status(args) -> int:
    """OFF or on, each kind's newest line from the destination and `LOCAL`
    together, and a STALE line for each kind overdue. Exit 1 when a kind's
    newest line is an ALERT, or a kind is STALE."""
    dest: Path = args.dest
    print("OFF" if (dest / "OFF").exists() else "on")
    lines = []
    for where in (dest, LOCAL):
        try:
            text = (where / "latest.md").read_text(encoding="utf-8")
        except OSError:
            continue
        lines += [line for line in text.splitlines() if line]
    current = [line for _, line in sorted(newest(lines).values())]
    for line in current:
        print(line)
    late = stale(lines, datetime.now(timezone.utc))
    for line in late:
        print(line)
    return 1 if late or any(line.startswith("ALERT ") for line in current) else 0


def _tasks(args) -> dict[str, list[str]]:
    # The script and its log live on C:, not in the destination: with Z:
    # missing, a log redirected there would stop cmd running the job at all.
    run = f'"{sys.executable}" "{LOCAL / "bin" / "forgejo_backup.py"}"'
    log = f'>> "{LOCAL / "task.log"}" 2>&1'
    return {
        "daily": ["/SC", "DAILY", "/ST", "03:00", "/TR", f'cmd /c "{run} daily {log}"'],
        "weekly": ["/SC", "DAILY", "/ST", "04:00", "/TR", f'cmd /c "{run} weekly {log}"'],
        "drill": [
            "/SC",
            "MONTHLY",
            "/D",
            "1",
            "/ST",
            "05:00",
            "/TR",
            f'cmd /c "{run} drill {log}"',
        ],
    }


def cmd_install(args) -> int:
    """Make the folder, give it to the current user alone, copy this script
    beside it, and register the three tasks as the current user, only while
    they are logged on (no stored password). The tasks run that copy."""
    dest: Path = args.dest
    dest.mkdir(parents=True, exist_ok=True)
    user = subprocess.run(
        ["whoami"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
    ).stdout.strip()
    subprocess.run(
        ["icacls", str(dest), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
        check=True, capture_output=True,
    )  # fmt: skip
    (LOCAL / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), LOCAL / "bin" / "forgejo_backup.py")
    for kind, schedule in _tasks(args).items():
        subprocess.run(
            ["schtasks", "/Create", "/TN", f"{TASK}-{kind}", *schedule, "/F"],
            check=True, capture_output=True,
        )  # fmt: skip
        print(f"installed {TASK}-{kind}: {' '.join(schedule[:4])}")
    print(f"{dest}: only {user} may read it")
    return 0


def cmd_uninstall(args) -> int:
    for kind in _tasks(args):
        subprocess.run(["schtasks", "/Delete", "/TN", f"{TASK}-{kind}", "/F"], check=False)
    print(f"removed the tasks; the backups in {args.dest} are kept")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", choices=("daily", "weekly", "drill", "status", "install", "uninstall")
    )
    parser.add_argument("--dest", type=Path, default=DEST)
    parser.add_argument("--container", default=CONTAINER)
    parser.add_argument("--runner", default=RUNNER)
    parser.add_argument("--volume", default=VOLUME)
    parser.add_argument("--secrets", type=Path, default=SECRETS)
    parser.add_argument("--clone", type=Path, default=CLONE)
    args = parser.parse_args(argv)
    return {
        "daily": cmd_daily,
        "weekly": cmd_weekly,
        "drill": cmd_drill,
        "status": cmd_status,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
