"""A GitHub wiki as a publish target: a git repository of markdown pages.

A wiki is `https://github.com/<owner>/<repo>.wiki.git`, one file per page,
named by title with spaces as hyphens (`Access-Review-Standard.md`). It has
no storage format and no version number, so the three guards in
`publisher.py` are answered from git: a commit sha where Confluence has a
version, a commit trailer where it has a version message, the file itself
where it has storage markup.

Two differences from the Confluence adapter are deliberate.

**Bytes are the comparison.** Confluence may reflow a page when it saves,
so its adapter compares storage format with the whitespace between tags
collapsed. A git-backed wiki changes nothing on its own: every byte that
differs, differs because somebody changed it. So `same_content` compares
the rendered body to the file, newline folding aside, and any difference is
an edit — the direction to be wrong in for a guard whose job is refusing to
overwrite work nobody has seen.

**Nothing is read from anywhere but the clone.** The working clone lives
under `output/.wiki/<owner>-<repo>/`, gitignored, fetched before every
operation. A page is never read from the API, so what the guard compares
and what the write pushes are the same bytes on the same disk.

Git runs by argv, never through a shell, as `edit/tree.py` and
`frameworks/drift.py` do. Credentials reach it through the environment and
never touch a command line or `.git/config` — see `_wiki_auth.py`.
"""

from __future__ import annotations

import re
import shlex
import subprocess  # nosec B404 - argv only, never shell=True
from dataclasses import dataclass, field
from pathlib import Path

from policyforge.export.publisher import LivePage, Publisher

#: The key a document's `targets:` block uses. Underscored, like every other
#: frontmatter key; the CLI spells it `github-wiki`, and the two are held
#: apart here so they cannot drift into meaning different things.
GITHUB_WIKI = "github_wiki"
CLI_TARGET = "github-wiki"

#: The trailer that says this tool wrote a commit, and which document it
#: wrote. The wiki's counterpart to Confluence's version-message marker.
PUBLISH_MARKER = "Policyforge-Publish:"

#: Formats a GitHub wiki accepts that are not markdown. A page in one of
#: these is refused by name for the reason macros are: the round trip would
#: not survive it.
UNSUPPORTED_FORMATS = (
    ".asciidoc",
    ".creole",
    ".mediawiki",
    ".org",
    ".pod",
    ".rdoc",
    ".rst",
    ".textile",
)

#: A markdown link to a path inside the tree: `[text](../standards/x.md)`.
_TREE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)\)")

#: A wiki link, either `[[Title]]` or `[[text|Title]]`.
_WIKI_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def page_filename(title: str) -> str:
    """The file a wiki keeps a page in. GitHub's own rule: spaces to hyphens."""
    return title.replace(" ", "-") + ".md"


def page_url(repository: str, title: str) -> str:
    return f"https://github.com/{repository}/wiki/{title.replace(' ', '-')}"


@dataclass
class _Git:
    """`git`, in one directory, by argv.

    `check=False` throughout: a failing git command is a fact to read, not
    an exception to catch in five places. The one place a failure must stop
    the run — a push that did not land — raises there, where the message can
    name the remote.
    """

    cwd: Path
    #: The whole environment for the subprocess, built by `_wiki_auth`. This
    #: module never reads `os.environ` itself: credentials are read in
    #: declared places, and `tests/test_credential_containment.py` enforces
    #: that — it caught this file doing it.
    env: dict[str, str] = field(default_factory=dict)

    def __call__(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        """For git whose output is SHOWN -- clone, commit and push messages.
        `replace` cannot raise; the one text test on it, "nothing to commit",
        is ASCII and survives replacement (#287)."""
        return subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["git", *args],
            cwd=str(self.cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=self.env or None,
        )

    def data(self, *args: str, timeout: int = 60) -> tuple[int, str]:
        """(returncode, stdout) for git whose output is DATA -- the branch a
        push goes to, the SHA and marker a page's version is read from.
        Bytes, decoded strictly in this thread, so a byte that is not UTF-8
        refuses rather than becoming U+FFFD inside a SHA or a branch (#287)."""
        from policyforge.child_output import strict_text

        argv = ["git", *args]
        result = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            argv,
            cwd=str(self.cwd),
            capture_output=True,
            timeout=timeout,
            check=False,
            env=self.env or None,
        )
        return result.returncode, strict_text(result.stdout, site="GitHub wiki", argv=argv)


