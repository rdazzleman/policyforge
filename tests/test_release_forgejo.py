"""`scripts/release.py --forge forgejo`: the team-side steps on Forgejo (#7).

The fake server answers in the shapes Forgejo 16.0.5 returned when measured
on #7 (1d, 2026-09-28), not shapes derived from the port: the milestone
object's `open_issues` counts PRs too, a filtered list pages, an unknown
milestone NAME is ignored as a filter, and PRs carry `merged` and
`merge_commit_sha`. The user-facing steps (tag archive, tap, published
formula) stay GitHub and are covered by `test_release.py`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release
from release import Context

R = release.FORGEJO_REPO


class Forgejo:
    """Routes (method, path) to canned answers; list endpoints page by limit/page."""

    def __init__(self, lists=None, objects=None, posts=None):
        self.lists = lists or {}
        self.objects = objects or {}
        self.posts = posts if posts is not None else {}
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method, path, data):
        self.calls.append((method, path, data))
        if method == "POST":
            return self.posts.get(path.split("?")[0], 201), {}, {}
        base, _, query = path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if p)
        key = base + (
            "?"
            + "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k not in ("limit", "page"))
            if params
            else ""
        )
        if key in self.lists:
            items = self.lists[key]
            if items is None:
                return 500, None, {}
            limit, page = int(params.get("limit", 50)), int(params.get("page", 1))
            return 200, items[(page - 1) * limit : page * limit], {"X-Total-Count": str(len(items))}
        if base in self.objects:
            status, body = self.objects[base]
            return status, body, {}
        return 404, None, {}


def _ctx(fake, run=None, **kw):
    return Context(
        version="9.9.9",
        run=run or (lambda argv: subprocess.CompletedProcess(argv, 0, "", "")),
        forge="forgejo",
        forgejo=fake,
        **kw,
    )


MILESTONES = f"/repos/{R}/milestones?state=all"


def _issues(state, kind, mid=7):
    return f"/repos/{R}/issues?milestones={mid}&state={state}&type={kind}"


def _milestone(open_issues, mid=7, title="9.9.9"):
    return [{"id": mid, "title": title, "open_issues": open_issues}]


# --- step 1: the zero, by two instruments that reconcile ----------------------


def test_zero_issues_with_the_object_reconciling_passes():
    fake = Forgejo(
        lists={
            MILESTONES: _milestone(1),
            _issues("open", "issues"): [],
            _issues("open", "pulls"): [{"number": 25}],
        }
    )
    check = release._milestone_open(_ctx(fake))
    assert check.ok, check.measured
    assert any("#25" in line for line in check.measured), "the open PR is named"


def test_an_open_issue_fails_the_gate():
    fake = Forgejo(
        lists={
            MILESTONES: _milestone(1),
            _issues("open", "issues"): [{"number": 3}],
            _issues("open", "pulls"): [],
        }
    )
    check = release._milestone_open(_ctx(fake))
    assert not check.ok and any("#3" in line for line in check.measured)


def test_the_object_counts_prs_so_reading_it_as_issues_would_be_wrong():
    """Measured on #7: object 7 = 6 issues + 1 PR. Here the object says 1, and
    it is the PR: a gate that took the object as the issue count would fail a
    releasable train, and one that took a PR-free object at 0 would be right
    only by luck."""
    fake = Forgejo(
        lists={
            MILESTONES: _milestone(1),
            _issues("open", "issues"): [],
            _issues("open", "pulls"): [{"number": 25}],
        }
    )
    assert release._milestone_open(_ctx(fake)).ok


def test_an_object_that_does_not_reconcile_fails():
    """One instrument saw a population the other did not."""
    fake = Forgejo(
        lists={
            MILESTONES: _milestone(3),
            _issues("open", "issues"): [],
            _issues("open", "pulls"): [{"number": 25}],
        }
    )
    check = release._milestone_open(_ctx(fake))
    assert not check.ok
    assert any("reconcile" in line or ": False" in line for line in check.measured)


def test_a_milestone_that_does_not_exist_is_refused_not_counted():
    """By id, never by name: a name filter that matches nothing is ignored by
    Forgejo and returns every open issue (measured on #7)."""
    fake = Forgejo(lists={MILESTONES: _milestone(0, title="1.7")})
    check = release._milestone_open(_ctx(fake))
    assert not check.ok and "no milestone titled '9.9.9'" in check.measured[0]
    assert not any("milestones=" in path and "issues" in path for _, path, _ in fake.calls)


def test_a_list_that_does_not_answer_is_not_zero():
    fake = Forgejo(
        lists={
            MILESTONES: _milestone(0),
            _issues("open", "issues"): None,
            _issues("open", "pulls"): [],
        }
    )
    assert not release._milestone_open(_ctx(fake)).ok


def test_the_issue_list_is_read_past_its_first_page():
    """51 open issues: one page of 50 would be a sample."""
    fake = Forgejo(
        lists={
            MILESTONES: _milestone(51),
            _issues("open", "issues"): [{"number": n} for n in range(1, 52)],
            _issues("open", "pulls"): [],
        }
    )
    check = release._milestone_open(_ctx(fake))
    assert not check.ok
    assert any("open issues on 9.9.9: 51" in line for line in check.measured)


def test_the_zero_time_is_the_latest_closed_issue():
    fake = Forgejo(
        lists={
            MILESTONES: _milestone(0),
            _issues("closed", "issues"): [
                {"number": 1, "closed_at": "2026-09-25T01:00:00Z"},
                {"number": 2, "closed_at": "2026-09-25T03:00:00Z"},
            ],
        }
    )
    assert release._milestone_zero_at(_ctx(fake)) == "2026-09-25T03:00:00Z"


# --- step 2: the train tip, asked of the server --------------------------------


def test_the_train_tip_is_read_from_the_branch_api():
    sha = "a" * 40
    fake = Forgejo(objects={f"/repos/{R}/branches/release%2F9.9.9": (200, {"commit": {"id": sha}})})
    assert release._server_tip(_ctx(fake), "release/9.9.9") == sha


def test_a_branch_the_server_does_not_have_is_no_tip():
    assert release._server_tip(_ctx(Forgejo()), "release/9.9.9") == ""


# --- steps 3-4: the tracking records -------------------------------------------


def _comments(*rows):
    return [
        {"created_at": at, "body": f"Reviewed.\n\n{line}", "user": {"login": login}}
        for at, line, login in rows
    ]


def test_records_count_from_the_team_bots_and_the_user_only():
    fake = Forgejo(
        objects={
            f"/repos/{R}/issues/99/comments": (
                200,
                _comments(
                    ("2026-09-25T02:00:00Z", "line from 1d", "policyforge-1d"),
                    ("2026-09-25T02:01:00Z", "line from the user", "rdazzleman"),
                    ("2026-09-25T02:02:00Z", "line from a stranger", "someone-else"),
                    ("2026-09-25T02:03:00Z", "line from the GitHub bot", "rdazzlebot"),
                ),
            )
        }
    )
    ctx = _ctx(fake, tracking=99)
    records = release._tracking_records(ctx)
    assert [r[2] for r in records] == ["line from 1d", "line from the user"]
    assert "someone-else" in ctx.notes["ignored records"]
    assert "rdazzlebot" in ctx.notes["ignored records"], "no such account on Forgejo"


# --- steps 9-11: the release PR and main ---------------------------------------


def _pr(state="open", merged=False, head="9b/release-9.9.9", title="Release 9.9.9", base="main"):
    return {
        "number": 40,
        "state": state,
        "merged": merged,
        "merge_commit_sha": "m" * 40 if merged else None,
        "head": {"ref": head, "sha": "c" * 40},
        "base": {"ref": base},
        "title": title,
    }


PULLS = f"/repos/{R}/pulls?state=all"


@pytest.mark.parametrize(
    "pr, state",
    [(_pr(), "OPEN"), (_pr("closed", True), "MERGED"), (_pr("closed"), "CLOSED")],
)
def test_the_release_pr_is_shaped_as_gh_returned_it(pr, state):
    got = release._release_pr(_ctx(Forgejo(lists={PULLS: [pr]})))
    assert got["state"] == state and got["headRefOid"] == "c" * 40
    assert (got["mergeCommit"] or {}).get("oid") == ("m" * 40 if state == "MERGED" else None)


def test_a_merged_pr_wins_over_a_closed_first_attempt_and_strangers_are_skipped():
    pulls = [
        _pr("closed"),
        _pr("closed", True),
        _pr(title="Release 9.9.90"),
        _pr(head="ba/something"),
        _pr(base="release/9.9.9"),
    ]
    assert release._release_pr(_ctx(Forgejo(lists={PULLS: pulls})))["state"] == "MERGED"


def test_opening_the_pr_pushes_to_forgejo_then_posts_it():
    calls = []

    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    fake = Forgejo()
    release._open_pr(_ctx(fake, run=run))
    pushes = [a for a in calls if "push" in a]
    assert pushes and pushes[0][4] == release.FORGEJO_REPO_URL, pushes
    assert not any("origin" in a for a in calls), "nothing goes to GitHub"
    posted = [c for c in fake.calls if c[0] == "POST"]
    assert posted and posted[0][1] == f"/repos/{R}/pulls" and posted[0][2]["base"] == "main"


def test_a_refused_push_stops_before_the_pr_is_opened():
    def run(argv):
        return subprocess.CompletedProcess(argv, 1 if "push" in argv else 0, "", "rejected")

    fake = Forgejo()
    ctx = _ctx(fake, run=run)
    release._open_pr(ctx)
    assert not any(c[0] == "POST" for c in fake.calls)
    assert "exited 1" in ctx.notes["release-pr"]


def test_the_main_check_reads_forgejo_main():
    calls = []

    def run(argv):
        calls.append(argv)
        out = "c" * 40 if argv[-1] == "HEAD" else "t" * 40 if argv[-1].endswith("^{tree}") else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    ctx = _ctx(Forgejo(lists={PULLS: [_pr("closed", True)]}), run=run)
    check = release._main_merged(ctx)
    assert check.ok, check.measured
    assert any(a[3:6] == ["fetch", "-q", release.FORGEJO_REPO_URL] for a in calls), calls
    assert any(release.FORGEJO_MAIN_REF in a for a in calls)


def test_the_install_record_is_posted_to_forgejo(monkeypatch):
    monkeypatch.setattr(
        release, "_install", lambda ctx, url, note: ctx.notes.update({note: "passed"})
    )
    calls = []

    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "c" * 40, "")

    fake = Forgejo()
    ctx = _ctx(fake, run=run, tracking=99)
    # On the Forgejo path the approval install is of the public commit P
    # (#33), not the cut commit: the record names P.
    ctx.notes["public_sha"] = "p" * 40
    release._install_head(ctx)
    posted = [c for c in fake.calls if c[0] == "POST"]
    assert posted and posted[0][1] == f"/repos/{R}/issues/99/comments"
    assert "Release-install: " + "p" * 40 in posted[0][2]["body"]
    assert f"/archive/{'p' * 40}.tar.gz" in posted[0][2]["body"]
    assert not any(a[:2] == ["gh", "api"] for a in calls)


