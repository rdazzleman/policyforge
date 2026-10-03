"""Reading a Homebrew formula: the parts that decide what a user installs.

`scripts/release_check.py` had no tests at all — not the new assertion, the
whole file. So its behaviour was proven by two people probing it by hand on
the night it was written, and by nothing afterwards. That is this project's
own finding pointed at itself: **a written rule fires only if recalled; an
assertion fires whether or not anyone remembers it exists.**

Only the pure functions are covered here. Everything else in that script
fetches over the network, and a unit test that mocks the fetch would be
asserting the mock. The network half is verified by running the script
against the published formula after a tag, which is what it is for.

The fragile part is `sha256_in_formula`. A formula carries **one** source
hash at two spaces and one per `resource` at four, so the indentation
anchor is the whole mechanism: matching a resource hash instead would
compare a wheel's digest against the source archive and mismatch forever.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release_check

#: The shape a real formula has: source url and hash at two spaces, then
#: resources with their own at four. Trimmed, not invented — the ordering
#: and indentation are what the tap actually ships.
FORMULA = """class Policyforge < Formula
  desc "Generate cross-mapped security policies"
  homepage "https://github.com/rdazzlebot/policyforge"
  url "https://github.com/rdazzlebot/policyforge/archive/refs/tags/v1.5.0.tar.gz"
  sha256 "28136768f54dff1dd2d351869dfdbab82f701c83518e3545f663355381a84b94"
  license "Apache-2.0"

  resource "anyio" do
    url "https://files.pythonhosted.org/packages/a9/d2/anyio-4.15.1.tar.gz"
    sha256 "1111111111111111111111111111111111111111111111111111111111111111"
  end

  resource "click" do
    url "https://files.pythonhosted.org/packages/bb/aa/click-8.3.0.tar.gz"
    sha256 "2222222222222222222222222222222222222222222222222222222222222222"
  end