class GitHubWikiPublisher(Publisher):
    """Publishes documents to a GitHub wiki's git repository."""

    kind = GITHUB_WIKI

    def __init__(
        self,
        repository: str,
        *,
        workdir: Path,
        remote: str = "",
        token_env: str = "",
        public: bool | None = None,
        allow_public: bool = False,
    ):
        self.repository = repository
        #: Tests pass a local bare repository here, which is what lets the
        #: whole suite run with no network.
        self.remote = remote or f"https://github.com/{repository}.wiki.git"
        self.workdir = Path(workdir)
        self.token_env = token_env
        #: True public, False private, None unknown — and unknown is treated
        #: as public, because the cost of being wrong runs one way.
        self.public = public
        self.allow_public = allow_public
        self._notes: list[str] = []
        self._wiki_titles: dict[str, str] = {}
        self._fetched = False
        self._branch = ""

    # -- the clone ---------------------------------------------------------

    @property
    def _git(self) -> _Git:
        from policyforge.export._wiki_auth import git_environment

        return _Git(cwd=self.workdir, env=git_environment(self.token_env))

    def _ensure_clone(self) -> None:
        """Clone or refresh the working copy. Reads only; safe on a dry run."""
        if self._fetched:
            return

        from policyforge.export._wiki_auth import git_environment

        env = git_environment(self.token_env)
        if not (self.workdir / ".git").exists():
            self.workdir.mkdir(parents=True, exist_ok=True)
            clone = _Git(cwd=self.workdir.parent, env=env)(
                "clone", self.remote, str(self.workdir), timeout=120
            )
            if clone.returncode != 0:
                raise RuntimeError(
                    f"Could not clone the wiki at {self.remote}:\n  "
                    f"{(clone.stderr or clone.stdout).strip()}"
                )
        else:
            self._git("fetch", "origin", timeout=120)

        self._branch = self._default_branch()
        # An empty wiki has no commits and nothing to reset to; that is a
        # wiki with no pages, not a failure.
        if self._branch:
            self._git("reset", "--hard", f"origin/{self._branch}")
        self._fetched = True

    def _default_branch(self) -> str:
        """Whatever this wiki calls its branch. GitHub wikis are `master`."""
        code, head = self._git.data("symbolic-ref", "refs/remotes/origin/HEAD")
        if code == 0 and head.strip():
            return head.strip().rsplit("/", 1)[-1]
        for candidate in ("master", "main"):
            if self._git("rev-parse", "--verify", f"origin/{candidate}").returncode == 0:
                return candidate
        return ""

    # -- where a document goes ---------------------------------------------

    def location(self, doc) -> str:
        """The repository this document publishes into, or "".

        A document declares intent by carrying a `github_wiki` block at all;
        the repository comes from configuration unless the block names its
        own, which is how one tree publishes into two wikis. An empty block
        means "publish here, to the configured wiki, under this document's
        title" — and "" means the file declares no wiki target, which is how
        a draft stays a draft.
        """
        # Asked of `targets` rather than `target()`, because the two answers
        # that matter differ only in whether the key is there at all: a
        # declared-but-empty block publishes to the configured wiki, and a
        # missing one is not a wiki page.
        declared = doc.targets.get(GITHUB_WIKI)
        if declared is None:
            return ""
        block = declared if isinstance(declared, dict) else {}
        return str(block.get("repository") or self.repository or "")

    def title(self, doc) -> str:
        return str(doc.target(GITHUB_WIKI).get("title") or doc.title)

    def recorded_version(self, doc) -> str | None:
        recorded = doc.target(GITHUB_WIKI).get("commit")
        return str(recorded) if recorded else None

    def _page_path(self, title: str) -> Path:
        return self.workdir / page_filename(title)

    # -- reading -----------------------------------------------------------

    def fetch(self, location: str, title: str) -> LivePage:
        self._ensure_clone()

        unsupported = tuple(
            extension
            for extension in UNSUPPORTED_FORMATS
            if (self.workdir / (page_filename(title)[: -len(".md")] + extension)).exists()
        )
        path = self._page_path(title)
        if not path.exists():
            if unsupported:
                # The page exists; it is simply not markdown. Reported as a
                # page this tool refuses rather than as one that is absent,
                # because publishing would replace somebody's AsciiDoc with
                # a markdown file of the same name.
                return LivePage(
                    version="",
                    written_by_tool=False,
                    url=page_url(location or self.repository, title),
                    body="",
                    unsupported=unsupported,
                )
            raise LookupError(f"no page titled {title!r} in the {location} wiki")

        code, log = self._git.data(
            "log", "-1", "--format=%H%x00%an%x00%as%x00%B", "--", page_filename(title)
        )
        sha = author = date = message = ""
        if code == 0 and log.strip():
            parts = log.split("\x00")
            sha, author, date, message = (parts + ["", "", "", ""])[:4]

        return LivePage(
            version=sha.strip(),
            written_by_tool=PUBLISH_MARKER in message,
            url=page_url(location or self.repository, title),
            body=path.read_text(encoding="utf-8"),
            page_id=page_filename(title),
            author=f"{author.strip()} on {date.strip()}" if author.strip() else "",
            unsupported=unsupported,
        )

    def same_content(self, doc, live: LivePage) -> bool:
        """Byte comparison, newline folding aside.

        A wiki reflows nothing, so anything that differs was changed by
        somebody. 1d asked for this to be held by a test of its own: it is
        the mirror of the Confluence reflow case, and it is the reason the
        two adapters answer "unchanged" differently on purpose.
        """
        from policyforge.textfile import normalise_newlines

        def comparable(text: str) -> str:
            # Trailing newlines, and only those, are forgiven. `doc.body`
            # arrives from the tree with its final newline stripped, every
            # editor GitHub offers adds one back, and a file that ends
            # without one is not what this adapter should write anyway — so
            # a disagreement about the last byte is an artefact of this
            # pipeline rather than an edit somebody made, and reporting it
            # as MOVED would mean reporting every page forever. Everything
            # inside the file is still compared byte for byte.
            return normalise_newlines(text).rstrip("\n")

        return comparable(self._rendered(doc)) == comparable(live.body)

    def as_markdown(self, live: LivePage) -> str:
        """The page as it is, with wiki links turned back into tree paths."""
        return self._links_to_tree(live.body)

    def binding(self, location: str, title: str, live: LivePage) -> dict:
        return {
            "targets": {
                GITHUB_WIKI: {
                    "repository": location or self.repository,
                    "title": title,
                    "commit": live.version,
                }
            }
        }

    # -- writing -----------------------------------------------------------

    def write(self, doc) -> str:
        from policyforge.textfile import write_text_lf

        self._ensure_clone()
        title = self.title(doc)
        path = self._page_path(title)
        write_text_lf(path, self._rendered(doc))

        self._git("add", "--", page_filename(title))
        commit = self._git(
            "-c",
            "user.name=policyforge",
            "-c",
            "user.email=policyforge@localhost",
            "commit",
            "-m",
            f"Publish {title}\n\n{PUBLISH_MARKER} {doc.relative_path}",
        )
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout or ""):
            raise RuntimeError(
                f"Could not commit {title!r} to the wiki clone:\n  "
                f"{(commit.stderr or commit.stdout).strip()}"
            )

        branch = self._branch or "master"
        push = self._git("push", "origin", f"HEAD:refs/heads/{branch}", timeout=120)
        if push.returncode != 0:
            raise RuntimeError(
                f"Could not push to {self.remote}:\n  {(push.stderr or push.stdout).strip()}"
            )
        return page_url(self.location(doc) or self.repository, title)

    # -- the hooks ---------------------------------------------------------

    def prepare(self, documents) -> None:
        """Learn which documents are wiki pages, for the link rewrite.

        A cross-reference can only become `[[Title]]` once it is known that
        the file it points at has a wiki target of its own; a link to a
        document that has none is left exactly as written and said once in
        the report, rather than rewritten into a page that does not exist.
        """
        self._wiki_titles = {
            doc.relative_path: self.title(doc) for doc in documents if self.location(doc)
        }

    def refuse(self, doc) -> str:
        """Licensed content is never published to a public wiki.

        The same ceiling `llm/boundary.py` applies to a third-party model,
        applied to a third-party audience — and, like that one, it is not a
        flag's to lift: `--allow-public` says the operator accepts a public
        audience for the organization's own documents, and says nothing
        about somebody else's licensed text.
        """
        if not self._is_public():
            return ""
        if _licensed(doc):
            return (
                "licensed content is never published to a public wiki, whatever --allow-public says"
            )
        return ""

    def _is_public(self) -> bool:
        """Unknown counts as public: the fail-closed direction."""
        return self.public is not False

    def plan_header(self) -> str:
        if self.public is True:
            state = "PUBLIC"
        elif self.public is False:
            state = "private"
        else:
            state = "PUBLIC (visibility could not be determined, so it is treated as public)"
        return f"target wiki: {state} — {self.repository}"

    def notes(self) -> list[str]:
        return list(self._notes)

    def reconcile_command(self, doc) -> str:
        # **Quoted with `shlex.quote`, because this line is printed for a
        # person to copy.** A document title is user-authored prose: the
        # title `Vendor "Bring Your Own" Policy` inside a hand-written
        # `"..."` produces `--title "Vendor Bring"`, which is a valid
        # command naming the wrong document, silently. #187.
        return (
            f"policyforge pull --target {CLI_TARGET} "
            f"--title {shlex.quote(self.title(doc))} "
            f"--tier {shlex.quote(doc.tier or 'standard')} --apply"
        )

    # -- links -------------------------------------------------------------

    def _rendered(self, doc) -> str:
        """What this document looks like as a wiki page.

        Ends with exactly one newline: `doc.body` arrives without it, and a
        text file that does not end with one is a file every editor will
        silently fix — which would then read as somebody's edit.
        """
        return self._links_to_wiki(doc.body, doc.relative_path).rstrip("\n") + "\n"

    def _links_to_wiki(self, body: str, from_path: str) -> str:
        """Tree-relative markdown links become wiki links."""
        from posixpath import normpath

        base = from_path.rsplit("/", 1)[0] if "/" in from_path else ""

        def swap(match: re.Match) -> str:
            text, href = match.group(1), match.group(2)
            if "://" in href:
                return match.group(0)
            resolved = normpath(f"{base}/{href}" if base else href)
            title = self._wiki_titles.get(resolved)
            if title is None:
                note = (
                    f"{from_path} links to {href}, which declares no wiki page; "
                    "the link is published as written"
                )
                if note not in self._notes:
                    self._notes.append(note)
                return match.group(0)
            return f"[[{text}|{title}]]" if text != title else f"[[{title}]]"

        return _TREE_LINK.sub(swap, body)

    def _links_to_tree(self, body: str) -> str:
        """Wiki links become tree-relative markdown links, where the title is
        one this tree publishes; anything else is left as it is."""
        by_title = {title: path for path, title in self._wiki_titles.items()}

        def swap(match: re.Match) -> str:
            first, second = match.group(1), match.group(2)
            text, title = (first, second) if second else (first, first)
            path = by_title.get(title)
            return f"[{text}]({path})" if path else match.group(0)

        return _WIKI_LINK.sub(swap, body)


