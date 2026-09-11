"""Parser for the open-source nginx stub_status page."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ACTIVE_RE = re.compile(r"Active connections:\s*(\d+)")
_COUNTERS_RE = re.compile(r"server accepts handled requests\s*\n\s*(\d+)\s+(\d+)\s+(\d+)")
_STATES_RE = re.compile(r"Reading:\s*(\d+)\s+Writing:\s*(\d+)\s+Waiting:\s*(\d+)")


@dataclass(frozen=True)
class StubStatus:
    """Live counters parsed from a stub_status body."""

    active_connections: int
    accepts: int
    handled: int
    requests: int
    reading: int
    writing: int
    waiting: int

    @property
    def dropped(self) -> int:
        """Connections accepted but never handled (resource limits)."""
        return max(self.accepts - self.handled, 0)


def parse_stub_status(text: str) -> StubStatus | None:
    """Parse a stub_status body; None when any section fails to match."""
    active = _ACTIVE_RE.search(text or "")
    counters = _COUNTERS_RE.search(text or "")
    states = _STATES_RE.search(text or "")
    if active is None or counters is None or states is None:
        return None
    return StubStatus(
        active_connections=int(active.group(1)),
        accepts=int(counters.group(1)),
        handled=int(counters.group(2)),
        requests=int(counters.group(3)),
        reading=int(states.group(1)),
        writing=int(states.group(2)),
        waiting=int(states.group(3)),
    )
