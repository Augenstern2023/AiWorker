# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import re
import subprocess
import tarfile
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from alembic.config import Config
from alembic.script import ScriptDirectory


MIGRATION_DIRECTORY = "system_database/alembic/versions/"
SCHEMA_PATHS = frozenset({"system_database/schema.py"})
ZERO_SHA = "0" * 40
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@dataclass(frozen=True)
class MigrationReport:
    head: str
    revisions: tuple[str, ...]


class GuardError(RuntimeError):
    """Raised when repository state cannot safely represent its database schema."""


@dataclass(frozen=True)
class GitChange:
    status: str
    old_path: str | None
    path: str


@dataclass(frozen=True)
class PushUpdate:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def run_git_bytes(repo_root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            input=input_bytes,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise GuardError(f"Git 命令执行失败: git {' '.join(args)} {detail}".rstrip()) from exc
    return result.stdout


def run_git(repo_root: Path, *args: str) -> str:
    return run_git_bytes(repo_root, *args).decode("utf-8", errors="strict").strip()


def _number_prefix(value: str) -> str | None:
    match = re.match(r"^(\d+)(?:_|$)", value)
    return match.group(1) if match else None


def _validate_filename_prefixes(scripts: ScriptDirectory) -> None:
    for script in scripts.walk_revisions():
        filename = Path(script.path).stem
        filename_prefix = _number_prefix(filename)
        revision_prefix = _number_prefix(script.revision)
        if filename_prefix != revision_prefix:
            raise GuardError(
                "迁移文件编号不一致: "
                f"文件名前缀 {filename_prefix or '<缺失>'}，"
                f"revision 前缀 {revision_prefix or '<缺失>'} "
                f"({filename})"
            )


def validate_migration_tree(repo_root: Path) -> MigrationReport:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(repo_root / "system_database" / "alembic"),
    )
    try:
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            scripts = ScriptDirectory.from_config(config)
            heads = scripts.get_heads()
            duplicate_warnings = [
                str(item.message)
                for item in caught_warnings
                if "present more than once" in str(item.message)
            ]
            if len(heads) != len(set(heads)) or duplicate_warnings:
                details = "; ".join(duplicate_warnings) or repr(heads)
                raise GuardError(f"Alembic revision 必须唯一: {details}")
            if len(heads) != 1:
                raise GuardError(f"Alembic 必须只有一个 head，当前为: {heads}")
            revisions = tuple(
                script.revision for script in scripts.walk_revisions()
            )
            _validate_filename_prefixes(scripts)
    except GuardError:
        raise
    except Exception as exc:
        raise GuardError(f"无法解析 Alembic 迁移图: {exc}") from exc
    return MigrationReport(head=heads[0], revisions=revisions)


def untracked_migration_files(repo_root: Path) -> tuple[Path, ...]:
    raw = run_git_bytes(
        repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        "system_database/alembic/versions",
    )
    return tuple(
        Path(value.decode("utf-8", errors="strict"))
        for value in raw.split(b"\0")
        if value and value.lower().endswith(b".py")
    )


def validate_repository(repo_root: Path) -> MigrationReport:
    untracked = untracked_migration_files(repo_root)
    if untracked:
        rendered = ", ".join(path.as_posix() for path in untracked)
        raise GuardError(f"存在未跟踪迁移脚本: {rendered}")
    return validate_migration_tree(repo_root)


def parse_name_status(raw: bytes) -> tuple[GitChange, ...]:
    tokens = [token.decode("utf-8", errors="strict") for token in raw.split(b"\0")]
    if tokens and not tokens[-1]:
        tokens.pop()
    changes: list[GitChange] = []
    index = 0
    while index < len(tokens):
        status_token = tokens[index]
        index += 1
        if "\t" in status_token:
            status_token, first_path = status_token.split("\t", 1)
        else:
            if index >= len(tokens):
                raise GuardError("无法解析 Git name-status 输出: 缺少文件路径")
            first_path = tokens[index]
            index += 1
        status = status_token[:1]
        if status in {"R", "C"}:
            if index >= len(tokens):
                raise GuardError("无法解析 Git name-status 输出: 重命名缺少目标路径")
            changes.append(GitChange(status, first_path, tokens[index]))
            index += 1
        else:
            changes.append(GitChange(status, None, first_path))
    return tuple(changes)


def staged_changes(repo_root: Path) -> tuple[GitChange, ...]:
    return parse_name_status(
        run_git_bytes(
            repo_root,
            "diff",
            "--cached",
            "--name-status",
            "-z",
            "--find-renames",
        )
    )


def _is_migration(path: str | None) -> bool:
    return bool(path and path.startswith(MIGRATION_DIRECTORY) and path.endswith(".py"))