def _licensed(doc) -> bool:
    """Does this document carry, or come from, licensed catalog content?

    Two places say so: the document's own frontmatter, and the provenance
    stamp `generate` writes into it. Either is enough — a stamp recording a
    licensed source is exactly as binding as a hand-written declaration.

    **An unrecognised class counts as not licensed, and that is deliberate.**
    It is the opposite direction from unknown visibility a few lines above,
    which counts as public, so the asymmetry is stated here rather than left
    to be discovered and "corrected" in whichever direction the next reader
    guesses. Unknown visibility is a question this tool asked and failed to
    get an answer to, and there are only two answers it could have had.
    Content class is an open field on a document anybody may hand-write, and
    most documents in most trees carry none at all: failing closed on it
    would refuse every unstamped document on every public wiki, which makes
    the feature unusable and gets the check disabled rather than obeyed. So
    the ceiling holds where the class is *said*, and `generate` says it on
    everything it writes — all 59 documents of the measured corpus carry
    `generated_by.content_class`. Where nothing says it, the wiki's
    visibility rule is what stands between the document and the world.

    Values are stripped before comparison because frontmatter is
    hand-editable, and `content_class: "licensed "` is precisely the kind of
    thing hand-editing produces.
    """
    metadata = getattr(doc, "metadata", {}) or {}
    if str(metadata.get("content_class") or "").strip().lower() == "licensed":
        return True

    stamp = metadata.get("generated_by")
    if isinstance(stamp, dict):
        return str(stamp.get("content_class") or "").strip().lower() == "licensed"
    if isinstance(stamp, str):
        return "licensed" in stamp.lower()
    return False