def test_no_public_commit_means_no_install_and_no_record(monkeypatch):
    """Without P an install would post a record naming an empty SHA, as this
    PR's first run did; it refuses before installing or posting anything."""
    installed = []
    monkeypatch.setattr(release, "_install", lambda ctx, url, note: installed.append(url))
    fake = Forgejo()
    ctx = _ctx(fake, tracking=99)
    release._install_head(ctx)
    assert installed == [] and not [c for c in fake.calls if c[0] == "POST"]
    assert ctx.notes["head install"].startswith("refused")


# --- the real side: credentials stay out of argv -------------------------------


def test_the_token_reaches_git_by_environment_never_argv(monkeypatch):
    monkeypatch.setenv("FORGEJO_TOKEN", "t0ken")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    env = release._git_env()
    count = int(env["GIT_CONFIG_COUNT"])
    pairs = [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(count)]
    headers = [(k, v) for k, v in pairs if v == "Authorization: token t0ken"]
    assert headers and all(
        k.startswith("http.http://") and k.endswith(".extraHeader") for k, _ in headers
    ), pairs
    # The only other entries: the credential helpers reset for exactly those
    # Forgejo prefixes, so the header is Forgejo's only credential (ba on
    # #124) and GitHub keeps the operator's own helper (1d on #124).
    prefixes = [k.removeprefix("http.").removesuffix(".extraHeader") for k, _ in headers]
    resets = [(f"credential.{prefix}.helper", "") for prefix in prefixes]
    assert sorted(p for p in pairs if p not in headers) == sorted(resets), pairs


