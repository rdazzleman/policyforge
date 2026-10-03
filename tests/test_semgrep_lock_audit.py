"""semgrep's lock is audited in advisory mode, bounded exactly (#82; 80's ruling 1774).

Reports are shaped like pip-audit 2.10.1's `-f json` output as measured on
this lock (`dependencies[].vulns[].id/aliases/fix_versions`), not built from
`semgrep_lock_audit`'s own reading of them. Each of 80's four conditions has
tests here: (1) only this lock is advisory, by path, and every other audit
still blocks; (2) advisories are listed and counted, and an audit that can't
run fails; (3) an advisory the product shares still blocks; (4) the
release notes name it, read wherever they are: the fragment while the train
is unreleased, and the version's `CHANGELOG.md` section once the release
commit has assembled the fragments and deleted them (#118).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import semgrep_lock_audit as sla  # noqa: E402

#: A runtime lock pinning pyjwt at the FIXED version, as the real one does.
RUNTIME = {"pyjwt": {"2.14.0"}, "requests": {"2.34.2"}}


def _dep(name, version, *vulns):
    return {"name": name, "version": version, "vulns": list(vulns)}


def _vuln(vid, aliases=(), fixes=("2.14.0",)):
    return {"id": vid, "aliases": list(aliases), "fix_versions": list(fixes)}


def _report(*deps):
    return {"dependencies": list(deps) or [_dep("mcp", "1.23.3")], "fixes": []}


# ---- condition 2: listed and counted, never blocking on its own -------------


def test_a_clean_lock_has_nothing_to_report():
    assert sla.classify(_report(_dep("pyjwt", "2.13.0")), RUNTIME) == ([], [])


def test_an_advisory_the_product_cannot_install_is_listed_not_blocking():
    blocking, advisories = sla.classify(
        _report(_dep("PyJWT", "2.13.0", _vuln("CVE-2026-0001"), _vuln("CVE-2026-0002"))),
        RUNTIME,
    )
    assert blocking == []
    assert advisories == [
        "ADVISORY  pyjwt 2.13.0: CVE-2026-0001 (fixed in 2.14.0)",
        "ADVISORY  pyjwt 2.13.0: CVE-2026-0002 (fixed in 2.14.0)",
    ]


def test_the_summary_line_counts_every_advisory():
    advisories = ["ADVISORY  a", "ADVISORY  b", "ADVISORY  c"]
    assert sla.summary_line(advisories) == (
        "semgrep lock: 3 advisories (advisory, not blocking; #82)"
    )


def test_a_skipped_dependency_still_blocks():
    dep = {"name": "semgrep", "version": "1.177.0", "skip_reason": "could not resolve"}
    blocking, _ = sla.classify(_report(dep, _dep("pyjwt", "2.13.0")), RUNTIME)
    assert blocking == ["NOT AUDITED  semgrep: could not resolve"]


def test_a_report_with_no_dependencies_still_blocks():
    blocking, _ = sla.classify({"dependencies": [], "fixes": []}, RUNTIME)
    assert any("ZERO" in b for b in blocking)


# ---- condition 3: shared exposure still blocks ------------------------------


def test_an_advisory_the_product_shares_at_the_same_version_blocks():
    """Written from the runtime lock's side: requests 2.34.2 is what the
    product installs, so an advisory on exactly that version is the
    product's too."""
    blocking, advisories = sla.classify(
        _report(_dep("requests", "2.34.2", _vuln("CVE-2026-0003"))), RUNTIME
    )
    assert advisories == []
    assert blocking == [
        "SHARED WITH THE PRODUCT  requests 2.34.2: CVE-2026-0003 (fixed in 2.14.0): "
        "requirements/runtime.txt pins requests 2.34.2 too, so this blocks"
    ]


def test_the_same_package_at_a_different_version_stays_advisory():
    """pyjwt is in both locks, 2.13.0 in semgrep's and 2.14.0 in the
    product's: the real case today. Only the exact version is shared."""
    blocking, advisories = sla.classify(
        _report(_dep("pyjwt", "2.13.0", _vuln("CVE-2026-0001"))), RUNTIME
    )
    assert blocking == [] and len(advisories) == 1


