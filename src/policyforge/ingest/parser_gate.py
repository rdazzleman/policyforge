"""Gate a model-written parser before it runs, and run it once under watch.

`generate-parser` asks a model to write a loader for a licensed framework
export, and the sample export is part of that prompt. That makes the sample
untrusted input to a prompt whose output is code this project will import
and execute — over the very licensed file it parses. A tampered CSV, or one
a colleague pasted a chat transcript into, can steer the model toward a
loader that sends the export somewhere, and `ast.parse` was the only check
between that output and `src/`. It proves the output is Python. It proves
nothing else.

Two checks replace it, in the order a reviewer would want them:

* **Static, over the AST** (`check_generated_parser`). An allowlist of
  imports rather than a denylist: a parser needs to read a CSV or a workbook
  and nothing more, so what it may import is a short list and what it must
  not is unbounded. Calls that turn strings into code or attribute lookups,
  reach through dunder attributes, or write anything are refused by name.
  It is the walk `tests/test_byoc_boundary.py` already runs over the
  hand-written loaders, applied at the one point where the code is not
  hand-written.
* **Dynamic, in a child process** (`trial_run`). The candidate runs once
  against the sample under a PEP 578 audit hook that refuses sockets,
  subprocesses and any file opened for writing, and the number of records
  it returns is reported before anything is promoted into the package.

Neither is a sandbox, and neither is described as one. Code clever enough
can be written around a static check, and an audit hook runs inside the
interpreter it watches. What the two together change is the default: model
output used to land in `src/` and be imported on the next run; now it lands
in `output/`, is checked, is run once under watch, and reaches the package
only when somebody says so.
"""

from __future__ import annotations

import ast
import subprocess  # nosec B404 - runs this interpreter on a fixed script, never a shell
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: What a generated parser may import. Reading a CSV or a workbook needs
#: nothing outside this list, and anything outside it is a capability the
#: task does not call for. `pandas` is deliberately absent: it is not a
#: dependency of this project, and a parser importing it would fail the
#: first time it ran anywhere but the machine that generated it.
ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "functools",
        "html",
        "io",
        "itertools",
        "json",
        "openpyxl",
        "operator",
        "pathlib",
        "re",
        "string",
        "typing",
        "unicodedata",
    }
)

#: The sibling modules a generated parser is told to import from.
# `levelled`, not `hitrust`: the HITRUST parser moves to a private plugin
# (#32), and what a drafted parser builds on is the public assembler.
ALLOWED_RELATIVE = frozenset({"levelled", "schema"})

#: Builtins that turn a string into code, a module, or an attribute lookup.
#: `getattr` is here because `getattr(Path(p), "write_" + "text")` walks
#: straight past every attribute check below.
FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
    }
)

#: Methods that write or run something. `replace` and `copy` are not here,
#: although `Path.replace` moves a file, because `str.replace` and
#: `dict.copy` are in half the parsers anyone writes; the audit hook in
#: `trial_run` refuses the move at the moment it happens instead.
FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "write",
        "write_text",
        "write_bytes",
        "writelines",
        "mkdir",
        "makedirs",
        "touch",
        "unlink",
        "rmdir",
        "rename",
        "chmod",
        "symlink_to",
        "hardlink_to",
        "save",
        "to_csv",
        "to_json",
        "to_excel",
        "dump",
        "safe_dump",
        "system",
        "popen",
        "FileIO",
    }
)

#: Dunder names with no escape route behind them. Everything else of that
#: shape — `__class__`, `__subclasses__`, `__globals__`, `__builtins__` —
#: is the classic way out of any restriction on what code may reach.
_HARMLESS_DUNDERS = frozenset({"__name__", "__init__", "__all__", "__doc__"})


@dataclass(frozen=True)
class Violation:
    """One reason a generated parser was refused."""

    line: int
    reason: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.reason}"


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and name not in _HARMLESS_DUNDERS


def _open_refusal(call: ast.Call) -> str | None:
    """Why this `open(...)` or `.open(...)` call is refused, or None if it reads.

    The mode must be a literal. A mode computed at runtime is a mode this
    check cannot read, and a parser has no reason to compute one.
    """
    func = call.func
    if isinstance(func, ast.Name) and func.id == "open":
        position = 1
    elif isinstance(func, ast.Attribute) and func.attr == "open":
        position = 0
    else:
        return None

    if any(isinstance(a, ast.Starred) for a in call.args) or any(
        k.arg is None for k in call.keywords
    ):
        return "open() with unpacked arguments, so its mode cannot be read"

    mode = call.args[position] if len(call.args) > position else None
    mode = next((k.value for k in call.keywords if k.arg == "mode"), mode)
    if mode is None:
        return None
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return "open() with a mode that is not a literal"
    if any(flag in mode.value for flag in "wax+"):
        return f"open() for writing ({mode.value!r})"
    return None


