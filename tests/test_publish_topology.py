"""publish_public's topology preflight (#194): the repositories the run
writes to must HOLD in policyforge/ops's inventory before anything runs.

The check itself, `tools/inventory_check.py`, lives in `policyforge/ops` with
its own tests. These test this repository's half: which repositories are
required (derived from the run's own URLs, never listed), what refuses, that
`main` refuses before any step, and that each read goes through the run's own
instrument (GitHub only signed out). The ops tool is a stand-in on disk with
the real one's interface: `load`, `check`, `Unreadable`, and verdicts with
`repo`, `kind` and `line()`.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import publish_public as pp  # noqa: E402
import release  # noqa: E402

PUBLIC = "http://127.0.0.1:3000/policyforge/policyforge-public.git"
TAP = "http://127.0.0.1:3000/policyforge/homebrew-tap.git"
GITHUB = "https://github.com/rdazzleman/policyforge.git"
GITHUB_TAP = "https://github.com/rdazzleman/homebrew-tap.git"

#: The stand-in tool: each entry's verdict is the inventory's own `kind:` key,
#: so a test states the world in the file it writes.
TOOL = textwrap.dedent(
    """
    from dataclasses import dataclass

    class Unreadable(Exception):
        pass

    @dataclass
    class Verdict:
        repo: str
        kind: str

        def line(self):
            return f"{self.kind:<15} {self.repo}"

    def load(path):
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def check(inventory, inst):
        return [Verdict(e["id"], e.get("kind", "HOLDS")) for e in inventory["repositories"]]
    """
)


def _ops(tmp_path: Path, kinds: dict[str, str] | None = None, *, drop: str = "") -> Path:
    import yaml

    kinds = kinds or {}
    entries = [
        # Written as the inventory writes them: localhost, no `.git`.
        ("forgejo/policyforge-public", "http://localhost:3000/policyforge/policyforge-public"),
        ("forgejo/homebrew-tap", "http://localhost:3000/policyforge/homebrew-tap"),
        ("github/rdazzleman/policyforge", "https://github.com/rdazzleman/policyforge"),
        ("github/rdazzleman/homebrew-tap", "https://github.com/rdazzleman/homebrew-tap"),
        ("forgejo/policyforge", "http://localhost:3000/policyforge/policyforge"),
        ("forgejo/policyforge-ci", "http://localhost:3000/policyforge/policyforge-ci"),
    ]
    inventory = {
        "repositories": [
            {"id": i, "url": u, "kind": kinds.get(i, "HOLDS")} for i, u in entries if i != drop
        ]
    }
    ops = tmp_path / "ops"
    (ops / "tools").mkdir(parents=True)
    (ops / "tools" / "inventory_check.py").write_text(TOOL, encoding="utf-8")
    (ops / "inventory.yaml").write_text(yaml.safe_dump(inventory), encoding="utf-8")
    return ops


def _ctx(ops: Path | None, **over) -> pp.PublicContext:
    def run(argv):
        return subprocess.CompletedProcess(argv, 0, "abc1234\n", "")

    fields = {
        "version": "9.9.0", "run": run, "forge": "forgejo", "tracking": 1, "local_issue": 1,
        "public_url": PUBLIC, "tap_url": TAP, "github_url": GITHUB, "github_tap_url": GITHUB_TAP,
        "ops_root": ops,
    }  # fmt: skip
    fields.update(over)
    return pp.PublicContext(**fields)


# ---- which repositories are required ------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (PUBLIC, "http://localhost:3000/policyforge/policyforge-public"),
        (GITHUB, "https://github.com/rdazzleman/policyforge/"),
        (GITHUB_TAP, "https://github.com/RDAZZLEMAN/homebrew-tap"),
    ],
)
def test_a_repository_is_the_same_however_its_url_is_written(a, b):
    assert pp._repo_key(a) == pp._repo_key(b)


def test_forgejo_and_github_names_dont_collide():
    assert pp._repo_key("http://localhost:3000/rdazzleman/policyforge") != pp._repo_key(GITHUB)


def test_the_five_repositories_the_run_writes_to_are_required_and_others_arent(tmp_path):
    check = pp._topology(_ctx(_ops(tmp_path, {"forgejo/policyforge": "DRIFT"})))
    assert check.ok, check.measured
    shown = "\n".join(check.measured)
    for repo in ("forgejo/policyforge-public", "forgejo/homebrew-tap",
                 "github/rdazzleman/policyforge", "github/rdazzleman/homebrew-tap",
                 "forgejo/policyforge-ci"):  # fmt: skip
        assert f"HOLDS           {repo}" in shown
    assert "not required: 1 not holding, e.g. forgejo/policyforge DRIFT" in shown
    assert "ops inventory at abc1234" in shown


# ---- what refuses --------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["DRIFT", "NOT MEASURABLE"])
def test_a_required_repository_that_doesnt_hold_refuses(tmp_path, kind):
    check = pp._topology(_ctx(_ops(tmp_path, {"github/rdazzleman/policyforge": kind})))
    assert not check.ok
    assert any(line.startswith(kind) and "github/rdazzleman/policyforge" in line
               for line in check.measured)  # fmt: skip


def test_a_repository_the_run_writes_to_that_the_inventory_doesnt_record_refuses(tmp_path):
    check = pp._topology(_ctx(_ops(tmp_path, drop="forgejo/homebrew-tap")))
    assert not check.ok
    assert f"NOT IN THE INVENTORY  {TAP} (this run writes to it)" in check.measured


def test_no_ops_checkout_refuses(tmp_path):
    check = pp._topology(_ctx(None))
    assert not check.ok and "pass --ops" in check.measured[0]


def test_an_ops_checkout_without_the_check_refuses(tmp_path):
    ops = _ops(tmp_path)
    (ops / "tools" / "inventory_check.py").unlink()
    check = pp._topology(_ctx(ops))
    assert not check.ok and "could not read the ops inventory or its check" in check.measured[0]


# ---- main refuses before any step, in a dry run too ------------------------------------


def _main(monkeypatch, topology_ok: bool) -> tuple[int, list]:
    ran = []
    monkeypatch.setenv("FORGEJO_TOKEN", "sentinel-not-a-token")
    monkeypatch.setattr(pp, "_topology", lambda ctx: release.Check(topology_ok, ["topology: fake"]))
    monkeypatch.setattr(release, "run_steps", lambda plan, ctx, execute: ran.append(execute) or 0)
    code = pp.main(["9.9.0", "--tracking-issue", "1", "--local-issue", "1",
                    "--identity", "A <a@b>", "--formula", "f.rb", "--ops", "ops"])  # fmt: skip
    return code, ran


def test_main_refuses_with_3_before_any_step(monkeypatch, capsys):
    code, ran = _main(monkeypatch, topology_ok=False)
    assert code == 3 and ran == []
    out = capsys.readouterr().out
    assert "topology: fake" in out and "REFUSED before any step" in out


def test_main_runs_the_plan_when_the_topology_holds(monkeypatch):
    code, ran = _main(monkeypatch, topology_ok=True)
    assert code == 0 and ran == [False], "the dry run's plan ran after the preflight"


# ---- each read goes through the run's own instrument --------------------------------------


class _Unreadable(Exception):
    pass


def test_github_refs_are_read_signed_out_never_through_the_runs_git():
    signed_out, run = [], []
    ctx = _ctx(
        None,
        run=lambda argv: run.append(argv),
        run_signed_out=lambda argv: (
            signed_out.append(argv),
            subprocess.CompletedProcess(argv, 0, "abc\trefs/heads/main\n", ""),
        )[1],
    )
    refs = pp._RunInstruments(ctx, _Unreadable).github_refs(GITHUB)
    assert refs == ["refs/heads/main"]
    assert run == [] and signed_out[0][-2:] == ["ls-remote", GITHUB]


def test_github_repo_is_read_anonymously_from_the_api(tmp_path):
    asked = []

    def fetch(url, dest):
        asked.append(url)
        dest.write_text('{"visibility": "public"}', encoding="utf-8")
        return 200

    status, body = pp._RunInstruments(_ctx(None, fetch_anonymous=fetch), _Unreadable).github_repo(
        "rdazzleman/policyforge"
    )
    assert (status, body) == (200, {"visibility": "public"})
    assert asked == ["https://api.github.com/repos/rdazzleman/policyforge"]


def test_forgejo_is_read_as_the_operators_bot_and_a_refusal_is_unreadable():
    def forgejo(method, path, data):
        return (
            (404, {"message": "not found"}, {})
            if "hidden" in path
            else (200, {"private": True}, {})
        )

    inst = pp._RunInstruments(_ctx(None, forgejo=forgejo), _Unreadable)
    assert inst.forgejo_repo("policyforge/ops") == {"private": True}
    with pytest.raises(_Unreadable):
        inst.forgejo_repo("policyforge/hidden")
    with pytest.raises(_Unreadable):
        inst.runner_capacity()