def test_package_names_are_normalised_when_compared_with_the_runtime_lock():
    blocking, _ = sla.classify(
        _report(_dep("Typing_Extensions", "4.15.0", _vuln("CVE-2026-0004"))),
        {"typing-extensions": {"4.15.0"}},
    )
    assert blocking and blocking[0].startswith("SHARED WITH THE PRODUCT  typing-extensions")


def test_pinned_reads_the_real_lock_shape():
    text = (
        "pyjwt==2.13.0 \\\n    --hash=sha256:aa \\\n    # via mcp\n"
        "cffi==2.1.1 ; platform_python_implementation != 'PyPy' \\\n"
        "Typing_Extensions==4.15.0 \\\n"
    )
    assert sla.pinned(text) == {
        "pyjwt": {"2.13.0"},
        "cffi": {"2.1.1"},
        "typing-extensions": {"4.15.0"},
    }


# A split pin as `uv pip compile --universal` writes one: the same package
# twice, under complementary markers, each with its own hashes. Hand-written
# from the tool's output shape, not from `pinned`'s reading of it.
SPLIT = (
    "pyjwt==2.13.0 ; python_full_version < '3.12' \\\n"
    "    --hash=sha256:aa \\\n"
    "    --hash=sha256:bb\n"
    "    # via mcp\n"
    "pyjwt==2.14.0 ; python_full_version >= '3.12' \\\n"
    "    --hash=sha256:cc \\\n"
    "    --hash=sha256:dd\n"
    "    # via mcp\n"
    "requests==2.34.2 \\\n"
    "    --hash=sha256:ee\n"
)


def test_a_split_pin_keeps_every_version():
    assert sla.pinned(SPLIT)["pyjwt"] == {"2.13.0", "2.14.0"}


@pytest.mark.parametrize("version", ["2.13.0", "2.14.0"])
def test_an_advisory_on_either_half_of_a_split_pin_blocks(version):
    """#85 (ba on #84): the product installs BOTH versions, one per Python,
    so an advisory on either is shared. Keeping only the last pin read the
    lower one as advisory, the unsafe direction of 80's condition 3."""
    blocking, advisories = sla.classify(
        _report(_dep("pyjwt", version, _vuln("CVE-2026-0005"))), sla.pinned(SPLIT)
    )
    assert advisories == []
    assert blocking and blocking[0].startswith(f"SHARED WITH THE PRODUCT  pyjwt {version}")


def test_a_version_outside_the_split_stays_advisory():
    blocking, advisories = sla.classify(
        _report(_dep("pyjwt", "2.12.0", _vuln("CVE-2026-0006"))), sla.pinned(SPLIT)
    )
    assert blocking == [] and len(advisories) == 1


def test_the_real_runtime_lock_is_read():
    """Against the committed file, not a fixture: the product's pyjwt pin is
    what makes today's eight advisories advisory rather than shared."""
    pins = sla.pinned(sla.RUNTIME_LOCK.read_text(encoding="utf-8"))
    assert pins.get("pyjwt") and "2.13.0" not in pins["pyjwt"]


# ---- condition 1: only this lock is advisory, by path -----------------------


def test_the_advisory_lock_is_semgreps_by_path():
    assert sla.LOCK.relative_to(ROOT).as_posix() == "requirements/semgrep/semgrep.txt"
    assert sla.RUNTIME_LOCK.relative_to(ROOT).as_posix() == "requirements/runtime.txt"


@pytest.mark.parametrize(
    "path", ["scripts/check.py", ".github/workflows/ci.yml", "scripts/ci_in_docker.py"]
)
def test_every_other_audit_is_plain_blocking_pip_audit(path):
    """The must-fail side of condition 1: no audit of any other lock or
    environment is routed through the advisory script. Each pip-audit line
    either audits semgrep's lock through this script, or is a direct
    pip-audit call (which exits non-zero on any advisory)."""
    text = (ROOT / path).read_text(encoding="utf-8")
    advisory = [line for line in text.splitlines() if "semgrep_lock_audit.py" in line]
    assert len(advisory) == 1, (path, advisory)
    direct = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\bpip[-_]audit\b", line) and "semgrep_lock_audit" not in line
    ]
    assert direct, f"{path}: no blocking pip-audit left beside the advisory one"


