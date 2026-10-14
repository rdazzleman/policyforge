"""Keep the test suite hermetic.

Every test in this project is meant to run offline, for free, and to give
the same answer every time. That held until the shell started building a
provider at launch: with `ANTHROPIC_API_KEY` set in a developer's
environment, CLI tests quietly began making real API calls — slow, billed,
and non-deterministic. One of them passed and failed on consecutive runs
depending on how a model routed a question.

So credentials are stripped for the whole suite. Tests that need a model
pass a fake one explicitly, which is what every one of them already does.

The side effect is the more valuable half: with no key present, the CLI
takes its own no-model path, so the suite now exercises the way most people
will actually run this. That path was crashing — `AnthropicProvider` raises
`RuntimeError` for a missing key and the shell only caught `ValueError` —
and nobody noticed, because everybody working on it had a key.

**Stripping the environment is not sufficient on its own, and that was
measured rather than assumed.** LiteLLM calls `load_dotenv()` at import, so
it reads this repository's `.env` off disk and writes the keys into
`os.environ` — which happens *after* the fixture below has run, and happens
even when the variable was never in the environment to begin with. Observed
directly: with `OPENROUTER_API_KEY` absent from the environment, `import
litellm` created it, populated from `.env`. Any test importing litellm after
that point would hold a live, billable key.

So the load is disabled before anything can import litellm, and the fixture
strips what is already there. Two mechanisms, because they fail differently:
one covers a key in the developer's shell, the other covers a key on disk.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import os
import socket
import tempfile
from pathlib import Path

import pytest

try:
    import dotenv
except ImportError:  # nothing installed can load .env, so nothing to disarm
    dotenv = None

#: Disarm dotenv before any test, fixture or module import can pull a
#: credential off disk.
#:
#: Done at conftest import — the earliest point pytest gives us, and before
#: litellm can be imported by anything — because `load_dotenv()` runs at
#: *its* import and a fixture would be far too late. Patching a third-party
#: module is heavy-handed, and it is the only thing that actually works here:
#: the alternative is a suite whose hermeticity depends on which test
#: imported litellm first.
#:
#: Scoped to the test session by being in conftest. Nothing in `src/` is
#: affected, and a real run loads `.env` exactly as before.
#:
#: dotenv is not a dependency of this project. It arrives with litellm (and,
#: before mcp 2.0, with mcp), so without it there is no loader to disarm.
if dotenv is not None:
    dotenv.load_dotenv = lambda *args, **kwargs: False
    if hasattr(dotenv, "main"):
        dotenv.main.load_dotenv = lambda *args, **kwargs: False

#: LiteLLM's own price table, not the copy it fetches from GitHub. Set here,
#: at conftest import and before anything imports litellm, for the same
#: reason as the dotenv line above: the suite must not reach the network.
#: Measured on #31: once CI installed the `litellm` extra (1.103.0, from the
#: lock), 87 tests failed on `no_network` refusing raw.githubusercontent.com.
#: The developer venv's 1.100.1 fetches it at import too. Locally the import
#: happened at collection, before `no_network` exists, so the fetch went out
#: unblocked on every run and the local gate stayed green. Unconditional, not
#: setdefault: a developer's `False` must not make the suite fetch.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

#: Every `GIT_*` variable, removed before anything runs git (#45; 9b found
#: it, b5 and 1d reproduced it). git's environment OVERRIDES its config, so
#: an identity a session exports to commit as its bot replaced the author a
#: fixture set with `git config user.name`, and two tests that assert on the
#: author went red: 2 passed without GIT_AUTHOR_*/GIT_COMMITTER_*, 2 failed
#: with them, and GIT_AUTHOR_NAME alone was enough. Identity is one family
#: of many: GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_CONFIG_* and the
#: date variables all change what a test's git does.
#:
#: Removed by RULE (every name starting GIT_), not from a list of known
#: offenders, so a variable nobody has met yet is covered too. None is kept:
#: every fixture sets its own identity through config, and every commit a
#: test makes passes `-m`, so the suite needs none of them. ONE is then set,
#: by the suite rather than inherited: `GIT_TERMINAL_PROMPT=0`, so git
#: itself never falls back to asking at the terminal (ba on #52; #56).
#: That's ALL it stops: a credential helper still runs, and on this machine
#: the system gitconfig sets `credential.helper=manager`, which can open a
#: dialog and wait (9b measured that hang on #121). This conftest doesn't
#: touch config, `GCM_INTERACTIVE` or `GIT_CONFIG_NOSYSTEM` (b5 on #209), so
#: a test that reached a network remote could still hang there. No test
#: reaches an authenticating remote through THIS environment: the
#: signed-out reads in `test_publish_public.py` hit a local HTTP server
#: that answers 401, but through `publish_public.signed_out_env` (no system
#: config, `GCM_INTERACTIVE=never`, an empty home), which is what keeps
#: them from hanging (b5 on #209). At conftest import, because a test
#: module's top-level code runs git at collection, before any fixture.
#:
#: NOT restored at session end, unlike `POLICYFORGE_CONFIG` in
#: `pytest_unconfigure`: the removal lasts as long as this python process.
#: That's harmless only because nothing runs `pytest.main` in-process (none
#: under tests/ or scripts/ when this was written); nothing in `src/` is
#: affected.
#:
#: NOT covered, by design: the developer's global and system git CONFIG
#: (e.g. a system `core.autocrlf=true`). Isolating those changes line-ending
#: behaviour under the tests and is its own change.
for _name in [name for name in os.environ if name.upper().startswith("GIT_")]:
    del os.environ[_name]
os.environ["GIT_TERMINAL_PROMPT"] = "0"

#: Every environment variable that could let a provider reach the network.
#:
#: The second group is the one the suite was missing. LiteLLM reaches most
#: vendors behind one model string and resolves each one's key from that
#: vendor's own environment convention, so the project's rule — that a key
#: is only ever reached through a variable config names — does not constrain
#: it. `openrouter/…` is the model string this project's own measurements
#: are run with, and `OPENROUTER_API_KEY` was not on this list.
_CREDENTIALS = (
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CONFLUENCE_API_TOKEN",
    "CONFLUENCE_USERNAME",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "AZURE_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "TOGETHERAI_API_KEY",
    "HUGGINGFACE_API_KEY",
    "XAI_API_KEY",
)


def pytest_configure(config):
    """Stop before collection when `policyforge` is not this tree's.

    The editable install pins the checkout it was made from, so a bare
    `pytest` in a git worktree imports the main checkout's source and
    reports all-pass on a branch it never loaded. `scripts/check.py` refuses
    the same way; pytest gets its own hook because it is what people run
    between full gates. Same code, one place: scripts/tree_guard.py.

    It also points config at nothing BEFORE COLLECTION (#26, 1d on #29).
    A test module's top-level code runs at collection, before any fixture:
    `test_level_scoping.py`'s module-level `normalize_framework` call filled
    the declared-key cache from the developer's config, and a config that
    re-keyed 800-53 then failed 4 tests in `test_bundles.py`. The per-test
    fixture below re-points it for each test; this covers what runs first.
    """
    config.addinivalue_line(
        "markers",
        "own_config: the test builds and reads its own config, not the suite's empty one (#26)",
    )
    config.addinivalue_line(
        "markers",
        "slow: measured at scripts/slow_tests.py's threshold or more; applied only from "
        "tests/slow_tests.txt, never written on a test (#292)",
    )
    config.addinivalue_line(
        "markers",
        "github_config: reads .github/workflows or .github/dependabot.yml, which the public "
        "snapshot leaves out (161); deselected in a tree that has neither",
    )
    config._policyforge_config_before = os.environ.get("POLICYFORGE_CONFIG")
    os.environ["POLICYFORGE_CONFIG"] = str(
        Path(tempfile.mkdtemp(prefix="policyforge-tests-")) / "no-config-for-this-session.yaml"
    )
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("tree_guard", root / "scripts" / "tree_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    refusal = guard.foreign_source(guard.resolved_origin(), root, invocation="pytest")
    if refusal:
        pytest.exit(refusal, returncode=2)


def public_snapshot(root: Path) -> bool:
    """Whether `root` is the public snapshot's shape (161): it has neither
    GitHub's workflows nor its Dependabot config, which `publish_public.py`
    leaves out of P. Both, not either, so a tree that lost one by accident
    still runs every test and fails on what it lost."""
    github = root / ".github"
    return not any(
        (github / name).exists() for name in ("workflows", "dependabot.yml", "dependabot.yaml")
    )


def pytest_collection_modifyitems(config, items):
    """Mark `slow` exactly the tests tests/slow_tests.txt lists, each with the
    time a serial run measured (scripts/slow_tests.py writes it).

    In the public snapshot's shape, deselect the tests marked `github_config`:
    they read files that tree doesn't have (161). Deselected, not skipped, so
    `skip_gate.py`'s expected skips are the same in both trees, and counted
    on the terminal so the run says what it left out."""
    root = Path(__file__).resolve().parents[1]
    if public_snapshot(root):
        left_out = [item for item in items if item.get_closest_marker("github_config")]
        if left_out:
            config.hook.pytest_deselected(items=left_out)
            items[:] = [item for item in items if not item.get_closest_marker("github_config")]
    spec = importlib.util.spec_from_file_location("slow_tests", root / "scripts" / "slow_tests.py")
    slow_tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(slow_tests)
    listed = slow_tests.read()
    for item in items:
        if item.nodeid in listed:
            item.add_marker(pytest.mark.slow)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say the public snapshot's deselection on the terminal. Decided from the
    tree, not from what collection recorded: under xdist the workers collect,
    and the controller that prints this never sees their deselection."""
    if public_snapshot(Path(__file__).resolve().parents[1]):
        terminalreporter.write_line(
            "public snapshot: the tests marked github_config were deselected, since this "
            "tree has no .github/workflows and no Dependabot config (161)"
        )


def pytest_unconfigure(config):
    """Put back the variable `pytest_configure` replaced: the process may
    outlive the session (an in-process `pytest.main`)."""
    before = getattr(config, "_policyforge_config_before", None)
    if before is None:
        os.environ.pop("POLICYFORGE_CONFIG", None)
    else:
        os.environ["POLICYFORGE_CONFIG"] = before


#: The process-wide caches in `src/` that are filled from config: module
#: globals assigned behind a `global` statement whose fill reads config (1d
#: on #29, re-derived by b5). `content/deontic.py` has three more, filled
#: from the BUNDLED catalogs, which config can't change, so they aren't here.
#: A new config-derived cache belongs on this list, or it carries one test's
#: config (or the collection-time state) into every later test.
#:
#: **What this can't see:** a module-level dict or list mutated in place
#: (`CACHE[key] = ...`) needs no `global`, so neither this list's sweep
#: (`test_config_isolation._swept_caches`) nor its classification test finds
#: it. 1d measured that blind spot on #29: its only member today is
#: `llm.prompts.REGISTRY`, which isn't config-derived. A config-derived cache
#: of that shape would have to be added here by hand.
_CONFIG_CACHES = (
    ("policyforge.mapping.crosswalk", "_DECLARED_KEYS"),
    ("policyforge.content.tags", "_KNOWN_NAMES"),
    # Every requirement id on disk, read through the same config as the names (#189).
    ("policyforge.content.tags", "_KNOWN_IDS"),
    # Every catalog on disk as a citation scope, the fallback when a run has no set (#215).
    ("policyforge.content.tags", "_DISK_SCOPE"),
    ("policyforge.topics.anchoring", "_RULES"),
    # Which catalogs check a part below a leaf against its text, from the
    # manifests config finds (#441).
    ("policyforge.topics.anchoring", "_LEAF_CHECKS"),
)

#: Process caches filled from which PLUGINS are installed (#32), not from
#: config: a third class for `test_every_process_cache_in_src_is_classified`.
#: Emptied per test like the config caches, so a fake plugin one test
#: installs can't answer for the next.
_PLUGIN_CACHES = (("policyforge.plugins", "_cache"),)


@pytest.fixture(autouse=True)
def escalations_start_empty():
    """Every test starts with no pending escalation (#315).

    `llm/escalation.py` keeps announced escalations in a ContextVar until a
    ledger row takes them. Under xdist a worker runs many tests in one
    context, so records one test announced and never took were read by the
    next: `test_effort_announces_its_own_2x_retry` failed a batch gate on two
    records it never made (9b, gating PR 314). Each test now gets a fresh
    list, and the previous value is restored after it.

    Leftovers are discarded, not failed: 1d measured 32 tests that leave one
    or two behind, every one a provider's retry path called outside a ledger
    row, where nothing takes them, correctly. Production's own check that a
    pending escalation is never lost is `escalation.end`, tested on its own.
    """
    from policyforge.llm import escalation

    token = escalation._pending.set(None)
    yield
    escalation._pending.reset(token)


@pytest.fixture(autouse=True)
def no_installed_plugins(monkeypatch):
    """Answer every test as a tree with no licensed-framework plugin (#32).

    The plugin is a separately installed package, found through its entry
    point. A developer who has it installed would otherwise run a different
    suite from CI, which never has it: the same leak as a developer's config
    (#26), one package over. A test that wants a plugin installs a fake one
    (`test_plugins.py`'s `install`), which replaces this for that test.
    """
    from policyforge import plugins

    monkeypatch.setattr(plugins, "_entry_points", lambda: [])


@pytest.fixture(autouse=True)
def no_live_credentials(monkeypatch):
    """Strip provider credentials so no test can reach a real MODEL provider.

    Not every service needs a credential: eCFR does not, and a test that
    stubbed only half its fetch reached it live (#432). `no_network` below
    is what stops that; this stops a keyed provider.

    Autouse and unconditional. A test that wants a model injects a fake;
    there is no legitimate reason for the suite to hold a live key, and
    "only this one test calls out" is how a suite becomes slow and flaky.
    """
    for name in _CREDENTIALS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def ledger_writes_nowhere_real(monkeypatch, tmp_path):
    """Point the model-call ledger at a throwaway file.

    Same reasoning as the credentials above, in the other direction. The
    ledger defaults to `output/.model-log/calls.jsonl` relative to the
    working directory, and `get_provider` wraps every provider in it — so
    without this, running the suite would append several hundred fake
    entries to whatever real audit record the developer happens to have.
    A record somebody may one day rely on is the last file a test run
    should be writing to by accident.

    Tests that care what was recorded pass their own path explicitly.
    """
    from policyforge.llm import ledger

    monkeypatch.setattr(ledger, "DEFAULT_LEDGER_PATH", tmp_path / "calls.jsonl")


#: The checkout the suite runs from. Its `config/config.yaml`, if any, is
#: the developer's own and is never a test input.
_ROOT = Path(__file__).resolve().parents[1]


def config_leak(cwd: Path, *, own: bool, override: str | None) -> str:
    """Why a finished test's config was not its own, or "" (#26).

    Two ways, one per shape the matrix found. A test that is NOT marked
    `own_config` but left a `config/config.yaml` in its working directory
    wrote a config the suite then hid from it: it passed without reading
    its own input, which is a test silently measuring less. A test marked
    `own_config` that stands in the checkout with no `POLICYFORGE_CONFIG`
    of its own would read the developer's file. Kept apart from the fixture
    so both directions are tested directly.
    """
    here = cwd.resolve()
    if not own and here != _ROOT and (here / "config" / "config.yaml").is_file():
        return (
            f"this test wrote {here / 'config' / 'config.yaml'} and the suite hid it: "
            "config is isolated per test (#26). Mark the test @pytest.mark.own_config "
            "so it reads the config it built."
        )
    if own and not override and here == _ROOT:
        return (
            "this test is marked own_config but runs in the checkout with no "
            "POLICYFORGE_CONFIG, so it would read the developer's config/config.yaml. "
            "chdir to its tmp_path, or set POLICYFORGE_CONFIG to the file it builds."
        )
    return ""


@pytest.fixture(autouse=True)
def no_developer_config(request, monkeypatch, tmp_path):
    """Give no test the developer's config (#26).

    Config is read from `config/config.yaml` under the working directory,
    or from the file `POLICYFORGE_CONFIG` names. The suite runs in the
    checkout, so without this every test read whatever the person running
    it keeps there. b5 measured it on release/1.7: 20 of 4668 tests changed
    result under an empty, malformed or local-model config, or with the
    variable set. `check.py` answered differently on different machines.

    So every test gets `POLICYFORGE_CONFIG` pointing at a file that does not
    exist, which is the clean-machine state. It is an environment variable,
    not a patch, so child processes inherit it too.

    **Opting in:** a test whose subject IS config loading is marked
    `@pytest.mark.own_config`. The variable is removed, and the test builds
    its own file: `chdir` to `tmp_path` and write `config/config.yaml`
    there, or set `POLICYFORGE_CONFIG` itself. `config_leak` checks both
    directions at teardown, so a test can't quietly hide its own input, and
    an opted-in one can't quietly read the developer's.
    """
    own = request.node.get_closest_marker("own_config") is not None
    if own:
        monkeypatch.delenv("POLICYFORGE_CONFIG", raising=False)
    else:
        monkeypatch.setenv("POLICYFORGE_CONFIG", str(tmp_path / "no-config-for-this-test.yaml"))
    # Empty every config-derived cache, so it refills from THIS test's
    # config: not from collection, and not from the test before.
    import importlib

    for module, name in _CONFIG_CACHES + _PLUGIN_CACHES:
        monkeypatch.setattr(importlib.import_module(module), name, None)
    yield
    # Before monkeypatch restores the working directory: this fixture asked
    # for monkeypatch, so its teardown runs first.
    problem = config_leak(Path.cwd(), own=own, override=os.environ.get("POLICYFORGE_CONFIG"))
    if problem:
        pytest.fail(problem, pytrace=False)


#: Hosts a test may connect to: the machine itself. Tests name
#: `localhost:11434` for a local model with the transport stubbed; loopback
#: is allowed so a stub that misses fails on its own terms, not on this.
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", ""})


def _is_loopback(host) -> bool:
    """Only the machine itself. Anything this cannot read is NOT loopback:
    the first version passed every non-str host, so `getaddrinfo(b"host")`
    made a real lookup (policyforge-b5 on #434)."""
    if host is None:
        return True  # getaddrinfo(None, port): the local host, by definition
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", errors="replace")
    if not isinstance(host, str):
        return False
    name = host.strip("[]").lower()
    if name in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(name.split("%")[0]).is_loopback
    except ValueError:
        return False


class NetworkUsedInTest(RuntimeError):
    """A test reached past this machine. Not an `OSError`, so no HTTP client
    reads it as a connection failure to retry or to report as the server's."""


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Refuse name lookups, connections and datagrams beyond loopback (#432).

    `test_etl_hipaa_fetches_and_parses` stubbed the XML fetch and not the
    date lookup before it, so it called eCFR live; one CI run at a commit
    failed on eCFR's 403 while another at the same commit passed. A red
    that is about a third party's rate limiter teaches a reader to re-run,
    and re-running is what hides a real red. Refused at the socket, before
    any client library, so a new unstubbed call is found by the test that
    makes it, with the host named, whatever library it goes through.

    **What is patched, exactly** (policyforge-ba on #434, who found the
    first version claimed "every lookup" and patched one resolver):
    `getaddrinfo`, `gethostbyname`, `gethostbyname_ex`, `gethostbyaddr`;
    `socket.connect`, `connect_ex`, `sendto`, `sendmsg`.

    **Not covered, named:** Windows' asyncio Proactor connects through
    `ConnectEx` below Python, so an asyncio connection to a LITERAL IP there
    passes. A hostname still needs a lookup, which is refused. No test here
    uses asyncio networking today; if one starts to, this sentence is the
    gap to close. **Child processes** are not covered either: a subprocess
    has its own sockets. policyforge-b5 measured the suite's 1,570 child
    processes on #434, and all were local git or scripts; a test that starts
    one which fetches is outside this guard.
    """
    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname
    real_gethostbyname_ex = socket.gethostbyname_ex
    real_gethostbyaddr = socket.gethostbyaddr
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_sendmsg = getattr(socket.socket, "sendmsg", None)  # absent on Windows

    def refuse(host):
        raise NetworkUsedInTest(
            f"this test reached the network ({host!r}); stub the call instead (#432)"
        )

    def getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            refuse(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def by_name(real):
        def lookup(host, *args, **kwargs):
            if not _is_loopback(host):
                refuse(host)
            return real(host, *args, **kwargs)

        return lookup

    def sendto(self, data, *rest):
        address = rest[-1]  # sendto(data, address) or sendto(data, flags, address)
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            refuse(address[0])
        return real_sendto(self, data, *rest)

    def sendmsg(self, buffers, ancdata=(), flags=0, address=None):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            refuse(address[0])
        return real_sendmsg(self, buffers, ancdata, flags, *((address,) if address else ()))

    def connect(self, address):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            refuse(address[0])
        return real_connect(self, address)

    def connect_ex(self, address):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            refuse(address[0])
        return real_connect_ex(self, address)

    # boto3 with no credentials asks the cloud instance-metadata service at
    # 169.254.169.254 for some. On a CI runner, a cloud VM, that address
    # answers: three Bedrock construction tests were talking to the runner's
    # metadata service (found by this guard, #432). botocore's own switch.
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", by_name(real_gethostbyname))
    monkeypatch.setattr(socket, "gethostbyname_ex", by_name(real_gethostbyname_ex))
    monkeypatch.setattr(socket, "gethostbyaddr", by_name(real_gethostbyaddr))
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    if real_sendmsg is not None:
        monkeypatch.setattr(socket.socket, "sendmsg", sendmsg)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
