"""The network guard, tests/netguard/sitecustomize.py (design/internal/g1-stage2.md P7a): G1-13's and G3's one test

Each case is a subprocess with the guard on its PYTHONPATH. The guard refuses before the system tries, so no case
depends on the machine's interfaces.
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.criterion("G1-13")

NETGUARD = Path(__file__).resolve().parent / "netguard"

# The connection, run as `python -c CHILD <family> <address as JSON> <method>`. It prints the errno name the
# connection fails with, or what it returns.
CHILD = """
import errno, json, socket, sys
family, address, method = getattr(socket, sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
sock = socket.socket(family, socket.SOCK_STREAM)
sock.settimeout(5)
try:
    result = getattr(sock, method)(tuple(address) if isinstance(address, list) else address)
except OSError as e:
    print(errno.errorcode.get(e.errno, repr(e)))
else:
    print(errno.errorcode.get(result, result) if method == "connect_ex" else "connected")
"""


def _ipv6():
    if not socket.has_ipv6:
        return False
    try:
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM).close()
    except OSError:
        return False
    return True


needs_ipv6 = pytest.mark.skipif(not _ipv6(), reason="an IPv6 socket can't be made on this machine")


@pytest.fixture
def closed_port():
    """A port on 127.0.0.1 that nothing holds, so a connection to it is refused by the system. A port bound but not
    listening isn't enough: macOS drops the connection rather than refusing it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return port


def connect(tmp_path, family, address, *, method="connect", allow=None, guard=True):
    """Run the connection under the guard; give its outcome and the log's lines (None if there is no log)."""
    log = tmp_path / "net.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith("LMT_NETGUARD_")}
    env["PYTHONPATH"] = str(NETGUARD)
    if guard:
        env["LMT_NETGUARD_LOG"] = str(log)
    if allow is not None:
        env["LMT_NETGUARD_ALLOW"] = allow
    proc = subprocess.run([sys.executable, "-c", CHILD, family, json.dumps(address), method],
                          env=env, capture_output=True, text=True, check=False, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip(), (log.read_text(encoding="utf-8").splitlines() if log.exists() else None)


def assert_refused(outcome, lines, family, address):
    assert outcome == "ENETUNREACH"
    assert lines == [f"{family} {tuple(address) if isinstance(address, list) else address}"]


@pytest.mark.parametrize("address", [["127.0.0.1", 9], ["192.0.2.1", 80]])
def test_default_allows_nothing(tmp_path, address):
    outcome, lines = connect(tmp_path, "AF_INET", address)
    assert_refused(outcome, lines, "AF_INET", address)


@needs_ipv6
def test_default_refuses_ipv6_loopback(tmp_path):
    outcome, lines = connect(tmp_path, "AF_INET6", ["::1", 9])
    assert_refused(outcome, lines, "AF_INET6", ["::1", 9])


def test_allowed_address_reaches_the_system(tmp_path, closed_port):
    outcome, lines = connect(tmp_path, "AF_INET", ["127.0.0.1", closed_port], allow="127.0.0.1")
    assert outcome == "ECONNREFUSED"
    assert not lines


@pytest.mark.parametrize("address", [["127.0.0.2", 9], ["192.0.2.1", 80]])
def test_allowance_refuses_other_addresses(tmp_path, address):
    outcome, lines = connect(tmp_path, "AF_INET", address, allow="127.0.0.1")
    assert_refused(outcome, lines, "AF_INET", address)


@needs_ipv6
def test_allowance_refuses_ipv6_loopback(tmp_path):
    outcome, lines = connect(tmp_path, "AF_INET6", ["::1", 9], allow="127.0.0.1")
    assert_refused(outcome, lines, "AF_INET6", ["::1", 9])


def test_connect_ex_is_refused(tmp_path):
    outcome, lines = connect(tmp_path, "AF_INET", ["192.0.2.1", 80], method="connect_ex")
    assert_refused(outcome, lines, "AF_INET", ["192.0.2.1", 80])


def test_unix_socket_is_refused(tmp_path):
    path = str(tmp_path / "server.sock")
    outcome, lines = connect(tmp_path, "AF_UNIX", path)
    assert_refused(outcome, lines, "AF_UNIX", path)


def test_off_without_log(tmp_path, closed_port):
    outcome, lines = connect(tmp_path, "AF_INET", ["127.0.0.1", closed_port], guard=False)
    assert outcome == "ECONNREFUSED"
    assert lines is None
    assert not (tmp_path / "net.log").exists()