def test_the_runtime_lock_is_still_audited_strictly_in_docker():
    text = (ROOT / "scripts" / "ci_in_docker.py").read_text(encoding="utf-8")
    assert 'step "pip-audit (runtime lock)" audit_lock requirements/runtime.txt' in text
    assert re.search(
        r"audit_lock\(\) \{ pip-audit --disable-pip --require-hashes -r \"\$1\"; \}", text
    )


# ---- condition 4: the release says so --------------------------------------


FRAGMENT = Path("changelog.d") / "1d-semgrep-lock-pyjwt.md"


def _release_notes(root: Path) -> tuple[str, str]:
    """Where condition 4 is checked, and the text there.

    While the train is unreleased, the fragment holds the entry. The release
    commit assembles every fragment into a `## <version>` section of
    `CHANGELOG.md` and deletes `changelog.d/*.md`, so from then on the entry
    is in the section for the version `pyproject.toml` declares. Reading the
    fragment by name failed at the cut with FileNotFoundError, which no train
    gate could see (#118). Neither place holding it is a failure, never an
    error and never a pass: the section is cut at the next release heading,
    so an older release's notes can't stand in for this one's.
    """
    fragment = root / FRAGMENT
    if fragment.is_file():
        return str(FRAGMENT), fragment.read_text(encoding="utf-8")
    import tomllib

    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        rf"^## {re.escape(version)}\s*$(.*?)(?=^## \d+\.\d+\.\d+|\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    return f"CHANGELOG.md ## {version}", match.group(1) if match else ""


def _names_advisory_mode(text: str) -> bool:
    return "advisory mode" in text and "#82" in text and "#83" in text


def test_the_release_notes_name_advisory_mode_and_the_issue():
    where, text = _release_notes(ROOT)
    assert _names_advisory_mode(text), f"condition 4: {where} doesn't name advisory mode, #82, #83"


# The arms (#118): the check must hold at BOTH stages, and fail at each.

ENTRY = "audited in advisory mode in this release (#82); #83 replaces it.\n"
OTHER = "**Something else changed.**\n"


def _tree(tmp_path: Path, *, version: str, fragment: str | None, section: str | None) -> Path:
    """A repository at the train (fragment present) or at the cut (assembled)."""
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "policyforge"\nversion = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "changelog.d").mkdir()
    if fragment is not None:
        (tmp_path / FRAGMENT).write_text(fragment, encoding="utf-8")
    head = f"## {version}\n\n{section}\n" if section is not None else ""
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n{head}## 1.6.1\n\n{ENTRY}", encoding="utf-8"
    )
    return tmp_path


def test_at_the_train_the_fragment_is_read(tmp_path):
    root = _tree(tmp_path, version="1.6.1", fragment=ENTRY, section=None)
    assert _release_notes(root)[0] == str(FRAGMENT)
    assert _names_advisory_mode(_release_notes(root)[1])


def test_at_the_train_a_fragment_without_it_fails(tmp_path):
    root = _tree(tmp_path, version="1.6.1", fragment=OTHER, section=None)
    assert not _names_advisory_mode(_release_notes(root)[1])


def test_at_the_cut_the_section_is_read_without_raising(tmp_path):
    root = _tree(tmp_path, version="1.7.0", fragment=None, section=ENTRY)
    where, text = _release_notes(root)
    assert where == "CHANGELOG.md ## 1.7.0"
    assert _names_advisory_mode(text)


def test_at_the_cut_a_section_without_it_fails(tmp_path):
    """The OLDER section (1.6.1) does carry the text, and must not count."""
    root = _tree(tmp_path, version="1.7.0", fragment=None, section=OTHER)
    assert not _names_advisory_mode(_release_notes(root)[1])


def test_with_neither_the_fragment_nor_a_section_it_fails(tmp_path):
    root = _tree(tmp_path, version="1.7.0", fragment=None, section=None)
    assert _release_notes(root)[1] == ""
    assert not _names_advisory_mode(_release_notes(root)[1])


# ---- through main(), the path check.py and CI take ---------------------------


def _fake_audit(monkeypatch, report, returncode=1, runtime=RUNTIME):
    def run(cmd, **kwargs):
        if report is not None:
            Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(report), encoding="utf-8")
        return sla.subprocess.CompletedProcess(cmd, returncode)

    monkeypatch.setattr(sla.subprocess, "run", run)
    monkeypatch.setattr(sla, "pinned", lambda text: dict(runtime))


