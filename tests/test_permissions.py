from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from permissions import lock, restore


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(os.name != "posix", reason="Action permission hardening targets ubuntu-latest")
def test_lock_makes_only_results_writable_and_restore_recovers_modes(tmp_path: Path) -> None:
    root = tmp_path / "agent"
    source = root / "src" / "agent.py"
    results = root / ".foundry" / "results"
    state = tmp_path / "state" / "permissions.json"
    source.parent.mkdir(parents=True)
    source.write_text("print('not executed')\n", encoding="utf-8")
    os.chmod(root, 0o750)
    os.chmod(source, 0o640)

    lock(root, results, state)

    assert state.is_file()
    assert _mode(root) & 0o222 == 0
    assert _mode(source) & 0o222 == 0
    assert _mode(results) & stat.S_IWUSR

    restore(root, results, state)

    assert _mode(root) == 0o750
    assert _mode(source) == 0o640
