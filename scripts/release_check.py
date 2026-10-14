"""After cutting a tag: does what a user installs match what you released?

Every check in this repository looks *inside* the repository. On 2026-09-19
three sessions declared 1.4.0 done — the tag was right, the version triple
agreed, the changelog was correct, the gate was green, the release notes
were published — and for roughly three hours `brew install
rdazzlebot/tap/policyforge`, the first command in this project's own README
(the tap has since moved to `rdazzleman`),
installed **1.3.0**. Every individual check passed. None of them looked at
the artefact a user receives.

That release was the one adding the `etl-hipaa` guard, so anyone installing
in that window got the build where running `etl-hipaa` on its own silently
rewrote their catalog from 65 crosswalk mappings to 0 and reported nothing
wrong. The tag is not the deliverable. For anyone following the README, the
tap is.

Five assertions, in the order they fail:

1. The published formula's `url` names the tag just cut.
1b. ...and its `sha256` matches the archive that URL actually serves. A
    formula can name the right tag beside a stale hash, and then every
    install fails at verification while assertion 1 reports it correct.
2. The formula's pinned resource versions match the lock at that tag.
3. `changelog.d/` is empty — no fragment survived the release.
4. A user can actually install it — `brew install --build-from-source` in
   a clean container, then the CLI runs, and from 1.8.0 `policyforge
   --version` prints exactly the version cut, with nothing on stderr.
5. The archive the formula names carries no licensed parser: by PATH (a
   licensed module's path at any depth), and by CONTENT, `publish_guard`'s
   own L2 to L4 over the archive's files, against the parsers in the local
   release tag's history (#237). The content layers find the bytes whatever
   the member is called. A tag that gives no reference set is CANNOT ANSWER,
   never a pass.

**Assertion 4 exists because this script was the substitution.** The
recorded procedure was: verify in a container BEFORE pushing the formula.
What happened at 1.6.0 was push → run this script → call it done, and the
formula was live and unverified for the whole gap. The container run
afterwards passed, which is not the point: it ran because the user asked
*"don't you usually run a docker test after cutting a release?"*

This script reads the formula and compares values. **It never installed
anything.** Those are different questions, and the cheap one stood in for
the one that matters — so the fix is that the cheap one can no longer
report success on its own. A skipped step leaves a gap; a substituted step
leaves a false assurance, which is worse.

Assertion 4 is SKIPPABLE and skipping it FAILS unless you say so, the same
contract `scripts/check.py` uses for gitleaks: a check nobody ran must not
look like a check that passed.

**Every one compares values and prints them, and none reads an exit
status.** That is not stylistic. On the same night a Homebrew build failed
while its harness reported success, because the shell returns the status of
the *last* command in a sequence and that was `tail`; `set -o pipefail`
protects a pipe and has no opinion about `cmd; echo; tail`. A release step
that shells out and trusts `$?` inherits exactly that. So does one that
exits quietly on success — a step nobody can tell ran is the gate defect
this project already fixed once, wearing different clothes.

Run it after pushing the tap, not before: it fetches the *published*
formula, because the question is what a user gets rather than what is in
your working copy.
"""

from __future__ import annotations

import argparse
import hashlib
import posixpath
import re
import shlex
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: **The one place the owner is named.** The formula URL, the tap in the
#: install steps and the canonical-owner assertion below all derive from it,
#: so a future transfer is one line rather than a hunt -- and so the
#: assertion and the URL it checks cannot disagree about who the owner is.
#: A literal owner is still a coordinate that moves; it is here because a
#: formula URL has to name *some* owner, and one constant is the least-bad
#: form of that. (content.yml's publish gate, which compared against the old
#: owner by string, is the failure this avoids repeating in five places.)
OWNER = "rdazzleman"
REPOSITORY = f"{OWNER}/policyforge"

FORMULA_URL = f"https://raw.githubusercontent.com/{OWNER}/homebrew-tap/main/Formula/policyforge.rb"

#: What a formula's source `url` must start with. **Trailing slash
#: included**, so `rdazzleman/policyforge-evil/` does not pass as a prefix.
CANONICAL_SOURCE_PREFIX = f"https://github.com/{REPOSITORY}/"


def names_canonical_owner(url: str | None) -> bool:
    """Does the formula's source `url` name the canonical repository?

    **Compared as a string, deliberately never by following the URL.** A
    formula still pointing at the old owner installs perfectly -- through
    GitHub's 301 from `rdazzlebot/policyforge` to `rdazzleman/policyforge`
    -- so assertion 4 cannot see it. And redirect-counting cannot tell the
    two apart either: the canonical archive URL ALSO redirects, a 302 to
    codeload. Measured 2026-09-23. So the only thing that distinguishes them
    is the text the formula carries. #258, found by policyforge-9b (handle
    9b) reviewing #252.
    """
    return bool(url) and url.startswith(CANONICAL_SOURCE_PREFIX)


