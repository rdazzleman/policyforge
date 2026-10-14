"""C-02 — credentials are read in known places, and never where prompts are built.

The product commitments (https://zardoz.io/trust/commitments/) state that
no credential ever reaches a model: not in a system prompt, not in a user
prompt, not in the ledger, not in version history. The ledger half is
enforced in `test_llm_ledger.py`, which asserts it records a hash and never
content. This is the prompt half.

The failure being prevented is mundane rather than exotic. Nobody sets out
to put an API key in a prompt. What happens is that a module which already
builds prompt text acquires a reason to read an environment variable — a
feature flag, a default host, an account id — and six months later somebody
interpolates the wrong one into an f-string that is three lines from a
prompt constant. The two capabilities being in the same file is what makes
that possible, so the invariant enforced here is that they are never in the
same file.

Two checks, in the order a reviewer would want them:

* **Every module that reads the environment is declared**, with what it
  reads and why. A new env read anywhere in `src/` fails until somebody
  writes it down, which is the review conversation that should happen.
* **No module that defines prompt text reads the environment at all**, and
  no prompt constant mentions a credential variable by name. This half is
  detected rather than declared: a new prompt module is covered the day it
  is written, without anybody remembering to add it here.

What this does not prove: that a credential cannot reach a model by some
path with more indirection than one module — a value read in a provider,
passed through three calls, and appended to a prompt by a caller that never
touched the environment. Nothing short of taint tracking proves that, and
CodeQL's `security-extended` suite is the thing in this repo that looks for
it. What this makes impossible is the version that actually happens.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import policyforge

SRC = Path(policyforge.__file__).resolve().parent

#: Every module in `src/` permitted to read the environment, and what for.
#: Not every entry reads a secret; each says what it reads. They are all here because
#: the check is "who reads the environment at all", which is the question
#: with a stable answer. A narrower check on credential-shaped names only
#: would pass a module that read `ANTHROPIC_KEY` under a different spelling.
DECLARED_ENV_READERS: dict[str, str] = {
    "llm/anthropic_provider.py": "the Anthropic API key, by the variable name config gives",
    "llm/litellm_provider.py": "same, for OpenRouter and other LiteLLM-routed providers",
    "llm/openai_compat_provider.py": "same, for an OpenAI-compatible endpoint",
    "llm/gemini_provider.py": "the Google AI Studio key, by the variable name config gives",
    "export/_confluence_auth.py": "CONFLUENCE_API_TOKEN and CONFLUENCE_USERNAME",
    "export/_wiki_auth.py": (
        "the GitHub token, by the variable name config gives — passed to git through "
        "GIT_CONFIG_* so it never reaches argv or .git/config"
    ),
    "config.py": "POLICYFORGE_CONFIG, a path — lets a second config be run without editing one",
    "zardoz/budgets.py": "budget overrides, integers",
    "llm/litellm_map.py": (
        "reads none: it SETS LITELLM_LOCAL_MODEL_COST_MAP=True before importing "
        "LiteLLM, so no run downloads a price table (#79). No credential"
    ),
    "llm/boundary.py": (
        "every variable, to refuse a licensed call under a data agreement while one "
        "could move the endpoint (#11): a name is matched against *_BASE_URL, *_API_BASE "
        "and *_ENDPOINT_URL, and a value is only parsed for an http(s) URL. It returns "
        "names; no value is kept, printed or used. Also whether HTTP_PROXY or ALL_PROXY "
        "is set, to name it when a loopback litellm call would be proxied (#69)"
    ),
}

#: Credential variables this project reads. Named here so the prompt check
#: can assert no prompt text mentions one.
CREDENTIAL_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_USERNAME",
    "GITHUB_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


def _python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


#: The names through which Python code reads the process environment.
_ENV_NAMES = frozenset({"environ", "environb", "getenv", "getenvb"})
#: The Windows API's own readers, reached through `ctypes` as attributes.
_NATIVE_ENV_NAMES = frozenset(
    {"GetEnvironmentVariableW", "GetEnvironmentVariableA", "GetEnvironmentStringsW"}
)
#: A process's environment as a file: `/proc/self/environ`, `/proc/1/environ`.
_PROC_ENVIRON = re.compile(r"^/proc/[^/]+/environ$")


def _names_an_env_reader(node: ast.AST) -> bool:
    """A string literal naming one of `_ENV_NAMES`, dotted paths included
    (`attrgetter("os.environ")`), or a literal `/proc/<pid>/environ` path."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (bool(_ENV_NAMES & set(node.value.split("."))) or bool(_PROC_ENVIRON.match(node.value)))
    )


