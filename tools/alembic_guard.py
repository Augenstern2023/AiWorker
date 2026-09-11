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
import sys
import tarfile
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Mapping

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


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


@contextmanager
def _isolated_system_database_imports(repo_root: Path) -> Iterator[None]:
    original_path = list(sys.path)
    saved_modules = {
        name: module
        for name, module in tuple(sys.modules.items())
        if name == "system_database" or name.startswith("system_database.")
    }
    for name in saved_modules:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(repo_root.resolve()))
    try:
        yield
    finally:
        for name in tuple(sys.modules):
            if name == "system_database" or name.startswith("system_database."):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        sys.path[:] = original_path


def validate_migration_tree(repo_root: Path) -> MigrationReport:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(repo_root / "system_database" / "alembic"),
    )
    try:
        with _isolated_system_database_imports(repo_root):
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
        "-z",
        "--",
        "system_database/alembic/versions",
    )
    tracked = {
        value.decode("utf-8", errors="strict").replace("\\", "/")
        for value in raw.split(b"\0")
        if value and value.lower().endswith(b".py")
    }
    versions = repo_root / "system_database" / "alembic" / "versions"
    physical = {
        path.relative_to(repo_root).as_posix()
        for path in versions.glob("*.py")
        if path.is_file()
    }
    return tuple(Path(path) for path in sorted(physical - tracked))


def validate_repository(repo_root: Path) -> MigrationReport:
    untracked = untracked_migration_files(repo_root)
    if untracked:
        rendered = ", ".join(path.as_posix() for path in untracked)
        raise GuardError(f"存在未跟踪迁移脚本（未进入 Git 索引）: {rendered}")
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
        "system_database",
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
        raise GuardError(f"存在未跟踪迁移脚本（未进入 Git 索引）: {rendered}")
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


def push_update_from_pre_commit_environment(
    environment: Mapping[str, str],
) -> tuple[str, PushUpdate] | None:
    remote_name = environment.get("PRE_COMMIT_REMOTE_NAME")
    local_sha = environment.get("PRE_COMMIT_TO_REF")
    remote_sha = environment.get("PRE_COMMIT_FROM_REF")
    if not remote_name or not local_sha or not remote_sha:
        return None
    return (
        remote_name,
        PushUpdate(
            local_ref=environment.get("PRE_COMMIT_LOCAL_BRANCH", "(unknown)"),
            local_sha=local_sha,
            remote_ref=environment.get("PRE_COMMIT_REMOTE_BRANCH", "(unknown)"),
            remote_sha=remote_sha,
        ),
    )


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
            if not (member.isfile() or member.isdir()):
                raise GuardError(f"Git archive 包含不安全条目: {member.name}")
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
        "system_database",
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
        raise GuardError(f"存在未跟踪迁移脚本（未进入 Git 索引）: {rendered}")
    reports: list[MigrationReport] = []
    for update in updates:
        if update.local_sha == ZERO_SHA:
            continue
        if remote_name == "manjuxuanpin":
            reports.append(
                validate_commit_range(
                    repo_root,
                    push_base(repo_root, remote_name, update),
                    update.local_sha,
                )
            )
        else:
            with exported_commit_migration_tree(repo_root, update.local_sha) as tree_root:
                reports.append(validate_migration_tree(tree_root))
    return tuple(reports)


def validate_commit_range(
    repo_root: Path,
    base_sha: str,
    tip_sha: str,
) -> MigrationReport:
    effective_base = EMPTY_TREE_SHA if base_sha == ZERO_SHA else base_sha
    changes = committed_changes(repo_root, effective_base, tip_sha)
    _reject_historical_migration_changes(changes)
    _require_migration_for_schema_changes(changes, context="推送范围")
    with exported_commit_migration_tree(repo_root, tip_sha) as tree_root:
        return validate_migration_tree(tree_root)


def tracked_worktree_changes(repo_root: Path) -> tuple[str, ...]:
    output = run_git(repo_root, "status", "--porcelain", "--untracked-files=no")
    return tuple(line for line in output.splitlines() if line)


def read_remote_main_sha(repo_root: Path) -> str:
    try:
        output = run_git(
            repo_root,
            "ls-remote",
            "--exit-code",
            "manjuxuanpin",
            "refs/heads/main",
        )
    except GuardError as exc:
        raise GuardError("无法确认 manjuxuanpin/main") from exc
    fields = output.split()
    if len(fields) != 2 or fields[1] != "refs/heads/main" or len(fields[0]) != 40:
        raise GuardError("无法确认 manjuxuanpin/main")
    return fields[0]


def read_database_revision(database_url: str) -> str | None:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            table_name = connection.execute(
                text("SELECT to_regclass('alembic_version')")
            ).scalar_one()
            if table_name is None:
                return None
            rows = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().fetchmany(2)
            if len(rows) > 1:
                raise GuardError("alembic_version 包含多个当前 revision")
            return rows[0] if rows else None
    except GuardError:
        raise
    except SQLAlchemyError as exc:
        raise GuardError("无法读取数据库当前 revision") from exc
    finally:
        engine.dispose()


def validate_production_preconditions(
    repo_root: Path,
    *,
    remote_sha: str,
    database_revision: str | None,
) -> MigrationReport:
    if run_git(repo_root, "branch", "--show-current") != "main":
        raise GuardError("生产迁移必须从 main 分支执行")
    if tracked_worktree_changes(repo_root):
        raise GuardError("生产迁移前工作树不干净")
    report = validate_repository(repo_root)
    local_sha = run_git(repo_root, "rev-parse", "HEAD")
    if local_sha != remote_sha:
        raise GuardError("当前 HEAD 尚未同步至 manjuxuanpin/main")
    if database_revision is not None and database_revision not in report.revisions:
        raise GuardError(
            f"数据库 revision {database_revision} 不存在于本地迁移图"
        )
    return report
