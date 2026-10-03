"""Editing a topic's documents in the repository instead of on the wiki.

`edit-topic` used to have one destination: it fetched live Confluence pages,
planned, rewrote, showed a diff in the terminal, and published. The review
was a diff that scrolled away, looked at by whoever happened to run the
command. For governance documents that is the wrong review surface. The
people who own a topic are not the person at the terminal, and the plan,
the diff and the traceability warnings all vanish when the window closes.

The repo-backed model already had every other piece: `pull` brings pages
into a markdown tree, `check` gates a pull request offline, and `publish`
runs on merge. This is the missing edge. With `--content-dir`, `edit-topic`
reads the topic's files from the tree, and writes the revision back to the
same files, with the plan beside them. It never runs git. It prints the
branch and commit it suggests, and a person opens the pull request — which
is where the owners review it, with the plan, the diff and CI's `check` in
one place, and where publish-on-merge carries the approved change to the
wiki.

**A file in the tree is not a trusted input.** It is tempting to think so —
it is in a repository, behind review — and it is wrong for this tree in
particular, because `pull` writes wiki page bodies into it verbatim. A line
somebody planted on a page arrives in the file with the next pull, and a
pull request full of pulled pages is not read line by line. So the tree
path gets exactly the protections the wiki path has: the injection scan
refuses before any model call, the document is fenced in the prompt, and
`check_edit` reports every change the plan did not call for. Those are
shared code, not copies.

Three things are specific to files, and each is the file-shaped version of
a guard the wiki path already has:

* **Only the body is edited.** The frontmatter — the `confluence:` binding
  that tells publish which page this is, the owner, the provenance stamp —
  is carried through byte for byte and never shown to the model. A rewrite
  that dropped `page_id` would make the next publish create a duplicate
  page, and a reformatted YAML block would put noise in the one diff this
  feature exists to make readable.
* **A file changed since it was read is refused, not overwritten.** The
  file analogue of the wiki's version guard. The model takes long enough
  that somebody can save the file in between.
* **A file with uncommitted changes is refused on apply.** A model edit
  written on top of an unreviewed human edit produces one diff containing
  both, and the reviewer can no longer tell which change the plan made.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from policyforge.textfile import write_text_lf

#: A frontmatter block at the very start of a file. Captured raw, with its
#: delimiters and the blank lines after it, so it can be written back exactly.
_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n(?:\r?\n)*", re.DOTALL)


def split_frontmatter(text: str) -> tuple[str, str]:
    """`(raw frontmatter block, body)`. The block is "" when there is none."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return "", text
    return match.group(0), text[match.end() :]


def join_frontmatter(frontmatter: str, body: str) -> str:
    """The inverse of `split_frontmatter`, normalising only the final newline."""
    return frontmatter + body.rstrip("\n") + "\n"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_TIER_ORDER = ("policy", "standard", "procedure")


def _tier_rank(tier: str) -> tuple[int, str]:
    return (_TIER_ORDER.index(tier) if tier in _TIER_ORDER else len(_TIER_ORDER), tier)


class TreeEditError(ValueError):
    """A tree edit that cannot proceed, with a message a person can act on."""


@dataclass
class TreeFile:
    """Where a tree target came from, and what it held when it was read."""

    path: Path
    relative: str
    frontmatter: str
    #: Of the whole file as read. The guard against a save in between.
    read_digest: str
    #: How this file was matched to the topic, for the run's output.
    matched_by: str = ""
    #: The document's own title, as the planner is shown it.
    #:
    #: Not the path. The wiki path shows the planner "Page title: Access
    #: Control Standard"; showing it "standards/access-control-standard.md"
    #: here would make the two modes different model inputs, and the edit
    #: evals — measured against page titles — would stop describing this one.
    #: Same title, same document body, same prompt.
    title: str = ""


@dataclass
class TreeSelection:
    """The files one topic resolves to, and anything that stopped it."""

    files: dict[str, TreeFile] = field(default_factory=dict)
    #: tier -> relative paths, where more than one file claims a tier.
    ambiguous: dict[str, list[str]] = field(default_factory=dict)