def test_no_token_leaves_git_environment_alone(monkeypatch):
    monkeypatch.delenv("FORGEJO_TOKEN", raising=False)
    assert release._git_env() is None


def test_a_non_http_forgejo_url_is_refused_before_anything_is_opened(monkeypatch):
    import urllib.request

    opened = []
    monkeypatch.setenv("FORGEJO_TOKEN", "t0ken")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: opened.append(a))
    for url in ("file:///etc/passwd", "ftp://example.invalid"):
        monkeypatch.setattr(release, "FORGEJO_URL", url)
        assert release._real_forgejo("GET", "/version", None)[0] == 0
    assert opened == []


def test_the_default_forge_needs_a_token(monkeypatch, capsys):
    monkeypatch.delenv("FORGEJO_TOKEN", raising=False)
    monkeypatch.setattr(release, "run_steps", lambda *a, **k: 0)
    assert release.main(["9.9.9"]) == 2
    assert "needs FORGEJO_TOKEN" in capsys.readouterr().out
    assert release.main(["9.9.9", "--forge", "github"]) == 0, "GitHub needs no Forgejo token"


def test_git_commands_run_with_that_environment_and_others_do_not(monkeypatch):
    """`_git_env` is only half: `_real_run` must hand it to git, and only git."""
    seen = {}

    def fake_run(argv, **kw):
        seen[argv[0]] = kw.get("env")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("FORGEJO_TOKEN", "t0ken")
    monkeypatch.setattr(release.subprocess, "run", fake_run)
    for argv in (["git", "-C", "x", "push", "forgejo"], ["docker", "info"]):
        release._real_run(argv)
        seen[" ".join(argv[:4])] = seen.pop(argv[0])
    assert seen["git -C x push"].get("GIT_CONFIG_COUNT"), "a push gets the scoped header"
    assert seen["docker info"] is None, "nothing else inherits the token"


