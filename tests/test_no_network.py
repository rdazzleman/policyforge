"""The suite may not reach past this machine (#432).

`conftest.no_network` refuses DNS and connections beyond loopback. These
pin what it refuses AND what it must allow: a guard specified only by what
it refuses is satisfied by refusing everything.
"""

from __future__ import annotations

import socket

import pytest
from conftest import NetworkUsedInTest


@pytest.mark.parametrize("host", ["www.ecfr.gov", "169.254.169.254", "8.8.8.8", "example.com"])
def test_a_lookup_past_this_machine_is_refused_naming_the_host(host):
    with pytest.raises(NetworkUsedInTest, match=host.replace(".", r"\.")):
        socket.getaddrinfo(host, 443)


def test_a_connection_past_this_machine_is_refused_before_it_is_made():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkUsedInTest):
            sock.connect(("93.184.216.34", 80))
        with pytest.raises(NetworkUsedInTest):
            sock.connect_ex(("93.184.216.34", 80))
    finally:
        sock.close()


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_loopback_is_allowed(host):
    """Tests name `localhost:11434` with the transport stubbed. If a stub
    misses, the test should fail on its own terms, not on this guard."""
    try:
        socket.getaddrinfo(host, 80)
    except NetworkUsedInTest:  # pragma: no cover - the failure being guarded
        pytest.fail(f"loopback {host!r} was refused")
    except OSError:
        pass  # e.g. no IPv6 on this machine: a real answer, not the guard


def test_the_refusal_is_not_an_oserror():
    """An HTTP client reads an OSError as a connection failure to retry or to
    report as the server's; this has to surface as what it is."""
    assert not issubclass(NetworkUsedInTest, OSError)


def test_boto3_is_told_not_to_ask_the_instance_metadata_service():
    import os

    assert os.environ.get("AWS_EC2_METADATA_DISABLED") == "true"


@pytest.mark.parametrize("resolver", ["gethostbyname", "gethostbyname_ex", "gethostbyaddr"])
def test_every_resolver_is_refused_not_only_getaddrinfo(resolver):
    """policyforge-ba on #434: these made real resolver queries."""
    target = "8.8.8.8" if resolver == "gethostbyaddr" else "www.ecfr.gov"
    with pytest.raises(NetworkUsedInTest):
        getattr(socket, resolver)(target)


def test_a_datagram_past_this_machine_is_refused():
    """policyforge-ba on #434: a UDP sendto went out unrefused."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(NetworkUsedInTest):
            sock.sendto(b"x", ("8.8.8.8", 53))
        with pytest.raises(NetworkUsedInTest):
            sock.sendto(b"x", 0, ("8.8.8.8", 53))
        if hasattr(sock, "sendmsg"):
            with pytest.raises(NetworkUsedInTest):
                sock.sendmsg([b"x"], [], 0, ("8.8.8.8", 53))
    finally:
        sock.close()


def test_a_datagram_to_loopback_is_allowed():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(b"x", ("127.0.0.1", 9))  # discard port; nothing need listen
    except NetworkUsedInTest:  # pragma: no cover - the failure being guarded
        pytest.fail("loopback datagram was refused")
    except OSError:
        pass
    finally:
        sock.close()


@pytest.mark.parametrize("resolver", ["getaddrinfo", "gethostbyname", "gethostbyname_ex"])
def test_a_host_given_as_bytes_is_refused_too(resolver):
    """policyforge-b5 on #434: every non-str host was passed as loopback, so
    `getaddrinfo(b"www.ecfr.gov")` made a real lookup."""
    call = getattr(socket, resolver)
    with pytest.raises(NetworkUsedInTest, match="ecfr"):
        call(b"www.ecfr.gov", 443) if resolver == "getaddrinfo" else call(b"www.ecfr.gov")


def test_bytes_loopback_and_the_local_host_are_allowed():
    for host in (b"localhost", b"127.0.0.1", None):
        try:
            socket.getaddrinfo(host, 80)
        except NetworkUsedInTest:  # pragma: no cover - the failure being guarded
            pytest.fail(f"{host!r} was refused")
        except OSError:
            pass


def test_a_connection_to_a_bytes_host_is_refused():
    """policyforge-b5 on #434: `connect((b"host", port))` resolves the name
    itself, below Python's resolver functions, so the guard's `connect` must
    check a bytes host, not only a str one (#436). What refuses it is
    `_is_loopback` treating a host it cannot read as NOT loopback; the bytes
    decode is what lets `b"localhost"` through, and
    `test_bytes_loopback_and_the_local_host_are_allowed` pins that half
    (policyforge-ba on #437, who measured the first wording wrong)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkUsedInTest, match="ecfr"):
            sock.connect((b"www.ecfr.gov", 443))
    finally:
        sock.close()
