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
    PushUpdate,
    ZERO_SHA,
    parse_push_updates,
    push_update_from_pre_commit_environment,
    validate_migration_tree,
    validate_push_updates,
    validate_repository,
    validate_staged_changes,
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


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


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


def test_staged_schema_change_requires_new_staged_migration(git_repo: Path) -> None:
    schema = git_repo / "system_database" / "schema.py"
    schema.write_text("schema_version = 2\n", encoding="utf-8")
    _git(git_repo, "add", "system_database/schema.py")

    with pytest.raises(GuardError, match="必须同时暂存新增 Alembic migration"):
        validate_staged_changes(git_repo)


def test_staged_schema_and_new_migration_pass(git_repo: Path) -> None:
    schema = git_repo / "system_database" / "schema.py"
    schema.write_text("schema_version = 2\n", encoding="utf-8")
    versions = git_repo / "system_database" / "alembic" / "versions"
    _write_revision(versions, "0003_added.py", "0003_added", "0002_current")
    _git(git_repo, "add", "system_database/schema.py")
    _git(git_repo, "add", "system_database/alembic/versions/0003_added.py")

    report = validate_staged_changes(git_repo)

    assert report is not None
    assert report.head == "0003_added"


@pytest.mark.parametrize("operation", ["modify", "delete", "rename"])
def test_staged_historical_migration_is_immutable(
    git_repo: Path,
    operation: str,
) -> None:
    versions = git_repo / "system_database" / "alembic" / "versions"
    migration = versions / "0002_current.py"
    if operation == "modify":
        migration.write_text(migration.read_text("utf-8") + "# changed\n", "utf-8")
        _git(git_repo, "add", migration.as_posix())
    elif operation == "delete":
        _git(git_repo, "rm", migration.as_posix())
    else:
        _git(git_repo, "mv", migration.as_posix(), (versions / "0002_moved.py").as_posix())

    with pytest.raises(GuardError, match="禁止修改、删除或重命名历史 migration"):
        validate_staged_changes(git_repo)


def test_staged_validation_reads_index_instead_of_worktree(git_repo: Path) -> None:
    versions = git_repo / "system_database" / "alembic" / "versions"
    migration = versions / "0003_added.py"
    _write_revision(migration.parent, migration.name, "0003_added", "0002_missing")
    _git(git_repo, "add", migration.as_posix())
    _write_revision(migration.parent, migration.name, "0003_added", "0002_current")

    with pytest.raises(GuardError, match="无法解析 Alembic 迁移图"):
        validate_staged_changes(git_repo)


def test_parse_push_updates_accepts_git_hook_protocol() -> None:
    value = (
        "refs/heads/main " + "a" * 40 + " refs/heads/main " + "b" * 40 + "\n"
    )

    assert parse_push_updates(value) == (
        PushUpdate(
            local_ref="refs/heads/main",
            local_sha="a" * 40,
            remote_ref="refs/heads/main",
            remote_sha="b" * 40,
        ),
    )


def test_pre_commit_push_environment_reconstructs_update() -> None:
    environment = {
        "PRE_COMMIT_LOCAL_BRANCH": "refs/heads/main",
        "PRE_COMMIT_REMOTE_BRANCH": "refs/heads/main",
        "PRE_COMMIT_REMOTE_NAME": "manjuxuanpin",
        "PRE_COMMIT_REMOTE_URL": "https://example.invalid/repository.git",
        "PRE_COMMIT_FROM_REF": "b" * 40,
        "PRE_COMMIT_TO_REF": "a" * 40,
    }

    remote_name, update = push_update_from_pre_commit_environment(environment)

    assert remote_name == "manjuxuanpin"
    assert update == PushUpdate(
        local_ref="refs/heads/main",
        local_sha="a" * 40,
        remote_ref="refs/heads/main",
        remote_sha="b" * 40,
    )


def test_push_to_manjuxuanpin_requires_migration_for_schema_change(
    git_repo: Path,
) -> None:
    base_sha = _git(git_repo, "rev-parse", "HEAD")
    schema = git_repo / "system_database" / "schema.py"
    schema.write_text("schema_version = 2\n", encoding="utf-8")
    tip_sha = _commit(git_repo, "schema without migration")
    update = PushUpdate("refs/heads/main", tip_sha, "refs/heads/main", base_sha)

    with pytest.raises(GuardError, match="推送范围修改了数据库结构.*没有新增 migration"):
        validate_push_updates(git_repo, "manjuxuanpin", (update,))


def test_push_validates_committed_tree_not_worktree(git_repo: Path) -> None:
    base_sha = _git(git_repo, "rev-parse", "HEAD")
    schema = git_repo / "system_database" / "schema.py"
    schema.write_text("schema_version = 2\n", encoding="utf-8")
    versions = git_repo / "system_database" / "alembic" / "versions"
    migration = versions / "0003_added.py"
    _write_revision(versions, migration.name, "0003_added", "0002_current")
    tip_sha = _commit(git_repo, "schema and migration")
    migration.write_text("broken worktree content\n", encoding="utf-8")
    update = PushUpdate("refs/heads/main", tip_sha, "refs/heads/main", base_sha)

    reports = validate_push_updates(git_repo, "manjuxuanpin", (update,))

    assert tuple(report.head for report in reports) == ("0003_added",)


def test_push_deletion_has_no_commit_tree_to_validate(git_repo: Path) -> None:
    update = PushUpdate(
        "(delete)",
        ZERO_SHA,
        "refs/heads/obsolete",
        _git(git_repo, "rev-parse", "HEAD"),
    )

    assert validate_push_updates(git_repo, "manjuxuanpin", (update,)) == ()


def test_other_remote_only_requires_valid_commit_tree(git_repo: Path) -> None:
    base_sha = _git(git_repo, "rev-parse", "HEAD")
    schema = git_repo / "system_database" / "schema.py"
    schema.write_text("schema_version = 2\n", encoding="utf-8")
    tip_sha = _commit(git_repo, "schema without migration")
    update = PushUpdate("refs/heads/main", tip_sha, "refs/heads/main", base_sha)

    reports = validate_push_updates(git_repo, "origin", (update,))

    assert tuple(report.head for report in reports) == ("0002_current",)