# --- the version the operator types, through main() (9b and 80 on #28) ----------
#
# 1d's first live run called `_milestone_open` with `version="1.7"`, which
# no operator can type: `main()` requires X.Y.Z, and the milestones are
# titled `1.7`. So every case here goes through `main([...])`.


def _operator(monkeypatch, milestones, open_issues=({"number": 3},)):
    """main()'s real seams replaced: Forgejo answers, git answers nothing."""
    lists = {f"/repos/{R}/milestones?state=all": milestones}
    for m in milestones:
        lists[_issues("open", "issues", m["id"])] = list(open_issues)
        lists[_issues("open", "pulls", m["id"])] = []
    monkeypatch.setenv("FORGEJO_TOKEN", "not-a-real-token")
    monkeypatch.setattr(release, "_real_forgejo", Forgejo(lists=lists))
    monkeypatch.setattr(
        release, "_real_run", lambda argv: subprocess.CompletedProcess(argv, 0, "", "")
    )


def _milestones(*titles):
    return [{"id": i, "title": t, "open_issues": 1} for i, t in enumerate(titles, 1)]


def test_x_y_0_is_cut_from_the_short_milestone(monkeypatch, capsys):
    _operator(monkeypatch, _milestones("1.7", "1.8"))
    assert release.main(["1.7.0"]) == 11, "step 1 refuses: an issue is open"
    out = capsys.readouterr().out
    assert "1.7.0 is cut from milestone '1.7'" in out
    assert "open issues on 1.7.0: 1" in out and "#3" in out


def test_x_y_0_is_cut_from_its_exact_title_too(monkeypatch, capsys):
    _operator(monkeypatch, _milestones("1.7.0"))
    assert release.main(["1.7.0"]) == 11
    assert "cut from milestone '1.7.0'" in capsys.readouterr().out


def test_a_patch_never_reaches_the_short_milestone(monkeypatch, capsys):
    _operator(monkeypatch, _milestones("1.7", "1.8"))
    assert release.main(["1.7.1"]) == 11
    out = capsys.readouterr().out
    assert "no milestone titled '1.7.1'" in out and "open issues" not in out


def test_both_titles_present_is_refused_naming_both(monkeypatch, capsys):
    _operator(monkeypatch, _milestones("1.7", "1.7.0"))
    assert release.main(["1.7.0"]) == 11
    out = capsys.readouterr().out
    assert "2 milestones match 1.7.0: '1.7', '1.7.0'" in out and "open issues" not in out


