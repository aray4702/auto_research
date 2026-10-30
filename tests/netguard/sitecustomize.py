"""The network guard (design/internal/g1-stage2.md P7a, and g3-stage1.md "The network guard")

Put this directory on PYTHONPATH for a subprocess and set LMT_NETGUARD_LOG to a file: every `socket.connect`
and `connect_ex` to an address not in LMT_NETGUARD_ALLOW (comma-separated, empty by default) appends one line,
`<family> <address>`, to that file and raises OSError(ENETUNREACH) before the system tries. Every family counts,
AF_UNIX included. Without LMT_NETGUARD_LOG it does nothing, so the guarded program runs unchanged.
"""

import errno
import os
import socket


def _install(log_path, allow):
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def check(sock, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host in allow:
            return
        try:
            family = socket.AddressFamily(sock.family).name
        except ValueError:
            family = str(sock.family)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"{family} {address}\n")
        raise OSError(errno.ENETUNREACH, f"{os.strerror(errno.ENETUNREACH)} (netguard: {family} {address})")

    def guarded_connect(self, address):
        check(self, address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        check(self, address)
        return connect_ex(self, address)

    socket.socket.connect, socket.socket.connect_ex = guarded_connect, guarded_connect_ex


if os.environ.get("LMT_NETGUARD_LOG"):
    _install(os.environ["LMT_NETGUARD_LOG"],
             {a.strip() for a in os.environ.get("LMT_NETGUARD_ALLOW", "").split(",") if a.strip()})