def _reject_historical_migration_changes(
    changes: tuple[GitChange, ...],
) -> None:
    invalid = [
        change
        for change in changes
        if change.status != "A"
        and (_is_migration(change.old_path) or _is_migration(change.path))
    ]
    if invalid:
        paths = ", ".join(change.path for change in invalid)
        raise GuardError(
            "禁止修改、删除或重命名历史 migration；请新增后续 migration: " + paths
        )


def _require_migration_for_schema_changes(
    changes: tuple[GitChange, ...],
    *,
    context: str,
) -> None:
    schema_changed = any(
        change.path in SCHEMA_PATHS or change.old_path in SCHEMA_PATHS
        for change in changes
    )
    migration_added = any(
        change.status == "A" and _is_migration(change.path) for change in changes
    )
    if schema_changed and not migration_added:
        if context == "推送范围":
            raise GuardError("推送范围修改了数据库结构，但没有新增 migration")
        raise GuardError("数据库结构已变更，必须同时暂存新增 Alembic migration")


def _index_paths(repo_root: Path) -> tuple[str, ...]:
    raw = run_git_bytes(
        repo_root,
        "ls-files",
        "--cached",
        "-z",
        "--",
        "alembic.ini",
        "system_database/alembic",
    )
    return tuple(
        value.decode("utf-8", errors="strict")
        for value in raw.split(b"\0")
        if value
    )


@contextmanager
def exported_index_migration_tree(repo_root: Path) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="aiworker-alembic-index-") as raw_dir:
        destination = Path(raw_dir)
        for relative in _index_paths(repo_root):
            target = destination / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(run_git_bytes(repo_root, "show", f":{relative}"))
        yield destination


def validate_staged_changes(repo_root: Path) -> MigrationReport | None:
    untracked = untracked_migration_files(repo_root)
    if untracked:
        rendered = ", ".join(path.as_posix() for path in untracked)
        raise GuardError(f"存在未跟踪迁移脚本: {rendered}")
    changes = staged_changes(repo_root)
    _reject_historical_migration_changes(changes)
    _require_migration_for_schema_changes(changes, context="暂存区")
    if not any(_is_migration(change.path) or _is_migration(change.old_path) for change in changes):
        return None
    with exported_index_migration_tree(repo_root) as tree_root:
        return validate_migration_tree(tree_root)


def parse_push_updates(value: str) -> tuple[PushUpdate, ...]:
    updates: list[PushUpdate] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise GuardError(f"pre-push 第 {line_number} 行必须包含 4 个字段")
        updates.append(PushUpdate(*fields))
    return tuple(updates)


def _try_git(repo_root: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return None
    return result.stdout.decode("utf-8", errors="strict").strip()


def push_base(repo_root: Path, remote_name: str, update: PushUpdate) -> str:
    if update.remote_sha != ZERO_SHA:
        return update.remote_sha
    merge_base = _try_git(
        repo_root,
        "merge-base",
        update.local_sha,
        f"refs/remotes/{remote_name}/main",
    )
    return merge_base or EMPTY_TREE_SHA


def committed_changes(repo_root: Path, base_sha: str, tip_sha: str) -> tuple[GitChange, ...]:
    return parse_name_status(
        run_git_bytes(
            repo_root,
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            base_sha,
            tip_sha,
        )
    )


def _safe_extract_archive(archive: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise GuardError(f"Git archive 包含不安全路径: {member.name}")
        bundle.extractall(destination)


@contextmanager
def exported_commit_migration_tree(
    repo_root: Path,
    revision: str,
) -> Iterator[Path]:
    archive = run_git_bytes(
        repo_root,
        "archive",
        "--format=tar",
        revision,
        "alembic.ini",
        "system_database/alembic",
    )
    with tempfile.TemporaryDirectory(prefix="aiworker-alembic-commit-") as raw_dir:
        destination = Path(raw_dir)
        _safe_extract_archive(archive, destination)
        yield destination


def validate_push_updates(
    repo_root: Path,
    remote_name: str,
    updates: tuple[PushUpdate, ...],
) -> tuple[MigrationReport, ...]:
    untracked = untracked_migration_files(repo_root)
    if untracked:
        rendered = ", ".join(path.as_posix() for path in untracked)
        raise GuardError(f"存在未跟踪迁移脚本: {rendered}")
    reports: list[MigrationReport] = []
    for update in updates:
        if update.local_sha == ZERO_SHA:
            continue
        changes = committed_changes(
            repo_root,
            push_base(repo_root, remote_name, update),
            update.local_sha,
        )
        if remote_name == "manjuxuanpin":
            _reject_historical_migration_changes(changes)
            _require_migration_for_schema_changes(changes, context="推送范围")
        with exported_commit_migration_tree(repo_root, update.local_sha) as tree_root:
            reports.append(validate_migration_tree(tree_root))
    return tuple(reports)
