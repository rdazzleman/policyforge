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
  not is unbounded. Calls that turn strings into code or attribute lookups
  (the builtins, and what the admitted modules offer: `operator.attrgetter`,
  `typing.get_type_hints`), reach through dunder attributes, reach the
  interpreter's own machinery through an admitted module, by attribute or by
  imported name (`pathlib.sys.modules["builtins"].eval`, `from pathlib
  import os`), reach a private `_name` on any object (`typing._eval_type`;
  namedtuple's `_asdict` and kin excepted), or write anything are refused by
  name. It is the walk
  `tests/test_byoc_boundary.py` already runs over the hand-written loaders,
  applied at the one point where the code is not hand-written.
* **Dynamic, in a child process** (`trial_run`). The candidate runs once
  against the sample under a PEP 578 audit hook that refuses sockets,
  subprocesses, any file opened for writing, and any file read other than
  the sample (and the interpreter's own), and the number of records it
  returns is reported before anything is promoted into the package.

Neither is a sandbox, and neither is described as one. **They catch
known-dangerous shapes, not every one.** Code clever enough can be written
around a static check, and an audit hook runs inside the interpreter it
watches. **A payload that fires only on some inputs can't be found by
either:** the trial run sees one sample, and a loader that misbehaves on a
row the sample lacks behaves on this one. So the generated code is reviewed
by a person before it is committed, and that review is the control; these
checks only decide what reaches it. What the two together change is the
default: model output used to land in `src/` and be imported on the next
run; now it lands in `output/`, is checked, is run once under watch, and
reaches the package only when somebody says so.
"""

from __future__ import annotations

import ast
import os
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

#: Attribute names that reach the interpreter's own machinery through a
#: module the allowlist admits: `pathlib.sys.modules["builtins"].eval(...)`
#: imports nothing off the allowlist and names no forbidden builtin, and it
#: passed both checks (CR-30). So the route is refused, whatever it starts
#: from: the module table, the builtins module, `eval` and `exec` reached as
#: attributes, and the modules that run or send things (`pathlib.os` is the
#: same move one module over). `compile` and `open` are NOT here as
#: attributes: `re.compile` and `Path(sample).open()` are in every parser,
#: and the builtins they would otherwise be reached through are refused on
#: the way (`.builtins`, `__builtins__`).
ESCAPE_ATTRIBUTES = frozenset(
    {
        "sys",
        "os",
        "modules",
        "builtins",
        "eval",
        "exec",
        "importlib",
        "subprocess",
        "socket",
        "shutil",
        "ctypes",
    }
)

#: What the allowed modules themselves offer that turns a string into an
#: attribute lookup or into code, refused as a name and as an attribute:
#: `operator.attrgetter("s" + "ys")(pathlib)` is `getattr` under another name
#: (80 on #179's PR), and `typing.get_type_hints` and
#: `functools.singledispatch` evaluate a string annotation (measured: each ran
#: a `print` written as an annotation). Refused as an attribute and as an
#: imported name. Not as a bare name: none is a builtin, so a bare name can
#: only be bound to one through an import or an attribute, both refused, and
#: a bare-name rule was a mutant no input could tell apart (ba measured it).
STRING_EVALUATORS = frozenset(
    {
        "attrgetter",
        "methodcaller",
        "get_type_hints",
        "ForwardRef",
        "singledispatch",
        "singledispatchmethod",
        # `string.Formatter` resolves a dotted path held in a string: its
        # `get_field` returns the object (1d on #179's PR), and a subclass
        # overriding `format_field` captures it without naming `get_field`
        # (ba measured both reaching eval). So the class is refused, which
        # also refuses subclassing it; `get_field` itself isn't listed, since
        # nothing allowed reaches a Formatter without naming it (an arm
        # dropping `get_field` changed no result). `str.format`, `Template`
        # and `capwords` stay allowed: they only ever produce a string.
        "Formatter",
    }
)

#: The same route by subscript: a string key that names the builtins, the
#: module table or a builtin that runs code (`modules["builtins"]`). `os` and
#: `open` are left out here because a CSV column can be called that
#: (`row["open"]`), and the attribute that reaches the table is refused anyway.
ESCAPE_KEYS = frozenset(
    {"builtins", "__builtins__", "__import__", "eval", "exec", "compile", "sys", "modules"}
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


#: The underscore names that are a documented public API, not internals:
#: namedtuple's. Everything else spelled `_x` is refused as an attribute.
PUBLIC_UNDERSCORE = frozenset({"_asdict", "_replace", "_fields", "_make", "_field_defaults"})


def _is_private(name: str) -> bool:
    """`_x`: internals. `typing._eval_type` evaluates a string annotation
    (ba measured it running a `print`), and a module's private helpers are
    not something this check can audit one by one.

    Refused on ANY object, not only on a name an import bound: which object a
    name holds is not something a static check can know (`t = typing;
    t._eval_type` passed a check keyed on the root's spelling, 1d on #179's
    PR, and so would `self = typing; self._eval_type` in a plain function, ba
    measured both reaching eval). The cost is that a parser's own `self._x`
    is refused too: loud, and a person renames it."""
    return name.startswith("_") and not name.startswith("__") and name not in PUBLIC_UNDERSCORE


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
            # The names, whatever module they come from: `from pathlib import
            # os` is `pathlib.os` reached by import rather than attribute (80
            # on #179's PR), and `*` brings in names nothing here can see.
            for alias in node.names:
                name = alias.name
                if name == "*":
                    flag(node, "a star import, whose names this check cannot see")
                elif (
                    name in ESCAPE_ATTRIBUTES
                    or name in STRING_EVALUATORS
                    or name in FORBIDDEN_NAMES
                    or _is_dunder(name)
                    or (node.level == 0 and _is_private(name))
                ):
                    flag(node, f"imports the name {name!r}, a route to code or to the interpreter")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                flag(node, f"uses {node.id}, which turns data into code or a lookup")
            elif _is_dunder(node.id):
                flag(node, f"refers to {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRIBUTES:
                flag(node, f"calls .{node.attr}, which writes or runs something")
            elif node.attr in ESCAPE_ATTRIBUTES:
                flag(node, f"reaches .{node.attr}, the interpreter's own machinery")
            elif node.attr in STRING_EVALUATORS:
                flag(node, f"uses .{node.attr}, which turns data into code or a lookup")
            elif _is_private(node.attr):
                flag(node, f"reaches .{node.attr}, a private name")
            elif _is_dunder(node.attr):
                flag(node, f"reaches through .{node.attr}")
        elif isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in ESCAPE_KEYS:
                flag(node, f"looks up [{key.value!r}], the interpreter's own machinery")
        elif isinstance(node, ast.Call):
            reason = _open_refusal(node)
            if reason:
                flag(node, reason)

    expected = f"load_{framework_slug}_export"
    if not any(isinstance(n, ast.FunctionDef) and n.name == expected for n in tree.body):
        found.append(Violation(0, f"defines no top-level {expected}()"))

    return sorted(found, key=lambda v: (v.line, v.reason))


def path_key(path) -> str:
    """A path as the trial's read rule compares it. Plain string work only:
    it runs inside the audit hook, and a call that opened or resolved
    anything here would raise the very event being judged."""
    return os.path.normcase(os.path.abspath(os.fsdecode(path)))


def readable_path(
    path, files: tuple[str, ...], roots: tuple[str, ...], exists=os.path.exists
) -> bool:
    """Whether the trial run lets the candidate read `path`.

    `files` and `roots` are `path_key`s. A name whose LAST component is
    `<...>` and that is not a real file is a traceback's source lookup, not a
    read: `<string>`, `<unknown>`, and on Python 3.10 an editable install's
    `__editable__.policyforge-X.finder.__path_hook__/<string>`, which a test
    of the whole name missed (9b on #179's PR: a crashing parser's own error
    was replaced by a refusal). `exists` is `os.stat`, which raises no audit
    event."""
    if isinstance(path, int):
        return True  # a descriptor already open
    name = os.fsdecode(path)
    base = os.path.basename(name)
    if base.startswith("<") and base.endswith(">") and not exists(name):
        return True
    key = path_key(name)
    return key in files or key.startswith(roots)


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
from policyforge.ingest.parser_gate import path_key, readable_path

# Read once here, before the hook: openpyxl's first workbook load calls
# mimetypes.init(), which reads the system's own mime.types files
# (/etc/mime.types on Linux; Windows reads the registry instead). They are the
# interpreter's environment, not a file the candidate chose, and the read
# refusal below would otherwise fail every workbook parser on Linux (CI on
# #179's PR caught it). A later init() naming other files still meets the hook.
import mimetypes

mimetypes.init()

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


# What the candidate may READ: the sample it was given, its own source and
# the bytecode cache import looks for beside it, this package, and the
# interpreter's own files (lazy stdlib and openpyxl imports). Anything else is
# a file the parser was not given (CR-30). The rule itself is
# `parser_gate.readable_path`, imported before the hook, so it is tested.
_READABLE_FILES = (path_key(sample_path), path_key(parser_path))
_READABLE_ROOTS = tuple(
    path_key(root).rstrip(os.sep) + os.sep
    for root in {
        package_root,
        sys.prefix,
        sys.base_prefix,
        sys.exec_prefix,
        sys.base_exec_prefix,
        os.path.join(os.path.dirname(os.path.abspath(parser_path)), "__pycache__"),
    }
    if root
)


def _readable(path):
    return readable_path(path, _READABLE_FILES, _READABLE_ROOTS)


def _hook(event, args):
    if event == "open":
        path, mode, flags = args
        writes = any(c in mode for c in "wax+") if mode else bool(flags & _WRITE_FLAGS)
        if writes:
            _refuse("open for writing " + repr(path))
        elif not _readable(path):
            _refuse("read a file other than the sample " + repr(path))
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
