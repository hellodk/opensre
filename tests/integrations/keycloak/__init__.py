"""Keycloak integration tests: fixture loader shared by the test modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Read a captured Keycloak payload (JSON parsed, ``.txt`` raw)."""
    path = _FIXTURES_DIR / name
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".txt":
        return text
    return json.loads(text)