def select_topic_files(content_dir: Path, topic, *, tiers: set[str] | None = None) -> TreeSelection:
    """The topic's documents in the tree, one per tier.

    A file belongs to the topic when its frontmatter says so (`topic:`), or
    when it is bound to one of the pages the registry declares for the topic
    (`confluence.title`, or the title under any other declared `targets:` kind,
    matching a declared page title for the same tier).
    The second rule is what makes pulled files work: `pull` records the page
    binding, not the topic.

    Two files claiming one tier is refused rather than resolved. Picking one
    would edit a document nobody chose, and the other would go on saying the
    old thing.
    """
    from policyforge.content.tree import load_content_tree

    documents, _problems = load_content_tree(content_dir)
    # Every kind of store the topic declares pages in, so a file pulled from
    # any of them is matched the way a Confluence-pulled file is.
    kinds = ["confluence"] + [
        k for k in (getattr(topic, "targets", None) or {}) if k != "confluence"
    ]
    declared = {
        (kind, tier.lower()): title
        for kind in kinds
        for tier, title in (topic.confluence_pages() if kind == "confluence" else topic.pages(kind))
    }
    name = topic.name.strip().casefold()

    by_tier: dict[str, list[tuple[object, str]]] = {}
    for document in documents:
        tier = (document.tier or "").lower()
        if not tier or (tiers and tier not in tiers):
            continue
        if document.topic.strip().casefold() == name:
            rule = "frontmatter topic"
        elif bound := next(
            (
                declared[kind, tier]
                for kind in kinds
                if declared.get((kind, tier))
                and document.target(kind).get("title") == declared[kind, tier]
            ),
            "",
        ):
            rule = f"bound to page {bound!r}"
        else:
            continue
        by_tier.setdefault(tier, []).append((document, rule))

    selection = TreeSelection()
    # Policy, then Standard, then Procedure — the order `Topic.confluence_pages`
    # uses, so a set reviewed from the tree reads the way the same set reads
    # from the wiki, top of the hierarchy first. Alphabetical would put the
    # Procedure before the Standard it implements.
    for tier, matches in sorted(by_tier.items(), key=lambda item: _tier_rank(item[0])):
        if len(matches) > 1:
            selection.ambiguous[tier] = sorted(d.relative_path for d, _ in matches)
            continue
        document, rule = matches[0]
        text = document.path.read_text(encoding="utf-8")
        frontmatter, _body = split_frontmatter(text)
        selection.files[tier] = TreeFile(
            path=document.path,
            relative=document.relative_path,
            frontmatter=frontmatter,
            read_digest=digest(text),
            matched_by=rule,
            title=document.title,
        )
    return selection


