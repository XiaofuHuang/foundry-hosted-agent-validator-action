#!/usr/bin/env python3
"""Run isolated Copilot validation invocations with bounded concurrency."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence


MAX_WORKERS_LIMIT = 3
SETTINGS = '{"disableAllHooks":true,"ide":{"autoConnect":false}}\n'


class InvocationError(ValueError):
    """Raised when validation invocations cannot be scheduled safely."""


class InvocationCancelled(RuntimeError):
    """Raised after running child processes are terminated and reaped."""

    def __init__(self, signal_number: int):
        super().__init__(f"Validation invocations cancelled by signal {signal_number}")
        self.signal_number = signal_number


@dataclass(frozen=True)
class Worker:
    index: int
    workspace: Path
    root: Path
    output: Path
    copilot_home: Path
    stdout_path: Path
    stderr_path: Path
    status_path: Path


@dataclass
class RunningWorker:
    worker: Worker
    process: subprocess.Popen[bytes]
    stdout: BinaryIO
    stderr: BinaryIO


def read_invocation_paths(path: Path) -> list[Path]:
    try:
        raw_paths = path.read_bytes()
    except OSError as error:
        raise InvocationError(f"Cannot read invocation list: {error}") from error
    if raw_paths and not raw_paths.endswith(b"\0"):
        raise InvocationError("Invocation list must be NUL-terminated")

    paths = [Path(os.fsdecode(value)) for value in raw_paths.split(b"\0") if value]
    if len(set(paths)) != len(paths):
        raise InvocationError("Invocation list contains duplicate workspaces")
    for workspace in paths:
        if not workspace.is_absolute() or not workspace.is_dir():
            raise InvocationError(
                f"Invocation workspace must be an existing absolute directory: {workspace}"
            )
    return paths


def _prepare_workers(
    invocation_paths: Sequence[Path],
    invocation_root: Path,
    template_home: Path,
) -> list[Worker]:
    if not template_home.is_dir():
        raise InvocationError(f"Copilot template home does not exist: {template_home}")
    invocation_root.mkdir(parents=True, exist_ok=True)

    workers: list[Worker] = []
    for index, workspace in enumerate(invocation_paths, start=1):
        root = invocation_root / f"{index:06d}"
        output = root / "output"
        copilot_home = root / "copilot-home"
        root.mkdir()
        output.mkdir()
        shutil.copytree(template_home, copilot_home, symlinks=True)
        (copilot_home / "settings.json").write_text(SETTINGS, encoding="utf-8")
        workers.append(
            Worker(
                index=index,
                workspace=workspace,
                root=root,
                output=output,
                copilot_home=copilot_home,
                stdout_path=root / "stdout.log",
                stderr_path=root / "stderr.log",
                status_path=root / "status",
            )
        )
    return workers


def _prompt(
    worker: Worker,
    report_id: str,
    rules_file: Path | None,
    rules_kind: str | None,
) -> str:
    rules_prompt = ""
    if rules_file is not None and rules_kind is not None:
        rules_prompt = (
            f" Use rulesFile={rules_file} as the {rules_kind} batch rules file."
        )
    return (
        "Use the /validate-foundry-ci skill with "
        f"workspacePath={worker.workspace}, outputPath={worker.output}, "
        f"and reportId={report_id}.\n"
        "Run the downloaded validation workflow once for only the azure.yaml directly\n"
        "under workspacePath; do not process nested azure.yaml files because the Action\n"
        "schedules their containing directories separately. Process every hosted agent\n"
        "service in that file in skill-defined order, write every report pair under\n"
        f"outputPath, and return its batch summary.{rules_prompt}"
    )


def _command(
    worker: Worker,
    copilot_command: Sequence[str],
    skill_root: Path,
    report_id: str,
    rules_root: Path | None,
    rules_file: Path | None,
    rules_kind: str | None,
) -> list[str]:
    command = [
        *copilot_command,
        "-C",
        str(worker.workspace),
        "--prompt",
        _prompt(worker, report_id, rules_file, rules_kind),
        "--add-dir",
        str(skill_root),
        "--add-dir",
        str(worker.output),
    ]
    if rules_root is not None:
        command.extend(("--add-dir", str(rules_root)))
    command.extend(
        (
            "--available-tools=view,grep,glob,edit,apply_patch,create",
            "--allow-tool=write",
            "--deny-tool=shell",
            "--deny-tool=url",
            "--disable-builtin-mcps",
            "--no-ask-user",
            "--no-auto-update",
            "--no-custom-instructions",
            "--silent",
        )
    )
    return command


def _write_status(worker: Worker, status: int) -> None:
    temporary = worker.status_path.with_name(".status.tmp")
    temporary.write_text(f"{status}\n", encoding="ascii")
    os.replace(temporary, worker.status_path)


def _finish_worker(running: RunningWorker, status: int | None = None) -> int:
    if status is None:
        status = running.process.wait()
    running.stdout.close()
    running.stderr.close()
    if status < 0:
        status = 128 + abs(status)
    _write_status(running.worker, status)
    return status


def _signal_process(process: subprocess.Popen[bytes], signal_number: int) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal_number)
        elif signal_number == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _terminate_and_reap(running_workers: list[RunningWorker]) -> None:
    for running in running_workers:
        _signal_process(running.process, signal.SIGTERM)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if all(running.process.poll() is not None for running in running_workers):
            break
        time.sleep(0.05)

    for running in running_workers:
        if running.process.poll() is None:
            _signal_process(
                running.process, getattr(signal, "SIGKILL", signal.SIGTERM)
            )
    for running in running_workers:
        _finish_worker(running)


def run_invocations(
    invocation_paths: Sequence[Path],
    invocation_root: Path,
    template_home: Path,
    skill_root: Path,
    report_id: str,
    *,
    rules_root: Path | None = None,
    rules_file: Path | None = None,
    rules_kind: str | None = None,
    max_workers: int = MAX_WORKERS_LIMIT,
    copilot_command: Sequence[str] = ("copilot",),
    environment: dict[str, str] | None = None,
    poll_interval: float = 0.05,
) -> list[int]:
    if not 1 <= max_workers <= MAX_WORKERS_LIMIT:
        raise InvocationError(
            f"Worker count must be between 1 and {MAX_WORKERS_LIMIT}"
        )
    if not copilot_command:
        raise InvocationError("Copilot command must not be empty")
    if not skill_root.is_dir():
        raise InvocationError(f"Validation skill path does not exist: {skill_root}")
    if (rules_root is None) != (rules_file is None) or (
        rules_file is None
    ) != (rules_kind is None):
        raise InvocationError(
            "Rules root, file, and kind must be supplied together"
        )

    workers = _prepare_workers(invocation_paths, invocation_root, template_home)
    statuses: list[int | None] = [None] * len(workers)
    pending = list(workers)
    running: list[RunningWorker] = []
    cancelled_signal: int | None = None
    previous_handlers: dict[int, signal.Handlers] = {}

    def request_cancellation(signal_number: int, _frame: object) -> None:
        nonlocal cancelled_signal
        cancelled_signal = signal_number

    handled_signals = [
        signal_number
        for signal_number in (
            getattr(signal, "SIGHUP", None),
            signal.SIGINT,
            signal.SIGTERM,
        )
        if signal_number is not None
    ]
    for signal_number in handled_signals:
        previous_handlers[signal_number] = signal.signal(
            signal_number, request_cancellation
        )

    try:
        while pending or running:
            if cancelled_signal is not None:
                cancelled_workers = list(running)
                running.clear()
                _terminate_and_reap(cancelled_workers)
                raise InvocationCancelled(cancelled_signal)

            while pending and len(running) < max_workers:
                worker = pending.pop(0)
                child_environment = dict(
                    environment if environment is not None else os.environ
                )
                child_environment.update(
                    {
                        "COPILOT_HOME": str(worker.copilot_home),
                        "COPILOT_AUTO_UPDATE": "false",
                        "GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS": "false",
                    }
                )
                stdout = worker.stdout_path.open("wb")
                stderr = worker.stderr_path.open("wb")
                try:
                    process = subprocess.Popen(
                        _command(
                            worker,
                            copilot_command,
                            skill_root,
                            report_id,
                            rules_root,
                            rules_file,
                            rules_kind,
                        ),
                        env=child_environment,
                        stdout=stdout,
                        stderr=stderr,
                        start_new_session=os.name == "posix",
                    )
                except OSError:
                    stdout.close()
                    stderr.close()
                    _write_status(worker, 127)
                    statuses[worker.index - 1] = 127
                except BaseException:
                    stdout.close()
                    stderr.close()
                    raise
                else:
                    running.append(
                        RunningWorker(
                            worker=worker,
                            process=process,
                            stdout=stdout,
                            stderr=stderr,
                        )
                    )

            completed = [
                running_worker
                for running_worker in running
                if running_worker.process.poll() is not None
            ]
            if not completed:
                time.sleep(poll_interval)
                continue
            for running_worker in completed:
                status = _finish_worker(
                    running_worker, running_worker.process.returncode
                )
                statuses[running_worker.worker.index - 1] = status
                running.remove(running_worker)
        if cancelled_signal is not None:
            raise InvocationCancelled(cancelled_signal)
    except BaseException:
        if running:
            _terminate_and_reap(running)
        raise
    finally:
        for signal_number, previous_handler in previous_handlers.items():
            signal.signal(signal_number, previous_handler)

    if any(status is None for status in statuses):
        raise InvocationError("Not every validation invocation produced a status")
    return [int(status) for status in statuses]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--invocations-file", type=Path, required=True)
    parser.add_argument("--invocation-root", type=Path, required=True)
    parser.add_argument("--template-home", type=Path, required=True)
    parser.add_argument("--skill-root", type=Path, required=True)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--rules-root", type=Path)
    parser.add_argument("--rules-file", type=Path)
    parser.add_argument("--rules-kind", choices=("explicit", "root"))
    parser.add_argument("--max-workers", type=int, default=MAX_WORKERS_LIMIT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        statuses = run_invocations(
            read_invocation_paths(args.invocations_file),
            args.invocation_root,
            args.template_home,
            args.skill_root,
            args.report_id,
            rules_root=args.rules_root,
            rules_file=args.rules_file,
            rules_kind=args.rules_kind,
            max_workers=args.max_workers,
        )
    except InvocationCancelled as error:
        print(str(error), file=sys.stderr)
        return 128 + error.signal_number
    except (InvocationError, OSError) as error:
        print(f"Validation invocation scheduling failed: {error}", file=sys.stderr)
        return 1
    return 1 if any(status != 0 for status in statuses) else 0


if __name__ == "__main__":
    raise SystemExit(main())
