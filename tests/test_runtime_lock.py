from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"


def test_runtime_manifest_pins_only_reviewed_copilot() -> None:
    package = json.loads((RUNTIME / "package.json").read_text(encoding="utf-8"))
    assert package["private"] is True
    assert package["dependencies"] == {
        "@github/copilot": "1.0.83",
        "yaml": "2.8.1",
    }


def test_lock_pins_complete_integrity_checked_graph() -> None:
    lock = json.loads((RUNTIME / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    assert lock["lockfileVersion"] == 3
    assert packages[""]["dependencies"] == {
        "@github/copilot": "1.0.83",
        "yaml": "2.8.1",
    }

    dependency_entries = {
        path: metadata for path, metadata in packages.items() if path.startswith("node_modules/")
    }
    assert len(dependency_entries) == 11
    for metadata in dependency_entries.values():
        assert metadata["resolved"].startswith("https://registry.npmjs.org/")
        assert metadata["integrity"].startswith("sha512-")

    copilot = packages["node_modules/@github/copilot"]
    assert copilot["version"] == "1.0.83"
    assert set(copilot["optionalDependencies"].values()) == {"1.0.83"}
    assert copilot["dependencies"] == {"detect-libc": "^2.1.2"}

    detect_libc = packages["node_modules/detect-libc"]
    assert detect_libc == {
        "version": "2.1.2",
        "resolved": "https://registry.npmjs.org/detect-libc/-/detect-libc-2.1.2.tgz",
        "integrity": "sha512-Btj2BOOO83o3WyH59e8MgXsxEQVcarkUOpEYrubB0urwnN10yQ364rsiByU11nZlqWYZm05i/of7io4mzihBtQ==",
        "license": "Apache-2.0",
        "engines": {"node": ">=8"},
    }

    yaml = packages["node_modules/yaml"]
    assert yaml["version"] == "2.8.1"
    assert yaml["integrity"] == (
        "sha512-lcYcMxX2PO9XMGvAJkJ3OsNMw+/7FKes7/hgerGUYWIoWu5j/+"
        "YQqcZr5JnPZWzOsEBgMbSbiSTn/dv/69Mkpw=="
    )
