"""Reserve private lane ports for synthetic lifecycle receipts."""

import socket
from collections.abc import Iterator
from contextlib import ExitStack

import pytest

type LanePorts = dict[str, tuple[int, int]]


@pytest.fixture
def reserved_e2e_ports() -> Iterator[LanePorts]:
    """Hold bound, non-listening sockets so real absence probes stay isolated."""
    with ExitStack() as stack:
        sockets = tuple(
            stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) for _ in range(4)
        )
        for probe in sockets:
            probe.bind(("127.0.0.1", 0))
        ports = tuple(probe.getsockname()[1] for probe in sockets)
        yield {"scripted": (ports[0], ports[1]), "live": (ports[2], ports[3])}
