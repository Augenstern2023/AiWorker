# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_worker_launcher.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

"""Tests for worker command generation."""

import importlib
import sys

from runtime.worker_launcher import build_worker_command, build_worker_env


def test_web_and_worker_runtime_repositories_use_default_credential_store(monkeypatch):
    """Would catch either runtime factory omitting the live credential store."""

    module = importlib.import_module("analysis.llm_settings")
    system_database = importlib.import_module("system_database")

    class FakeKeyringCredentialStore:
        def get(self, provider_id):
            return None

        def set(self, provider_id, api_key):
            return None

        def delete(self, provider_id):
            return None

    class FakeSettings:
        @classmethod
        def from_environment(cls):
            return cls()

    class FakeDatabase:
        def __init__(self, settings):
            self.settings = settings

    monkeypatch.setenv("AIWORKER_LLM_DATABASE_CONFIG_ENABLED", "1")
    monkeypatch.setattr(module, "_RUNTIME_REPOSITORY", None)
    monkeypatch.setattr(
        module,
        "default_llm_credential_store",
        FakeKeyringCredentialStore,
    )
    monkeypatch.setattr(system_database, "SystemDatabase", FakeDatabase)
    monkeypatch.setattr(system_database, "SystemDatabaseSettings", FakeSettings)

    web_repository = module.web_settings_repository(object())
    worker_repository = module.runtime_settings_repository()

    assert worker_repository is not None
    assert isinstance(web_repository.credential_store, FakeKeyringCredentialStore)
    assert isinstance(worker_repository.credential_store, FakeKeyringCredentialStore)
    assert isinstance(worker_repository.database, FakeDatabase)


def test_build_worker_command():
    assert build_worker_command(".runtime/jobs/job.json") == [
        sys.executable,
        "main.py",
        "--job-file",
        ".runtime/jobs/job.json",
    ]


def test_build_hongguo_worker_command():
    assert build_worker_command(".runtime/jobs/hg.json", platform="hg") == [
        sys.executable,
        "-m",
        "agents.hongguo_csv_worker",
        "--job-file",
        ".runtime/jobs/hg.json",
    ]


def test_build_douyin_rank_worker_command():
    assert build_worker_command(".runtime/jobs/dy-rank.json", platform="dy", crawler_type="rank") == [
        sys.executable,
        "-m",
        "agents.douyin_rank_worker",
        "--job-file",
        ".runtime/jobs/dy-rank.json",
    ]


def test_build_creative_phase4_worker_command():
    assert build_worker_command(
        ".runtime/jobs/creative.json",
        platform="creative",
        crawler_type="phase4_generate",
    ) == [
        sys.executable,
        "-m",
        "creative.phase4_worker",
        "--job-file",
        ".runtime/jobs/creative.json",
    ]


def test_build_creative_breakdown_worker_command():
    assert build_worker_command(
        ".runtime/jobs/breakdown.json",
        platform="creative",
        crawler_type="breakdown_generate",
    ) == [
        sys.executable,
        "-m",
        "creative.breakdown_worker",
        "--job-file",
        ".runtime/jobs/breakdown.json",
    ]


def test_build_creative_screenplay_review_worker_command():
    assert build_worker_command(
        ".runtime/jobs/script-review.json",
        platform="creative",
        crawler_type="screenplay_review",
    ) == [
        sys.executable,
        "-m",
        "screenplay_review.worker",
        "--job-file",
        ".runtime/jobs/script-review.json",
    ]


def test_build_creative_project_review_worker_command():
    assert build_worker_command(
        ".runtime/jobs/project-review.json",
        platform="creative",
        crawler_type="project_review",
    ) == [
        sys.executable,
        "-m",
        "project_review.worker",
        "--job-file",
        ".runtime/jobs/project-review.json",
    ]


def test_build_creative_screenplay_optimization_worker_command():
    assert build_worker_command(
        ".runtime/jobs/script-optimization.json",
        platform="creative",
        crawler_type="screenplay_optimization",
    ) == [
        sys.executable,
        "-m",
        "screenplay_optimization.worker",
        "--job-file",
        ".runtime/jobs/script-optimization.json",
    ]


def test_build_data_file_operation_worker_command():
    assert build_worker_command(
        ".runtime/jobs/data-operation.json",
        platform="data",
        crawler_type="file_operation",
    ) == [
        sys.executable,
        "-m",
        "agents.data_file_operation_worker",
        "--job-file",
        ".runtime/jobs/data-operation.json",
    ]


def test_build_worker_env_sets_utf8():
    assert build_worker_env()["PYTHONIOENCODING"] == "utf-8"


def test_build_worker_env_enables_database_llm_settings(monkeypatch):
    """Would catch child workers continuing to use stale process environment defaults."""

    monkeypatch.setenv(
        "AIWORKER_SYSTEM_DATABASE_URL",
        "postgresql+psycopg://app:secret@127.0.0.1:5432/aiworker_test",
    )

    assert build_worker_env()["AIWORKER_LLM_DATABASE_CONFIG_ENABLED"] == "1"


def test_worker_env_inherits_provider_key_without_fernet_key(monkeypatch):
    monkeypatch.setenv("SUB2API_KEY", "worker-secret")
    monkeypatch.delenv("AIWORKER_LLM_CONFIG_KEY", raising=False)

    environment = build_worker_env()

    assert environment["SUB2API_KEY"] == "worker-secret"
    assert "AIWORKER_LLM_CONFIG_KEY" not in environment


def test_build_worker_env_removes_playwright_incompatible_node_proxy_option(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=4096 --use-env-proxy")

    environment = build_worker_env()

    assert environment["NODE_OPTIONS"] == "--max-old-space-size=4096"
