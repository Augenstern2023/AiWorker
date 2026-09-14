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

from scripts.upgrade_production_database import guarded_upgrade
from tools.alembic_guard import GuardError, validate_production_preconditions


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
    ).strip()


def _write_revision(
    versions: Path,
    filename: str,
    revision: str,
    down_revision: str | None,
) -> None:
    (versions / filename).write_text(
        f"revision = {revision!r}\ndown_revision = {down_revision!r}\n",
        encoding="utf-8",
    )


@pytest.fixture
def production_repo(tmp_path: Path) -> Path:
    versions = tmp_path / "system_database" / "alembic" / "versions"
    versions.mkdir(parents=True)
    (tmp_path / "alembic.ini").write_text(
        "[alembic]\nscript_location = system_database/alembic\n",
        encoding="utf-8",
    )
    (tmp_path / "system_database" / "schema.py").write_text(
        "schema_version = 1\n",
        encoding="utf-8",
    )
    _write_revision(versions, "0001_base.py", "0001_base", None)
    _write_revision(versions, "0002_current.py", "0002_current", "0001_base")
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "production-guard@example.invalid")
    _git(tmp_path, "config", "user.name", "Production Guard Test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "baseline")
    return tmp_path


def test_production_preconditions_accept_synced_known_revision(
    production_repo: Path,
) -> None:
    head_sha = _git(production_repo, "rev-parse", "HEAD")

    report = validate_production_preconditions(
        production_repo,
        remote_sha=head_sha,
        database_revision="0002_current",
    )

    assert report.head == "0002_current"


def test_production_preconditions_require_main_branch(production_repo: Path) -> None:
    _git(production_repo, "switch", "-c", "feature/local-only")
    head_sha = _git(production_repo, "rev-parse", "HEAD")

    with pytest.raises(GuardError, match="必须从 main 分支执行"):
        validate_production_preconditions(
            production_repo,
            remote_sha=head_sha,
            database_revision="0002_current",
        )


def test_production_preconditions_reject_tracked_changes(production_repo: Path) -> None:
    schema = production_repo / "system_database" / "schema.py"
    schema.write_text("schema_version = 2\n", encoding="utf-8")

    with pytest.raises(GuardError, match="工作树不干净"):
        validate_production_preconditions(
            production_repo,
            remote_sha=_git(production_repo, "rev-parse", "HEAD"),
            database_revision="0002_current",
        )


def test_production_preconditions_reject_untracked_migration(
    production_repo: Path,
) -> None:
    versions = production_repo / "system_database" / "alembic" / "versions"
    _write_revision(versions, "0003_local.py", "0003_local", "0002_current")

    with pytest.raises(GuardError, match="存在未跟踪迁移脚本"):
        validate_production_preconditions(
            production_repo,
            remote_sha=_git(production_repo, "rev-parse", "HEAD"),
            database_revision="0002_current",
        )


def test_production_preconditions_require_remote_head(production_repo: Path) -> None:
    with pytest.raises(GuardError, match="当前 HEAD 尚未同步"):
        validate_production_preconditions(
            production_repo,
            remote_sha="f" * 40,
            database_revision="0002_current",
        )


def test_production_preconditions_reject_unknown_database_revision(
    production_repo: Path,
) -> None:
    with pytest.raises(
        GuardError,
        match="数据库 revision 0003_missing 不存在于本地迁移图",
    ):
        validate_production_preconditions(
            production_repo,
            remote_sha=_git(production_repo, "rev-parse", "HEAD"),
            database_revision="0003_missing",
        )


def test_guarded_upgrade_never_runs_when_remote_cannot_be_confirmed(
    production_repo: Path,
) -> None:
    calls: list[str] = []

    def unavailable_remote() -> str:
        raise GuardError("无法确认 manjuxuanpin/main")

    with pytest.raises(GuardError, match="无法确认 manjuxuanpin/main"):
        guarded_upgrade(
            production_repo,
            read_remote_sha=unavailable_remote,
            read_database_revision=lambda: "0002_current",
            upgrade=calls.append,
        )

    assert calls == []


def test_guarded_upgrade_calls_head_after_all_checks(production_repo: Path) -> None:
    calls: list[str] = []

    report = guarded_upgrade(
        production_repo,
        read_remote_sha=lambda: _git(production_repo, "rev-parse", "HEAD"),
        read_database_revision=lambda: "0002_current",
        upgrade=calls.append,
    )

    assert report.head == "0002_current"
    assert calls == ["head"]


def test_guarded_upgrade_redacts_upgrade_failure_details(production_repo: Path) -> None:
    def failing_upgrade(_target: str) -> None:
        raise RuntimeError("secret-production-password")

    with pytest.raises(GuardError) as captured:
        guarded_upgrade(
            production_repo,
            read_remote_sha=lambda: _git(production_repo, "rev-parse", "HEAD"),
            read_database_revision=lambda: "0002_current",
            upgrade=failing_upgrade,
        )

    assert str(captured.value) == "Alembic 升级失败；数据库事务已由 Alembic 处理"
    assert "secret-production-password" not in str(captured.value)
