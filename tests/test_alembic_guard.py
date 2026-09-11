# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tools.alembic_guard import (
    GuardError,
    validate_migration_tree,
    validate_repository,
)


PROJECT_ROOT = Path(__file__).parent.parent


def _write_revision(
    versions_dir: Path,
    filename: str,
    revision: str,
    down_revision: str | tuple[str, ...] | None,
) -> None:
    (versions_dir / filename).write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "",
                f"revision = {revision!r}",
                f"down_revision = {down_revision!r}",
                "branch_labels = None",
                "depends_on = None",
                "",
                "def upgrade() -> None:",
                "    pass",
                "",
                "def downgrade() -> None:",
                "    pass",
                "",
            )
        ),
        encoding="utf-8",
    )


def _migration_tree(
    tmp_path: Path,
    revisions: dict[str, tuple[str, str | tuple[str, ...] | None]],
) -> Path:
    versions_dir = tmp_path / "system_database" / "alembic" / "versions"
    versions_dir.mkdir(parents=True)
    (tmp_path / "alembic.ini").write_text(
        "[alembic]\nscript_location = system_database/alembic\n",
        encoding="utf-8",
    )
    for filename, (revision, down_revision) in revisions.items():
        _write_revision(versions_dir, filename, revision, down_revision)
    return tmp_path


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
    ).strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = _migration_tree(
        tmp_path,
        {
            "0001_base.py": ("0001_base", None),
            "0002_current.py": ("0002_current", "0001_base"),
        },
    )
    (root / "system_database" / "schema.py").write_text(
        "schema_version = 1\n",
        encoding="utf-8",
    )
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "alembic-guard@example.invalid")
    _git(root, "config", "user.name", "Alembic Guard Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def test_real_repository_has_one_resolvable_head() -> None:
    report = validate_migration_tree(PROJECT_ROOT)

    assert report.head
    assert report.head in report.revisions


def test_revision_number_must_match_filename_prefix(tmp_path: Path) -> None:
    root = _migration_tree(
        tmp_path,
        {
            "0021_base.py": ("0021_base", None),
            "0022_wrong.py": ("0023_actual", "0021_base"),
        },
    )

    with pytest.raises(
        GuardError,
        match="文件名前缀 0022.*revision 前缀 0023",
    ):
        validate_migration_tree(root)


def test_missing_parent_revision_is_rejected(tmp_path: Path) -> None:
    root = _migration_tree(
        tmp_path,
        {"0021_orphan.py": ("0021_orphan", "0020_missing")},
    )

    with pytest.raises(GuardError, match="无法解析 Alembic 迁移图"):
        validate_migration_tree(root)


def test_multiple_heads_are_rejected(tmp_path: Path) -> None:
    root = _migration_tree(
        tmp_path,
        {
            "0021_left.py": ("0021_left", None),
            "0021_right.py": ("0021_right", None),
        },
    )

    with pytest.raises(GuardError, match="必须只有一个 head"):
        validate_migration_tree(root)


def test_duplicate_revision_is_rejected(tmp_path: Path) -> None:
    root = _migration_tree(
        tmp_path,
        {
            "0001_base.py": ("0001_base", None),
            "0001_duplicate.py": ("0001_base", None),
        },
    )

    with pytest.raises(GuardError, match="revision 必须唯一"):
        validate_migration_tree(root)


def test_repository_rejects_untracked_migration(git_repo: Path) -> None:
    versions = git_repo / "system_database" / "alembic" / "versions"
    _write_revision(versions, "0003_untracked.py", "0003_untracked", "0002_current")

    with pytest.raises(GuardError, match="存在未跟踪迁移脚本.*0003_untracked.py"):
        validate_repository(git_repo)
