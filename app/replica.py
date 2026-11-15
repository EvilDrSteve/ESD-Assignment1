"""Which replica (A, B, C, ...) this process is.

Compose scaling gives every copy the same environment, so the name can't come
from an env var. Docker's DNS knows each container's name, though: reverse DNS
of our own IP gives e.g. "assignment-1-app-2.assignment-1_default", and the
trailing number 2 maps to "B".

The PTR query goes straight to the DNS server (Docker's, 127.0.0.11, from
/etc/resolv.conf). socket.gethostbyaddr() would read /etc/hosts first, where
Docker only lists the container id.
"""

import os
import re
import socket
import string

_INDEX_RE = re.compile(r"-(\d+)$")


def replica_from_hostname(name: str) -> str | None:
    """'assignment-1-app-2.assignment-1_default' -> 'B'; None if there is no index."""
    match = _INDEX_RE.search(name.split(".")[0])
    if not match:
        return None
    index = int(match.group(1))
    return string.ascii_uppercase[index - 1] if 1 <= index <= 26 else None


def detect() -> str:
    if os.environ.get("REPLICA"):  # override for tests and local runs
        return os.environ["REPLICA"]
    import dns.exception
    import dns.resolver
    import dns.reversename

    hostname = socket.gethostname()
    try:
        own_ip = socket.gethostbyname(hostname)
        ptr = dns.resolver.resolve(dns.reversename.from_address(own_ip), "PTR", lifetime=2)
        name = replica_from_hostname(str(ptr[0]).rstrip("."))
        if name:
            return name
    except (OSError, dns.exception.DNSException):
        pass
    return hostname  # not under Compose: fall back to the container id


REPLICA = detect()
