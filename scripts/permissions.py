from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path


class PermissionGuardError(ValueError):
    pass


def _inside(child: Path, parent: Path) -> None:
    try:
        child.relative_to(parent)
    except ValueError as error:
        raise PermissionGuardError(f"{child} is outside {parent}") from error


def _walk(root: Path) -> list[Path]:
    paths = [root]
    for directory, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        directory_names[:] = [
            name for name in directory_names if not (base / name).is_symlink()
        ]
        paths.extend(base / name for name in directory_names)
        paths.extend(base / name for name in file_names if not (base / name).is_symlink())
    return paths


def _safe_chmod(path: Path, mode: int) -> None:
    if path.is_symlink():
        return
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except NotImplementedError:
        os.chmod(path, mode)


def lock(root: Path, results: Path, state_path: Path) -> None:
    root = root.resolve(strict=True)
    results = results.absolute()
    _inside(results, root)
    if results != root / ".foundry" / "results":
        raise PermissionGuardError("Results directory must be <agent-root>/.foundry/results")

    foundry = root / ".foundry"
    for candidate in (foundry, results):
        if candidate.is_symlink():
            raise PermissionGuardError(f"Refusing symlinked results component: {candidate}")
    foundry.mkdir(mode=0o700, exist_ok=True)
    results.mkdir(mode=0o700, exist_ok=True)

    entries: list[dict[str, int | str]] = []
    for path in _walk(root):
        mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
        entries.append({"path": os.fspath(path.relative_to(root)), "mode": mode})

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    os.chmod(state_path, 0o600)

    for entry in entries:
        path = root / str(entry["path"])
        _safe_chmod(path, int(entry["mode"]) & ~0o222)

    for path in _walk(results):
        current = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
        writable = current | (0o700 if path.is_dir() else 0o600)
        _safe_chmod(path, writable)


def restore(root: Path, results: Path, state_path: Path) -> None:
    root = root.resolve(strict=True)
    _inside(results.absolute(), root)
    data = json.loads(state_path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("entries"), list):
        raise PermissionGuardError("Invalid permission state")
    for entry in reversed(data["entries"]):
        if not isinstance(entry, dict) or set(entry) != {"path", "mode"}:
            raise PermissionGuardError("Invalid permission state entry")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or not isinstance(entry["mode"], int):
            raise PermissionGuardError("Unsafe permission state entry")
        path = root / relative
        if path.exists() and not path.is_symlink():
            _safe_chmod(path, entry["mode"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("lock", "restore"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "lock":
            lock(args.root, args.results, args.state)
        else:
            restore(args.root, args.results, args.state)
    except (OSError, json.JSONDecodeError, PermissionGuardError) as error:
        print(f"::error::Permission guard failed: {str(error).replace('%', '%25').replace(chr(10), ' ')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