end
"""


def test_the_source_hash_is_read_and_not_a_resource_hash():
    """The two-space anchor is the mechanism, not a formatting preference.

    A resource's hash compared against the source archive would mismatch
    on every run forever — loud rather than silent, which is the right
    direction to fail, but it would make the check useless.
    """
    assert (
        release_check.sha256_in_formula(FORMULA)
        == "28136768f54dff1dd2d351869dfdbab82f701c83518e3545f663355381a84b94"
    )


def test_a_formula_with_no_source_hash_returns_none():
    """`None` rather than falling through to the first resource's hash.

    Absent and wrong are different answers, and the caller reports the
    first as "cannot answer" instead of a mismatch nobody can act on.
    """
    stripped = FORMULA.replace(
        '  sha256 "28136768f54dff1dd2d351869dfdbab82f701c83518e3545f663355381a84b94"\n', ""
    )

    assert release_check.sha256_in_formula(stripped) is None


def test_the_source_url_is_read_and_not_a_resource_url():
    assert release_check.source_url_in_formula(FORMULA) == (
        "https://github.com/rdazzlebot/policyforge/archive/refs/tags/v1.5.0.tar.gz"
    )


@pytest.mark.parametrize(
    "url, expected",
    [
        ('  url "https://x/archive/refs/tags/v1.5.0.tar.gz"', "v1.5.0"),
        ('  url "https://x/archive/refs/tags/v1.10.2.tar.gz"', "v1.10.2"),
        ('  url "https://x/some/other/path.tar.gz"', None),
    ],
)
def test_the_tag_comes_from_the_url_or_not_at_all(url, expected):
    """A url that names no tag returns `None`, so the caller says so rather
    than comparing a guess against the version it was given."""
    assert release_check.tag_in_formula(url + "\n") == expected


def test_resources_are_read_from_their_filenames():
    """A `resource` block has no version field — only the sdist filename."""
    assert release_check.formula_resources(FORMULA) == {"anyio": "4.15.1", "click": "8.3.0"}


def test_lock_pins_ignore_hash_continuations():
    """A hashed lock wraps with `\\` and `--hash=` lines that are not pins."""
    lock = (
        "anyio==4.15.1 \\\n"
        "    --hash=sha256:abc \\\n"
        "    --hash=sha256:def\n"
        "click==8.3.0 \\\n"
        "    --hash=sha256:aaa\n"
        "# a comment\n"
    )

    assert release_check.lock_pins(lock) == {"anyio": "4.15.1", "click": "8.3.0"}


def test_names_are_compared_case_and_separator_insensitively():
    """PyPI writes `Jinja2` and `python-frontmatter`; a lock may not agree
    on case or on `_` versus `-`, and a false mismatch there would block a
    release for a naming convention."""
    assert release_check.lock_pins("Typing_Extensions==4.16.0\n") == {"typing-extensions": "4.16.0"}


# ---- assertion 4: the cheap check may no longer stand in for the real one ----


def test_the_install_steps_are_the_corrected_ones():
    """**A smoke test that is wrong is indistinguishable from a release
    that is broken, until somebody checks which.**

    The 1.6.0 container run used `policyforge --version` — a flag that
    does not exist, so click exits 2 — and ran `policyforge frameworks`
    in an empty directory, which exits 1 by design because every command
    reads `data/frameworks/` relative to where it runs. Both read as
    release defects. Neither was one.

    Pinned here so the sequence is not retyped from memory at each cut.
    """
    from release_check import INSTALL_STEPS

    names = [name for name, _ in INSTALL_STEPS]
    commands = " ; ".join(command for _, command in INSTALL_STEPS)

    assert "--version" not in commands, (
        "`policyforge --version` does not exist; click exits 2 and it reads as a broken release"
    )
    assert names.index("init") < names.index("frameworks"), (
        "`frameworks` must run after `init`: in an empty directory it exits 1 "
        "by design, which is the product working as documented"
    )
    assert "--build-from-source" in commands, (
        "installing a bottle does not exercise the formula being released"
    )


def test_a_check_that_did_not_run_does_not_pass(monkeypatch, capsys):
    """**The whole point of the issue.** A skipped step leaves a gap; a
    substituted step leaves a false assurance, and this script *was* the
    substitution — it compares values and never installs anything.

    So "docker is unavailable" must not read as "the install is fine".
    Same contract `scripts/check.py` uses for gitleaks: a check nobody ran
    fails until somebody says otherwise, out loud.
    """
    import shutil

    import release_check

    # Patched on `shutil` itself, because `run_install_check` imports it
    # inside the function -- so there is no module attribute to replace.
    # Worth stating: the first version patched `release_check.shutil` and
    # failed with AttributeError rather than silently passing, which is
    # the good kind of wrong.
    monkeypatch.setattr(shutil, "which", lambda _: None)
    ran, lines = release_check.run_install_check()

    assert ran is False, "no docker must report DID NOT RUN, not a result"
    assert any("docker" in line for line in lines)


def test_did_not_run_is_a_different_answer_from_failed():
    """Two states that must not collapse into one.

    `run_install_check` returns `(ran, lines)` rather than a bool,
    precisely so the caller can tell *nothing happened* from *the install
    is broken*. A single boolean would force one of them to masquerade as
    the other, and the one that would masquerade is the dangerous one.
    """
    import inspect

    from release_check import run_install_check

    signature = inspect.signature(run_install_check)
    assert signature.return_annotation != "bool"
    source = inspect.getsource(run_install_check)
    assert "return False" in source and "return True" in source


def test_each_step_failure_stops_the_run():
    """The defect this file's own header records: a Homebrew build failed
    while its harness reported success, because a shell returns the status
    of the LAST command and that was `tail`.

    `&&` propagates the first failure; `;` does not. This is also #182 on
    the same milestone, which is why it is asserted rather than assumed.
    """
    import inspect

    from release_check import run_install_check

    source = inspect.getsource(run_install_check)
    assert '" && ".join' in source, (
        "steps joined with `;` would report the status of the last one, so a "
        "failed install followed by a successful command reads as success"
    )


def _code_of(func) -> str:
    """A function's executable source, with the docstring removed.

    **Both tests below failed on their own explanation first.** They read
    `inspect.getsource`, which includes the docstring — and the docstring
    names the very strings they assert are absent, because it explains why
    those strings are not used.

    A rule that penalises its own reasoning loses to the reasoning being
    deleted. That is the third instance of this shape today and the second
    written by the same hand, which is why it is a helper rather than a
    care-taken-once.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


