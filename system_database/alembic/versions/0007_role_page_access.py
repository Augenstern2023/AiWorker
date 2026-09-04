# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/system_database\alembic\versions\0007_role_page_access.py
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

# Copyright (c) 2025 relakkes@gmail.com

"""Add role page defaults and account deny overrides."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from system_database.schema import role_page_access


revision = "0007_role_page_access"
down_revision = "0006_user_page_access"
branch_labels = None
depends_on = None


_DEFAULT_ROUTES = {
    "selection_operator": (
        "/selection/dashboard", "/selection/ranks", "/selection/hits",
        "/selection/topics", "/selection/dramas",
    ),
    "creative_planner": ("/planning/projects",),
    "screenwriter": ("/writing/projects",),
    "project_owner": (
        "/admin/roles", "/admin/audit", "/admin/data", "/admin/drama-player",
        "/selection/dashboard", "/selection/ranks", "/selection/hits",
        "/selection/topics", "/selection/dramas",
    ),
}


def upgrade() -> None:
    op.add_column(
        "user_page_access",
        sa.Column("is_allowed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    role_page_access.create(bind=op.get_bind())
    for role_code, routes in _DEFAULT_ROUTES.items():
        for route in routes:
            op.execute(
                sa.text(
                    """
                    INSERT INTO role_page_access (role_id, route)
                    SELECT id, :route FROM roles WHERE lower(code) = :role_code
                    ON CONFLICT (role_id, route) DO NOTHING
                    """
                ).bindparams(route=route, role_code=role_code)
            )


def downgrade() -> None:
    role_page_access.drop(bind=op.get_bind())
    op.drop_column("user_page_access", "is_allowed")
