from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "validate-target.mjs"
RUNTIME = ROOT / "runtime"


def _run_target(agent_path: str, workspace: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "node",
            str(HELPER),
            "--agent-path",
            agent_path,
            "--workspace",
            str(workspace),
            "--runtime-root",
            str(RUNTIME),
            "--github-output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _selected_service(tmp_path: Path, yaml: str) -> str:
    (tmp_path / "azure.yaml").write_text(yaml, encoding="utf-8")
    output = tmp_path / "github-output"
    result = _run_target(".", tmp_path, output)
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert Path(values["agent-root"]) == tmp_path.resolve()
    return values["service-name"]


def test_selects_exactly_one_hosted_agent(tmp_path: Path) -> None:
    assert _selected_service(
        tmp_path,
        "name: sample\nservices:\n  api:\n    host: containerapp\n  agent:\n    host: azure.ai.agent\n",
    ) == "agent"


def test_accepts_four_space_service_indentation(tmp_path: Path) -> None:
    assert _selected_service(
        tmp_path,
        "services:\n"
        "    api:\n"
        "        host: containerapp\n"
        "    agent:\n"
        "        host: azure.ai.agent\n",
    ) == "agent"


def test_inspects_only_direct_host_in_nested_flow_mapping(tmp_path: Path) -> None:
    assert _selected_service(
        tmp_path,
        "services: {agent: {metadata: {host: containerapp}, host: azure.ai.agent}}\n",
    ) == "agent"


def test_nested_flow_host_is_not_a_service_host(tmp_path: Path) -> None:
    (tmp_path / "azure.yaml").write_text(
        "services: {not-an-agent: {metadata: {host: azure.ai.agent}, host: containerapp}}\n",
        encoding="utf-8",
    )
    result = _run_target(".", tmp_path, tmp_path / "output")
    assert result.returncode == 1
    assert "found 0" in result.stderr


@pytest.mark.parametrize(
    "services",
    [
        "  api:\n    host: containerapp\n",
        "  first:\n    host: azure.ai.agent\n  second:\n    host: azure.ai.agent\n",
        "",
    ],
)
def test_rejects_missing_multiple_or_empty_hosted_agent(tmp_path: Path, services: str) -> None:
    (tmp_path / "azure.yaml").write_text(f"services:\n{services}", encoding="utf-8")
    result = _run_target(".", tmp_path, tmp_path / "output")
    assert result.returncode == 1


def test_rejects_target_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "azure.yaml").write_text(
        "services:\n  agent:\n    host: azure.ai.agent\n", encoding="utf-8"
    )
    result = _run_target(str(outside), workspace, tmp_path / "output")
    assert result.returncode == 1
    assert "inside GITHUB_WORKSPACE" in result.stderr


def test_rejects_symlinked_agent_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "azure.yaml").write_text(
        "services:\n  agent:\n    host: azure.ai.agent\n", encoding="utf-8"
    )
    link = tmp_path / "target-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this platform")
    result = _run_target("target-link", tmp_path, tmp_path / "output")
    assert result.returncode == 1
    assert "symbolic links" in result.stderr


def test_never_imports_target_python(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    (tmp_path / "azure.yaml").write_text(
        "services:\n  agent:\n    host: azure.ai.agent\n", encoding="utf-8"
    )
    (tmp_path / "agent.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n", encoding="utf-8"
    )
    assert _selected_service(
        tmp_path,
        "services:\n  agent:\n    host: azure.ai.agent\n",
    ) == "agent"
    assert not marker.exists()
