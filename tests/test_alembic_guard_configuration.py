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