def test_every_did_not_run_reason_says_why_and_none_reads_as_a_failure():
    """**policyforge-ba's finding, and policyforge-9b's correction of my
    test for it.**

    ba found that a bad image returned docker's own 125 while this
    reported `ran=True`, so the caller printed *"the published formula did
    not install in a clean container"* about a container the formula never
    reached — and `--allow-skip install`, consulted only on the `not ran`
    branch, could not help an offline operator.

    **My test for that asserted one reason's spelling.** There are two
    ways not to run — no docker, and docker refusing the image — and it
    asserted the message from the second. Written on a machine with
    docker; the macOS runner has neither, took the first branch, and the
    test failed while the code was correct.

    *The test for the finding about a check misattributing why it did not
    run, collapsing two reasons for not running into each other.*

    So: **both reasons are constructed here rather than hoped for**, and
    what is asserted is the property — it did not run, and it said why —
    rather than which sentence came back.
    """
    import shutil
    import subprocess

    import release_check

    class _Refused:
        returncode = 125
        stdout = ""
        stderr = "docker: pull access denied for nope"

    reasons = {}

    # 1. docker is absent.
    real_which = shutil.which
    shutil.which = lambda _name: None
    try:
        reasons["no docker"] = release_check.run_install_check()
    finally:
        shutil.which = real_which

    # 2. docker is present and refuses the image. Constructed, so it holds
    #    on a runner with no docker at all.
    real_run = subprocess.run
    shutil_which = shutil.which
    shutil.which = lambda _name: "/usr/bin/docker"
    subprocess.run = lambda *a, **k: _Refused()
    try:
        reasons["image refused"] = release_check.run_install_check(image="nope")
    finally:
        subprocess.run = real_run
        shutil.which = shutil_which

    assert set(reasons) == {"no docker", "image refused"}, "a reason was not exercised"
    for label, (ran, lines) in reasons.items():
        assert ran is False, (
            f"{label!r} reported ran=True, so the caller states that the formula "
            f"did not install — about a container it never reached"
        )
        assert lines and lines[0].strip(), f"{label!r} gives no reason at all"


def test_the_probe_is_what_separates_did_not_run_from_failed():
    """**Why a probe rather than an exit-code allowlist.**

    The suggested remedy was to treat 125, 126 and 127 as did-not-run.
    That masks a real failure: **127 is also what
    `policyforge: command not found` gives inside the container after a
    broken install** — precisely what assertion 4 exists to catch.

    Probing makes *did not run* a fact about docker, and leaves the real
    run's exit status unambiguously about what happened inside.
    """
    from release_check import run_install_check

    code = _code_of(run_install_check)
    assert '"true"' in code or "'true'" in code, (
        "no probe run: did-not-run is being inferred from an exit status"
    )
    for masking in ("125", "126", "127"):
        assert masking not in code, (
            f"{masking} is treated as did-not-run in code. Inside the container "
            f"it can mean the install produced no working CLI, which is the "
            f"failure this check is for."
        )


def test_the_docstring_describes_the_mechanism_the_code_uses():
    """It claimed each step was a separate `docker run`, two lines above
    code joining them into one with `&&`.

    Both propagate correctly, so nothing misbehaved — but this is the file
    whose header records a harness reporting success because the last
    command was `tail`. **A false claim about exit-status mechanics belongs
    here least of anywhere.**

    Asserted as a positive claim rather than the absence of a phrase: the
    docstring now recounts the old wording while correcting it, so *"this
    phrase must not appear"* would fail on the correction itself.
    """
    import inspect

    from release_check import run_install_check

    doc = inspect.getdoc(run_install_check) or ""
    assert "ONE container" in doc, "the docstring no longer states the mechanism used"
    # Quote-agnostic: `ast.unparse` normalises `" && "` to `' && '`, so
    # matching the source spelling fails against the reconstructed code.
    # The assertion is about the join, not about which quote was typed.
    code = _code_of(run_install_check)
    assert " && " in code and ".join" in code, (
        "the code no longer joins the steps, so the docstring is wrong again"
    )


# --- output decoding: the install check crashed before it could report --
#
# Found by policyforge-9b (handle 9b), running the gate against a real
# daemon on 2026-09-23: `text=True` with no `encoding` decoded Homebrew's
# UTF-8 with cp1252, the reader thread died on byte 0x8D, `stdout` stayed
# None, and the concatenation raised TypeError -- so assertion 4 could not
# answer on the platform the release is cut from.


def _emit(tmp_path, payload: bytes) -> list[str]:
    """A real child process writing raw bytes -- the decode under test
    happens inside subprocess, so a mocked result could not reach it."""
    script = tmp_path / "emit.py"
    script.write_text(
        f"import sys\nsys.stdout.buffer.write({payload!r})\nsys.stdout.buffer.flush()\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_output_undecodable_on_every_platform_does_not_crash(tmp_path):
    """**A lone 0x8D is undefined in cp1252 AND invalid as UTF-8**, so the
    old kwargs fail on the Windows machine the release is cut from and on
    the Linux CI runners alike. A real Homebrew line would only have failed
    on Windows, and a test that passes on the runner nobody cuts from would
    have been green for the wrong reason."""
    result = release_check._run(_emit(tmp_path, b"ok \x8d done\n"), timeout=60)
    assert result.stdout is not None, "the reader thread died; stdout is None"
    assert "ok" in result.stdout and "done" in result.stdout


def test_homebrew_output_decodes_to_the_characters_it_wrote(tmp_path):
    """Not merely survived: decoded correctly. errors="replace" must not be
    what makes valid UTF-8 look fine."""
    beer = "\U0001f37a Pouring policyforge"
    result = release_check._run(_emit(tmp_path, beer.encode("utf-8") + b"\n"), timeout=60)
    assert beer in result.stdout


#: Every function in `subprocess` that starts a child. The first version of
#: the guard below checked `run` only, so a `check_output` added elsewhere
#: slipped through while its docstring said any direct call would fail --
#: found by policyforge-80 (handle 80) mutating it. **A guard naming one
#: entry point guards one entry point.**
SUBPROCESS_ENTRY_POINTS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
)


