"""Contract tests for the Chaos Mesh workshop kit (workshop/chaos-mesh/).

The kit extends ``workshop/chaos/`` with eight NATS trouble scenarios. The
same safety contract applies: every mutating shell script prints its revert
command up front, performs zero cluster side effects under ``DRY_RUN=1``, and
refuses a live run without ``--confirm``. Every Chaos Mesh CR carries the
``workshop: chaos-mesh`` label so ``revert-all.sh`` can never delete an
experiment that was not applied by this kit.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

KIT_DIR = Path(__file__).resolve().parents[2] / "workshop" / "chaos-mesh"

SHELL_SCRIPTS = [
    "scenario-1-slow-consumer.sh",
    "scenario-2-connection-leak.sh",
    "scenario-3-duplicate-subs.sh",
    "scenario-4-flood-void.sh",
    "revert-all.sh",
]

CHAOS_YAMLS = [
    "scenario-5-nats-latency.yaml",
    "scenario-6-nats-pod-kill.yaml",
    "scenario-7-nats-cpu.yaml",
    "scenario-8-partition.yaml",
]

EXPECTED_ARTIFACTS = SHELL_SCRIPTS + CHAOS_YAMLS + ["README.md"]

CHAOS_KINDS = {"NetworkChaos", "PodChaos", "StressChaos"}


def _stub_bin(tmp_path: Path, calls_log: Path) -> dict[str, str]:
    """Install a fake kubectl that records invocations instead of running them."""
    env = dict(os.environ)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "kubectl"
    stub.write_text(
        f'#!/usr/bin/env bash\necho "kubectl $*" >> "{calls_log}"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_scripts_pass_bash_syntax_check(script: str) -> None:
    result = subprocess.run(
        ["bash", "-n", str(KIT_DIR / script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_all_artifacts_exist() -> None:
    missing = [name for name in EXPECTED_ARTIFACTS if not (KIT_DIR / name).is_file()]
    assert not missing, f"missing chaos-mesh kit artifacts: {missing}"


@pytest.mark.parametrize("yaml_file", CHAOS_YAMLS)
def test_chaos_cr_labels_and_shape(yaml_file: str) -> None:
    doc = yaml.safe_load((KIT_DIR / yaml_file).read_text(encoding="utf-8"))
    assert doc["apiVersion"] == "chaos-mesh.org/v1alpha1"
    assert doc["kind"] in CHAOS_KINDS
    assert doc.get("metadata", {}).get("namespace") == "hetu"
    assert doc["metadata"]["labels"]["workshop"] == "chaos-mesh"
    assert doc["spec"]["selector"]["namespaces"] == ["hetu"]
    assert doc["spec"]["mode"] in {"one", "all"}
    assert "duration" in doc["spec"]


def test_partition_uses_spec_direction_field() -> None:
    """NetworkChaos put ``direction`` at spec level, not under ``partition``.

    A ``spec.partition`` key is rejected by the CRD on apply; the partition
    action has no parameter block of its own.
    """
    doc = yaml.safe_load((KIT_DIR / "scenario-8-partition.yaml").read_text(encoding="utf-8"))
    assert doc["spec"]["action"] == "partition"
    assert "partition" not in doc["spec"]
    assert doc["spec"]["direction"] in {"to", "from", "both"}


@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_script_safe_under_dry_run(tmp_path: Path, script: str) -> None:
    calls_log = tmp_path / "calls.log"
    env = _stub_bin(tmp_path, calls_log)
    env["DRY_RUN"] = "1"
    result = subprocess.run(
        [str(KIT_DIR / script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0
    assert "[dry-run]" in result.stdout
    assert not calls_log.exists()


@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_script_refuses_unconfirmed(tmp_path: Path, script: str) -> None:
    calls_log = tmp_path / "calls.log"
    env = _stub_bin(tmp_path, calls_log)
    result = subprocess.run(
        [str(KIT_DIR / script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 2
    assert "without --confirm" in result.stderr
    assert not calls_log.exists()


@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_every_mutating_script_prints_revert_first(script: str) -> None:
    """Audit the printed instructions: revert must lead, not trail."""
    lines = (KIT_DIR / script).read_text(encoding="utf-8").splitlines()
    printed_reverts = [i for i, ln in enumerate(lines) if "REVERT" in ln]
    assert printed_reverts, f"{script} never prints a revert command"
    first_action = next(i for i, ln in enumerate(lines) if ln.strip().startswith("kubectl"))
    assert printed_reverts[0] < first_action, f"{script} acts before announcing revert"