def _reads_environment(tree: ast.AST) -> bool:
    """True if this module reads an environment variable.

    Matches, by syntax, any of `_ENV_NAMES`:

    * as an attribute (`os.environ`, `_o.getenv`, whatever `os` is bound to),
      and the Windows API's readers (`kernel32.GetEnvironmentVariableW`);
    * as a bare name (`environ[...]` after `from os import environ`);
    * imported under ANY alias (`from os import environ as _e`), which binds
      a name neither rule above can see (#68). From any module, as the
      bare-name rule is: a re-export (`from ._compat import environ as e`)
      reads the environment just the same, and `os`, `posix` and `nt` are
      only the modules that define it. A star import from `os`, `posix` or
      `nt` counts, since it binds these names unseen;
    * named by a string LITERAL passed to ANY call, positionally or by
      keyword, or used as a subscript key: `getattr(os, "environ")`,
      `operator.attrgetter("environ")(os)`, `os.__getattribute__("environ")`,
      `inspect.getattr_static(os, "environ")`, `vars(os)["environ"]`,
      `os.__dict__["getenv"]` (9b's and b5's shapes on #70). Any callee,
      because a list of callees that fetch by name is a list of spellings
      and the next one is missing from it;
    * a literal `/proc/<pid>/environ` path, the environment read as a file.

    It does not see a name or path BUILT at runtime (`getattr(os, "env" +
    "iron")`, a key from a variable, `Path("/proc") / "self" / "environ"`),
    `importlib` plus a computed attribute, a native reader reached some
    other way than those attribute names, or a read done by a dependency
    such as `dotenv` on this module's behalf. Those are the "more
    indirection" this file's docstring leaves to CodeQL.

    It errs the safe way: a WSGI-style `environ` parameter, `self.environ`,
    `request["environ"]` or `mapping.get("environ")` is flagged too, though
    it isn't the process environment. A false alarm here costs one
    declaration; a miss costs C-02.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _ENV_NAMES | _NATIVE_ENV_NAMES:
            return True
        if isinstance(node, ast.Name) and node.id in _ENV_NAMES:
            return True
        if isinstance(node, ast.ImportFrom) and any(
            alias.name in _ENV_NAMES or (alias.name == "*" and node.module in {"os", "posix", "nt"})
            for alias in node.names
        ):
            return True
        if isinstance(node, ast.Subscript) and _names_an_env_reader(node.slice):
            return True
        if isinstance(node, ast.Call) and any(
            _names_an_env_reader(arg)
            for arg in [*node.args, *(keyword.value for keyword in node.keywords)]
        ):
            return True
    return False


def _prompt_constants(tree: ast.AST) -> list[tuple[str, str]]:
    """Module-level bindings that hold prompt text, as (name, text).

    Detected by name rather than declared, so a prompt added tomorrow is
    covered tomorrow. `_PROMPT`, `_RULES`, `_CONTRACT`, `_INSTRUCTION` and
    `_SYSTEM` are the markers this project actually uses for text that
    reaches a model.

    The text is gathered from every string constant *underneath* the
    assigned value rather than from a bare `ast.Constant`, because a prompt
    is not always assigned directly. `llm/prompts.py` introduced
    `SYSTEM_PROMPT = register("...")`, which is an `ast.Call`; implicit
    concatenation across lines and f-strings are the other two shapes in
    this tree. A check written for the bare form silently stopped covering
    the answering, planning and entailment prompts the day the registry
    landed — which is the failure this docstring exists to stop repeating.
    """
    markers = ("PROMPT", "RULES", "CONTRACT", "INSTRUCTION", "SYSTEM")
    found: list[tuple[str, str]] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if node.value is None:
            continue
        text = "\n".join(
            sub.value
            for sub in ast.walk(node.value)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        )
        if not text:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and any(m in target.id.upper() for m in markers):
                found.append((target.id, text))
    return found


def test_every_environment_reader_is_declared():
    """No module reads the environment without being written down."""
    undeclared = [
        _rel(path)
        for path in _python_files()
        if _reads_environment(ast.parse(path.read_text(encoding="utf-8")))
        and _rel(path) not in DECLARED_ENV_READERS
    ]
    assert not undeclared, (
        "Module(s) read the environment without being declared in "
        "DECLARED_ENV_READERS. Credentials are read in known places on "
        "purpose (https://zardoz.io/trust/commitments/, C-02) — add the module with what it "
        "reads and why, or read the value somewhere that already may:\n  "
        + "\n  ".join(sorted(undeclared))
    )


def test_declared_env_readers_all_exist():
    """The declaration does not outlive the code it describes."""
    missing = [name for name in DECLARED_ENV_READERS if not (SRC / name).exists()]
    assert not missing, f"DECLARED_ENV_READERS names module(s) that no longer exist: {missing}"


def test_no_prompt_building_module_reads_the_environment():
    """A module that defines prompt text never also reads a credential.

    This is the invariant that makes the commitment hold: the two
    capabilities are never in the same file, so the accident that puts a key
    in an f-string has nowhere to happen.
    """
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prompts = _prompt_constants(tree)
        if prompts and _reads_environment(tree):
            names = ", ".join(name for name, _ in prompts)
            offenders.append(f"{_rel(path)} (defines {names})")

    assert not offenders, (
        "Module(s) both define prompt text and read the environment. Keep "
        "them apart: read the value in a provider and pass it as an "
        "argument, never alongside a prompt constant "
        "(https://zardoz.io/trust/commitments/, C-02):\n  " + "\n  ".join(sorted(offenders))
    )


def test_no_prompt_text_mentions_a_credential_variable():
    """No prompt constant names a credential environment variable.

    A prompt that says "set ANTHROPIC_API_KEY" is harmless; a prompt that
    interpolates one is not, and the two are one character apart. Refusing
    the name outright in prompt text costs nothing — error messages and
    docstrings, which are where that advice belongs, are not prompts.
    """
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, text in _prompt_constants(tree):
            for credential in CREDENTIAL_ENV_NAMES:
                if credential in text:
                    offenders.append(f"{_rel(path)}:{name} mentions {credential}")

    assert not offenders, (
        "Prompt text names a credential variable (https://zardoz.io/trust/commitments/, "
        "C-02):\n  " + "\n  ".join(sorted(offenders))
    )


# ---- the detector itself (#68) ----------------------------------------------
#
# Written from the ways Python can spell an environment read, not from the
# detector's own name set: a case list derived from `_ENV_NAMES` would lose
# a case in the same edit that dropped a name from it.

READS = [
    pytest.param("import os\nx = os.environ['K']", id="os.environ"),
    pytest.param("import os\nx = os.getenv('K')", id="os.getenv"),
    pytest.param("import os as _o\nx = _o.getenv('K')", id="aliased module"),
    pytest.param("from os import environ\nx = environ['K']", id="from-import"),
    pytest.param("from os import getenv\nx = getenv('K')", id="from-import getenv"),
    pytest.param("from os import environ as _e\nx = _e['K']", id="from-import aliased"),
    pytest.param("from os import getenv as g\nx = g('K')", id="from-import getenv aliased"),
    pytest.param("from os import environb as _b\nx = _b[b'K']", id="environb aliased"),
    pytest.param("from posix import environ as _e\nx = _e[b'K']", id="posix aliased"),
    pytest.param("from nt import environ as _e\nx = _e['K']", id="nt aliased"),
    pytest.param("from os import *\nx = 1", id="star import"),
    pytest.param("from ._compat import getenv\nx = getenv('K')", id="re-export"),
    pytest.param("from ._compat import environ as e\nx = e['K']", id="re-export aliased"),
    pytest.param("from ._compat import *\nx = getenv('K')", id="star re-export, then the name"),
    pytest.param("import os\nx = getattr(os, 'environ')['K']", id="getattr literal"),
    pytest.param("import os\nx = vars(os)['environ']['K']", id="vars subscript"),
    pytest.param("import os\nx = os.__dict__['getenv']('K')", id="__dict__ subscript"),
    pytest.param("import operator, os\nx = operator.attrgetter('environ')(os)", id="attrgetter"),
    pytest.param(
        "from operator import attrgetter\nimport sys\n"
        "x = attrgetter('os.environ')(sys.modules['__main__'])",
        id="attrgetter dotted",
    ),
    pytest.param(
        "import ctypes\nk = ctypes.windll.kernel32\nk.GetEnvironmentVariableW('K', None, 0)",
        id="ctypes win32",
    ),
    pytest.param("def f():\n    from os import environ as e\n    return e", id="nested import"),
    pytest.param("import os\nx = os.__getattribute__('environ')", id="__getattribute__"),
    pytest.param(
        "import os\nx = object.__getattribute__(os, 'environ')", id="object.__getattribute__"
    ),
    pytest.param(
        "import inspect, os\nx = inspect.getattr_static(os, 'environ')", id="getattr_static"
    ),
    pytest.param("import os\nx = getattr(os, name='environ')", id="literal as a keyword"),
    pytest.param("x = open('/proc/self/environ').read()", id="/proc/self/environ"),
    pytest.param("x = open('/proc/1/environ', 'rb').read()", id="/proc/<pid>/environ"),
    # A false alarm, pinned so it is a decision (the docstring's safe way):
    pytest.param("x = {}\ny = x.get('getenv')", id="mapping get (accepted false alarm)"),
]

NOT_READS = [
    pytest.param("import os\nx = os.path.join('a', 'b')", id="os without env"),
    pytest.param("from os import path as environment\nx = environment", id="lookalike alias"),
    pytest.param("from posixpath import normpath\nx = normpath('a')", id="posixpath"),
    pytest.param("from pathlib import *\nx = Path('a')", id="star import of another module"),
    pytest.param("cfg = {}\nx = cfg['environment']", id="subscript key that only starts alike"),
    pytest.param("x = getattr(object(), 'environment', None)", id="getattr of a look-alike name"),
    pytest.param("x = {'environ': 1, 'getenv': 2}", id="the words as dict keys"),
    pytest.param("x = 'environment variables'", id="the word in prose"),
    pytest.param("x = open('/proc/self/status')", id="another /proc file"),
    pytest.param("import subprocess\nsubprocess.run(['git'], check=True)", id="inherits env only"),
]


@pytest.mark.parametrize("source", READS)
def test_the_detector_sees_every_spelling_of_an_environment_read(source):
    assert _reads_environment(ast.parse(source))


@pytest.mark.parametrize("source", NOT_READS)
def test_the_detector_passes_a_module_that_reads_no_environment(source):
    assert not _reads_environment(ast.parse(source))
