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
import shutil
import subprocess

from pre_commit.clientlib import load_config
import yaml

from tools import install_git_hooks


PROJECT_ROOT = Path(__file__).parent.parent


def test_pre_commit_registers_commit_and_push_alembic_guards() -> None:
    config = load_config(str(PROJECT_ROOT / ".pre-commit-config.yaml"))
    hooks = {
        hook["id"]: hook
        for repository in config["repos"]
        if repository["repo"] == "local"
        for hook in repository["hooks"]
    }

    assert hooks["check-alembic-repository"]["stages"] == ["pre-commit"]
    assert hooks["check-alembic-staged"]["stages"] == ["pre-commit"]
    assert hooks["check-alembic-pre-push"]["stages"] == ["pre-push"]
    for name in (
        "check-alembic-repository",
        "check-alembic-staged",
        "check-alembic-pre-push",
    ):
        assert hooks[name]["always_run"] is True
        assert hooks[name]["pass_filenames"] is False


def test_hook_installer_creates_commit_and_push_hooks(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    shutil.copy2(PROJECT_ROOT / ".pre-commit-config.yaml", tmp_path)

    install_git_hooks.install(tmp_path)

    hooks = tmp_path / ".git" / "hooks"
    assert (hooks / "pre-commit").is_file()
    assert (hooks / "pre-push").is_file()


def test_gitea_workflow_runs_range_and_postgres_migration_checks() -> None:
    workflow_path = PROJECT_ROOT / ".gitea" / "workflows" / "alembic-integrity.yml"
    workflow = yaml.load(workflow_path.read_text("utf-8"), Loader=yaml.BaseLoader)
    job = workflow["jobs"]["verify-migrations"]

    assert job["services"]["postgres"]["image"] == "postgres:16"
    assert "AIWORKER_TEST_SYSTEM_DATABASE_URL" in job["env"]
    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert "uv run python tools/check_alembic_integrity.py repository" in commands
    assert any("check_alembic_integrity.py compare" in command for command in commands)
    assert "uv run alembic upgrade head" in commands
    assert any("tests/test_alembic_guard.py" in command for command in commands)
