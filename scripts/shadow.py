from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
from pathlib import Path


class ShadowError(ValueError):
    pass


def _assert_nested(path: Path, parent: Path, label: str) -> None:
    try:
        path.relative_to(parent)
    except ValueError as error:
        raise ShadowError(f"{label} must remain inside {parent}") from error


def reject_unsafe_entries(source: Path) -> None:
    for directory, directory_names, file_names in os.walk(source, topdown=True, followlinks=False):
        base = Path(directory)
        for name in [*directory_names, *file_names]:
            path = base / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ShadowError(f"Target contains a symbolic link: {path.relative_to(source)}")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ShadowError(f"Target contains a non-regular entry: {path.relative_to(source)}")


def create_shadow(source: Path, shadow_root: Path) -> Path:
    if source.is_symlink():
        raise ShadowError("Target root must not be a symbolic link")
    source = source.resolve(strict=True)
    shadow_root = shadow_root.absolute()
    if shadow_root.exists():
        raise ShadowError("Shadow root already exists")
    reject_unsafe_entries(source)
    shadow_root.mkdir(parents=True, mode=0o700)
    evidence = shadow_root / "evidence"
    shutil.copytree(source, evidence, symlinks=False, copy_function=shutil.copy2)
    if (shadow_root / ".git").exists() or (shadow_root / ".github").exists():
        raise ShadowError("Shadow root must not contain repository configuration")
    return evidence


def _assert_regular_source(path: Path, expected_parent: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ShadowError(f"Validated report is not a regular file: {path.name}")
    if path.resolve().parent != expected_parent.resolve():
        raise ShadowError(f"Validated report escapes its results directory: {path.name}")


def _copy_exclusive(source: Path, destination: Path) -> None:
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file)
        output_file.flush()
        os.fsync(output_file.fileno())


def publish_reports(
    shadow_evidence: Path,
    target_root: Path,
    report_id: str,
) -> tuple[Path, Path]:
    shadow_evidence = shadow_evidence.resolve(strict=True)
    target_root = target_root.resolve(strict=True)
    source_results = shadow_evidence / ".foundry" / "results"
    destination_results = target_root / ".foundry" / "results"
    _assert_nested(source_results, shadow_evidence, "Shadow results")
    _assert_nested(destination_results, target_root, "Destination results")

    for component in (target_root / ".foundry", destination_results):
        if component.is_symlink():
            raise ShadowError(f"Refusing symlinked destination component: {component}")
    destination_results.mkdir(parents=True, exist_ok=True)

    names = (f"validation-{report_id}.json", f"validation-{report_id}.md")
    sources = tuple(source_results / name for name in names)
    destinations = tuple(destination_results / name for name in names)
    for source in sources:
        _assert_regular_source(source, source_results)
    for destination in destinations:
        if destination.exists() or destination.is_symlink():
            raise ShadowError(f"Destination report already exists: {destination.name}")

    _copy_exclusive(sources[0], destinations[0])
    try:
        _copy_exclusive(sources[1], destinations[1])
    except Exception:
        destinations[0].unlink(missing_ok=True)
        raise
    return destinations


def publish_validated_reports(
    shadow_evidence: Path,
    target_root: Path,
    report_id: str,
    validation_state_path: Path,
    publication_state_path: Path,
) -> tuple[Path, Path]:
    publication_state_path.unlink(missing_ok=True)
    state = json.loads(validation_state_path.read_text(encoding="utf-8"))
    expected_keys = {"version", "reportId", "conclusion", "summary", "counts", "blocking"}
    if not isinstance(state, dict) or set(state) != expected_keys:
        raise ShadowError("Validation state is malformed")
    if state["version"] != 1 or state["reportId"] != report_id:
        raise ShadowError("Validation state does not match the report")
    if state["conclusion"] not in {"success", "failure"}:
        raise ShadowError("Validation state conclusion is invalid")

    destinations = publish_reports(shadow_evidence, target_root, report_id)
    source_results = shadow_evidence / ".foundry" / "results"
    sources = (
        source_results / f"validation-{report_id}.json",
        source_results / f"validation-{report_id}.md",
    )
    for source, destination in zip(sources, destinations, strict=True):
        if destination.is_symlink() or not destination.is_file():
            raise ShadowError(f"Published report is missing: {destination.name}")
        if source.read_bytes() != destination.read_bytes():
            raise ShadowError(f"Published report does not match validated source: {destination.name}")

    publication = {
        **state,
        "jsonReport": os.fspath(destinations[0]),
        "markdownReport": os.fspath(destinations[1]),
    }
    publication_state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = publication_state_path.with_name(f".{publication_state_path.name}.tmp")
    temporary.write_text(json.dumps(publication, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, publication_state_path)
    return destinations


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--source", required=True, type=Path)
    create_parser.add_argument("--shadow-root", required=True, type=Path)
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--shadow-evidence", required=True, type=Path)
    publish_parser.add_argument("--target-root", required=True, type=Path)
    publish_parser.add_argument("--report-id", required=True)
    publish_parser.add_argument("--validation-state", required=True, type=Path)
    publish_parser.add_argument("--publication-state", required=True, type=Path)
    args = parser.parse_args()

    try:
        if args.operation == "create":
            create_shadow(args.source, args.shadow_root)
        else:
            publish_validated_reports(
                args.shadow_evidence,
                args.target_root,
                args.report_id,
                args.validation_state,
                args.publication_state,
            )
    except (OSError, json.JSONDecodeError, ShadowError) as error:
        message = str(error).replace("%", "%25").replace("\r", " ").replace("\n", " ")
        print(f"::error::Shadow evidence operation failed: {message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
