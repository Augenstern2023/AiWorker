# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import re
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


@dataclass(frozen=True)
class MigrationReport:
    head: str
    revisions: tuple[str, ...]


class GuardError(RuntimeError):
    """Raised when repository state cannot safely represent its database schema."""


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
