from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .utils import generated_artifact


def git(
    cwd: Path,
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    if check and process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip() or "git command failed"
        raise RuntimeError(f"git {' '.join(args)}: {message}")
    return process


def repo_root(path: Path) -> Path:
    process = git(path, "rev-parse", "--show-toplevel")
    return Path(process.stdout.strip()).resolve()


def head_commit(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").stdout.strip()


def ensure_repo_ready(root: Path) -> None:
    git(root, "rev-parse", "--is-inside-work-tree")
    head_commit(root)


def ensure_clean(root: Path) -> None:
    process = git(root, "status", "--porcelain", "--untracked-files=all")
    lines = [
        line
        for line in process.stdout.splitlines()
        if line and ".olo/" not in line.replace("\\", "/")
    ]
    if lines:
        preview = "\n".join(lines[:20])
        raise RuntimeError(
            "the repository must be clean before Olo initialization; commit or stash:\n"
            + preview
        )


def add_local_exclude(root: Path, pattern: str = ".olo/") -> None:
    relative = git(root, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    path = Path(relative)
    if not path.is_absolute():
        path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    patterns = (
        [
            ".olo/",
            ".olo-history/",
            "__pycache__/",
            "*.pyc",
            ".pytest_cache/",
            "node_modules/",
            "dist/",
            "build/",
        ]
        if pattern == ".olo/"
        else [pattern]
    )
    missing = [item for item in patterns if item not in lines]
    if missing:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            for item in missing:
                handle.write(item + "\n")


def create_worktree(root: Path, path: Path, branch: str, commit: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(root, "worktree", "add", "-b", branch, str(path), commit)


def remove_worktree(root: Path, path: Path, branch: str | None) -> None:
    if path.exists():
        git(root, "worktree", "remove", "--force", str(path), check=False)
    git(root, "worktree", "prune", check=False)
    if branch:
        git(root, "branch", "-D", branch, check=False)


def changed_files(worktree: Path) -> list[str]:
    process = git(
        worktree,
        "status",
        "--porcelain",
        "--untracked-files=all",
        check=False,
    )
    paths: list[str] = []
    for line in process.stdout.splitlines():
        if len(line) < 4:
            continue
        value = line[3:].strip()
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        normalized = value.strip('"').replace("\\", "/")
        if not generated_artifact(normalized):
            paths.append(normalized)
    return sorted(set(paths))


def capture_diff(worktree: Path) -> str:
    fd, index_name = tempfile.mkstemp(prefix="olo-index-")
    os.close(fd)
    Path(index_name).unlink(missing_ok=True)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_name
    try:
        git(worktree, "read-tree", "HEAD", env=env)
        git(worktree, "add", "-A", env=env)
        process = git(
            worktree,
            "diff",
            "--cached",
            "--binary",
            "--no-ext-diff",
            "HEAD",
            check=False,
            env=env,
        )
        return process.stdout
    finally:
        Path(index_name).unlink(missing_ok=True)


def commit_all(worktree: Path, message: str) -> str:
    git(worktree, "add", "-A")
    tracked = git(worktree, "ls-files", "-z").stdout.split("\0")
    generated = [name for name in tracked if name and generated_artifact(name)]
    if generated:
        git(worktree, "rm", "--cached", "--ignore-unmatch", "--", *generated)
    has_changes = git(worktree, "diff", "--cached", "--quiet", check=False).returncode == 1
    if has_changes:
        git(
            worktree,
            "-c",
            "user.name=Olo Autoresearch",
            "-c",
            "user.email=olo@local.invalid",
            "commit",
            "-m",
            message,
        )
    return git(worktree, "rev-parse", "HEAD").stdout.strip()


def diff_between(root: Path, left: str, right: str) -> str:
    return git(root, "diff", "--binary", left, right, check=False).stdout


def read_blob(root: Path, commit: str, path: str) -> bytes:
    process = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=root, capture_output=True, check=False)
    if process.returncode:
        raise RuntimeError(f"cannot read donor file: {path}")
    return process.stdout


def parent_commit(graph: dict[str, Any], parent_id: str) -> str:
    parent = graph.get("nodes", {}).get(parent_id)
    if not parent or not parent.get("commit"):
        raise RuntimeError(f"parent {parent_id} has no committed revision")
    return str(parent["commit"])