#: What a formula's `homepage` must be, exactly. `brew info` shows it, so it
#: is something a USER fetches, and an old-owner homepage works only through
#: the same 301 as an old-owner url (policyforge-9b on #348, with 80's ruling
#: that both name the owner). Derived from `OWNER`, like the url prefix.
CANONICAL_HOMEPAGE = f"https://github.com/{REPOSITORY}"

_FORMULA_HOMEPAGE_RE = re.compile(r'^\s*homepage "([^"]+)"', re.MULTILINE)


def homepage_in_formula(formula: str) -> str | None:
    """The formula's `homepage`, as written."""
    match = _FORMULA_HOMEPAGE_RE.search(formula)
    return match.group(1) if match else None


def names_canonical_homepage(homepage: str | None) -> bool:
    """Is the formula's `homepage` the canonical repository, compared as a
    string, never by following it, for the reason `names_canonical_owner`
    gives. A trailing slash is tolerated; any other suffix is not."""
    return bool(homepage) and homepage.rstrip("/") == CANONICAL_HOMEPAGE


#: The lock the formula's resources must agree with. `runtime.txt` rather
#: than `ci.txt`: Homebrew installs what a user runs, not the dev extras.
LOCK = "requirements/runtime.txt"

_FORMULA_URL_RE = re.compile(r'^\s*url "([^"]+)"', re.MULTILINE)
#: The FIRST top-level sha256 — the source archive's. `resource` blocks
#: carry their own below it; assertion 2 handles those against the lock.
_FORMULA_SHA_RE = re.compile(r'^  sha256 "([0-9a-f]{64})"', re.MULTILINE)
_RESOURCE_RE = re.compile(
    r'^  resource "([^"]+)" do\n\s*url "([^"]+)"',
    re.MULTILINE,
)
#: `name==1.2.3` at the start of a line, before any `\` continuation.
_PIN_RE = re.compile(r"^([A-Za-z0-9._-]+)==([^\s\\;]+)", re.MULTILINE)


def fetch_formula(url: str = FORMULA_URL) -> str:
    """The formula as published, not as it sits in a local clone."""
    # The default is a fixed https URL to this project's own tap.
    # `--formula-url` is a maintainer-supplied override, used only to point
    # the check at a perturbed copy while proving it can fail — it is not
    # reachable from any user-facing command, and this script is a release
    # step run by hand rather than anything `policyforge` invokes.
    #
    # Both suppressions sit on the offending line: semgrep honours
    # `nosemgrep` only on that line or the one directly above, so a comment
    # a few lines up reads as documentation and suppresses nothing.
    with urllib.request.urlopen(url, timeout=30) as response:  # nosec B310  # nosemgrep
        return response.read().decode("utf-8")


def source_url_in_formula(formula: str) -> str | None:
    """The source archive the formula fetches, as written."""
    match = _FORMULA_URL_RE.search(formula)
    return match.group(1) if match else None


def sha256_in_formula(formula: str) -> str | None:
    """The formula's top-level `sha256`, which is the source archive's.

    The first one in the file: `resource` blocks carry their own further
    down, and they are checked by assertion 2 against the lock instead.
    """
    match = _FORMULA_SHA_RE.search(formula)
    return match.group(1) if match else None


