"""Which content may be sent to which provider, decided before the call.

PolicyForge writes security policy, so "we only send licensed catalog
content to a model we run ourselves" should be a control it can evidence
rather than a sentence in a README. Today the one live case is advisory:
`generate-parser` prints a paragraph asking a human to confirm their MyCSF
licence permits sending an export to a third-party API, and then trusts the
answer. A paragraph is not a control. This module is.

**Two classifications and a ceiling between them.**

Providers are classified by where the bytes end up:

* `local` — a model running on this machine. Ollama, llama-server, LM
  Studio: a loopback endpoint. Nothing leaves the host.
* `self-hosted` — a model the organization runs, reached over its own
  network. An RFC 1918 or link-local address, or an internal name. It
  leaves the host but not the boundary.
* `third-party` — somebody else's processor. Anthropic, Bedrock, Vertex,
  OpenRouter, any public endpoint. It leaves the boundary, and a licence
  that permits internal use has now been tested against a question it was
  probably not written to answer.

Content is classified by who may hold it:

* `public-domain` — NIST 800-53, FedRAMP, ARC-AMPE, the HIPAA Security
  Rule. Government works; anyone may redistribute them.
* `organization-internal` — the organization's own material: generated
  documents, its topic registry, its synthesis output. Sensitive, but the
  organization decides where it goes, and sending it to a model is the
  entire purpose of this tool.
* `licensed` — HITRUST CSF and GovRAMP exports, and anything under
  `local_content/`. Held under a licence that permits *the licensee's* use
  and says nothing helpful about handing a copy to a processor.

The rule is a ceiling per content class: the most exposed provider class
that class of content may reach. Licensed content's ceiling is `local`;
everything else may reach a third party, because that is what the tool does
every working day. A configuration can tighten any ceiling and cannot
loosen one, since tightening is the only direction that is safe to make
easy.

**It fails closed in three places, on purpose.** A provider nobody can
classify is third-party, because the expensive mistake is assuming a model
is local when it is not. A framework with no manifest is licensed, which is
`frameworks/registry.py`'s existing rule and this module defers to it. And
a ceiling naming a class that does not exist raises rather than falling
back to a default, because a typo that silently restores the default
permission is how a control stops being one.

**What this is not.** It does not decide whether a generated document
citing `[HITRUST 01.c]` may be redistributed — that is a fair-use question
for a lawyer, and `registry.derived_from` exists to put the right files in
front of one. It decides only whether a specific file may be sent to a
specific endpoint, which is a question with an answer.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlsplit

from policyforge.frameworks import registry

# ---- provider classes, ordered by exposure --------------------------------

LOCAL = "local"
SELF_HOSTED = "self-hosted"
THIRD_PARTY = "third-party"

#: Ordered least- to most-exposed. The order is the whole comparison: a
#: ceiling permits every class at or below it, and a cascade takes the
#: maximum of its halves.
PROVIDER_CLASSES = (LOCAL, SELF_HOSTED, THIRD_PARTY)

# ---- content classes ------------------------------------------------------

PUBLIC_DOMAIN = "public-domain"
ORG_INTERNAL = "organization-internal"
LICENSED = "licensed"

CONTENT_CLASSES = (PUBLIC_DOMAIN, ORG_INTERNAL, LICENSED)

#: The most exposed provider class each kind of content may reach, absent a
#: configured ceiling. Only `licensed` is restrictive, and it is restrictive
#: because the licence under which that content arrived did not contemplate
#: a processor.
DEFAULT_CEILINGS: dict[str, str] = {
    PUBLIC_DOMAIN: THIRD_PARTY,
    ORG_INTERNAL: THIRD_PARTY,
    LICENSED: LOCAL,
}


@dataclass(frozen=True)
class LicensedAgreement:
    """An organisation's declaration that a NAMED hosted party is covered by
    its data agreement for licensed content (#11; the user's ruling, 80's spec
    1405). The first loosening of this module, so it is reachable only this
    way: `llm.licensed_agreement` in the organisation's own config. No
    environment variable, no default, and no inference from a name.

    It binds to the PARTY the agreement is with (80's ruling 1432). For a
    named API (`anthropic`, `bedrock`, `vertex`, `gemini`) the provider name
    is the vendor. For `openai-compat`, the `local` alias and `litellm` the
    name is a protocol, so the declaration also carries `base_url` (matched to
    the configured endpoint by host and port) or, for `litellm` with no
    `api_base`, `vendor` (matched to the vendor the model names).

    The provider keeps its true class. This raises the licensed ceiling for
    that party; it never relabels it `local` (option (i), rejected)."""

    provider: str
    #: When set, only this model is covered; when empty, any model of it.
    model: str = ""
    #: Free text naming the agreement, recorded and never validated.
    reference: str = ""
    #: The endpoint the agreement covers, for the protocol providers.
    base_url: str = ""
    #: The vendor the agreement covers, for `litellm` with no `api_base`.
    vendor: str = ""

    def covers(self, provider: ProviderClassification) -> bool:
        """Exactly this party (and model, if one is named). Never a prefix,
        never a look-alike: equality, case-folded, on the provider name, and
        on the scheme, host and port, or the vendor, where the name is a
        protocol. The scheme belongs to the agreement (80's ruling 1446): plain
        http hands the text to every hop between, which is not the party."""
        if not provider.name or provider.name.casefold() != self.provider.casefold():
            return False
        if self.model and provider.model != self.model:
            return False
        if _needs_endpoint(provider):
            return (
                bool(self.base_url)
                and _scheme(self.base_url) == _scheme(provider.endpoint)
                and _host_port(self.base_url) == _host_port(provider.endpoint)
            )
        if provider.name == "litellm":
            return bool(self.vendor) and self.vendor.casefold() == provider.vendor
        return True

    def party(self) -> str:
        """Who the content goes to, as the ledger and the reports say it."""
        if self.base_url:
            return _endpoint_party(self.base_url)
        return self.vendor or self.provider

    def as_record(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "party": self.party(),
            "model": self.model,
            "reference": self.reference,
        }

    def __str__(self) -> str:
        party = f" at {self.party()}" if self.party() != self.provider else ""
        model = f" model {self.model}" if self.model else " (any model)"
        ref = f", reference {self.reference!r}" if self.reference else ""
        return f"llm.licensed_agreement: {self.provider}{party}{model}{ref}"


_AGREEMENT_KEYS = frozenset({"provider", "model", "reference", "base_url", "vendor"})

#: The protocol providers: their name says how, not who (80's ruling 1432).
PROTOCOL_PROVIDERS = frozenset({"openai-compat", "local"})

#: litellm vendors that send to this machine when no `api_base` is set AND
#: no environment variable moves them: litellm reads OLLAMA_API_BASE before
#: its localhost default (1d's 1441), so `_checked_declaration` holds the
#: claim only while `endpoint_overrides()` is empty. Any other vendor a
#: model names is a hosted service (1d on #66).
LITELLM_LOCAL_VENDORS = frozenset({"ollama", "ollama_chat"})


#: The port a scheme implies when none is written.
_DEFAULT_PORTS = {"https": 443, "http": 80}


def _scheme(url: str) -> str:
    """A URL's scheme, case-folded, by `urlsplit`; "" when unreadable."""
    try:
        return urlsplit(url).scheme.casefold()
    except ValueError:
        return ""


def _endpoint_party(url: str) -> str:
    """An endpoint as the matcher compares it: host[:port], with the scheme
    written unless it is https, so plain http stays visible in the ledger
    (1446) and a refusal never shows two sides that read the same (80's
    1469)."""
    host, port = _host_port(url)
    party = f"{host}:{port}" if port else host
    scheme = _scheme(url)
    return party if scheme == "https" else f"{scheme}://{party}"


def _host_port(url: str) -> tuple[str, int | None]:
    """(host, port) of a URL, by `urlsplit` (the reader `_host_class` uses,
    never a string search), host case-folded. The path is ignored, and the
    scheme is read only for its default port: the host is the processor. An
    unreadable URL yields ("", None), which matches nothing. A port written
    out equal to its scheme's default (443 for https, 80 for http) is the
    same endpoint as no port, so it compares as no port (80 on #66); any
    other port compares as written."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return ("", None)
    if port is not None and _DEFAULT_PORTS.get(parts.scheme.casefold()) == port:
        port = None
    return (parts.hostname or "").casefold(), port


#: What `llm.licensed_agreement` takes, in the words every message uses.
_AGREEMENT_SHAPE = (
    "provider; for openai-compat, local or litellm with an api_base, its base_url; "
    "for litellm with no api_base, its vendor; optionally model and reference"
)


def _needs_endpoint(provider: ProviderClassification) -> bool:
    """Whether this provider's party is named by its endpoint."""
    return provider.name in PROTOCOL_PROVIDERS or (
        provider.name == "litellm" and bool(provider.endpoint)
    )


def licensed_agreement(config: dict | None) -> LicensedAgreement | None:
    """The declared agreement, or None. Malformed is refused, not ignored: a
    typo that silently dropped the declaration would read as a working
    boundary, and one that silently widened it would be worse. A declaration
    for a protocol provider without the field that names its party is
    malformed (80's ruling 1432)."""
    llm = (config or {}).get("llm") or {}
    block = llm.get("licensed_agreement")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ValueError(
            f"llm.licensed_agreement should be a mapping ({_AGREEMENT_SHAPE}), "
            f"not {type(block).__name__}."
        )
    unknown = sorted(str(k) for k in block if str(k) not in _AGREEMENT_KEYS)
    if unknown:
        raise ValueError(
            f"llm.licensed_agreement has {', '.join(unknown)}, which it does not read. "
            f"It takes: {', '.join(sorted(_AGREEMENT_KEYS))}."
        )
    provider = str(block.get("provider") or "").strip()
    if not provider:
        raise ValueError(
            "llm.licensed_agreement names no provider. Name the provider your agreement "
            "covers, as `llm.provider` spells it."
        )
    agreement = LicensedAgreement(
        provider=provider,
        model=str(block.get("model") or "").strip(),
        reference=str(block.get("reference") or "").strip(),
        base_url=str(block.get("base_url") or "").strip(),
        vendor=str(block.get("vendor") or "").strip(),
    )
    if agreement.base_url and not _host_port(agreement.base_url)[0]:
        raise ValueError(
            f"llm.licensed_agreement.base_url is {agreement.base_url!r}, which names no host."
        )
    name = provider.casefold()
    configured = str(llm.get("provider", "")).strip().casefold()
    why = (
        " The provider name says how the content is sent, not who receives it, so the "
        "declaration names the party the agreement is with."
    )
    if name in PROTOCOL_PROVIDERS and not agreement.base_url:
        raise ValueError(f"llm.licensed_agreement names {provider!r} but no base_url." + why)
    if name == "litellm" and configured == "litellm":
        if llm.get("api_base") and not agreement.base_url:
            raise ValueError(
                "llm.licensed_agreement names 'litellm' with an api_base configured, "
                "but no base_url." + why
            )
        if not llm.get("api_base") and not agreement.vendor:
            raise ValueError(
                "llm.licensed_agreement names 'litellm' with no api_base, but no vendor "
                "(the vendor the model names, e.g. 'openrouter' for openrouter/...)." + why
            )
    return agreement


#: Environment variables that can move a provider's endpoint without the
#: config knowing: the SDKs read them when no endpoint is passed (b5 on #66
#: measured ANTHROPIC_BASE_URL and OPENROUTER_API_BASE sending a covered call
#: to another host). A NAME rule alone is a list of spellings: b5 then sent
#: the canary through AWS_BEDROCK_RUNTIME_ENDPOINT, AZURE_OPENAI_ENDPOINT and
#: WATSONX_URL, none of which it matched, and counted 107 of 300 endpoint
#: names the three SDKs read outside it. So a variable is also refused by its
#: VALUE: any that holds an http(s) URL (`endpoint_overrides`).
_ENDPOINT_OVERRIDE = re.compile(r"(?:^|_)(?:BASE_URL|API_BASE|API_BASE_URL|ENDPOINT_URL)(?:_|$)")

#: What separates URLs in one variable's value (a proxy list, say).
_VALUE_SPLIT = re.compile(r"[\s,;]+")


def _holds_url(value: str) -> bool:
    """Whether any piece of `value` is an http(s) URL with a host, by
    `urlsplit`. The value is parsed, never kept, printed or used. A
    scheme-less `host:port` value is NOT read as one: no SDK b5 drove
    redirected to such a value (#66, 1495), and a variable named like an
    override is refused by its name whatever its value."""
    for piece in _VALUE_SPLIT.split(value):
        try:
            parts = urlsplit(piece.strip("\"'<>"))
            if parts.scheme.casefold() in _DEFAULT_PORTS and parts.hostname:
                return True
        except ValueError:
            continue
    return False


#: The standard proxy variables, exempt from the value rule when the
#: destination is https (80's ruling 1446). A proxy routes the connection;
#: for https the TLS session still ends at the host the agreement names, no
#: SDK reads these as a base URL, and the organisations holding agreements
#: are the ones behind corporate proxies. ONLY these names, compared
#: upper-cased. For an http destination they are NOT exempt: httpx 0.28.1
#: sends http://localhost itself through HTTP_PROXY (measured on #66), so
#: the proxy would read the text. NO_PROXY only removes proxying, so it is
#: exempt for both.
_PROXY_NAMES = frozenset({"HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY"})


def endpoint_overrides(*, destination: str = "https") -> list[str]:
    """The set, non-empty environment variables that could send a call
    somewhere the agreement does not name: every one whose NAME is an
    endpoint override, and every one whose VALUE holds an http(s) URL,
    whatever it is called, except the proxy names above when `destination`
    is "https" (and NO_PROXY always). A CI runner's GITHUB_SERVER_URL still
    refuses. Names only; a value never leaves this function."""
    exempt = _PROXY_NAMES if destination == "https" else frozenset({"NO_PROXY"})
    return sorted(
        name
        for name, value in os.environ.items()
        if value.strip()
        and name.upper() not in exempt
        and (_ENDPOINT_OVERRIDE.search(name.upper()) or _holds_url(value))
    )


#: Where litellm's ollama routes send their model-info calls (`/api/show`)
#: when no api_base reaches them: its default, whatever api_base says (#69,
#: measured: 4 of 5 went here).
_LITELLM_OLLAMA_DEFAULT = "http://localhost:11434"


def _proxied(url: str) -> bool:
    """Whether httpx, reading the environment as litellm's clients do, would
    send `url` through a proxy. Asked of httpx itself, not re-implemented:
    NO_PROXY's shapes, and on Windows the system proxy settings, are what
    httpx honours, so a parser of our own could only disagree with it
    (1d on #69). Anything unreadable counts as proxied.

    These are the lines `httpx.Client` runs (0.28.1: `_get_proxy_map`, the
    sorted `_mounts`, `_transport_for_url`), without building a client,
    which costs an SSL context (about 0.6 s here) per call. Building each
    `Proxy` keeps a value that would make `Client` itself fail counted as
    proxied. test_loopback_proxy checks the answer against a real request."""
    try:
        import httpx
        from httpx._utils import URLPattern, get_environment_proxies

        mounts = sorted(
            (URLPattern(key), None if value is None else httpx.Proxy(url=value))
            for key, value in get_environment_proxies().items()
        )
        target = httpx.URL(url)
        for pattern, proxy in mounts:
            if pattern.matches(target):
                return proxy is not None
        return False
    except Exception:  # noqa: BLE001 - a setting httpx cannot read counts as proxied
        return True


def proxied_loopback(provider: ProviderClassification) -> list[str]:
    """The loopback URLs a litellm provider would reach through a proxy.

    litellm builds its own httpx clients, and for a loopback api_base they
    followed HTTP_PROXY with the text in cleartext (#69, measured on the
    ollama, ollama_chat and openai routes). A client with no proxy can be
    injected for the completion, but not for litellm's own model-info calls
    (`/api/show`, through its module-level client), so the route is refused
    for licensed content instead (80's ruling 1591). Every litellm route with
    a loopback endpoint is held to it, not only the measured ones, since
    each builds the same kind of client. requests-based clients reach a
    loopback endpoint directly and never appear here."""
    if provider.parts:
        return [url for part in provider.parts for url in proxied_loopback(part)]
    if provider.name != "litellm" or not is_loopback(provider.endpoint):
        return []
    urls = [provider.endpoint]
    # The default is a second URL only when it's a different server: an
    # api_base of http://LOCALHOST:11434/ is the same one (80's 1649).
    if provider.vendor in LITELLM_LOCAL_VENDORS and _endpoint_party(
        _LITELLM_OLLAMA_DEFAULT
    ) != _endpoint_party(provider.endpoint):
        urls.append(_LITELLM_OLLAMA_DEFAULT)
    return [url for url in urls if _proxied(url)]


def _destination(provider: ProviderClassification) -> str:
    """ "https" when every half that may answer is reached over https, else
    "http". A provider with no configured endpoint is a named API or a
    litellm vendor, whose SDKs use https (1446), except the litellm vendors
    whose default is http://localhost (ollama; 1d's 1478)."""
    if provider.parts:
        schemes = {_destination(part) for part in provider.parts}
        return "https" if schemes == {"https"} else "http"
    if not provider.endpoint:
        return "http" if provider.vendor in LITELLM_LOCAL_VENDORS else "https"
    return "https" if _scheme(provider.endpoint) == "https" else "http"


def _agreement_allows(
    provider: ProviderClassification, ceiling: str, agreement: LicensedAgreement
) -> bool:
    """Whether the agreement covers everything that may answer. A cascade is
    covered only if each half is inside the ceiling or covered itself, so the
    half that answers can never be an undeclared one."""
    if provider.parts:
        return all(
            exposure(part.klass) <= exposure(ceiling) or _agreement_allows(part, ceiling, agreement)
            for part in provider.parts
        )
    return agreement.covers(provider)


def licensed_route(config: dict | None = None, *, channel: str = "llm") -> str:
    """What to do when licensed content is refused: the one sentence every
    gate prints (80's spec 5). It describes the routes, and does not judge
    hosted use. It no longer sends anyone to `classification`, which states
    where a provider is and cannot widen the licensed ceiling: the loop
    where `self-hosted` was refused and only a false `local` passed."""
    if channel != "llm":
        return (
            f"  Licensed text reaches the {channel} endpoint only when it is on this machine. "
            f"The data-agreement opt-in (`llm.licensed_agreement`) covers `llm:` only in "
            f"this version; it does not cover `{channel}:`."
        )
    agreement = licensed_agreement(config)
    named = ""
    configured = (
        classify_provider((config or {}).get("llm") or {}) if agreement is not None else None
    )
    if configured is not None and not _agreement_allows(
        configured, ceilings(config)[LICENSED], agreement
    ):
        # Only when the agreement does NOT cover the provider. When it does,
        # the refusal had another reason (an endpoint override), which the
        # Decision's note names; "X does not cover X" would send the reader
        # looking for a typo that isn't there (80 on #66).
        target = (
            _endpoint_party(configured.endpoint)
            if _needs_endpoint(configured)
            else configured.vendor or configured.name
        )
        named = (
            f" Your config declares an agreement for {agreement.provider!r}"
            + (f" at {agreement.party()!r}" if agreement.party() != agreement.provider else "")
            + (f" model {agreement.model!r}" if agreement.model else "")
            + f", which does not cover this provider ({target or 'unresolved'})."
        )
    return (
        "  By default, licensed content reaches only a model on this machine (Ollama at "
        "http://localhost:11434/v1, say). If your organisation has a data agreement that "
        "covers sending it to a hosted provider, declare that provider under "
        f"`llm.licensed_agreement` ({_AGREEMENT_SHAPE}) in "
        "config.yaml; every call made under it is recorded in the model-call ledger." + named
    )


class BoundaryViolation(Exception):
    """Raised when a call would send content past its ceiling.

    An exception rather than a warning: the whole difference between this
    module and the paragraph it replaces is that this one stops.
    """


@dataclass(frozen=True)
class ProviderClassification:
    """Where a configured provider sends its bytes, and how that was decided."""

    klass: str
    reason: str
    #: True when config said so outright rather than this module inferring
    #: it from a URL. A declaration is the operator's statement about their
    #: own network and outranks any guess made here, except that `local` is
    #: checked: it means this machine, and a declaration the endpoint shows
    #: to be false is refused (80's ruling on #11).
    declared: bool = False
    #: The provider as the config names it (`llm.provider`), and its model:
    #: what a licensed-content agreement is matched against (#11). Empty for
    #: the embed and rerank channels, which the agreement does not cover.
    name: str = ""
    model: str = ""
    #: The endpoint config names (`base_url`/`api_base`), and, for litellm, the
    #: vendor its model names (the part before the first `/`, litellm's own
    #: convention): who a protocol provider's content goes to (80's 1432).
    endpoint: str = ""
    vendor: str = ""
    #: A cascade's halves, each classified, so an agreement can be checked
    #: against every model that may answer, not just the most exposed one.
    parts: tuple[ProviderClassification, ...] = ()

    def __str__(self) -> str:
        how = "declared" if self.declared else "inferred"
        return f"{self.klass} ({how}: {self.reason})"


@dataclass(frozen=True)
class ContentClassification:
    """What a path holds, and what said so."""

    klass: str
    reason: str
    #: The framework the path was found in, when it was found in one. Named
    #: so a refusal can say *which* catalog made the content licensed
    #: instead of leaving the operator to guess.
    framework_id: str | None = None

    def __str__(self) -> str:
        return f"{self.klass} ({self.reason})"


@dataclass(frozen=True)
class Decision:
    """The answer to "may this content go to this provider", with its reasons."""

    allowed: bool
    content: ContentClassification
    provider: ProviderClassification
    ceiling: str
    #: The organisation's declared agreement, when it is what allowed a
    #: pairing over the ceiling (#11). None whenever the ceiling alone
    #: decided, so a caller can always say on what basis content left.
    agreement: LicensedAgreement | None = None
    #: Why the agreement did not settle it, when it would have covered the
    #: provider: set when an endpoint override is in the environment.
    note: str = ""

    def explain(self) -> str:
        verdict = "allowed" if self.allowed else "REFUSED"
        lines = [
            f"{verdict}: {self.content.klass} content -> {self.provider.klass} provider "
            f"(the ceiling for {self.content.klass} is {self.ceiling})",
            f"  content:  {self.content}",
            f"  provider: {self.provider}",
        ]
        if self.agreement is not None:
            lines.append(f"  allowed under: {self.agreement}")
        if self.note:
            lines.append(f"  {self.note}")
        return "\n".join(lines)


def exposure(klass: str) -> int:
    """Rank a provider class. Higher means the bytes travel further."""
    try:
        return PROVIDER_CLASSES.index(klass)
    except ValueError:
        raise ValueError(
            f"Unknown provider class {klass!r}. Known: {', '.join(PROVIDER_CLASSES)}."
        ) from None


# ---- classifying a provider ----------------------------------------------


def _host_class(url: str | None) -> tuple[str, str]:
    """Classify an endpoint by its host. Returns (class, reason).

    A hostname is not proof of anything — a loopback address can be
    port-forwarded, and an internal DNS name can resolve to a CDN — so this
    is inference and is labelled as such. `llm.classification` exists for
    the operator who knows better, which is every operator with a
    non-obvious network.
    """
    if not url:
        return THIRD_PARTY, "no endpoint configured, so where it goes is unknown"

    host = urlparse(url).hostname
    if not host:
        return THIRD_PARTY, f"could not read a host out of {url!r}"

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A name, not an address. Only the names that cannot mean anything
        # else are treated as local; a name this module cannot resolve
        # safely stays at the closed end.
        lowered = host.lower()
        if lowered == "localhost" or lowered.endswith(".localhost"):
            return LOCAL, f"{host} is this machine"
        if lowered.endswith(".local") or lowered.endswith(".internal"):
            return SELF_HOSTED, f"{host} is an internal name"
        return THIRD_PARTY, f"{host} is a public name"

    if address.is_loopback:
        return LOCAL, f"{host} is a loopback address"
    if address.is_private or address.is_link_local:
        return SELF_HOSTED, f"{host} is on a private network"
    return THIRD_PARTY, f"{host} is a routable address"


def is_loopback(url: str | None) -> bool:
    """Whether `url` names this machine, by the same reading as `local`.

    The clients use it to reach a loopback model directly, never through a
    proxy the environment names: every client PolicyForge builds otherwise
    sent a loopback call through HTTP_PROXY, the text in cleartext (#69,
    measured through each real client). What `local` promises is that
    nothing leaves the host, and a proxy is off the host."""
    return _host_class(url)[0] == LOCAL


#: Providers that are somebody else's API by name, whatever else config says.
HOSTED_APIS = frozenset({"anthropic", "bedrock", "vertex", "gemini"})


def _checked_declaration(
    declared: object, *, key: str, inferred: ProviderClassification, endpoint: str | None
) -> str:
    """A declared class, refused when it is `local` and provably is not.

    `local` means THIS MACHINE (a loopback endpoint). A declaration of it on
    an endpoint this module can show is not loopback records a false fact:
    the relabelling the user rejected as option (i) on #11 (80's ruling
    1415). Refused when the provider is a named hosted API, or when an
    endpoint IS configured and its host is not loopback (`localhost`,
    `*.localhost`, 127.0.0.0/8, ::1; a private address is `self-hosted`),
    and when a `litellm` model with no `api_base` names a hosted vendor
    (1d on #66): litellm sends it to that vendor. With no endpoint and no
    such name, nothing proves the claim false, so it is not refused here:
    an `openai-compat` provider with no `base_url` is refused by the factory,
    and a litellm `ollama/...` model does run on this machine. Only `local` is
    policed. A `self-hosted` declaration against a public-looking name is
    left alone, because a hostname cannot disprove split-horizon DNS."""
    value = str(declared).strip().lower()
    if value not in PROVIDER_CLASSES:
        raise ValueError(
            f"{key}.classification is {declared!r}, which is not a provider class. "
            f"Use one of: {', '.join(PROVIDER_CLASSES)}."
        )
    provably_not_local = (
        inferred.name in HOSTED_APIS
        or (endpoint is not None and inferred.klass != LOCAL)
        # litellm with no api_base sends to the vendor its model names (1d on
        # #66): proof by name, unless that vendor defaults to this machine.
        or (
            inferred.name == "litellm"
            and endpoint is None
            and inferred.vendor not in LITELLM_LOCAL_VENDORS
        )
    )
    if (
        value == LOCAL
        and not provably_not_local
        and inferred.name == "litellm"
        and endpoint is None
        # ollama's default is http://localhost, so no proxy name is exempt.
        and (overrides := endpoint_overrides(destination="http"))
    ):
        # The vendor defaults to this machine, but litellm reads the
        # environment first (OLLAMA_API_BASE; 1d's 1441), so while any
        # variable could move it the claim cannot be shown. Blanket, by the
        # same rule as the agreement's: a loopback value is refused too.
        raise ValueError(
            f"{key}.classification is 'local', and {inferred.model!r} runs on this machine "
            f"only while nothing in the environment moves it, but "
            f"{', '.join(overrides)} {'is' if len(overrides) == 1 else 'are'} set. Unset "
            f"it for this run, or give {key}.api_base a loopback URL, which "
            "OLLAMA_API_BASE cannot move"
            + (
                "; a proxy variable still moves a loopback api_base unless NO_PROXY "
                "names its host (NO_PROXY=localhost,127.0.0.1)."
                if any(name.upper() in _PROXY_NAMES for name in overrides)
                else "."
            )
        )
    if value == LOCAL and provably_not_local:
        raise ValueError(
            f"{key}.classification is 'local', but {inferred.reason}, so it is not this "
            f"machine: it is {inferred.klass}. `local` means a model on this machine. "
            "Declare the class the provider really is (or leave it inferred). If your "
            "organisation has a data agreement that covers sending licensed content "
            "to this provider, declare that under `llm.licensed_agreement` instead."
        )
    return value


def classify_provider(llm_config: dict) -> ProviderClassification:
    """Where the provider described by an `llm:` block sends its bytes.

    Takes the config block rather than a built provider, so this can be
    answered before any client is constructed, any key is read, or any
    optional dependency is imported. Refusing a run should not require the
    credentials for the call being refused.

    The result carries the provider's NAME and MODEL as config states them:
    what a licensed-content agreement is matched against (#11).
    """
    inferred = _infer_provider(llm_config)
    declared = llm_config.get("classification")
    if declared is None:
        return inferred
    klass = _checked_declaration(
        declared, key="llm", inferred=inferred, endpoint=_endpoint_of(llm_config)
    )
    return ProviderClassification(
        klass,
        "llm.classification in config",
        declared=True,
        name=inferred.name,
        model=inferred.model,
        endpoint=inferred.endpoint,
        vendor=inferred.vendor,
        parts=inferred.parts,
    )


def _endpoint_of(llm_config: dict) -> str | None:
    """The URL an `llm:` block names, if it names one."""
    for key in ("base_url", "api_base"):
        if llm_config.get(key):
            return str(llm_config[key])
    return None


def _infer_provider(llm_config: dict) -> ProviderClassification:
    """The class inferred from the block, ignoring any declaration."""
    name = str(llm_config.get("provider", "")).strip().lower()
    model = str(llm_config.get("model", "") or "").strip()
    endpoint = str(_endpoint_of(llm_config) or "")
    # litellm's convention: the vendor is the part of the model before the
    # first `/`. A bare model names none, and so resolves to no vendor here.
    vendor = model.split("/", 1)[0].casefold() if name == "litellm" and "/" in model else ""

    def result(klass: str, reason: str, **extra) -> ProviderClassification:
        return ProviderClassification(
            klass, reason, name=name, model=model, endpoint=endpoint, vendor=vendor, **extra
        )

    if name == "cascade":
        # Content reaches whichever half answers, and which half that is
        # depends on a runtime failure nobody can predict at configuration
        # time. So a cascade is as exposed as its most exposed half — the
        # only reading that cannot be wrong in the dangerous direction.
        halves = tuple(
            classify_provider(llm_config.get(key) or {}) for key in ("primary", "escalate_to")
        )
        worst = max(halves, key=lambda c: exposure(c.klass))
        both = " and ".join(c.klass for c in halves)
        return result(
            worst.klass,
            f"a cascade is as exposed as its most exposed half ({both})",
            parts=halves,
        )

    if name in HOSTED_APIS:
        # Named rather than left to the unrecognised-provider fallback
        # below. That fallback is also third-party, so the outcome would be
        # the same today — but a provider whose classification depends on
        # falling off the end of this function is one rename away from
        # being classified by accident, and this table is what the licensed
        # ceiling is enforced from.
        return result(THIRD_PARTY, f"{name} is a hosted API")

    if name in ("openai-compat", "local"):
        # The `local` alias names the provider *protocol*, not the network.
        # Someone pointing it at a hosted vLLM endpoint would otherwise read
        # the alias as a guarantee, which is exactly backwards — so the
        # alias is ignored here and the URL decides.
        klass, reason = _host_class(llm_config.get("base_url"))
        return result(klass, reason)

    if name == "litellm":
        api_base = llm_config.get("api_base")
        if api_base:
            klass, reason = _host_class(api_base)
            return result(klass, reason)
        return result(
            THIRD_PARTY,
            f"litellm resolves {llm_config.get('model', '?')!r} to a hosted vendor",
        )

    return result(
        THIRD_PARTY,
        f"provider {name or '(unset)'} is unrecognised, so it is treated as exposed",
    )


def classify_endpoint(block: dict, *, default_url: str, key: str) -> ProviderClassification:
    """Where an `embed:` or `rerank:` block sends its bytes.

    The rule `classify_provider` applies to a URL-addressed `llm:` block,
    for the two channels addressed by URL alone: a declared `classification`
    wins, otherwise the host decides, and a declared `local` the host shows
    is not loopback is refused (80's ruling 1415 on #11). `default_url` is
    the one the factory would use, so an unset `base_url` is classified as
    what it actually is (this machine) rather than as unknown and therefore
    exposed. These channels carry no provider name: the licensed-content
    agreement covers `llm:` only in this version.
    """
    url = block.get("base_url") or default_url
    klass, reason = _host_class(url)
    inferred = ProviderClassification(klass, reason)
    declared = block.get("classification")
    if declared is None:
        return inferred
    value = _checked_declaration(declared, key=key, inferred=inferred, endpoint=url)
    return ProviderClassification(value, f"{key}.classification in config", declared=True)


# ---- classifying content --------------------------------------------------


def _under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def classify_path(
    path: Path, config: dict | None = None, *, assume_written: bool = False
) -> ContentClassification:
    """What kind of content lives at `path`.

    Three questions, in order of how much they know. Is it inside a
    discovered framework, which carries a manifest saying what it is? Is it
    under a search path this project keeps out of git, which is where it
    tells people to put licensed exports? Otherwise it is the organization's
    own material, which is the ordinary case and the one the tool exists to
    send to a model.

    `assume_written` answers for a file that does not exist yet, as the
    answer will be once it does (#459): a licensed ETL asks it about its
    `--out` before writing, and refuses a destination this would call
    anything but licensed.

    **And the file itself may say it is licensed** (#459 (b), 80's ruling):
    the licensed ETLs write their catalog as an envelope carrying
    `"licence": "licensed"`. Where the path and the file disagree, **the
    stricter class wins**, so a licensed catalog saved anywhere, or moved
    after it was written, is held to the licensed ceiling.
    """
    by_path = _classify_by_path(path, config or {}, assume_written=assume_written)
    if by_path.klass == LICENSED or assume_written:
        return by_path
    marked = _marked_licence(path)
    # Fail closed (#320): any envelope value but an explicit public one, in
    # any case or spelling, is licensed; `is_licensed` is the one rule.
    from policyforge.ingest.schema import is_licensed

    if is_licensed(marked):
        return ContentClassification(
            LICENSED,
            f"{path.name} is marked licensed by the ETL that wrote it (#459); the "
            f"path alone would make it {by_path.klass}",
            by_path.framework_id,
        )
    return by_path


def licensed_etl_frameworks() -> frozenset[str]:
    """The framework names the licensed BYOC ETLs write into their rows: the
    shape the unmarked-file warning keys on.

    **A fixed public list** (`plugins.LICENSED_FRAMEWORK_NAMES`; 80's ruling
    on #32, comment 562), not read from the parsers, which move to a private
    plugin: the warning must work for a user with no plugin installed, the
    user most likely to have a hand-made licensed file lying around."""
    from policyforge.plugins import LICENSED_FRAMEWORK_NAMES

    return frozenset(LICENSED_FRAMEWORK_NAMES.values())


#: 80's wording on #459: it describes and does not dictate.
UNMARKED_WARNING = (
    "this catalog looks like a licensed BYOC export saved outside local_content/, so "
    "your boundary settings haven't been applied to it; move it (re-run the ETL into "
    "local_content/) so they are."
)


def unmarked_licensed_catalog(path: Path, config: dict | None) -> bool:
    """A catalog written before #459 (b) by a licensed ETL, saved where the
    boundary does not read it as licensed: no marker, and its rows declare a
    framework only a licensed ETL writes.

    **A heuristic, so it only ever warns.** The shape is what identifies it;
    a file that happens to name HITRUST and sits outside `local_content/`
    is flagged, and one whose rows were edited to name something else is not.

    `config` is required, not defaulted: it is what makes a location
    licensed, and a caller that left it out told users whose configuration
    held the file as licensed that their settings hadn't been applied (9b on
    #10; required here on 1d's note, so a new caller can't forget it).
    """
    from policyforge.ingest.schema import read_catalog

    if path.suffix.lower() != ".json" or not path.is_file():
        return False
    if classify_path(path, config).klass == LICENSED:
        return False
    try:
        rows, _ = read_catalog(path)
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    names = {str(r.get("framework") or "") for r in rows if isinstance(r, dict)}
    return bool(names & licensed_etl_frameworks())


def _marked_licence(path: Path) -> str | None:
    """The licence a catalog file declares in its envelope, or None.

    Only a `.json` file that parses as a catalog is read; anything else (a
    markdown document, a directory, an unreadable file) declares nothing,
    and keeps the class its path gives it.
    """
    from policyforge.ingest.schema import read_catalog

    if path.suffix.lower() != ".json" or not path.is_file():
        return None
    try:
        _, licence = read_catalog(path)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return licence


def _classify_by_path(path: Path, config: dict, *, assume_written: bool) -> ContentClassification:
    resolved = path.resolve()
    pending = path if assume_written else None

    for framework in registry.discover(config, assume_written=pending):
        if _under(resolved, framework.path):
            if framework.redistributable:
                return ContentClassification(
                    PUBLIC_DOMAIN,
                    f"{framework.id} declares itself public domain",
                    framework.id,
                )
            how = "declares itself licensed" if framework.declared else "has no manifest"
            return ContentClassification(LICENSED, f"{framework.id} {how}", framework.id)

    for root in registry.search_paths(config):
        # `local_content/` is gitignored precisely so licensed exports
        # cannot be committed. A file put there was put there for that
        # reason, whether or not anyone got around to writing a manifest
        # beside it.
        if root.name == "local_content" and _under(resolved, root):
            return ContentClassification(LICENSED, f"under {root}, which is kept out of git")

    return ContentClassification(ORG_INTERNAL, "not part of any declared framework catalog")


# ---- the ceiling ----------------------------------------------------------


def ceilings(config: dict | None = None) -> dict[str, str]:
    """The ceiling per content class, configured values over defaults.

    Config tightens, never loosens::

        llm:
          boundary:
            organization-internal: self-hosted   # our drafts stay inside

    Raising a ceiling here is refused. The point of a declared boundary is
    that relaxing it takes more than a line of YAML written in a hurry. The
    one route by which licensed content reaches a provider outside this
    machine is `llm.licensed_agreement`: the organisation names the provider
    its data agreement covers, the provider keeps its true class, and every
    call under it is recorded (#11). Relabelling a hosted model `local` is not
    a route: that states a false fact, and a declared `local` the endpoint
    disproves is refused.
    """
    configured = ((config or {}).get("llm") or {}).get("boundary") or {}
    if not isinstance(configured, dict):
        raise ValueError(
            "llm.boundary should be a mapping of content class to the most exposed "
            f"provider class it may reach, not {type(configured).__name__}."
        )

    resolved = dict(DEFAULT_CEILINGS)
    for content_class, ceiling in configured.items():
        key = str(content_class).strip().lower()
        if key not in CONTENT_CLASSES:
            raise ValueError(
                f"llm.boundary names {content_class!r}, which is not a content class. "
                f"Use one of: {', '.join(CONTENT_CLASSES)}."
            )
        value = str(ceiling).strip().lower()
        if value not in PROVIDER_CLASSES:
            raise ValueError(
                f"llm.boundary.{key} is {ceiling!r}, which is not a provider class. "
                f"Use one of: {', '.join(PROVIDER_CLASSES)}."
            )
        if exposure(value) > exposure(DEFAULT_CEILINGS[key]):
            raise ValueError(
                f"llm.boundary.{key} is {value!r}, which is more permissive than the "
                f"default of {DEFAULT_CEILINGS[key]!r}. This setting tightens the "
                "boundary; it cannot loosen it."
                + (
                    " To send licensed content to a hosted provider your organisation has a "
                    "data agreement for, declare that provider under "
                    "`llm.licensed_agreement` instead."
                    if key == LICENSED
                    else " If a provider really is inside your boundary, declare where it "
                    "is with `llm.classification`."
                )
            )
        resolved[key] = value
    return resolved


def check(
    content: ContentClassification,
    provider: ProviderClassification,
    config: dict | None = None,
) -> Decision:
    """Decide one pairing. Returns the verdict; raises nothing.

    The ONE place the licensed ceiling can be raised (#11): a licensed pairing
    over the ceiling is allowed only when `llm.licensed_agreement` covers the
    provider, and the Decision then names the agreement."""
    ceiling = ceilings(config)[content.klass]
    within = exposure(provider.klass) <= exposure(ceiling)
    proxied = proxied_loopback(provider) if content.klass == LICENSED and within else []
    if proxied:
        # `local` is true of the endpoint and false of the route: litellm
        # would carry the text through a proxy, off this machine (#69; 80's
        # ruling 1591). Names the URLs, which are config, and the variables,
        # never a proxy's value.
        names = sorted(
            n for n in ("HTTP_PROXY", "ALL_PROXY") if os.environ.get(n) or os.environ.get(n.lower())
        )
        via = ", ".join(names) if names else "the system proxy settings"
        return Decision(
            allowed=False,
            content=content,
            provider=provider,
            ceiling=ceiling,
            note=(
                f"litellm would send this through the proxy that {via} names, off this "
                f"machine and in cleartext, to reach {', '.join(proxied)}. Add the host to "
                "NO_PROXY (NO_PROXY=localhost,127.0.0.1), or use `provider: openai-compat` "
                "with base_url at the same server's /v1, which reaches a loopback model "
                "directly."
            ),
        )
    if within or content.klass != LICENSED:
        return Decision(allowed=within, content=content, provider=provider, ceiling=ceiling)
    agreement = licensed_agreement(config)
    covered = agreement is not None and _agreement_allows(provider, ceiling, agreement)
    overrides = endpoint_overrides(destination=_destination(provider)) if covered else []
    if overrides:
        # The agreement names a party, and these can send the call somewhere
        # else without the config knowing, so the boundary cannot say where
        # the content would go. Refused, naming them (b5 on #66; 80's 1432).
        return Decision(
            allowed=False,
            content=content,
            provider=provider,
            ceiling=ceiling,
            note=(
                "The declared agreement would cover this provider, but "
                f"{', '.join(overrides)} {'is' if len(overrides) == 1 else 'are'} set, and "
                "an SDK or a proxy can send the call somewhere the agreement does not "
                "name. Unset it for this run."
            ),
        )
    return Decision(
        allowed=covered,
        content=content,
        provider=provider,
        ceiling=ceiling,
        agreement=agreement if covered else None,
    )


#: `enforce`'s advice for a refusal that is NOT about licensed content,
#: unchanged by #11.
ENFORCE_ADVICE = (
    "  Point `llm:` at a local model (Ollama at http://localhost:11434/v1, say) "
    "for this run, or — if this provider really is inside your boundary — "
    "declare that with `llm.classification` in config.yaml."
)


def refusal_advice(
    decision: Decision, config: dict | None, *, otherwise: str, channel: str = "llm"
) -> str:
    """The route after a refusal. Licensed content gets the one licensed
    route (#11). Anything else gets `otherwise`, the calling gate's own
    advice, unchanged, because the ruling changed only the licensed text."""
    if decision.content.klass == LICENSED:
        if exposure(decision.provider.klass) <= exposure(decision.ceiling):
            # The provider is within the ceiling and the ROUTE to it is not
            # (#69): the Decision's note says how to fix the route, and an
            # agreement, which the licensed route offers, would not.
            return ""
        return licensed_route(config, channel=channel)
    return otherwise


def enforce(path: Path, config: dict) -> Decision:
    """Refuse to send `path` to the configured provider, or say why it is fine.

    The one call a command that reads a file and then calls a model should
    make, before it reads the file.
    """
    decision = check(
        classify_path(path, config),
        classify_provider(config.get("llm") or {}),
        config,
    )
    if not decision.allowed:
        raise BoundaryViolation(
            f"Refusing to send {path} to the configured provider.\n"
            f"{decision.explain()}\n" + refusal_advice(decision, config, otherwise=ENFORCE_ADVICE)
        )
    return decision


def agreement_lines(config: dict | None, provider: ProviderClassification) -> list[str]:
    """What a declared data agreement adds to the matrix, for the two
    boundary reports (#11). Empty when none is declared, so a report without
    one reads exactly as before. Says whether it covers the CONFIGURED
    provider, because naming the wrong one is the likeliest mistake."""
    agreement = licensed_agreement(config)
    if agreement is None:
        return []
    ceiling = ceilings(config)[LICENSED]
    if exposure(provider.klass) <= exposure(ceiling):
        applies = "not needed for the configured provider, which is already within the ceiling"
    elif _agreement_allows(provider, ceiling, agreement):
        applies = (
            "covers the configured provider; every licensed call made under it is "
            "recorded in the ledger"
        )
    else:
        applies = "does NOT cover the configured provider, so licensed content is still refused"
    return [
        "Licensed content under a declared data agreement:",
        f"  {agreement}",
        f"  {applies}.",
    ]


def matrix(config: dict | None = None) -> str:
    """The ceilings as a table, for `policyforge boundary`."""
    resolved = ceilings(config)
    width = max(len(name) for name in CONTENT_CLASSES)
    header = "  ".join(klass.ljust(11) for klass in PROVIDER_CLASSES)
    lines = [f"{'content'.ljust(width)}  {header}"]
    for content_class in CONTENT_CLASSES:
        allowed = exposure(resolved[content_class])
        cells = "  ".join(
            ("yes" if exposure(klass) <= allowed else "no").ljust(11) for klass in PROVIDER_CLASSES
        )
        lines.append(f"{content_class.ljust(width)}  {cells}")
    return "\n".join(lines)