def test_every_subprocess_call_goes_through_the_decoding_helper():
    """**The class, not the instance.** The defect was on BOTH docker calls;
    9b noted the probe had the same shape two statements above the one that
    crashed. So the rule is that `_run` is the only place `subprocess.run`
    appears, and a new call added directly fails here rather than
    reintroducing the crash."""
    import ast

    tree = ast.parse((Path(release_check.__file__)).read_text(encoding="utf-8"))
    outside = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
            f = call.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr in SUBPROCESS_ENTRY_POINTS
                and getattr(f.value, "id", "") == "subprocess"
                and fn.name != "_run"
            ):
                outside.append(f"{fn.name}:{call.lineno}")
    assert not outside, f"a subprocess call outside _run, so not UTF-8 safe: {outside}"


# --- the print half: decoding fixed, then the crash moved to `print` ----
#
# Found by policyforge-9b (handle 9b) reading 0ef705d. errors="replace" on
# decode only replaces INVALID UTF-8; Homebrew's beer mug is VALID UTF-8, so
# it arrives as a real character that a cp1252 stdout cannot encode.


def test_a_character_cp1252_cannot_encode_prints_rather_than_raising():
    """**The opposite fixture to the decode test, deliberately.** The lone
    0x8D there is replaced on decode and never reaches `print` as an
    unencodable character, so that test cannot see this defect at all. This
    needs VALID UTF-8 that is NOT cp1252: the real Homebrew summary line."""
    import io

    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    release_check._safe_stdout(console)
    line = "\U0001f37a  /home/linuxbrew/.linuxbrew/Cellar/policyforge/1.6.0: 1,234 files"
    print(f"    {line}", file=console)
    console.flush()
    printed = console.buffer.getvalue().decode("cp1252")
    assert "Cellar/policyforge" in printed, "the rest of the line must survive the mug"


def test_the_strict_console_really_does_raise_without_the_fix():
    """**The second arm, expected to fail.** If a strict cp1252 stream did
    not raise on the mug, the test above would pass for the wrong reason."""
    import io

    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    with pytest.raises(UnicodeEncodeError):
        print("\U0001f37a", file=console)
        console.flush()


def test_main_actually_calls_the_stdout_fix():
    """**A fix nobody calls is not a fix.** `_safe_stdout` being correct says
    nothing about whether `main` invokes it; three loaders here shipped a
    guard that was proven correct and never proven called. Required to be
    the first statement, so no `print` can run before it."""
    import ast

    tree = ast.parse(Path(release_check.__file__).read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    first = main.body[0]
    assert (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Call)
        and getattr(first.value.func, "id", "") == "_safe_stdout"
    ), "main() must call _safe_stdout() before anything can print"


# --- #258: the formula's url must name the canonical owner --------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/rdazzleman/policyforge/archive/refs/tags/v1.6.1.tar.gz",
    ],
)
def test_a_canonical_source_url_passes(url):
    """What it must ALLOW, beside what it refuses."""
    assert release_check.names_canonical_owner(url)


@pytest.mark.parametrize(
    "url, why",
    [
        (
            "https://github.com/rdazzlebot/policyforge/archive/refs/tags/v1.6.1.tar.gz",
            "the OLD owner -- installs through the 301, so only the string can catch it",
        ),
        (
            "https://github.com/someone-else/policyforge/archive/refs/tags/v1.6.1.tar.gz",
            "any other owner",
        ),
        (
            "https://github.com/rdazzleman/policyforge-evil/archive/refs/tags/v1.6.1.tar.gz",
            "a repository whose NAME starts with ours -- the trailing slash is what refuses it",
        ),
        (
            "http://github.com/rdazzleman/policyforge/archive/refs/tags/v1.6.1.tar.gz",
            "plain http",
        ),
        (None, "a formula with no url at all"),
    ],
)
def test_a_non_canonical_source_url_fails(url, why):
    assert not release_check.names_canonical_owner(url), why


