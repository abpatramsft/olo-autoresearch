from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        dir=path.parent,
        encoding="utf-8",
        newline="\n",
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


class FileLock:
    """Small cross-platform lock based on atomic lock-file creation."""

    def __init__(self, path: Path, timeout: float = 15.0, stale_after: float = 300.0):
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self._fd: int | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.write(self._fd, f"{os.getpid()} {time.time()}\n".encode("ascii"))
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.stale_after:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    lock = path.with_suffix(path.suffix + ".lock")
    with FileLock(lock):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    values: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            values.append(item)
    return values


def quote_shell(value: str | Path) -> str:
    text = str(value)
    if os.name == "nt":
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def finite_number(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"score must be finite, got {value!r}")
    return number


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def iter_files(path: Path) -> Iterator[Path]:
    if not path.exists():
        return iter(())
    return (item for item in path.rglob("*") if item.is_file())


def tail_text(path: Path, max_chars: int = 12000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]