def uncommitted(paths: list[Path], *, cwd: Path) -> list[Path] | None:
    """Which of `paths` have uncommitted changes, or None if git cannot say.

    None rather than [] when git is unavailable or this is not a checkout,
    so the caller can tell "clean" from "unknown" — the same distinction
    this project keeps everywhere else, and for the same reason.

    An untracked file counts as uncommitted. That is the common case, not an
    edge: `pull` a page, then edit it straight away, and the model's change
    and the pulled content land in one commit where neither can be reviewed
    on its own. Commit the pull first.
    """
    import subprocess  # nosec B404

    if not paths:
        return []

    from policyforge.child_output import strict_text

    def git(*args: str) -> tuple[int, str] | None:
        """(returncode, stdout) -- stdout decoded strictly, in this thread.

        A DECISION rides on it: whether an edit may overwrite a file. `-z`
        output is NOT quoted, so a page named `café.md` arrives as raw UTF-8;
        read as cp1252 it never matched its own path, and a modified page was
        reported clean (#287). Bytes, decoded here, so a byte that is not
        UTF-8 refuses rather than answering "clean".
        """
        argv = ["git", *args]
        try:
            # A fixed executable and argument list, never a shell. The only
            # variable parts are file paths this command resolved itself from
            # the content tree, passed after `--` so none can read as an option.
            result = subprocess.run(  # nosec B603 B607
                argv, cwd=cwd, capture_output=True, timeout=10, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.returncode, strict_text(
            result.stdout, site="uncommitted-change check", argv=argv
        )

    top = git("rev-parse", "--show-toplevel")
    if top is None or top[0] != 0:
        return None
    root = Path(top[1].strip())

    status = git("status", "--porcelain=v1", "-z", "--", *(str(p) for p in paths))
    if status is None or status[0] != 0:
        return None

    # Porcelain v1 with -z: "XY path" entries separated by NUL, and a rename
    # or copy followed by one extra NUL-separated token naming the source.
    # Compared as resolved paths, never by string suffix, so `standard.md`
    # cannot match `old-standard.md`.
    dirty: set[Path] = set()
    tokens = status[1].split("\0")
    index = 0
    while index < len(tokens):
        entry = tokens[index]
        index += 1
        if len(entry) < 4:
            continue
        dirty.add((root / entry[3:]).resolve())
        if entry[0] in "RC":
            index += 1
    return [p for p in paths if p.resolve() in dirty]


def write_revision(tree_file: TreeFile, revised_body: str) -> None:
    """Write one revision back, refusing if the file moved underneath.

    The frontmatter written is the frontmatter read, byte for byte.
    """
    current = tree_file.path.read_text(encoding="utf-8")
    if digest(current) != tree_file.read_digest:
        raise TreeEditError(
            f"{tree_file.relative} changed on disk while the edit was being prepared. "
            f"Nothing was written to it. Re-run the command against the current file."
        )
    # Into a tracked tree, so LF: `*.md text eol=lf` governs the checkout,
    # and this write has to match it or the next `git status` shows a
    # whole-file change and the gate's mdformat step fails on a CR.
    write_text_lf(tree_file.path, join_frontmatter(tree_file.frontmatter, revised_body))


#: Long enough to say what the branch is for, short enough to type.
_BRANCH_WORDS_MAX = 50


def suggest_branch(topic_name: str, instruction: str) -> str:
    """A branch name a person could use. Suggested, never created.

    Cut at a word boundary. Cutting at a character count produced
    `edit/access-review-access-reviews-move-from-quarterly-t` on a live
    run, which reads as a typo in the one string someone copies verbatim.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", f"{topic_name} {instruction}".lower()) if w]
    kept: list[str] = []
    for word in words:
        if len("-".join([*kept, word])) > _BRANCH_WORDS_MAX:
            break
        kept.append(word)
    if not kept and words:
        kept = [words[0][:_BRANCH_WORDS_MAX]]
    return "edit/" + ("-".join(kept) or "topic")


def display_path(path: Path, *, cwd: Path) -> str:
    """`path` as someone standing in the repository would type it.

    Relative to the git checkout's root when there is one, forward slashes
    on every platform. A live run printed an absolute Windows path — correct,
    and useless to paste into a shell at the repo root, and it leaks a
    developer's home directory into whatever the output is copied into.
    Falls back to the path as given when git cannot say where the root is.
    """
    import subprocess  # nosec B404

    try:
        # Fixed executable and arguments, no shell, no user-supplied input.
        # SHOWN: only ever printed for a person to paste. `replace` cannot
        # raise, and a bad byte costs a U+FFFD in a message, not a decision.
        result = subprocess.run(  # nosec B603 B607
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return str(path)
    if result.returncode != 0:
        return str(path)
    try:
        return path.resolve().relative_to(Path(result.stdout.strip()).resolve()).as_posix()
    except ValueError:
        return str(path)


def git_instructions(
    *, branch: str, files: list[str], plans: list[str], topic_name: str, instruction: str
) -> str:
    """The commands that turn this edit into a reviewable pull request.

    Printed rather than run. Creating a branch and committing are changes to
    a person's repository that they should make deliberately, and a command
    that silently moved them onto a new branch would be a surprise nobody
    reviewing a policy change needs.
    """
    paths = " ".join(files + plans)
    subject = f"Edit {topic_name}: {instruction}"
    if len(subject) > 72:
        subject = subject[:69].rstrip() + "..."
    return "\n".join(
        [
            "To review this as a pull request:",
            f"    git checkout -b {branch}",
            f"    git add {paths}",
            f'    git commit -m "{subject}"',
            "Then open a pull request. `policyforge check` runs there, the plan files",
            "say what was asked and what was declined, and merging publishes.",
        ]
    )