def hash_of(url: str) -> str:
    """SHA-256 of whatever that URL actually serves, fetched now.

    From the URL the formula names rather than from a copy already on
    disk. A hash taken from the archive you happen to be holding matches
    whatever you are holding, which is the question nobody asked.
    """
    # The URL is read out of the published formula, which is fetched from a
    # fixed https address; `--formula-url` is a maintainer override used only
    # to point this at a perturbed copy while proving it can fail. Suppression
    # on the offending line, not above it — see CONTRIBUTING.
    with urllib.request.urlopen(url, timeout=120) as response:  # nosec B310  # nosemgrep
        digest = hashlib.sha256()
        for chunk in iter(lambda: response.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tag_in_formula(formula: str) -> str | None:
    """The tag the formula's source `url` names, e.g. `v1.4.0`."""
    match = _FORMULA_URL_RE.search(formula)
    if not match:
        return None
    tag = re.search(r"/tags/([^/]+)\.tar\.gz", match.group(1))
    return tag.group(1) if tag else None


def formula_resources(formula: str) -> dict[str, str]:
    """`{name: version}` for every `resource` block, from its sdist URL.

    Read from the URL rather than a version field because a `resource` has
    no version field — the version is only in the filename it fetches.
    """
    found: dict[str, str] = {}
    for name, url in _RESOURCE_RE.findall(formula):
        filename = url.rsplit("/", 1)[-1]
        stem = re.sub(r"\.(tar\.gz|zip|tar\.bz2)$", "", filename)
        if "-" in stem:
            found[name.lower().replace("_", "-")] = stem.rsplit("-", 1)[-1]
    return found


def lock_pins(text: str) -> dict[str, str]:
    """`{name: version}` from a hashed requirements lock."""
    return {name.lower().replace("_", "-"): version for name, version in _PIN_RE.findall(text)}


#: The container steps, in order, each run as its own process so its exit
#: status is read rather than inherited.
#:
#: **The smoke tests here are the corrected ones.** The 1.6.0 run used
#: `policyforge --version`, which did not exist in 1.6.0 — the flag was
#: invented, click exits 2, and that reads as a broken release to anyone
#: skimming. It exists from 1.8.0 (#219), and `version_step` adds it, for
#: those releases only, because its expected output names the version cut.
#: And it ran `policyforge frameworks` in an empty directory, which exits 1
#: by design: every command reads `data/frameworks/` relative to where it
#: runs, and the install's own caveats say so three lines above. **A smoke
#: test that is wrong is indistinguishable from a release that is broken
#: until somebody checks which**, so the correct sequence is pinned here
#: rather than retyped from memory each time.
INSTALL_STEPS: tuple[tuple[str, str], ...] = (
    ("tap", f"brew tap {OWNER}/tap"),
    ("install", f"brew install --build-from-source {OWNER}/tap/policyforge"),
    ("audit", f"brew audit --strict --online {OWNER}/tap/policyforge"),
    ("init", "cd /tmp/pf && policyforge init"),
    ("frameworks", "cd /tmp/pf && policyforge frameworks"),
)

CONTAINER_IMAGE = "homebrew/brew"

#: The first release with `policyforge --version` (#219 landed on the 1.8
#: train; `v1.7.0` has no such option, measured on #224). Against an older
#: release the step would exit 2 and read as a broken release, which is the
#: 1.6.0 mistake above, so it is left out there and the output says so.
FIRST_WITH_VERSION = (1, 8, 0)

#: What a check prints in place of the step for an older release: one
#: definition, so `release._install_script`'s chain (#232) says the same.
VERSION_NOT_RUN = "version: not run, `policyforge --version` first ships in " + ".".join(
    map(str, FIRST_WITH_VERSION)
)

#: A version as a cut names it. It goes into a shell command, so nothing
#: else is accepted: ASCII digits only, since `\d` (and `int`) also take
#: other scripts' digits.
_RELEASE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def version_step(version: str) -> tuple[str, str] | None:
    """The container step that requires `policyforge --version` to print
    exactly `policyforge <version>` with NOTHING on stderr (#224), or None
    for a release older than `FIRST_WITH_VERSION`.

    **The empty stderr is the point.** `--version` reads the installed
    distribution's metadata and writes a note to stderr when the running
    code's `policyforge.__version__` disagrees, so a skew between the two in
    what a user installed fails here. `release._install_script` runs this
    same step before the user approves (#232), beside its own read of
    `__version__` (the source string), which can't see that skew; the two
    read different things, and both stay.

    Each failure says which rule it broke, on stderr, because the caller
    shows the run's last lines and stderr comes last. An exit from
    `--version` itself (click's 2 for a missing option, or 1 for "not
    installed") stops the chain and prints its stderr. A pass prints
    `version: <what it read>` on stdout, so a run's output shows what was
    checked (b5 on #231).
    """
    bare = version[1:] if version.startswith("v") else version
    if not _RELEASE_VERSION.fullmatch(bare):
        raise ValueError(f"not a release version (X.Y.Z): {version!r}")
    if tuple(int(part) for part in bare.split(".")) < FIRST_WITH_VERSION:
        return None
    expected = shlex.quote(f"policyforge {bare}")
    return (
        "version",
        " && ".join(
            [
                "e=$(mktemp)",
                '{ v=$(policyforge --version 2>"$e") || { s=$?; '
                'echo "version: policyforge --version exited $s" >&2; cat "$e" >&2; false; }; }',
                '{ [ "$v" = '
                + expected
                + ' ] || { echo "version: expected "'
                + expected
                + '", got [$v]" >&2; false; }; }',
                '{ [ ! -s "$e" ] || { echo "version: policyforge --version wrote to stderr:" >&2; '
                'cat "$e" >&2; false; }; }',
                'echo "version: $v"',
            ]
        ),
    )


def install_steps(version: str) -> tuple[tuple[str, str], ...]:
    """`INSTALL_STEPS` for this release: the version step goes right after
    the install, when there is one (`version_step`)."""
    steps = list(INSTALL_STEPS)
    step = version_step(version)
    if step is not None:
        steps.insert([name for name, _ in steps].index("install") + 1, step)
    return tuple(steps)


#: The licensed-framework parsers that stay private from 1.7 (the user's
#: decision; #32, #33). One definition: release.py reads these too, so the
#: set it removes from the public commit and the set this checks the released
#: archive for are the same set.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_guard  # noqa: E402  (#237: its content layers, not a copy of them)

# LICENSED_MODULES is re-exported: release.py reads release_check.LICENSED_MODULES.
from licensed_modules import LICENSED_MODULES, LICENSED_PATHS  # noqa: E402, F401  (#48: one list)

#: The repository whose local release tag gives the content layers their
#: reference set: this checkout, which is the private one on the machine
#: that cuts the release (80's ruling on #237). A test points it elsewhere.
SOURCE_ROOT = REPO_ROOT


def _path_key(name: str) -> str:
    """A member name or an entry as `licensed_in_archive` compares them: `\\`
    as `/` (#239); a `:<stream>` suffix off the last component, and trailing
    dots and spaces off every component (#306); then normalised and
    casefolded. A `.` or `..` segment is left for `normpath`, or `x/../` would
    become `x//`."""
    parts = name.replace("\\", "/").split("/")
    parts[-1] = parts[-1].split(":", 1)[0]
    parts = [part if part in (".", "..") else part.rstrip(". ") for part in parts]
    return posixpath.normpath("/".join(parts)).casefold()


def licensed_in_archive(names: list[str]) -> list[str]:
    """The licensed module paths among a source archive's member names.

    **At any depth, the archive root included** (80's ruling on #236): a
    member matches when its path IS a `LICENSED_PATHS` entry, or ENDS WITH
    `/` plus one. A GitHub archive nests everything under one top directory
    (`policyforge-1.7.0/src/...`), and this used to match only on the path
    after it, so a licensed parser at the root or under any other prefix
    passed silently. This is the last check before licensed code is public,
    so it doesn't depend on the archive's layout.

    The `/` is the component boundary: `.../mysrc/policyforge/...` is not the
    entry. A basename alone never matches. That's this PATH rule's limit, not a
    claim that such a file is clean: a copy of a licensed parser under another
    name or directory is for the content layers to find (#237).

    **Case-insensitive** (80's ruling on PR 238, from ba's question): both
    sides are casefolded, because on the filesystems most users unpack onto
    (Windows, and macOS by default) a differently capitalised path IS the
    same file, and this check is about publishing those bytes. (Not because
    Python imports it: 1d measured that the importer checks case on 3.14,
    win32.) A finding keeps the archive's own spelling.

    **Normalised first** (1d on PR 238): a member is matched on
    `posixpath.normpath` of its name, so `src//…`, `src/./…` and
    `src/x/../…` (which `tarfile` extracts to the plain path) are the entry
    too. That also makes `<entry>/` match, which errs toward failing, and is
    what `archive_names` reports for a directory anyway: `tarfile` gives a
    directory member's name without its trailing `/`.

    **`\\` is a separator too** (80's ruling (a) on #239): a member named
    `pkg\\src\\policyforge\\...` is one name with no `/`, but on win32 both
    Python's `tarfile` and Windows' own `tar.exe` (bsdtar 3.8.8) extract it as
    the nested path (ba measured both), so it publishes those bytes at the
    licensed module's place. Every `\\` is mapped to `/` before `normpath`,
    on member and entry alike, which errs toward failing.

    **So are Windows' other spellings of the same path** (80's ruling (a) on
    #306): Windows drops trailing dots and spaces from each path component,
    and `<file>::$DATA` (or any `:<stream>`) names a file's main stream, so on
    win32 `…/ingest/<module>.py.`, `…/ingest/<module>.py `, `…/ingest./…` and
    `…/<module>.py::$DATA` all extract at the licensed path (b5 measured each
    through `tarfile`). Each component loses its trailing dots and spaces, and
    the last loses a `:<stream>` suffix, before matching. **Not claimed:** 8.3
    short names (`STAND_~1.PY`) and any other Windows aliasing are the content
    layer's to find (#237), not this path rule's."""
    # The entries go through the same normalisation as a member (1d on PR 238).
    licensed = tuple(_path_key(path) for path in LICENSED_PATHS)

    def matches(name: str) -> bool:
        folded = _path_key(name)
        return any(folded == path or folded.endswith("/" + path) for path in licensed)

    return sorted(name for name in names if matches(name))


def archive_members(url: str) -> list[tuple[str, bytes | None]]:
    """Every member of the .tar.gz a URL serves, in order: its name, and its
    bytes if it is a regular file (None for a directory or a link). Read in
    memory; nothing is extracted."""
    import io
    import tarfile
    import urllib.request

    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https archive URL: {url!r}")
    with urllib.request.urlopen(url, timeout=120) as response:  # nosec B310  # nosemgrep
        data = response.read()
    members: list[tuple[str, bytes | None]] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            handle = tar.extractfile(member) if member.isfile() else None
            members.append((member.name, handle.read() if handle else None))
    return members


def archive_names(url: str) -> list[str]:
    """The member names of the .tar.gz a URL serves."""
    return [name for name, _ in archive_members(url)]


def content_findings(files: list[tuple[str, bytes]], tag: str) -> list:
    """`publish_guard`'s content layers (L2 renamed blobs, L3 AST content, L4
    licensed catalogs) over an archive's files, against the parsers in the
    history of the local release tag `tag` (80's ruling on #237). Raises
    `publish_guard.NothingToCheck` when that tag or its history can't give a
    reference set: the caller reports CANNOT ANSWER, never "none".

    These find a licensed parser's BYTES whatever its member is called, which
    the path rule can't: a renamed file, a backslash-separated name (#239),
    a Windows 8.3 short name. What they can't see is the guard's own stated
    limit: a copy renamed with its names and imports rewritten."""
    return publish_guard.check_files(files, publish_guard.reference(tag, SOURCE_ROOT))


def _run(cmd: list[str], *, timeout: int):
    """`subprocess.run`, decoding output as UTF-8 and never failing to decode.

    **Both docker calls go through here so the fix cannot land on one and not
    the other**, which is what it would take to repeat this.

    `text=True` alone decodes with the locale encoding -- cp1252 on the
    Windows machine the release is cut from. Homebrew prints UTF-8, and its
    own `🍺` is `F0 9F 8D BA`; `0x8D` is undefined in cp1252. The
    reader thread died, `stdout` stayed `None`, and the next line raised
    `TypeError` concatenating it -- so the one assertion that proves a user
    can install the release **crashed before reporting**, on its first run on
    the platform it is cut from. Found by policyforge-9b (handle 9b) running
    it against a real daemon on 2026-09-23, reproduced here.

    `errors="replace"` because this output is only ever shown as a tail: a
    gate must not crash on a byte it is merely displaying. The exit code, not
    the text, is what decides the verdict.
    """
    import subprocess

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def run_install_check(version: str, image: str = CONTAINER_IMAGE) -> tuple[bool, list[str]]:
    """Install the published formula in a clean container and run the CLI.

    `version` is the release just cut. It is required, not defaulted, so a
    caller can't leave out the `--version` step by forgetting it
    (`install_steps`). A release older than `FIRST_WITH_VERSION` gets a
    line saying the step was not run, never a silent pass.

    Returns `(ran, lines)`. **`ran` is False when the container could not
    be started at all** — no docker, no daemon, no image — which is a
    different answer from "the install failed" and must not be collapsed
    into it. The caller turns *did not run* into a failure unless the
    operator said otherwise; it does not turn it into a pass.

    The steps run in ONE container, joined with `&&`, so the first failure
    stops the run and its status propagates. `;` would not -- that is the
    `cmd; echo; tail` shape this file's header records, where a Homebrew
    build failed while its harness reported success because the last
    command was `tail`.

    **This docstring said the opposite until policyforge-ba read it.** It
    claimed each step was a separate `docker run`, two lines above code
    joining them into one. Both mechanisms propagate correctly so nothing
    misbehaved -- but a false claim about exit-status mechanics, in the
    file whose header is about exit-status mechanics, is the worst place
    to leave one.

    **Whether the container could start is established by a probe rather
    than inferred from the run's exit status.** ba found that a bad image
    returns docker's own 125 and this function reported `ran=True`, so the
    caller printed "the published formula did not install" about a
    container the formula never reached -- and `--allow-skip` could not
    help, because it is only consulted on the `not ran` branch.

    Probing rather than treating 125/126/127 as did-not-run, which was the
    suggested remedy: **127 is also what `policyforge: command not found`
    gives inside a container after a genuinely broken install**, so that
    rule would mask the failure this check exists to find. The probe makes
    "did not run" a fact about docker and leaves the real exit status
    unambiguously about what happened inside.
    """
    import shutil

    # Built before docker is asked anything, so a bad version is refused
    # wherever this runs.
    steps = install_steps(version)
    lines: list[str] = []
    if not any(name == "version" for name, _ in steps):
        lines.append(VERSION_NOT_RUN)
    if shutil.which("docker") is None:
        return False, [*lines, "docker is not on PATH"]

    # Can docker start this image at all? Cheap, and it is the only way to
    # tell "the container never ran" from "the install failed", because
    # some exit codes are produced by both.
    probe = _run(["docker", "run", "--rm", image, "true"], timeout=600)
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip().splitlines()[-1:] or ["no output"]
        return False, [*lines, f"could not start {image}: {detail[0]}"]

    script = " && ".join(command for _, command in steps)
    # One container, but the steps are joined with `&&` so the FIRST
    # failure stops the run and its status propagates. `;` would not --
    # that is the `cmd; echo; tail` shape the header warns about, and it
    # is also issue #182 on this milestone.
    proc = _run(
        ["docker", "run", "--rm", image, "bash", "-lc", f"mkdir -p /tmp/pf && {script}"],
        timeout=3600,
    )
    # `or ""` as a second line of defence behind `_run`: an absent stream is
    # shown as absent, and the exit status below is reported either way.
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-12:]
    lines.extend(tail)
    lines.append(f"docker exit status: {proc.returncode}")
    return True, lines