def check_generated_parser(source: str, *, framework_slug: str) -> list[Violation]:
    """Every reason `source` should not be run, sorted by line.

    An empty list means the static check found nothing — not that the code
    is safe. `trial_run` is the second half.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Violation(exc.lineno or 0, f"not valid Python: {exc.msg}")]

    found: list[Violation] = []

    def flag(node: ast.AST, reason: str) -> None:
        found.append(Violation(getattr(node, "lineno", 0), reason))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    flag(node, f"imports {alias.name!r}, which a parser has no use for")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                module = node.module or ""
                if module.split(".")[0] not in ALLOWED_IMPORTS:
                    flag(node, f"imports {module!r}, which a parser has no use for")
            elif node.level == 1:
                siblings = [node.module] if node.module else [a.name for a in node.names]
                for sibling in siblings:
                    if sibling.split(".")[0] not in ALLOWED_RELATIVE:
                        flag(
                            node,
                            f"imports sibling module {sibling!r}; only "
                            f"{', '.join(sorted(ALLOWED_RELATIVE))} are offered",
                        )
            else:
                flag(node, "a relative import reaching outside ingest/")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                flag(node, f"uses {node.id}, which turns data into code or a lookup")
            elif _is_dunder(node.id):
                flag(node, f"refers to {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRIBUTES:
                flag(node, f"calls .{node.attr}, which writes or runs something")
            elif _is_dunder(node.attr):
                flag(node, f"reaches through .{node.attr}")
        elif isinstance(node, ast.Call):
            reason = _open_refusal(node)
            if reason:
                flag(node, reason)

    expected = f"load_{framework_slug}_export"
    if not any(isinstance(n, ast.FunctionDef) and n.name == expected for n in tree.body):
        found.append(Violation(0, f"defines no top-level {expected}()"))

    return sorted(found, key=lambda v: (v.line, v.reason))


#: Run in the child. Kept as a fixed string rather than assembled, so the
#: only inputs from outside are three paths and a function name passed as
#: argv — never code.
_CHILD = """
import importlib.util
import os
import sys
from pathlib import Path

parser_path, sample_path, function_name, package_root = sys.argv[1:5]
sys.path.insert(0, package_root)

# Imported before the hook goes in: the project's own modules are trusted,
# and the candidate's relative imports resolve against this package.
import policyforge.ingest
import policyforge.ingest.levelled
import policyforge.ingest.schema

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_REFUSED = (
    "socket.", "subprocess.", "os.system", "os.exec", "os.spawn",
    "os.posix_spawn", "os.fork", "os.startfile", "ctypes.", "urllib.",
    "http.", "ftplib.", "smtplib.", "webbrowser.", "shutil.",
    "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod",
    "os.truncate", "os.symlink", "os.link", "winreg.",
)


def _refuse(what):
    # Reported on stdout as it happens, so an attempt the candidate catches
    # and swallows is still seen by the parent.
    sys.stdout.write("REFUSED " + what + chr(10))
    sys.stdout.flush()
    raise PermissionError("refused: " + what)


def _hook(event, args):
    if event == "open":
        path, mode, flags = args
        writes = any(c in mode for c in "wax+") if mode else bool(flags & _WRITE_FLAGS)
        if writes:
            _refuse("open for writing " + repr(path))
    elif event.startswith(_REFUSED):
        _refuse(event)


sys.addaudithook(_hook)

spec = importlib.util.spec_from_file_location("policyforge.ingest._candidate_parser", parser_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
records = getattr(module, function_name)(Path(sample_path))
print("RECORDS", len(records))
"""


@dataclass(frozen=True)
class TrialRun:
    """What one supervised run of a candidate parser produced."""

    ok: bool
    records: int = 0
    detail: str = ""
    #: Everything the audit hook refused, including attempts the candidate
    #: caught and carried on past. Any entry here fails the trial: a loader
    #: that tried to open a socket and returned records anyway still tried.
    refused: tuple[str, ...] = field(default_factory=tuple)


def trial_run(
    parser_path: Path, sample_path: Path, *, framework_slug: str, timeout: float = 120.0
) -> TrialRun:
    """Run the candidate once against the sample, in a child process, under watch.

    A child rather than an import here: the candidate never shares an
    interpreter with the CLI that is about to offer to promote it, and a
    loader that hangs or crashes takes down only itself. `-I` keeps the
    child's environment and user site out of it; `-B` stops it writing
    bytecode, which the hook would otherwise refuse on the candidate's own
    import.
    """
    import policyforge
    from policyforge.child_output import strict_text

    package_root = str(Path(policyforge.__file__).resolve().parent.parent)
    try:
        # `-X utf8` because `-I` ignores PYTHONIOENCODING: without it the child
        # WRITES cp1252 on Windows, and before #287 the parent happened to READ
        # cp1252 too. Decoding the parent as UTF-8 alone would have made any
        # non-ASCII line a candidate prints refuse -- the two ends move together.
        argv = [
            sys.executable,
            "-I",
            "-B",
            "-X",
            "utf8",
            "-c",
            _CHILD,
            str(parser_path),
            str(sample_path),
            f"load_{framework_slug}_export",
            package_root,
        ]
        result = subprocess.run(argv, capture_output=True, timeout=timeout)  # nosec B603
    except subprocess.TimeoutExpired:
        return TrialRun(False, detail=f"did not finish within {timeout:.0f}s")

    # DATA: the RECORDS and REFUSED lines decide whether the candidate is
    # offered for promotion. The stderr tail is only SHOWN, so it may not raise.
    lines = strict_text(result.stdout, site="generate-parser trial run", argv=argv).splitlines()
    refused = tuple(line[len("REFUSED ") :] for line in lines if line.startswith("REFUSED "))
    counted = [line for line in lines if line.startswith("RECORDS ")]
    if refused:
        return TrialRun(False, detail="tried to " + "; ".join(refused), refused=refused)
    if counted:
        return TrialRun(True, records=int(counted[-1].split()[1]))
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    tail = (stderr.strip().splitlines() or ["exited with no output"])[-1]
    return TrialRun(False, detail=tail)
