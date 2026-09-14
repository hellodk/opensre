"""Contract tests for the workshop chaos kit (issue #6244).

The kit lives in ``workshop/chaos/`` and must be safe by construction:
every mutating shell script prints its revert command up front, performs
zero cluster side effects under ``DRY_RUN=1``, and refuses a live run
without ``--confirm``.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

CHAOS_DIR = Path(__file__).resolve().parents[2] / "workshop" / "chaos"

SHELL_SCRIPTS = [
    "pod-kill.sh",
    "scale-to-zero.sh",
    "nats-flood.sh",
    "keycloak-login-storm.sh",
    "log-flood.sh",
    "abort-all.sh",
]

STATIC_ARTIFACTS = [
    "cpu-hog-job.yaml",
    "yb-slow-query.sql",
]

EXPECTED_ARTIFACTS = SHELL_SCRIPTS + STATIC_ARTIFACTS

PROD_DEPLOYS = ("store-api-gateway", "store-inventory", "store-orders", "store-payments")


def _stub_bin(tmp_path: Path, calls_log: Path) -> dict[str, str]:
    """Install fake kubectl/curl/hey that record invocations instead of running them."""
    env = dict(os.environ)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("kubectl", "curl", "hey"):
        stub = bin_dir / name
        stub.write_text(
            f'#!/usr/bin/env bash\necho "{name} $*" >> "{calls_log}"\n',
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


def test_all_artifacts_exist() -> None:
    missing = [name for name in EXPECTED_ARTIFACTS if not (CHAOS_DIR / name).is_file()]
    assert not missing, f"missing chaos kit artifacts: {missing}"


def test_shell_scripts_pass_bash_syntax_check() -> None:
    failures = {}
    for name in SHELL_SCRIPTS:
        path = CHAOS_DIR / name
        if not path.is_file():
            failures[name] = "missing file"
            continue
        proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            failures[name] = proc.stderr.strip()
    assert not failures, f"bash -n failures: {failures}"


def test_dry_run_performs_no_cluster_side_effects(tmp_path: Path) -> None:
    """DRY_RUN=1 must not invoke kubectl/curl/hey at all."""
    calls_log = tmp_path / "calls.log"
    calls_log.write_text("", encoding="utf-8")
    env = _stub_bin(tmp_path, calls_log)
    env["DRY_RUN"] = "1"
    failures = {}
    for name in SHELL_SCRIPTS:
        path = CHAOS_DIR / name
        if not path.is_file():
            failures[name] = "missing file"
            continue
        calls_log.write_text("", encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(path), "--confirm"],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        calls = calls_log.read_text(encoding="utf-8").strip()
        if proc.returncode != 0:
            failures[name] = f"exit {proc.returncode}: {proc.stderr.strip()}"
        elif calls:
            failures[name] = f"side effects during dry run: {calls!r}"
    assert not failures, f"dry-run safety failures: {failures}"


def test_live_run_without_confirm_is_refused(tmp_path: Path) -> None:
    """A mutating run without --confirm must exit non-zero and touch nothing."""
    calls_log = tmp_path / "calls.log"
    calls_log.write_text("", encoding="utf-8")
    env = _stub_bin(tmp_path, calls_log)
    env.pop("DRY_RUN", None)
    failures = {}
    for name in SHELL_SCRIPTS:
        path = CHAOS_DIR / name
        if not path.is_file():
            failures[name] = "missing file"
            continue
        calls_log.write_text("", encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(path)], capture_output=True, text=True, timeout=60, env=env
        )
        calls = calls_log.read_text(encoding="utf-8").strip()
        if proc.returncode == 0:
            failures[name] = "live run without --confirm was allowed"
        elif calls:
            failures[name] = f"side effects despite refusal: {calls!r}"
    assert not failures, f"confirm-gate failures: {failures}"


def test_abort_all_restores_prod_replicas(tmp_path: Path) -> None:
    """abort-all.sh must rescale every prod deploy back to 2 replicas."""
    path = CHAOS_DIR / "abort-all.sh"
    if not path.is_file():
        pytest.fail("missing chaos kit artifact: abort-all.sh")
    calls_log = tmp_path / "calls.log"
    calls_log.write_text("", encoding="utf-8")
    env = _stub_bin(tmp_path, calls_log)
    proc = subprocess.run(
        ["bash", str(path), "--confirm"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr.strip()
    calls = calls_log.read_text(encoding="utf-8")
    missing = [
        deploy
        for deploy in PROD_DEPLOYS
        if f"scale -n prod deploy/{deploy} --replicas=2" not in calls
    ]
    assert not missing, f"abort-all does not restore: {missing}\n{calls}"


def test_yb_slow_query_is_read_only() -> None:
    path = CHAOS_DIR / "yb-slow-query.sql"
    if not path.is_file():
        pytest.fail("missing chaos kit artifact: yb-slow-query.sql")
    forbidden = ("DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE")
    hits = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().upper().startswith(forbidden)
    ]
    assert not hits, f"slow-query file must be read-only, found: {hits}"


def test_cpu_hog_manifest_is_pinned_and_bounded() -> None:
    path = CHAOS_DIR / "cpu-hog-job.yaml"
    if not path.is_file():
        pytest.fail("missing chaos kit artifact: cpu-hog-job.yaml")
    text = path.read_text(encoding="utf-8")
    assert ":latest" not in text, "container image must be pinned, not :latest"
    assert "chaos-load" in text, "load job must run in the chaos-load namespace"
    assert "resources:" in text, "load job must declare resource limits"