def _safe_stdout(stream=None) -> None:
    """Make every `print` below survive a character the console cannot encode.

    **`_run` fixed the decode and moved the crash to the print.** Decoding as
    UTF-8 with `errors="replace"` only replaces bytes that are *invalid*
    UTF-8. Homebrew's `🍺` is *valid* UTF-8, so it decodes into a real
    character -- and then `print` must re-encode it for stdout, which on the
    Windows machine the release is cut from is cp1252, where it does not
    exist. `UnicodeEncodeError`, same traceback, one step later. Found by
    policyforge-9b (handle 9b) reading `0ef705d`.

    **And it crashes on the path the gate exists for.** The tail is the last
    twelve lines. On success the last step is `audit`, whose output is ASCII.
    On a FAILED install the `&&` chain stops at `install`, and the tail is
    that step's last lines -- which include the beer-mug summary of the last
    dependency poured before the failure. So it hid the install error, the
    one output this check is for.

    **A class fix, not line 445.** Every `print` here can carry text from
    outside -- the docker tail, exception messages, package names -- so the
    stream is reconfigured once rather than each call site guarded.
    `errors="replace"` rather than switching to UTF-8: an operator on a
    cp1252 console then reads `?` for the mug instead of mojibake.

    The same mistake appeared in 1d's own probe an hour before 9b found this
    one -- a `print` of the mug failed to the console -- and 1d read it as
    proof the decode had worked without asking whether the product printed
    the same way.
    """
    stream = stream if stream is not None else sys.stdout
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(errors="replace")