def test_the_owner_is_named_in_one_place():
    """**#258 asked for the assertion to derive from the same constant as
    FORMULA_URL, not to type the owner twice.** So the owner must appear as
    a string literal exactly once in code -- the `OWNER` constant -- and
    FORMULA_URL, the install steps and the prefix must all be built from it.
    Two literals would let the URL and the check that guards it disagree.
    """
    import ast

    tree = ast.parse(Path(release_check.__file__).read_text(encoding="utf-8"))
    literals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "rdazzleman" in node.value
        and not node.value.lstrip().startswith(("After cutting", "Does the formula", "What "))
        and "\n" not in node.value  # docstrings are prose, not configuration
    ]
    assert [n.value for n in literals] == ["rdazzleman"], (
        f"the owner must be named once, as OWNER; found {[n.value for n in literals]}"
    )
    assert release_check.OWNER in release_check.FORMULA_URL
    assert all(release_check.OWNER in cmd for _, cmd in release_check.INSTALL_STEPS[:3])


def _report(monkeypatch, capsys, formula: str) -> str:
    """Drive the real `main()` report, with only the network stubbed.

    `hash_of` returns whatever the formula states, so 1b agrees and cannot
    be the thing that speaks; docker is reported absent and acknowledged,
    so assertion 4 does not run.
    """
    stated = release_check.sha256_in_formula(formula)
    monkeypatch.setattr(release_check, "fetch_formula", lambda url=None: formula)
    monkeypatch.setattr(release_check, "hash_of", lambda url: stated)
    monkeypatch.setattr(
        release_check, "run_install_check", lambda image=None: (False, ["docker is not on PATH"])
    )
    tag = release_check.tag_in_formula(formula).lstrip("v")
    release_check.main(["--version", tag, "--allow-skip", "install"])
    return capsys.readouterr().out


def test_the_report_refuses_a_formula_that_names_the_old_owner(monkeypatch, capsys):
    """**The wiring test, and the one the function tests cannot replace.**

    Deleting the check from `main()` while leaving `names_canonical_owner`
    intact kept every other test in this file green -- measured. A guard
    proven correct and never proven called is what three loaders here
    shipped. So this drives the report itself, with a formula whose `url`
    names `rdazzlebot`: the right tag, the right hash, installable through
    the redirect, and wrong.
    """
    assert "rdazzlebot/policyforge" in FORMULA, "the fixture's premise changed"
    assert "NOT CANONICAL" in _report(monkeypatch, capsys, FORMULA)


def test_the_report_accepts_a_formula_that_names_the_canonical_owner(monkeypatch, capsys):
    """The passing case, through the same path -- or the test above could be
    satisfied by a report that flags every formula."""
    canonical = FORMULA.replace("rdazzlebot/policyforge", "rdazzleman/policyforge")
    assert "NOT CANONICAL" not in _report(monkeypatch, capsys, canonical)


@pytest.mark.parametrize(
    ("homepage", "canonical"),
    [
        ("https://github.com/rdazzleman/policyforge", True),
        ("https://github.com/rdazzleman/policyforge/", True),
        ("https://github.com/rdazzlebot/policyforge", False),  # the published formula today
        ("https://github.com/rdazzleman/policyforge-evil", False),
        (None, False),
    ],
)
def test_the_homepage_must_name_the_canonical_repository(homepage, canonical):
    """policyforge-9b on #348: `brew info` shows `homepage`, and the old
    owner's works only through the 301. Compared as a string, like the url."""
    assert release_check.names_canonical_homepage(homepage) is canonical


def test_the_homepage_is_read_from_the_formula():
    formula = (
        'class Policyforge < Formula\n  homepage "https://github.com/rdazzlebot/policyforge"\n'
    )
    assert release_check.homepage_in_formula(formula) == "https://github.com/rdazzlebot/policyforge"
    assert release_check.homepage_in_formula("no homepage here") is None


def test_the_report_refuses_an_old_owner_homepage_beside_a_canonical_url(monkeypatch, capsys):
    """The wiring test for the homepage half (9b on #348): url canonical,
    homepage still the old owner, which is what 1.6.1 would have shipped."""
    mixed = FORMULA.replace("rdazzlebot/policyforge/archive", "rdazzleman/policyforge/archive")
    assert 'homepage "https://github.com/rdazzlebot/policyforge"' in mixed, "premise"
    report = _report(monkeypatch, capsys, mixed)
    assert "HOMEPAGE NOT CANONICAL" in report
    assert "formula url owner : canonical" in report
