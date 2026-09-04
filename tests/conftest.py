# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\conftest.py
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

"""
Pytest configuration and shared fixtures
"""

import pytest
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def pytest_ignore_collect(collection_path: Path, config) -> bool:
    """Ignore suites whose runtime contracts were explicitly retired."""
    del config
    return collection_path.name in {
        # Superseded by the PostgreSQL/Core suites after runtime system SQLite
        # support was removed.  SQLite remains covered only as an import source.
        "test_audit_center.py",
        "test_audit_service.py",
        "test_identity_admin_api.py",
        "test_identity_auth.py",
        "test_identity_permissions.py",
        "test_identity_team_management.py",
        "test_role_workflow_migration.py",
        # Superseded by the current direct-completion and Writer review flows.
        "test_approval_free_system.py",
        "test_creative_workflow_integration.py",
        "test_delivery_permissions.py",
        "test_job_permissions.py",
        "test_project_visibility.py",
        "test_role_workflow_e2e.py",
        "test_selection_stage_snapshots.py",
        "test_workflow_api.py",
        "test_workflow_approvals.py",
        "test_workflow_bootstrap.py",
        "test_workflow_production.py",
        "test_workflow_tasks.py",
        "test_workflow_transitions.py",
    }


@pytest.fixture(scope="session")
def project_root_path():
    """Return project root path"""
    return project_root


@pytest.fixture
def sample_xhs_note():
    """Sample Xiaohongshu note data for testing"""
    return {
        "note_id": "test_note_123",
        "type": "normal",
        "title": "测试标题 Test Title",
        "desc": "这是一个测试描述 This is a test description",
        "video_url": "",
        "time": 1700000000,
        "last_update_time": 1700000000,
        "user_id": "user_123",
        "nickname": "测试用户",
        "avatar": "https://example.com/avatar.jpg",
        "liked_count": 100,
        "collected_count": 50,
        "comment_count": 25,
        "share_count": 10,
        "ip_location": "上海",
        "image_list": "https://example.com/img1.jpg,https://example.com/img2.jpg",
        "tag_list": "测试,编程,Python",
        "note_url": "https://www.xiaohongshu.com/explore/test_note_123",
        "source_keyword": "测试关键词",
        "xsec_token": "test_token_123"
    }


@pytest.fixture
def sample_xhs_comment():
    """Sample Xiaohongshu comment data for testing"""
    return {
        "comment_id": "comment_123",
        "create_time": 1700000000,
        "ip_location": "北京",
        "note_id": "test_note_123",
        "content": "这是一条测试评论 This is a test comment",
        "user_id": "user_456",
        "nickname": "评论用户",
        "avatar": "https://example.com/avatar2.jpg",
        "sub_comment_count": 5,
        "pictures": "",
        "parent_comment_id": 0,
        "like_count": 15
    }


@pytest.fixture
def sample_xhs_creator():
    """Sample Xiaohongshu creator data for testing"""
    return {
        "user_id": "creator_123",
        "nickname": "创作者名称",
        "gender": "女",
        "avatar": "https://example.com/creator_avatar.jpg",
        "desc": "这是创作者简介",
        "ip_location": "广州",
        "follows": 500,
        "fans": 10000,
        "interaction": 50000,
        "tag_list": '{"profession": "设计师", "interest": "摄影"}'
    }