def parser() -> argparse.ArgumentParser:
    """The command line, apart from running it: release.py's step 14 test
    parses the argv that step builds with this exact parser, so the two
    cannot drift apart again (#415)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="the version just cut, e.g. 1.4.0")
    parser.add_argument(
        "--formula-url", default=FORMULA_URL, help="override the published formula location"
    )
    parser.add_argument(
        "--allow-skip",
        action="append",
        default=[],
        choices=["install"],
        help="acknowledge that a check did not run. Without this, a check that "
        "did not run fails -- the same contract scripts/check.py uses.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _safe_stdout()
    args = parser().parse_args(argv or [])

    expected_tag = args.version if args.version.startswith("v") else f"v{args.version}"
    failures: list[str] = []

    # ---------------------------------------------------------------- 1
    print("=" * 62)
    print(f"1. The published formula installs {expected_tag}")
    print("=" * 62)
    # Recorded rather than returned. An unreachable formula is a transient;
    # a leftover fragment is deterministic and needs no network. Returning
    # here let the transient mask it, so an operator fixed the network, re-ran,
    # and only then learned a fragment had missed the release — two round
    # trips, the second reporting the failure this script prints first
    # precisely because nothing else will catch it. A transient must not be
    # able to hide a result that was already known. (1d, demonstrated.)
    formula: str | None = None
    try:
        formula = fetch_formula(args.formula_url)
    except Exception as exc:  # noqa: BLE001 - any fetch failure is a real answer
        print(f"  could not fetch the formula: {type(exc).__name__}: {exc}")
        print("  -> CANNOT ANSWER")
        failures.append(
            f"could not fetch the formula ({type(exc).__name__}) — cannot tell what a "
            "user installs, so this is a failure rather than a pass; retry"
        )

    if formula is not None:
        found_tag = tag_in_formula(formula)
        print(f"  formula url names : {found_tag}")
        print(f"  tag just cut      : {expected_tag}")
        if found_tag != expected_tag:
            failures.append(f"the tap installs {found_tag}, not {expected_tag} — push the formula")
            print("  -> MISMATCH")
        else:
            print("  -> match")

        # The right tag at the wrong owner still installs -- through the
        # redirect -- so assertion 4 passes it. Only the string can tell.
        source = source_url_in_formula(formula)
        print(f"  formula url owner : {'canonical' if names_canonical_owner(source) else source}")
        if not names_canonical_owner(source):
            failures.append(
                f"the formula's url is {source!r}, not under {CANONICAL_SOURCE_PREFIX} -- it "
                "installs only through a redirect that ends if anything is created at the "
                "old name"
            )
            print("  -> NOT CANONICAL")
        homepage = homepage_in_formula(formula)
        print(
            "  formula homepage  : "
            f"{'canonical' if names_canonical_homepage(homepage) else homepage}"
        )
        if not names_canonical_homepage(homepage):
            failures.append(
                f"the formula's homepage is {homepage!r}, not {CANONICAL_HOMEPAGE} -- `brew info` "
                "shows it, and it resolves only through the old owner's redirect"
            )
            print("  -> HOMEPAGE NOT CANONICAL")

        # 1b. Naming the right tag is not the same as installing it. A
        # formula edited by hand can carry the new url beside the previous
        # release's sha256, and then *every* install fails at verification
        # while this check reports the url as correct. Asked of the archive
        # the formula actually names, fetched now — a hash taken from a copy
        # already on disk matches whatever you are holding, which is the
        # question nobody asked. (1d, who found assertion 1 could not see it.)
        print()
        print("=" * 62)
        print("1b. ...and the sha256 matches what that URL serves")
        print("=" * 62)
        stated = sha256_in_formula(formula)
        source = source_url_in_formula(formula)
        if not stated or not source:
            print(f"  could not read url/sha256 from the formula (url={source}, sha256={stated})")
            print("  -> CANNOT ANSWER")
            failures.append("the formula's url or sha256 could not be read — check its shape")
        else:
            try:
                actual = hash_of(source)
            except Exception as exc:  # noqa: BLE001 - not knowing is a failure
                print(f"  could not fetch the archive: {type(exc).__name__}: {exc}")
                print("  -> CANNOT ANSWER")
                failures.append(
                    f"could not fetch the source archive ({type(exc).__name__}) — cannot tell "
                    "whether the formula's sha256 is current; retry"
                )
            else:
                print(f"  formula sha256 : {stated}")
                print(f"  archive served : {actual}")
                if stated != actual:
                    failures.append(
                        "the formula's sha256 does not match the archive it names — every "
                        "`brew install` fails at verification; recompute it from that URL"
                    )
                    print("  -> MISMATCH")
                else:
                    print("  -> match")

    # ---------------------------------------------------------------- 2
    print()
    print("=" * 62)
    print("2. The formula's resources match the lock")
    print("=" * 62)
    if formula is None:
        # Skipped with a stated reason rather than silently absent. A check
        # that did not run and does not say so is the gate defect #112 fixed.
        # Flow continues: assertion 3 is local and must still be answered.
        print("  SKIPPED — the formula could not be fetched (see 1 above)")
        print("  -> not answered")
    else:
        resources = formula_resources(formula)
        pins = lock_pins((REPO_ROOT / LOCK).read_text(encoding="utf-8"))
        overlap = sorted(set(resources) & set(pins))
        mismatched = [(n, resources[n], pins[n]) for n in overlap if resources[n] != pins[n]]
        unlocked = sorted(set(resources) - set(pins))

        print(f"  formula resources : {len(resources)}")
        print(f"  lock pins         : {len(pins)}")
        print(f"  overlapping       : {len(overlap)}")
        print(f"  version mismatches: {len(mismatched)}")
        print(f"  in formula, not in lock: {len(unlocked)}")
        for name, got, want in mismatched:
            print(f"    {name}: formula {got}, lock {want}")
        for name in unlocked:
            print(f"    {name}: pinned by the formula and in no lock")
        if mismatched or unlocked:
            failures.append(
                f"{len(mismatched)} resource(s) disagree with {LOCK} and "
                f"{len(unlocked)} are unlocked — the lock decides"
            )
            print("  -> MISMATCH")
        else:
            print("  -> match")

    # ---------------------------------------------------------------- 3
    print()
    print("=" * 62)
    print("3. No changelog fragment survived the release")
    print("=" * 62)
    fragment_dir = REPO_ROOT / "changelog.d"
    leftover = (
        sorted(p.name for p in fragment_dir.glob("*.md") if p.name != "README.md")
        if fragment_dir.is_dir()
        else []
    )
    print(f"  changelog.d/ entries: {len(leftover)}")
    for name in leftover:
        print(f"    {name}")
    if leftover:
        failures.append(
            f"{len(leftover)} fragment(s) still in changelog.d/ — their entries "
            f"missed {args.version} and nothing else will say so"
        )
        print("  -> LEFTOVER")
    else:
        print("  -> empty")

    # ---------------------------------------------------------------- 4
    print()
    print("=" * 62)
    print("4. A user can actually install it")
    print("=" * 62)
    ran, lines = run_install_check(args.version)
    for line in lines:
        print(f"    {line}")
    if not ran:
        if "install" in args.allow_skip:
            print("  -> DID NOT RUN, acknowledged with --allow-skip install")
            print("     Nothing here has installed anything. The formula is unverified.")
        else:
            failures.append(
                "the install check did not run, and nothing else in this script "
                "installs anything -- run it where docker is available, or accept "
                "the gap explicitly with --allow-skip install"
            )
            print("  -> DID NOT RUN")
    elif lines and lines[-1].endswith(" 0"):
        print("  -> installed and ran")
    else:
        failures.append(
            "the published formula did not install in a clean container -- this is "
            "what a user following the README gets"
        )
        print("  -> FAILED")

    # ---------------------------------------------------------------- 5
    print()
    print("=" * 62)
    print("5. The released archive carries no licensed parser")
    print("=" * 62)
    # From 1.7 the licensed-framework parsers are private (#32, #33). The
    # archive the formula names is exactly what `brew` fetches, so it is the
    # one to ask, not a tree on disk.
    source = source_url_in_formula(formula) if formula is not None else None
    if not source:
        print("  SKIPPED — no formula url to read (see 1 above)")
        failures.append("could not read the formula's archive url, so the archive is unchecked")
        print("  -> not answered")
    else:
        try:
            members = archive_members(source)
            found = licensed_in_archive([name for name, _ in members])
        except Exception as exc:  # noqa: BLE001 - not knowing is a failure
            print(f"  could not read the archive: {type(exc).__name__}: {exc}")
            failures.append(
                f"could not list the source archive ({type(exc).__name__}), so whether it "
                "carries a licensed parser is unknown; retry"
            )
            print("  -> CANNOT ANSWER")
            members = None
        else:
            print(f"  licensed parser paths in {source}: {len(found)}")
            for name in found:
                print(f"    {name}")
            if found:
                failures.append(
                    f"the released archive carries {len(found)} licensed parser path(s) — "
                    "the public tool must not (#32, #33)"
                )
                print("  -> PRESENT")
            else:
                print("  -> none")
        if members is not None:
            # The path rule reads names; a licensed parser under any other
            # name is only found by its bytes (#237).
            files = [(name, data) for name, data in members if data is not None]
            print(f"  content layers (L2-L4) over {len(files)} file(s), against {expected_tag}:")
            try:
                content = content_findings(files, expected_tag)
            except publish_guard.NothingToCheck as exc:
                print(f"    could not derive the reference set: {exc}")
                failures.append(
                    f"the content layers could not run: {expected_tag}'s history gave no "
                    "reference set, so a licensed parser under another name would read "
                    "clean -- this is not a pass (80's ruling on #237)"
                )
                print("  -> CANNOT ANSWER")
            else:
                for finding in content:
                    print(f"    {finding}")
                if content:
                    failures.append(
                        f"the released archive carries licensed content: {len(content)} "
                        "content-layer finding(s) -- the public tool must not (#32, #33)"
                    )
                    print("  -> PRESENT")
                else:
                    print("  -> none")

    # ----------------------------------------------------------------
    print()
    print("=" * 62)
    if failures:
        # Leftover fragments first: the other two are visible the moment
        # someone installs, while a fragment that missed its version is
        # invisible until a reader goes looking for an entry that is not
        # there — and by then the release is published.
        for line in sorted(failures, key=lambda f: "fragment" not in f):
            print(f"  FAILED: {line}")
        print("=" * 62)
        return 1
    print(f"  All five hold for {expected_tag}.")
    print("=" * 62)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