def test_main_passes_advisories_and_prints_the_count(monkeypatch, capsys):
    deps = [_dep("pyjwt", "2.13.0", *(_vuln(f"CVE-2026-{n:04d}") for n in range(8)))]
    _fake_audit(monkeypatch, _report(*deps))
    assert sla.main() == 0
    out = capsys.readouterr().out
    assert out.count("ADVISORY  pyjwt 2.13.0") == 8
    assert "semgrep lock: 8 advisories (advisory, not blocking; #82)" in out


def test_main_fails_a_shared_advisory(monkeypatch, capsys):
    _fake_audit(monkeypatch, _report(_dep("requests", "2.34.2", _vuln("CVE-2099-1"))))
    assert sla.main() == 1
    assert "SHARED WITH THE PRODUCT" in capsys.readouterr().out


def test_main_exits_2_when_pip_audit_wrote_no_report(monkeypatch, capsys):
    """Condition 2: an audit that can't run fails, and never reads as an
    empty report. Exit 1 with no report is pip-audit 2.10.1's input error."""
    _fake_audit(monkeypatch, None, returncode=1)
    assert sla.main() == 2
    assert "COULD NOT READ" in capsys.readouterr().err


@pytest.mark.parametrize("returncode", [2, -1, 127])
def test_main_refuses_a_report_beside_an_unmeasured_exit_code(monkeypatch, capsys, returncode):
    _fake_audit(monkeypatch, _report(_dep("mcp", "1.23.3")), returncode=returncode)
    assert sla.main() == 2
    assert f"exited {returncode}" in capsys.readouterr().err


@pytest.mark.parametrize(
    "returncode,deps",
    [
        pytest.param(1, [_dep("mcp", "1.23.3")], id="exit 1, report lists nothing"),
        pytest.param(
            0, [_dep("mcp", "1.23.3", _vuln("CVE-2099-1"))], id="exit 0, report lists one"
        ),
    ],
)
def test_main_refuses_a_report_that_disagrees_with_its_exit_code(
    monkeypatch, capsys, returncode, deps
):
    _fake_audit(monkeypatch, _report(*deps), returncode=returncode)
    assert sla.main() == 2
    assert "disagree" in capsys.readouterr().err


@pytest.mark.parametrize(
    "report,returncode",
    [
        pytest.param([], 0, id="a list"),
        pytest.param(5, 0, id="a number"),
        pytest.param({"fixes": []}, 0, id="no dependencies key"),
        pytest.param({"dependencies": 5}, 0, id="dependencies a number"),
        pytest.param({"dependencies": ["pyjwt"]}, 0, id="a dependency that is a string"),
        pytest.param({"dependencies": [{"name": "x", "vulns": "CVE-1"}]}, 1, id="vulns a string"),
        pytest.param(
            {"dependencies": [{"name": "x", "vulns": ["CVE-1"]}]}, 1, id="a vuln a string"
        ),
    ],
)
def test_main_exits_2_on_a_report_of_an_unmeasured_shape(monkeypatch, capsys, report, returncode):
    _fake_audit(monkeypatch, report, returncode=returncode)
    assert sla.main() == 2
    assert "not trusted" in capsys.readouterr().err


def test_main_exits_2_when_classifying_raises(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    _fake_audit(monkeypatch, _report(_dep("mcp", "1.23.3")), returncode=0)
    monkeypatch.setattr(sla, "classify", boom)
    assert sla.main() == 2
    assert "COULD NOT CLASSIFY" in capsys.readouterr().err


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "scripts/ci_in_docker.py"])
def test_ci_and_the_docker_rehearsal_audit_semgreps_lock_through_this_script(path):
    """One classification everywhere: a raw pip-audit over semgrep's lock
    elsewhere would block where the gate reports."""
    lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
    assert any("scripts/semgrep_lock_audit.py" in line for line in lines), path
    raw = [
        line.strip()
        for line in lines
        if ("pip-audit" in line or "audit_lock" in line)
        and "requirements/semgrep/semgrep.txt" in line
    ]
    assert raw == [], f"{path} audits semgrep's lock around the script: {raw}"