def test_neither_title_present_is_refused(monkeypatch, capsys):
    _operator(monkeypatch, _milestones("1.7", "1.8"))
    assert release.main(["1.9.0"]) == 11
    assert "no milestone titled '1.9.0' or '1.9'" in capsys.readouterr().out


def test_the_short_form_itself_is_still_not_a_version(monkeypatch, capsys):
    _operator(monkeypatch, _milestones("1.7"))
    assert release.main(["1.7"]) == 2
    assert "is not X.Y.Z" in capsys.readouterr().out


def test_github_resolves_the_same_way_and_searches_the_resolved_title(monkeypatch, capsys):
    """The GitHub path compared titles exactly too, and its search instrument
    names the milestone by text: `1.7.0` must search `milestone:"1.7"`."""
    calls = []

    def run(argv):
        calls.append(argv)
        joined = " ".join(argv)
        if "milestones" in joined:
            return subprocess.CompletedProcess(argv, 0, '[{"title": "1.7", "number": 4}]', "")
        return subprocess.CompletedProcess(argv, 0, "2", "")

    monkeypatch.setattr(release, "_real_run", run)
    assert release.main(["1.7.0", "--forge", "github"]) == 11
    searches = [a for a in calls if "search/issues" in a]
    assert searches and any('milestone:"1.7"' in part for part in searches[0]), searches
    assert any("issues?milestone=4" in " ".join(a) for a in calls)


@pytest.mark.parametrize(
    "argv, gets_token",
    [
        (["git", "-C", "r", "push", "forgejo", "HEAD:x"], True),
        (["git", "-C", "r", "fetch", "-q", "forgejo", "main"], True),
        (["git", "-C", "r", "ls-remote", "forgejo"], True),
        (["git", "clone", "-q", "http://x", "d"], True),
        (["git", "-C", "r", "-c", "user.name=a", "commit", "-qam", "m"], False),
        (["git", "-C", "r", "rev-parse", "HEAD"], False),
        (["git", "-C", "r", "merge-base", "--is-ancestor", "a", "b"], False),
    ],
)
def test_only_commands_that_reach_a_server_get_the_token(monkeypatch, argv, gets_token):
    """ba on #28: `commit` runs the repository's hooks, which have no use
    for the token, so only git's network subcommands carry it."""
    seen = {}
    monkeypatch.setenv("FORGEJO_TOKEN", "t0ken")
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda a, **kw: seen.update(env=kw.get("env")) or subprocess.CompletedProcess(a, 0, "", ""),
    )
    release._real_run(argv)
    assert bool(seen["env"]) is gets_token, argv


def test_a_rejected_token_fails_instead_of_prompting(monkeypatch):
    monkeypatch.setenv("FORGEJO_TOKEN", "t0ken")
    env = release._git_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GCM_INTERACTIVE"] == "never"


def test_a_repeat_in_the_lists_is_refused_not_reconciled():
    """ba on #28: object 2, issues [] and PRs [#25, #25]. Counts reconcile
    (2 == 0 + 2), yet only one distinct item was seen, so an open issue
    could be hiding behind the repeat."""
    fake = Forgejo(
        lists={
            MILESTONES: _milestone(2),
            _issues("open", "issues"): [],
            _issues("open", "pulls"): [{"number": 25}, {"number": 25}],
        }
    )
    check = release._milestone_open(_ctx(fake))
    assert not check.ok
    assert any("repeat #25" in line for line in check.measured), check.measured


def test_a_skipped_item_is_refused_by_the_distinct_reconcile():
    """The skip half alone: the object sees one more than the lists."""
    fake = Forgejo(
        lists={
            MILESTONES: _milestone(2),
            _issues("open", "issues"): [],
            _issues("open", "pulls"): [{"number": 25}],
        }
    )
    assert not release._milestone_open(_ctx(fake)).ok


def test_an_unanswered_milestone_list_is_not_called_absent():
    fake = Forgejo(lists={MILESTONES: None})
    check = release._milestone_open(_ctx(fake))
    assert not check.ok and "did not answer" in check.measured[0]
    assert "no milestone titled" not in check.measured[0]
