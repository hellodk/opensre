"""Aerospike ``asinfo`` CLI subprocess wrapper — read-only info-protocol client.

``asinfo`` (shipped in the ``aerospike-tools`` package alongside every
Aerospike install) is Aerospike's own CLI client for the plaintext info
protocol. This module never talks to an Aerospike node directly — it shells
out to ``asinfo``, which owns proto framing, the bcrypt/fixed-salt auth
handshake, and TLS. See
``docs/superpowers/specs/2026-08-09-aerospike-integration-design.md`` §1/§3
for the full architecture rationale.

Structurally this mirrors ``integrations/helm/client.py`` (binary resolution,
a single subprocess seam, distinct exit-code/timeout error mapping) rather
than ``integrations/redis``, since Aerospike has no Python client library —
this file *is* the transport layer.

Note: this module intentionally does not import ``AerospikeConfig`` from
``integrations.aerospike`` (the package ``__init__.py``) — that would create
an import cycle, since ``__init__.py`` imports :func:`send_info_commands`
from here. :class:`AerospikeConfigLike` is a structural (``Protocol``) type
instead; any object with the listed attributes (in practice,
``AerospikeConfig``) works.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from typing import Protocol

logger = logging.getLogger(__name__)

_DEFAULT_ASINFO_BIN = "asinfo"


class AerospikeConfigLike(Protocol):
    """Structural type for the config fields this module needs."""

    host: str
    port: int
    username: str
    password: str
    timeout_seconds: float


class AsinfoBinaryNotFoundError(RuntimeError):
    """``asinfo`` is not on ``PATH``.

    Distinct from a connection failure: it means the ``aerospike-tools``
    package isn't installed on the host/container, not that the cluster is
    unreachable.
    """


class AsinfoConnectionError(RuntimeError):
    """``asinfo`` ran but exited non-zero.

    Covers both "couldn't connect" and "connected but the command itself
    errored" — ``asinfo``'s exact exit-code/stderr shape for each case has
    not been confirmed against a live binary (design doc §9 item 3), so this
    module does not attempt to distinguish them. Callers get ``returncode``
    and ``stderr`` to make their own best-effort classification.
    """

    def __init__(self, message: str, *, returncode: int, stderr: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class AsinfoTimeoutError(RuntimeError):
    """``asinfo`` did not return within ``config.timeout_seconds``."""


def _resolved_asinfo_path() -> str | None:
    """Resolve the ``asinfo`` binary via a ``PATH`` lookup.

    Mirrors ``HelmClient._resolved_helm_path()``'s ``PATH``-lookup branch.
    Unlike Helm, ``AerospikeConfig`` models no override-path field in v1
    (design doc §2.2 lists no such field), so there is no config-supplied
    override to check first.
    """
    return shutil.which(_DEFAULT_ASINFO_BIN)


def _build_args(config: AerospikeConfigLike, commands: Sequence[str]) -> list[str]:
    """Build the ``asinfo`` argv.

    ``-h <host> -p <port> -v "<cmd1>\\n<cmd2>..."``, plus ``-U <username>
    -P <password>`` when both are set (design doc §3.3). TLS flags are
    deferred — not wired in v1 (design doc §2.1/§9 item 6).
    """
    args = ["-h", config.host, "-p", str(config.port), "-v", "\n".join(commands)]
    if config.username and config.password:
        args.extend(["-U", config.username, "-P", config.password])
    return args


def _parse_stdout(stdout: str, commands: Sequence[str]) -> dict[str, str]:
    """Split ``asinfo`` stdout into ``{command_name: raw_value}``.

    Two shapes (design doc §1.2):

    - Single-command: ``asinfo`` prints the bare value with no ``name\\t``
      prefix, so the entire (trailing-newline-stripped) stdout is the one
      requested command's value.
    - Multi-command: each line is ``name\\tvalue``.
    """
    text = stdout.rstrip("\n")
    if len(commands) == 1:
        return {commands[0]: text}

    result: dict[str, str] = {}
    for line in text.split("\n"):
        if not line:
            continue
        name, sep, value = line.partition("\t")
        if not sep:
            logger.warning("aerospike asinfo: unparseable stdout line (no tab): %r", line)
            continue
        result[name] = value
    return result


def send_info_commands(config: AerospikeConfigLike, commands: Sequence[str]) -> dict[str, str]:
    """Run ``asinfo`` with the given info commands; return ``{command: raw_value}``.

    Caller-facing entrypoint — the only function
    ``integrations/aerospike/__init__.py``'s per-tool data functions call.
    One subprocess per call, no pooling: matches the "fresh connection per
    call" behavior ``integrations/redis`` already commits to.

    Raises:
        AsinfoBinaryNotFoundError: ``asinfo`` is not on ``PATH``.
        AsinfoTimeoutError: ``asinfo`` did not return within
            ``config.timeout_seconds``.
        AsinfoConnectionError: ``asinfo`` ran but exited non-zero.
    """
    binary = _resolved_asinfo_path()
    if binary is None:
        raise AsinfoBinaryNotFoundError(
            f"asinfo binary not found ({_DEFAULT_ASINFO_BIN!r}). Install the "
            "aerospike-tools package so asinfo is on PATH."
        )

    argv = [binary, *_build_args(config, commands)]
    logger.debug("aerospike asinfo subprocess: %s commands", len(commands))
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.timeout_seconds,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise AsinfoTimeoutError(
            f"asinfo command timed out after {config.timeout_seconds}s"
        ) from exc
    except OSError as exc:
        raise AsinfoConnectionError(
            f"asinfo subprocess failed: {exc}", returncode=1, stderr=str(exc)
        ) from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise AsinfoConnectionError(
            f"asinfo exited with code {proc.returncode}: {stderr or 'no stderr output'}",
            returncode=proc.returncode,
            stderr=stderr,
        )

    return _parse_stdout(proc.stdout or "", commands)
